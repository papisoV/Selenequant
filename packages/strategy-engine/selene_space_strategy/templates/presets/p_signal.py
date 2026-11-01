"""P-Signal reversal system (Kolmogorov probability-space entropy).

Measured quantity
-----------------
Let ``d`` be the bar-to-bar change of OHLC4.  Over an ``n_bars`` window,

    P-Signal = erf( mean(d) / stdev(d) / sqrt(2) )

The error-function squash maps the drift-to-dispersion ratio into [-1, 1]:
values near +1 say price changes are statistically regular (trending), values
near -1 say they are dominated by noise.  An optional SMA smooths the line.

The erf is evaluated with the classic Abramowitz-Stegun rational
approximation (Horner evaluation, 9 coefficients) rather than math.erf --
bit-for-bit fidelity with the ancestor's TradingView port matters more here
than the extra precision math.erf would give, so the coefficient table below
is load-bearing and must not be "upgraded".

Reversal rules
--------------
* long   : line negative AND ticking up  -- chaos starting to organise
* short  : line positive AND ticking down (needs shorts enabled)
* longs fold when the line turns positive while falling; shorts mirror it
* percentage bracket from fill price guards every position unless the base
  class ATR bracket is enabled.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.sma import SMA
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal

# Abramowitz-Stegun 7.1.26 coefficient ladder, evaluated innermost-first.
_ERF_LADDER: tuple[float, ...] = (
    1.00002368,
    0.37409196,
    0.09678418,
    -0.18628806,
    0.27886807,
    -1.13520398,
    1.48851587,
    -0.82215223,
    0.17087277,
)
_ERF_TAIL_CONSTANT: float = 1.26551223


def _rational_erf(x: float) -> float:
    """Abramowitz-Stegun style erf approximation (TradingView-compatible).

    Equivalent to the nested-parenthesis Horner form but written as a
    right-to-left fold over the coefficient ladder, which performs the very
    same arithmetic operations in the very same order.
    """
    t = 1.0 / (1.0 + 0.5 * abs(x))
    acc = 0.0
    for coeff in reversed(_ERF_LADDER):
        acc = coeff + t * acc
    result = 1.0 - t * math.exp(-x * x - _ERF_TAIL_CONSTANT + t * acc)
    return result if x >= 0 else -result


class PSignalStrategy(BacktestStrategyTemplate):
    """Entropy-of-change reversal signals with a percentage bracket."""

    def __init__(
        self,
        symbol: str = "BTC/USDT", initial_capital: float = 100000.0,
        leverage: int = 1, fee_rate: float = 0.0005,
        max_bars: int = 500, long_only: bool = True,
        # observation window + smoothing switch
        n_bars: int = 9, b_smooth: bool = True,
        # bracket percentages
        tp_pct: float = 0.05, sl_pct: float = 0.03,
        enable_long: bool = True, enable_short: bool = False,
        use_atr_stops: bool = False, atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0, atr_tp_mult: float = 3.0,
        **kwargs,
    ) -> None:
        super().__init__(
            symbol, initial_capital, leverage, fee_rate, max_bars,
            long_only, use_atr_stops=use_atr_stops,
            atr_stop_period=atr_stop_period, atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult, **kwargs,
        )

        if n_bars < 4 or n_bars > 100:
            raise ValueError(f"n_bars must be in range 4-100, got {n_bars}")

        self.n_bars, self.b_smooth = n_bars, b_smooth
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.enable_long = enable_long
        self.enable_short = enable_short

        # Armed bracket levels
        self._tp_price = self._sl_price = 0.0

    def get_parameters(self) -> dict[str, Any]:
        merged = super().get_parameters()
        exposed = ("n_bars", "b_smooth", "tp_pct", "sl_pct", "enable_long", "enable_short")
        merged.update({key: getattr(self, key) for key in exposed})
        return merged

    # ------------------------------------------------------------------
    # Indicator
    # ------------------------------------------------------------------

    def _entropy_line(self, data: pd.DataFrame) -> pd.Series:
        """Smoothed-or-raw P-Signal line; slots without a usable ratio read 0.0."""
        typical = data[["open", "high", "low", "close"]].mean(axis=1)
        moves = typical.diff()

        dispersion = moves.rolling(window=self.n_bars).std()
        drift = moves.rolling(window=self.n_bars).mean()

        scaled = drift / dispersion / math.sqrt(2.0)
        scaled = scaled.replace([np.inf, -np.inf], np.nan)

        line = pd.Series(0.0, index=scaled.index)
        usable = scaled.notna() & (dispersion > 0.0)
        line[usable] = scaled[usable].map(_rational_erf)

        if self.b_smooth:
            line = SMA(self.n_bars).calculate(pd.DataFrame({"close": line}))
        return line

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Tick-direction reversal rules plus the percentage bracket."""
        if len(data) < self.n_bars * 2 + 2:
            return SignalType.HOLD

        line = self._entropy_line(data)

        n = len(data) - 1
        level_now = line.iloc[n]
        level_before = line.iloc[n - 1]
        hi_now = data["high"].iloc[n]
        lo_now = data["low"].iloc[n]

        if pd.isna(level_now) or pd.isna(level_before):
            return SignalType.HOLD

        ticking_up = np.sign(level_now - level_before) > 0
        ticking_down = np.sign(level_now - level_before) < 0

        # === ENTRY ===
        if self._position == "flat":
            if self.enable_long and level_now < 0 and ticking_up:
                return SignalType.BUY
            if self.enable_short and not self.long_only and level_now > 0 and ticking_down:
                return SignalType.SELL

        # === EXIT ===
        elif self._position == "long":
            if self._tp_price > 0 and hi_now >= self._tp_price:
                return SignalType.CLOSE_LONG
            if self._sl_price > 0 and lo_now <= self._sl_price:
                return SignalType.CLOSE_LONG
            if level_now > 0 and ticking_down:
                return SignalType.CLOSE_LONG

        elif self._position == "short" and not self.long_only:
            if self._tp_price > 0 and lo_now <= self._tp_price:
                return SignalType.CLOSE_SHORT
            if self._sl_price > 0 and hi_now >= self._sl_price:
                return SignalType.CLOSE_SHORT
            if level_now < 0 and ticking_up:
                return SignalType.CLOSE_SHORT

        return SignalType.HOLD

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Execute via base class, then arm/disarm the bracket."""
        outcome = super()._process_signal(event, signal_type)
        if outcome is None:
            return None

        fill = event.close
        if signal_type == SignalType.BUY:
            if self._use_atr_stops:
                self._sl_price, self._tp_price = self._calculate_atr_stop_levels(fill, "long")
            else:
                self._tp_price = fill * (1.0 + self.tp_pct)
                self._sl_price = fill * (1.0 - self.sl_pct)
        elif signal_type == SignalType.SELL and not self.long_only and self._position == "flat":
            if self._use_atr_stops:
                self._sl_price, self._tp_price = self._calculate_atr_stop_levels(fill, "short")
            else:
                self._tp_price = fill * (1.0 - self.tp_pct)
                self._sl_price = fill * (1.0 + self.sl_pct)
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._tp_price = self._sl_price = 0.0

        return outcome

    def reset(self) -> None:
        """Clear armed levels."""
        super().reset()
        self._tp_price = self._sl_price = 0.0
