"""Cross-Sectional Momentum Deciles — 横截面动量多空策略.

Absorbed from ``momentum-factor-effect-in-stocks.py``
(academic-strategy-catalog). The academic strategy
is a long-short decile portfolio: stocks are ranked by trailing return,
the top decile is bought and the bottom decile is shorted, rebalanced
monthly, with the most recent month skipped in the ranking.

This template transfers the cross-sectional ranking to a single primary
symbol over a universe fed via set_universe_data():
  - the primary symbol's trailing ``lookback``-bar return is ranked
    against the rest of the universe at the current bar
  - rank in the top ``top_quantile``      -> BUY (long)
  - rank in the bottom ``bottom_quantile`` -> SELL (short, if enabled)
  - rank decays out of the quantile       -> CLOSE the open leg

``skip_bars`` mirrors the QC rule that the most recent month's return is
skipped when ranking (short-term reversal / trading frictions). The
monthly rebalance of the source is simplified to a per-bar evaluation,
which the engine's binary position model can express directly.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType


class CrossSectionalMomentumStrategy(BacktestStrategyTemplate):
    """Cross-sectional momentum over a universe, directional on the primary symbol.

    Requires universe close series via set_universe_data() before
    run_backtest() — same contract as RebalancePremiumStrategy.

    Args:
        symbol: Primary symbol; signals are emitted on it. Must be a key in
            the universe data to produce any signal.
        lookback: Trailing bars over which each symbol's return is measured.
        skip_bars: Most recent bars skipped in the return window (QC skips
            the latest month).
        top_quantile: Fraction (0..1) of the universe ranked *above* the
            primary symbol that still counts as "top" -> long entry.
        bottom_quantile: Fraction ranked *below* the primary symbol that
            still counts as "bottom" -> short entry.
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
        skip_bars: int = 0,
        top_quantile: float = 0.2,
        bottom_quantile: float = 0.2,
        enable_long: bool = True,
        enable_short: bool = False,
        **kwargs,
    ) -> None:
        # Direction is fully controlled by enable_long/enable_short, so the
        # base long_only flag is derived from enable_short.
        super().__init__(
            symbol,
            initial_capital,
            leverage,
            fee_rate,
            max_bars,
            long_only=not enable_short,
            **kwargs,
        )
        self.lookback = lookback
        self.skip_bars = skip_bars
        self.top_quantile = top_quantile
        self.bottom_quantile = bottom_quantile
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
                "skip_bars": self.skip_bars,
                "top_quantile": self.top_quantile,
                "bottom_quantile": self.bottom_quantile,
                "enable_long": self.enable_long,
                "enable_short": self.enable_short,
            }
        )
        return params

    @classmethod
    def from_config(cls, config: EngineStrategyConfig) -> "CrossSectionalMomentumStrategy":
        """Construct from an EngineStrategyConfig (factory integration)."""
        if not config.symbols:
            raise ValueError("CrossSectionalMomentumStrategy requires >=1 symbol")
        params = config.parameters or {}
        return cls(
            symbol=config.symbols[0],
            lookback=int(params.get("lookback", 20)),
            skip_bars=int(params.get("skip_bars", 0)),
            top_quantile=float(params.get("top_quantile", 0.2)),
            bottom_quantile=float(params.get("bottom_quantile", 0.2)),
            enable_long=bool(params.get("enable_long", True)),
            enable_short=bool(params.get("enable_short", False)),
        )

    def _momentum_cross_section(self, data: pd.DataFrame) -> pd.Series | None:
        """Trailing-return series over the universe aligned to the current bar.

        Returns a Series indexed by symbol (trailing ``lookback``-bar return
        skipping the last ``skip_bars`` bars), or None if there is not enough
        aligned history. Rows where any symbol is missing a close are dropped
        so the ranking is computed on common timestamps only.
        """
        if not self._universe_data:
            raise ValueError("Universe data not set. Call set_universe_data() first.")

        frames = []
        ts = data["timestamp"].iloc[-1] if "timestamp" in data.columns else None
        for sym, df in self._universe_data.items():
            if "close" not in df.columns or "timestamp" not in df.columns:
                continue
            sub = df[["timestamp", "close"]]
            if ts is not None:
                sub = sub[sub["timestamp"] <= ts]
            frames.append(sub.assign(symbol=sym))
        if not frames:
            raise ValueError("Universe data missing 'timestamp'/'close' columns")

        pivot = (
            pd.concat(frames)
            .pivot_table(index="timestamp", columns="symbol", values="close")
            .sort_index()
            .dropna(how="any")
        )
        if len(pivot) < self.lookback + self.skip_bars + 1:
            return None

        recent = pivot.iloc[-1 - self.skip_bars]
        past = pivot.iloc[-1 - self.skip_bars - self.lookback]
        return recent / past - 1.0  # Series indexed by symbol

    def _rank_above(self, mom: pd.Series) -> float | None:
        """Fraction of the rest of the universe with momentum higher than main."""
        if self.symbol not in mom.index:
            return None
        main_mom = mom[self.symbol]
        others = mom.drop(index=self.symbol)
        if len(others) == 0:
            return None
        return float((others > main_mom).sum() / len(others))

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        if len(data) < 2:
            return SignalType.HOLD

        try:
            mom = self._momentum_cross_section(data)
        except ValueError:
            return SignalType.HOLD
        if mom is None:
            return SignalType.HOLD
        frac_above = self._rank_above(mom)
        if frac_above is None:
            return SignalType.HOLD

        if self._position == "flat":
            if self.enable_long and frac_above <= self.top_quantile:
                return SignalType.BUY
            if self.enable_short and frac_above >= 1 - self.bottom_quantile:
                return SignalType.SELL
            return SignalType.HOLD

        if self._position == "long":
            if frac_above > self.top_quantile:
                return SignalType.CLOSE_LONG
            return SignalType.HOLD

        if self._position == "short":
            if frac_above < 1 - self.bottom_quantile:
                return SignalType.CLOSE_SHORT
            return SignalType.HOLD

        return SignalType.HOLD
