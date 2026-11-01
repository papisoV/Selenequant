"""Funding Arbitrage Strategy — long spot + short perp to capture funding payments.

Implementation:
- Reads `funding_rate` column from secondary data (perp market)
- When funding > funding_threshold and flat → BUY (long spot leg; perp short leg is virtual in backtest)
- When funding < exit_threshold or bars held > max_hold_bars → CLOSE_LONG
- PnL override adds funding credit: funding_rate * qty * leverage * bars_held / 8

Usage:
    strategy = FundingArbStrategy(
        symbol="BTC/USDT",
        funding_threshold=0.0005,  # 0.05% per 8h
        exit_threshold=0.0001,
        max_hold_bars=60,
    )
    strategy.set_secondary_data(perp_data_with_funding_rate)
    result = strategy.run_backtest(events)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType


class FundingArbStrategy(BacktestStrategyTemplate):
    """Funding rate arbitrage: long spot, short perp (virtual), capture funding payment.

    Args:
        symbol: Spot pair symbol (default "BTC/USDT").
        perp_symbol: Perpetual symbol. If None, defaults to f"{symbol}-SWAP".
        funding_threshold: Funding rate (per 8h) required to enter (default 0.0005 = 0.05%).
        exit_threshold: Funding rate below which to exit (default 0.0001).
        max_hold_bars: Maximum bars to hold before forced exit (default 60 ≈ 8h at 1h bars).
        leg_mismatch_recover: If True (default), log + flatten on partial fill in live mode.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        perp_symbol: str | None = None,
        funding_threshold: float = 0.0005,
        exit_threshold: float = 0.0001,
        max_hold_bars: int = 60,
        leg_mismatch_recover: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(symbol=symbol, **kwargs)
        self.perp_symbol = perp_symbol or f"{symbol}-SWAP"
        self.funding_threshold = funding_threshold
        self.exit_threshold = exit_threshold
        self.max_hold_bars = max_hold_bars
        self.leg_mismatch_recover = leg_mismatch_recover
        self._perp_data: pd.DataFrame | None = None
        self._bars_held: int = 0
        self._entry_funding_rate: float = 0.0

    def set_secondary_data(self, perp_data: pd.DataFrame) -> None:
        """Inject perp market data with `funding_rate` column.

        Args:
            perp_data: DataFrame indexed by timestamp with at least a `funding_rate` column
                (decimal per 8h, e.g. 0.0005 = 0.05%).
        """
        self._perp_data = perp_data

    def _current_funding_rate(self, data: pd.DataFrame) -> float | None:
        """Look up funding rate for the latest bar timestamp in perp_data.

        Handles both DatetimeIndex and integer index on `data` (falls back to
        last-known funding rate ≤ ts when exact match fails).
        """
        if self._perp_data is None or "funding_rate" not in self._perp_data.columns:
            return None
        if len(data) == 0 or len(self._perp_data) == 0:
            return None

        perp_idx = self._perp_data.index
        funding_col = self._perp_data["funding_rate"]

        # Try exact-match lookup first (fast path for aligned DatetimeIndex)
        ts = data.index[-1]
        try:
            matches = funding_col.loc[[ts]] if ts in perp_idx else None
        except (TypeError, KeyError):
            # ts type doesn't match perp_idx dtype (e.g. int vs Timestamp)
            matches = None

        if matches is not None and len(matches) > 0:
            val = matches.iloc[-1]
            if pd.notna(val):
                return float(val)

        # Fallback: last known funding rate ≤ ts (only valid when comparable types)
        try:
            prior = funding_col.loc[perp_idx <= ts].dropna()
        except TypeError:
            return None
        if len(prior) > 0:
            return float(prior.iloc[-1])
        return None

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Generate signal based on funding rate.

        BUY  when flat and funding_rate > funding_threshold (capture high funding payment)
        CLOSE_LONG when funding_rate < exit_threshold (funding no longer worth it)
        CLOSE_LONG when bars_held >= max_hold_bars (timeout)
        """
        funding = self._current_funding_rate(data)
        if funding is None:
            return SignalType.HOLD

        # Track hold duration when in position
        if self._position == "long":
            self._bars_held += 1
            if funding < self.exit_threshold:
                return SignalType.CLOSE_LONG
            if self._bars_held >= self.max_hold_bars:
                return SignalType.CLOSE_LONG
            return SignalType.HOLD

        # Entry: flat + funding exceeds threshold
        if self._position == "flat" and funding > self.funding_threshold:
            self._bars_held = 0
            self._entry_funding_rate = funding
            return SignalType.BUY

        return SignalType.HOLD

    def _calculate_pnl(self, exit_price: float, direction: str) -> float:
        """Override PnL to add funding rate credit (long spot receives funding when perp short).

        Funding is paid by perp longs to perp shorts every 8h. Our virtual short perp leg
        receives funding_rate * qty * leverage * bars_held / 8.

        Implementation note: super()._calculate_pnl mutates self._capital (adds net_pnl).
        We capture qty BEFORE the super() call so the funding credit uses the correct
        (pre-realization) capital — otherwise qty would be computed against inflated capital.
        """
        # Capture qty BEFORE super() mutates self._capital
        frac = self._position_fraction_at_entry
        entry_qty = (self._capital * frac) / self._entry_price if self._entry_price > 0 else 0.0

        base_pnl = super()._calculate_pnl(exit_price, direction)
        if direction != "long":
            return base_pnl

        # Funding credit: only when we held the long position
        if self._bars_held <= 0 or self._entry_price <= 0:
            return base_pnl

        # Snapshot funding rate at entry (best-effort: true average would require
        # tracking funding at each bar, deferred for v2)
        funding = self._entry_funding_rate
        # bars_held / 8 because funding is paid per 8h interval
        funding_credit = funding * entry_qty * self.leverage * (self._bars_held / 8.0)

        # Adjust capital delta for credit (base already added net_pnl)
        self._capital += funding_credit
        return base_pnl + funding_credit

    def get_parameters(self) -> dict[str, Any]:
        """Expose custom parameters."""
        params = super().get_parameters()
        params.update({
            "perp_symbol": self.perp_symbol,
            "funding_threshold": self.funding_threshold,
            "exit_threshold": self.exit_threshold,
            "max_hold_bars": self.max_hold_bars,
            "leg_mismatch_recover": self.leg_mismatch_recover,
        })
        return params
