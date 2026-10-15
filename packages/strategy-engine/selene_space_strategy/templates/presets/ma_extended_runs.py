"""Extended-run fade system ("MA extended runs").

Thesis
------
A long unbroken stretch of closes on one side of a baseline average marks a
stretched move.  The system stands aside during the stretch and fades the
moment it ends:

  long  : ``deviation`` consecutive closes ABOVE baseline, latest close back BELOW
  short : mirror image

The streak is measured by a bounded backward scan at entry-evaluation time.
(The ancestor implementation also maintained live per-bar streak counters and
since-entry extreme trackers; those were write-only bookkeeping that fed no
decision and are intentionally not carried over.)

Layered exits, evaluated top-down every bar while positioned:
  1. time stop     -- position older than ``hold_bars`` bars
  2. breakthrough  -- close touches the best price of the preceding
                      ``breakthrough_lookback`` bars (current bar excluded)
  3. ATR stop      -- fill shifted by ``atr_mult`` x ATR sampled at fill time
  4. fixed stop    -- fill shifted by ``stop_loss_pct``
  5. profit target -- fill shifted by ``take_profit_pct``
With ``use_atr_stops`` enabled, layers 3-5 collapse onto the base class ATR
bracket instead.

Baseline variants: ``ema`` delegates to the package indicator, ``lsma``
projects each rolling window's least-squares fit to its right edge, and both
``sma`` and any unrecognised value land on the package SMA.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.atr import ATR
from selene_space_strategy.indicators.ema import EMA
from selene_space_strategy.indicators.sma import SMA
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class MAExtendedRunsStrategy(BacktestStrategyTemplate):
    """Fade the first close back across a baseline after an extended run."""

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        # Baseline parameters
        ma_len: int = 10,
        ma_type: str = "sma",
        deviation: int = 3,
        hold_bars: int = 4,
        breakthrough_lookback: int = 34,
        atr_len: int = 43,
        atr_mult: float = 2.871,
        stop_loss_pct: float = 0.12,
        take_profit_pct: float = 0.96,
        enable_long: bool = True,
        enable_short: bool = False,
        # Trend filter
        trend_filter_enabled: bool = False,
        adx_length: int = 14,
        adx_threshold: float = 25.0,
        # ATR dynamic stops (use_atr_stops routes to the base class bracket)
        use_atr_stops: bool = False,
        atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0,
        atr_tp_mult: float = 3.0,
        # Template base params forwarded
        **kwargs,
    ) -> None:
        super().__init__(
            symbol, initial_capital, leverage, fee_rate, max_bars, long_only,
            use_atr_stops=use_atr_stops,
            atr_stop_period=atr_stop_period,
            atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult,
            **kwargs,
        )

        self.ma_len = ma_len
        self.ma_type = ma_type.lower()
        self.deviation = deviation
        self.hold_bars = hold_bars
        self.breakthrough_lookback = breakthrough_lookback
        self.atr_len = atr_len
        self.atr_mult = atr_mult
        self.stop_loss_pct = stop_loss_pct
        self.take_profit_pct = take_profit_pct
        self.enable_long = enable_long
        self.enable_short = enable_short
        self.trend_filter_enabled = trend_filter_enabled
        self.adx_length = adx_length
        self.adx_threshold = adx_threshold

        # Armed protective levels
        self._atr_stop_price: float = 0.0
        self._fixed_stop_price: float = 0.0
        self._take_profit_price: float = 0.0
        self._age_bars: int = 0

    def get_parameters(self) -> dict:
        merged = super().get_parameters()
        merged.update({
            "ma_len": self.ma_len,
            "ma_type": self.ma_type,
            "deviation": self.deviation,
            "hold_bars": self.hold_bars,
            "breakthrough_lookback": self.breakthrough_lookback,
            "atr_len": self.atr_len,
            "atr_mult": self.atr_mult,
            "stop_loss_pct": self.stop_loss_pct,
            "take_profit_pct": self.take_profit_pct,
            "enable_long": self.enable_long,
            "enable_short": self.enable_short,
            "trend_filter_enabled": self.trend_filter_enabled,
            "adx_length": self.adx_length,
            "adx_threshold": self.adx_threshold,
        })
        return merged

    # ------------------------------------------------------------------
    # Baseline construction
    # ------------------------------------------------------------------

    def _trend_baseline(self, data: pd.DataFrame) -> pd.Series:
        """Moving-average line the runs are measured against."""
        if self.ma_type == "ema":
            return EMA(self.ma_len).calculate(data)
        if self.ma_type == "lsma":
            return self._lsma_points(data["close"], self.ma_len)
        # "sma" and any unrecognised kind land here
        return SMA(self.ma_len).calculate(data)

    def _lsma_points(self, source: pd.Series, period: int) -> pd.Series:
        """Right-edge projection of a per-window least-squares fit.

        Each output point fits a straight line to the trailing ``period``
        closes and reports the fitted value at the newest bar; the first
        ``period - 1`` slots stay NaN.
        """
        design = np.arange(period)
        filled = pd.Series(np.nan, index=source.index)
        raw = source.values
        for pos in range(period - 1, len(source)):
            window = raw[pos - period + 1 : pos + 1]
            slope, intercept = np.polyfit(design, window, 1)
            filled.iloc[pos] = slope * (period - 1) + intercept
        return filled

    # ------------------------------------------------------------------
    # Streak measurement
    # ------------------------------------------------------------------

    def _streak_before(self, close: pd.Series, baseline: pd.Series, n: int, above: bool) -> int:
        """Length of the run ending at bar ``n - 1``.

        Walks backwards while closes sit on the requested side of the
        baseline.  The scan horizon is capped at ``deviation + 9`` bars --
        reproducing the ancestor's ``range(last_idx - 1, max(-1, last_idx -
        deviation - 10), -1)`` iteration count exactly, including its
        off-by-one shape.
        """
        matched = 0
        for step in range(1, min(n + 1, self.deviation + 10)):
            j = n - step
            sits = (
                close.iloc[j] > baseline.iloc[j]
                if above
                else close.iloc[j] < baseline.iloc[j]
            )
            if not sits:
                break
            matched += 1
        return matched

    # ------------------------------------------------------------------
    # Signals
    # ------------------------------------------------------------------

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Run-end fade entries plus the five-layer exit ladder."""
        if len(data) <= self.ma_len:
            return SignalType.HOLD

        n = len(data) - 1
        closes = data["close"]
        tops = data["high"]
        bottoms = data["low"]

        baseline = self._trend_baseline(data)
        atr_line = ATR(self.atr_len).calculate(data)

        c_now = closes.iloc[n]
        base_now = baseline.iloc[n]
        base_before = baseline.iloc[n - 1]

        if pd.isna(base_now) or pd.isna(base_before) or pd.isna(atr_line.iloc[n]):
            return SignalType.HOLD

        # === ENTRY ===
        if self._position == "flat":
            # A failed long-side trend gate aborts the whole bar, shorts included.
            if self.trend_filter_enabled and self.enable_long:
                trending, _ = self.check_trend_filter(
                    data, self.adx_length, self.adx_threshold, direction="long"
                )
                if not trending:
                    return SignalType.HOLD

            if self.enable_long and c_now < base_now:
                if self._streak_before(closes, baseline, n, above=True) >= self.deviation:
                    return SignalType.BUY

            if self.enable_short and not self.long_only:
                if self.trend_filter_enabled:
                    trending, _ = self.check_trend_filter(
                        data, self.adx_length, self.adx_threshold, direction="short"
                    )
                    if not trending:
                        return SignalType.HOLD
                if c_now > base_now:
                    if self._streak_before(closes, baseline, n, above=False) >= self.deviation:
                        return SignalType.SELL

        # === EXIT LADDER ===
        elif self._position == "long":
            self._age_bars += 1
            if self._age_bars > self.hold_bars:
                return SignalType.CLOSE_LONG
            if self.breakthrough_lookback > 0:
                prior_high = tops.iloc[max(0, n - self.breakthrough_lookback) : n].max()
                if c_now >= prior_high:
                    return SignalType.CLOSE_LONG
            if self._atr_stop_price > 0 and bottoms.iloc[n] <= self._atr_stop_price:
                return SignalType.CLOSE_LONG
            if self._fixed_stop_price > 0 and bottoms.iloc[n] <= self._fixed_stop_price:
                return SignalType.CLOSE_LONG
            if self._take_profit_price > 0 and tops.iloc[n] >= self._take_profit_price:
                return SignalType.CLOSE_LONG

        elif self._position == "short" and not self.long_only:
            self._age_bars += 1
            if self._age_bars > self.hold_bars:
                return SignalType.CLOSE_SHORT
            if self.breakthrough_lookback > 0:
                prior_low = bottoms.iloc[max(0, n - self.breakthrough_lookback) : n].min()
                if c_now <= prior_low:
                    return SignalType.CLOSE_SHORT
            if self._atr_stop_price > 0 and tops.iloc[n] >= self._atr_stop_price:
                return SignalType.CLOSE_SHORT
            if self._fixed_stop_price > 0 and tops.iloc[n] >= self._fixed_stop_price:
                return SignalType.CLOSE_SHORT
            if self._take_profit_price > 0 and bottoms.iloc[n] <= self._take_profit_price:
                return SignalType.CLOSE_SHORT

        return SignalType.HOLD

    # ------------------------------------------------------------------
    # Execution
    # ------------------------------------------------------------------

    def _fill_atr(self, event: KlineEvent) -> float:
        """ATR over the trailing ``atr_len`` recorded bars, 0.0 when unavailable."""
        if len(self._bars) < self.atr_len:
            return 0.0
        frame = pd.DataFrame(self._bars[-self.atr_len :])
        sample = ATR(self.atr_len).calculate(frame).iloc[-1]
        return 0.0 if pd.isna(sample) else float(sample)

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Execute via the base class, then maintain the protective levels."""
        outcome = super()._process_signal(event, signal_type)
        if outcome is None:
            return None

        if signal_type in (SignalType.BUY, SignalType.SELL):
            fill = event.close
            if self._use_atr_stops:
                leg = "long" if signal_type == SignalType.BUY else "short"
                self._atr_stop_price, self._take_profit_price = self._calculate_atr_stop_levels(fill, leg)
                self._fixed_stop_price = 0.0
            elif signal_type == SignalType.BUY:
                gap = self._fill_atr(event)
                self._atr_stop_price = fill - self.atr_mult * gap
                self._fixed_stop_price = fill * (1.0 - self.stop_loss_pct)
                self._take_profit_price = fill * (1.0 + self.take_profit_pct)
            else:
                gap = self._fill_atr(event)
                self._atr_stop_price = fill + self.atr_mult * gap
                self._fixed_stop_price = fill * (1.0 + self.stop_loss_pct)
                self._take_profit_price = fill * (1.0 - self.take_profit_pct)
            self._age_bars = 0

        # Deliberate historical quirk: a SELL satisfies both blocks, so a fresh
        # short has every price level wiped on the very bar it opens and can
        # only leave via the time stop or the breakthrough rule.
        if signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT, SignalType.SELL):
            self._age_bars = 0
            self._atr_stop_price = 0.0
            self._fixed_stop_price = 0.0
            self._take_profit_price = 0.0

        return outcome

    def reset(self) -> None:
        """Clear protective levels and the age counter."""
        super().reset()
        self._age_bars = 0
        self._atr_stop_price = 0.0
        self._fixed_stop_price = 0.0
        self._take_profit_price = 0.0
