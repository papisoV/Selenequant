"""Rebalancing Premium Strategy — 再平衡溢价策略.

Absorbed from ``rebalancing-premium-in-cryptocurrencies.py``
(academic-strategy-catalog). The academic strategy is
a two-leg portfolio:

  - LONG:  equal-weight basket of a universe, rebalanced daily
  - SHORT: the same basket held passively (buy-and-hold drift)

PnL source = "rebalancing premium" / volatility harvesting: when returns
are volatile and cross-sectionally diverse, the rebalanced version
systematically outperforms the drifting version.

This template computes the rebalancing-premium factor across a universe
(via set_universe_data) and emits directional exposure on the primary
symbol:
  - premium harvested over `lookback` bars > entry_threshold -> BUY
  - premium decays below exit_threshold                     -> CLOSE

The two-leg execution (rebalanced long / drift short) belongs to the
multi_asset_engine + rebalance_executor path; this template is the
factor-signal skeleton for it.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType


class RebalancePremiumStrategy(BacktestStrategyTemplate):
    """Rebalancing premium (volatility harvesting) factor strategy.

    Requires universe close series via set_universe_data() before
    run_backtest() — same contract as
    PairsTradingStrategy.set_secondary_data().

    Args:
        symbol: Primary symbol; signals are emitted on it.
        lookback: Trailing bars over which to measure the premium delta.
        entry_threshold: Cumulative premium delta to go long.
        exit_threshold: Premium delta below which to exit long.
        enable_long / enable_short: Direction switches.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        lookback: int = 20,
        entry_threshold: float = 0.01,
        exit_threshold: float = 0.0,
        enable_long: bool = True,
        enable_short: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(
            symbol,
            initial_capital,
            leverage,
            fee_rate,
            max_bars,
            long_only,
            **kwargs,
        )
        self.lookback = lookback
        self.entry_threshold = entry_threshold
        self.exit_threshold = exit_threshold
        self.enable_long = enable_long
        self.enable_short = enable_short
        self._universe_data: dict[str, pd.DataFrame] = {}

    def set_universe_data(self, data: dict[str, pd.DataFrame]) -> None:
        """Set close series for the universe.

        Each DataFrame must have "timestamp" (epoch ms) and "close" columns.
        Must be called before run_backtest().
        """
        self._universe_data = data

    def get_parameters(self) -> dict[str, Any]:
        params = super().get_parameters()
        params.update(
            {
                "lookback": self.lookback,
                "entry_threshold": self.entry_threshold,
                "exit_threshold": self.exit_threshold,
                "enable_long": self.enable_long,
                "enable_short": self.enable_short,
            }
        )
        return params

    @classmethod
    def from_config(cls, config: EngineStrategyConfig) -> "RebalancePremiumStrategy":
        """Construct from an EngineStrategyConfig (factory integration)."""
        if not config.symbols:
            raise ValueError("RebalancePremiumStrategy requires >=1 symbol")
        params = config.parameters or {}
        return cls(
            symbol=config.symbols[0],
            lookback=int(params.get("lookback", 20)),
            entry_threshold=float(params.get("entry_threshold", 0.01)),
            exit_threshold=float(params.get("exit_threshold", 0.0)),
            enable_long=bool(params.get("enable_long", True)),
            enable_short=bool(params.get("enable_short", False)),
        )

    def _premium_series(self) -> pd.Series:
        """Cumulative rebalancing premium over aligned timestamps.

        premium[t] = cum_bal[t] / (drift_value[t] / drift_value[0]) - 1

        where cum_bal compounds the equal-weight per-bar rebalanced return
        and drift_value is the buy-and-hold equal-weight portfolio value.
        Rows with missing closes for any symbol are dropped (common
        timestamps only).
        """
        if not self._universe_data:
            raise ValueError("Universe data not set. Call set_universe_data() first.")

        frames = []
        for sym, df in self._universe_data.items():
            if "close" not in df.columns or "timestamp" not in df.columns:
                continue
            frames.append(df[["timestamp", "close"]].assign(symbol=sym))
        if not frames:
            raise ValueError("Universe data missing 'timestamp'/'close' columns")

        close = (
            pd.concat(frames)
            .pivot_table(index="timestamp", columns="symbol", values="close")
            .sort_index()
            .dropna(how="any")
        )
        if len(close) < 2:
            return pd.Series(dtype=float)

        returns = close.pct_change().iloc[1:]
        rebal_ret = returns.mean(axis=1)  # equal-weight daily rebalance
        cum_bal = (1.0 + rebal_ret).cumprod()

        cum_sym = close / close.iloc[0]  # per-symbol cumulative value
        drift_value = cum_sym.mean(axis=1)  # buy-and-hold equal-weight value
        drift_value = drift_value / drift_value.iloc[0]

        return cum_bal / drift_value - 1.0  # index aligned on returns.index

    def _trailing_premium(self, data: pd.DataFrame) -> float:
        """Premium delta over the trailing `lookback` bars up to now."""
        try:
            premium = self._premium_series()
        except ValueError:
            return 0.0
        if premium.empty:
            return 0.0

        if "timestamp" in data.columns:
            ts = data["timestamp"].iloc[-1]
            premium = premium[premium.index <= ts]

        if len(premium) < self.lookback + 1:
            return 0.0
        window = premium.iloc[-self.lookback - 1:]
        return float(window.iloc[-1] - window.iloc[0])

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        if len(data) < 2:
            return SignalType.HOLD

        prem = self._trailing_premium(data)

        if self._position == "flat":
            if self.enable_long and prem > self.entry_threshold:
                return SignalType.BUY
            if self.enable_short and prem < -self.entry_threshold:
                return SignalType.SELL
            return SignalType.HOLD

        if self._position == "long":
            if prem < self.exit_threshold:
                return SignalType.CLOSE_LONG
            return SignalType.HOLD

        if self._position == "short":
            if prem > -self.exit_threshold:
                return SignalType.CLOSE_SHORT
            return SignalType.HOLD

        return SignalType.HOLD
