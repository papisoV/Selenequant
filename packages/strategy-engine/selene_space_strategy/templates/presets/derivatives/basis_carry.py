"""Basis Carry Strategy — long spot + short quarterly futures to capture basis convergence.

Implementation:
- Reads `DERIV_basis_rate` column from primary data (merged by derivatives_loader)
- When basis > basis_threshold and flat → BUY (long spot leg; futures short leg is virtual in backtest)
- When basis < exit_basis or now >= expiry_ts → CLOSE_LONG
- Missing basis column → HOLD (no crash)

Usage:
    strategy = BasisCarryStrategy(
        symbol="BTC/USDT",
        basis_threshold=0.02,  # 2% annualized basis rate
        exit_basis=0.005,
        expiry_ts=1735689600000,  # optional futures expiry (ms epoch)
    )
    result = strategy.run_backtest(events)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType


class BasisCarryStrategy(BacktestStrategyTemplate):
    """Basis carry: long spot, short futures (virtual), capture basis convergence.

    Args:
        symbol: Spot pair symbol (default "BTC/USDT").
        futures_symbol: Futures symbol. If None, defaults to f"{symbol}-QUARTERLY".
        basis_threshold: Annualized basis rate required to enter (default 0.02 = 2%).
        exit_basis: Basis rate below which to exit (default 0.005 = 0.5%).
        expiry_ts: Optional futures expiry in ms epoch. Position closes at expiry.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        futures_symbol: str | None = None,
        basis_threshold: float = 0.02,
        exit_basis: float = 0.005,
        expiry_ts: int | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(symbol=symbol, **kwargs)
        self.futures_symbol = futures_symbol or f"{symbol}-QUARTERLY"
        self.basis_threshold = basis_threshold
        self.exit_basis = exit_basis
        self.expiry_ts = expiry_ts

    def set_secondary_data(self, futures_data: pd.DataFrame) -> None:
        """Inject futures market data (optional — for live mode).

        For backtest, basis rate is expected to be merged into primary data as
        `DERIV_basis_rate` column by derivatives_loader.
        """
        # Stored for potential live-mode use; backtest reads from primary data.
        self._futures_data = futures_data  # type: ignore[attr-defined]

    def _current_basis_rate(self, data: pd.DataFrame) -> float | None:
        """Read basis rate from primary data's DERIV_basis_rate column."""
        if "DERIV_basis_rate" not in data.columns or len(data) == 0:
            return None
        val = data["DERIV_basis_rate"].iloc[-1]
        if pd.isna(val):
            return None
        return float(val)

    def _current_timestamp(self, data: pd.DataFrame) -> int | None:
        """Get latest bar timestamp (ms epoch) if available."""
        if len(data) == 0:
            return None
        if "timestamp" in data.columns:
            ts = data["timestamp"].iloc[-1]
            try:
                return int(ts)
            except (ValueError, TypeError):
                return None
        # DatetimeIndex → ms epoch (treat naive timestamps as UTC to avoid local-tz drift)
        try:
            idx = data.index[-1]
            # pandas Timestamp always has .timestamp() — for tz-naive it uses local
            # time, so coerce to UTC first when possible.
            if hasattr(idx, "tz"):
                if idx.tz is None:
                    # tz-naive: interpret as UTC
                    idx = idx.tz_localize("UTC")  # type: ignore[union-attr]
                return int(idx.timestamp() * 1000)  # type: ignore[union-attr]
        except (AttributeError, ValueError, TypeError):
            pass
        return None

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Generate signal based on basis rate.

        BUY  when flat and basis > basis_threshold
        CLOSE_LONG when basis < exit_basis (basis reversed)
        CLOSE_LONG when now >= expiry_ts (futures expiry)
        HOLD when DERIV_basis_rate column missing
        """
        basis = self._current_basis_rate(data)
        if basis is None:
            return SignalType.HOLD

        # Expiry check (close position regardless of basis)
        if self._position == "long" and self.expiry_ts is not None:
            now_ts = self._current_timestamp(data)
            if now_ts is not None and now_ts >= self.expiry_ts:
                return SignalType.CLOSE_LONG

        if self._position == "long":
            if basis < self.exit_basis:
                return SignalType.CLOSE_LONG
            return SignalType.HOLD

        if self._position == "flat" and basis > self.basis_threshold:
            return SignalType.BUY

        return SignalType.HOLD

    def get_parameters(self) -> dict[str, Any]:
        """Expose custom parameters."""
        params = super().get_parameters()
        params.update({
            "futures_symbol": self.futures_symbol,
            "basis_threshold": self.basis_threshold,
            "exit_basis": self.exit_basis,
            "expiry_ts": self.expiry_ts,
        })
        return params
