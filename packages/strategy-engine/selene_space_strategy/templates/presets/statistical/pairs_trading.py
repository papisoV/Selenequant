"""Two-leg statistical arbitrage on a hedge-ratio spread.

Mechanics:
    1. Align asset A and asset B closes on their shared timestamps.
    2. Estimate the hedge ratio once, OLS-style:
       ``beta = cov(A, B) / var(B)`` (cached after the first pass).
    3. Spread = A - beta * B.
    4. Certify the spread is mean-reverting with a rescaled-range Hurst
       exponent; a reading at or above ``hurst_threshold`` disarms all
       actions.
    5. Standardize the spread over a rolling window and trade its
       Z-score: deeply negative -> buy the cheap leg, deeply positive ->
       sell the rich leg, collapse toward zero -> flatten.

Missing secondary data or any lookup error degrades to HOLD so a
backtest never crashes on absent input -- an explicit behavioural
contract locked by tests.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType


class PairsTradingStrategy(BacktestStrategyTemplate):
    """Spread-reversion between two correlated instruments.

    The primary symbol rides in ``self.symbol``; the counterpart lives in
    :attr:`symbol_b` and must be supplied through :meth:`set_secondary_data`
    before :meth:`run_backtest` can trade. Use :meth:`from_config` to
    build the instance straight from an :class:`EngineStrategyConfig`.
    """

    def __init__(
        self,
        symbol_a: str,
        symbol_b: str,
        entry_z: float = 2.0,
        exit_z: float = 0.5,
        hurst_threshold: float = 0.5,
        lookback: int = 20,
        **kwargs,
    ) -> None:
        super().__init__(symbol=symbol_a, **kwargs)
        self.symbol_b = symbol_b
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.hurst_threshold = hurst_threshold
        self.lookback = lookback
        self._data_b: pd.DataFrame | None = None
        self._hedge_ratio: float | None = None

    def set_secondary_data(self, data_b: pd.DataFrame) -> None:
        """Register the counterpart frame (needs a ``close`` column).

        Must run before :meth:`run_backtest`; until then every signal
        degrades to HOLD.
        """
        self._data_b = data_b

    @classmethod
    def from_config(cls, config: EngineStrategyConfig) -> "PairsTradingStrategy":
        """Derive the pair from a config's first two symbols plus params.

        Raises:
            ValueError: when fewer than two symbols are configured.
        """
        if len(config.symbols) < 2:
            raise ValueError(
                f"PairsTradingStrategy requires >=2 symbols, got {len(config.symbols)}"
            )
        overrides = config.parameters or {}
        return cls(
            symbol_a=config.symbols[0],
            symbol_b=config.symbols[1],
            entry_z=float(overrides.get("entry_z", 2.0)),
            exit_z=float(overrides.get("exit_z", 0.5)),
            hurst_threshold=float(overrides.get("hurst_threshold", 0.5)),
            lookback=int(overrides.get("lookback", 20)),
        )

    def _spread_line(self, data_a: pd.DataFrame) -> pd.Series:
        """Hedged spread of A against B on their common timestamps."""
        if self._data_b is None:
            raise ValueError(
                "Secondary data not set. Call set_secondary_data() first."
            )

        shared = data_a.index.intersection(self._data_b.index)
        leg_a = data_a.loc[shared, "close"]
        leg_b = self._data_b.loc[shared, "close"]

        # Too little overlap to say anything -- emit a flat line instead.
        if len(leg_a) < 10:
            return pd.Series(0.0, index=data_a.index)

        if self._hedge_ratio is None:
            joint = np.stack([leg_a.to_numpy(), leg_b.to_numpy()])
            covariance = np.cov(joint)[0, 1]
            variance_b = np.var(leg_b)
            self._hedge_ratio = covariance / variance_b if variance_b > 0 else 1.0

        raw_spread = leg_a - self._hedge_ratio * leg_b
        return (
            raw_spread.reindex(data_a.index)
            .ffill()
            .fillna(0.0)
        )

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Trade the standardized spread under a Hurst regime gate."""
        try:
            spread = self._spread_line(data)
            spread_frame = pd.DataFrame({"close": spread})

            exponent = self.compute("hurst", spread_frame, max_lag=20).last()
            if exponent is None or exponent >= self.hurst_threshold:
                return SignalType.HOLD

            deviation = self.compute("zscore", spread_frame, window=self.lookback).last()
            if deviation is None:
                return SignalType.HOLD

            if deviation < -self.entry_z:
                return SignalType.BUY
            if deviation > self.entry_z:
                return SignalType.SELL
            if abs(deviation) < self.exit_z:
                return SignalType.CLOSE_LONG

            return SignalType.HOLD

        except (ValueError, KeyError):
            return SignalType.HOLD
