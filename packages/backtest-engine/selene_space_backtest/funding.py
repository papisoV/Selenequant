"""Funding rate and borrow fee models for backtesting derivatives.

Provides the protocol for applying periodic funding charges to perpetual
swap positions, and borrow fees to short stock positions.

Real funding data is wired in: ``HistoricalFundingModel`` consumes the
output of ``funding_rate_fetcher.FundingRateFetcher.get_history()``
(snapshot via ``get_all()`` feeds the live funding-rate feed), and the
injection point in ``BacktestEngine.run()`` applies it per bar. The
``Portfolio``/``Position``/``Metrics`` shape carries the resulting cash
flows.

See ``HistoricalFundingModel`` below for the per-bar settlement semantics.
"""

from __future__ import annotations

import bisect
from typing import Protocol


class FundingRateModel(Protocol):
    """Protocol for computing funding cost on a position over a holding period.

    Implementations should be deterministic given (notional, side, timestamp)
    so backtests are reproducible.

    Args:
        position_notional: Absolute notional value of the position in quote
            currency at the timestamp.
        position_side: ``"long"`` or ``"short"``. Perp funding convention:
            positive funding rate → longs pay shorts; negative → shorts pay.
        timestamp: ms epoch at which to evaluate funding.

    Returns:
        Funding amount in quote currency. Positive = deducted from cash
        (paid by the holder); negative = credited to cash (received).
    """

    def apply_funding(
        self,
        position_notional: float,
        position_side: str,
        timestamp: int,
    ) -> float: ...


class NoFunding:
    """Explicit no-op funding model.

    Returns 0.0 for every position. Use this instead of ``None`` when you
    want to make the intent explicit (e.g. spot-only backtests). For
    backwards-compatible default behavior, leave ``BacktestConfig.funding_model``
    as ``None`` — that path skips funding entirely.
    """

    def apply_funding(
        self,
        position_notional: float,
        position_side: str,
        timestamp: int,
    ) -> float:
        return 0.0


class HistoricalFundingModel:
    """Funding model backed by a sorted list of historical (timestamp, rate) pairs.

    Designed to consume the output of
    ``funding_rate_fetcher.FundingRateFetcher.get_history()`` — convert
    each ``FundingRateItem`` to ``(timestamp, funding_rate)`` and pass in.
    The model is kept dependency-free (no import of market-data package)
    so backtest-engine does not gain a runtime dependency on market-data.

    Semantics:
        - At each ``apply_funding(ts)`` call, find the most recent funding
          rate at or before ``ts`` using ``bisect``.
        - If no funding event has occurred yet (idx == -1), return 0.
        - If the same idx as the last call, return 0 (already applied —
          prevents double-counting across multiple klines between funding
          events).
        - If idx advanced, apply ALL rates between last_applied_idx+1 and
          idx inclusive (handles catch-up when the position was held across
          multiple funding events without apply_funding being called at
          each boundary — e.g. multi-bar gaps in the kline series).

    Funding convention:
        - Positive rate → longs pay, shorts receive (deducted from long
          cash, credited to short cash).
        - Negative rate → shorts pay, longs receive.
        - Returned amount is positive (cash deducted) when the holder pays.

    The model is stateful: ``_last_applied_idx`` advances across calls
    within a single backtest run. Reset by constructing a new instance
    for each run (BacktestEngine creates the model once at config time;
    for multiple runs, construct a fresh instance per run).
    """

    def __init__(self, rates: list[tuple[int, float]]) -> None:
        """Initialize with historical funding rates.

        Args:
            rates: List of (timestamp_ms, funding_rate) tuples. Unsorted
                input is tolerated — sorted here. Zero-rate entries are
                filtered (they have no economic effect and bloat the index).
        """
        sorted_rates = sorted(
            (r for r in rates if r[1] != 0.0),
            key=lambda r: r[0],
        )
        self._timestamps: list[int] = [r[0] for r in sorted_rates]
        self._rates: list[float] = [r[1] for r in sorted_rates]
        self._last_applied_idx: int = -1

    def apply_funding(
        self,
        position_notional: float,
        position_side: str,
        timestamp: int,
    ) -> float:
        # bisect_right finds the insertion point to the right of timestamp,
        # so idx-1 is the largest index with _timestamps[idx] <= timestamp.
        idx = bisect.bisect_right(self._timestamps, timestamp) - 1
        if idx < 0 or idx <= self._last_applied_idx:
            # No funding event yet, or already applied up to this idx.
            return 0.0

        total = 0.0
        for i in range(self._last_applied_idx + 1, idx + 1):
            rate = self._rates[i]
            if position_side == "long":
                # Long pays positive rate, receives negative rate.
                total += position_notional * rate
            else:  # short
                # Short receives positive rate, pays negative rate.
                total -= position_notional * rate
        self._last_applied_idx = idx
        return total


class BorrowFeeModel(Protocol):
    """Protocol for computing borrow fee on a short stock position.

    Borrow fees apply only to short stock positions (the holder of the
    short pays the lender for borrowing the shares). Long positions and
    perpetual swaps are not subject to borrow fees (perpetuals use funding
    instead, see :class:`FundingRateModel`).

    Implementations should be deterministic given (notional, timestamp)
    so backtests are reproducible.

    Args:
        position_notional: Absolute notional value of the short position in
            quote currency at the timestamp.
        timestamp: ms epoch at which to evaluate borrow fee.

    Returns:
        Borrow fee amount in quote currency. Positive = deducted from cash
        (paid by the short holder). Negative values are not expected but
        not forbidden — implementations may credit in rebate scenarios.
    """

    def apply_borrow_fee(
        self,
        position_notional: float,
        timestamp: int,
    ) -> float: ...


class NoBorrowFee:
    """Explicit no-op borrow fee model. Symmetric to :class:`NoFunding`.

    Returns 0.0 for every short position. Use this instead of ``None`` when
    you want to make the intent explicit. For backwards-compatible default
    behavior, leave ``BacktestConfig.borrow_fee_model`` as ``None``.
    """

    def apply_borrow_fee(
        self,
        position_notional: float,
        timestamp: int,
    ) -> float:
        return 0.0
