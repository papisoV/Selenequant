# Ported from AlphaPurify (MIT)
"""Directional transaction-cost models for :class:`~selene_space_factor_research.mining.weight_drift_backtest.WeightDriftBacktest`.

Faithful port of the AlphaPurify fee mapping in ``FactorAnalyzer.calc_stats_for_period``
L780-795: tax / fee / slippage are independent rates and the effective buy/sell fee
depends on ``tax_rate_direction`` (one of ``"both"`` / ``"buy"`` / ``"sell"``).
"""
from __future__ import annotations

from typing import Protocol

_COST_DIRECTIONS = ("both", "buy", "sell")


class CostConfig(Protocol):
    """Structural view of a config carrying the four cost knobs."""

    tax_rate: float
    fee_rate: float
    slippage_rate: float
    tax_rate_direction: str


class CostModel(Protocol):
    """Protocol for a directional transaction-cost model."""

    def compute_buy_sell_fee(self, config: CostConfig) -> tuple[float, float]:
        """Return ``(buy_fee, sell_fee)`` effective rates for ``config``."""
        ...


class AShareCostModel:
    """A-share fee model — the AlphaPurify default.

    Maps ``tax_rate_direction`` to effective buy/sell fee rates (L780-795):

    +-----------+------------------------------------+------------------------------------+
    | direction | buy_fee                            | sell_fee                           |
    +===========+====================================+====================================+
    | ``both``  | tax + fee + slippage               | tax + fee + slippage               |
    | ``buy``   | tax + fee + slippage               | fee + slippage                     |
    | ``sell``  | fee + slippage                     | tax + fee + slippage               |
    +-----------+------------------------------------+------------------------------------+

    Any other direction raises :class:`ValueError` (the L792 ``else`` branch).
    """

    def compute_buy_sell_fee(self, config: CostConfig) -> tuple[float, float]:
        direction = config.tax_rate_direction
        tax = config.tax_rate
        fee = config.fee_rate
        slip = config.slippage_rate

        if direction == "both":
            both = tax + fee + slip
            return both, both
        if direction == "buy":
            return tax + fee + slip, fee + slip
        if direction == "sell":
            return fee + slip, tax + fee + slip
        raise ValueError(
            f"tax_rate_direction can be set to 'buy', 'sell', or 'both', got {direction}"
        )


def validate_tax_rate_direction(direction: str) -> None:
    """Raise :class:`ValueError` unless ``direction`` is a supported value."""
    if direction not in _COST_DIRECTIONS:
        raise ValueError(
            f"tax_rate_direction can be set to 'buy', 'sell', or 'both', got {direction}"
        )


class FundingRateModel(Protocol):
    """Structural view of a funding-rate model (matches backtest-engine's protocol).

    Kept local so :mod:`cost_models` stays standalone — a compatible object is
    anything with an ``apply_funding(position_notional, position_side, timestamp)``
    method returning a funding amount in quote currency.
    """

    def apply_funding(
        self,
        position_notional: float,
        position_side: str,
        timestamp: int,
    ) -> float: ...


class NoFunding:
    """Funding model that never charges — the default when none is supplied."""

    def apply_funding(
        self,
        position_notional: float,
        position_side: str,
        timestamp: int,
    ) -> float:
        return 0.0


class SeleneCryptoCostModel:
    """Crypto perpetual cost model — no tax, optional periodic funding carry.

    Explainable difference vs :class:`AShareCostModel`: crypto spot/perp has no
    stamp duty, so ``compute_buy_sell_fee`` is ``fee + slippage`` on *both* sides
    regardless of ``tax_rate_direction`` and ignores ``config.tax_rate`` entirely.
    The only additional carry is periodic funding, which A-shares never charge:
    ``compute_funding`` delegates to the injected ``funding`` model, so a
    perpetual position pays/receives funding on top of the no-tax transaction fee.

    The default ``funding=None`` maps to :class:`NoFunding` (zero carry), keeping
    the model a drop-in ``WeightDriftConfig.cost_model`` with no funding wiring.

    Per-side state isolation (FD4): each position side routes to an independent
    funding sub-model (``_long_funding`` / ``_short_funding``). Backtest-engine's
    :class:`~selene_space_backtest.funding.HistoricalFundingModel` is stateful
    (it advances ``_last_applied_idx`` per call), so sharing one instance across
    both sides would silently zero the second side at the same timestamp (F-O3).
    ``from_rates`` therefore constructs one instance per side.
    """

    def __init__(self, funding: FundingRateModel | None = None) -> None:
        # M2: injected funding must be state-safe (NoFunding / custom stateless).
        # Structural guard: models carrying _last_applied_idx (e.g. the backtest
        # engine's HistoricalFundingModel) are stateful — a single instance shared
        # across both sides silently zeroes the second side (see F-O3), so reject
        # them here. Contract: this guard is a whitelist signal, not a complete
        # detector — it only recognizes the known stateful marker _last_applied_idx;
        # a future stateful model without that attribute would not be intercepted
        # (protocol evolution is a follow-up). State-safe injection (NoFunding /
        # custom stateless) is always legal, independent of the guard.
        if funding is not None and hasattr(funding, "_last_applied_idx"):
            raise ValueError(
                "inject state-safe funding only (NoFunding / custom stateless); "
                "use SeleneCryptoCostModel.from_rates() for HistoricalFundingModel"
            )
        self.funding: FundingRateModel = funding if funding is not None else NoFunding()
        self._long_funding: FundingRateModel = self.funding
        self._short_funding: FundingRateModel = self.funding

    def compute_buy_sell_fee(self, config: CostConfig) -> tuple[float, float]:
        fee = config.fee_rate + config.slippage_rate
        return fee, fee

    def compute_funding(
        self,
        position_notional: float,
        position_side: str,
        timestamp: int,
    ) -> float:
        m = self._long_funding if position_side == "long" else self._short_funding
        return m.apply_funding(position_notional, position_side, timestamp)

    @classmethod
    def from_rates(cls, rates: list[tuple[int, float]]) -> SeleneCryptoCostModel:
        """Build a model with periodic funding from historical ``(timestamp, rate)`` pairs.

        Wraps backtest-engine's :class:`~selene_space_backtest.funding.HistoricalFundingModel`
        (period catch-up, stateful last-applied index). Imported lazily so the
        common path — ``import selene_space_factor_research`` — never pulls
        backtest-engine.
        """
        from selene_space_backtest.funding import HistoricalFundingModel

        # One independent instance per side — deliberately bypasses __init__
        # single-instance injection (which would trip the M2 guard).
        obj = cls()
        obj._long_funding = HistoricalFundingModel(rates)
        obj._short_funding = HistoricalFundingModel(rates)
        obj.funding = obj._long_funding  # compat shim for existing self.funding refs
        # Note: this shim silently points self.funding at the long-side model.
        # External code must route through compute_funding(position_notional,
        # position_side, timestamp) — reading self.funding directly exposes
        # long-side state and must never be used for reconciliation / double
        # writes. Removable as the protocol evolves.
        return obj
