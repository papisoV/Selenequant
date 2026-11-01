"""Momentum-deviation system built on a smoothed RSI spread.

The engine measures how far RSI has stretched away from its own moving
average, then smooths that gap once more. While the smoothed gap holds
one side of zero the momentum regime is one-sided; flipping sides marks
the rotation point this template trades.

Per-bar pipeline:
    momentum     = RSI(close, ``rsi_len``)
    baseline     = MA(momentum, ``ma_len1``)
    gap          = momentum - baseline
    smoothed_gap = MA(gap, ``ma_len2``)

Trading rules:
    smoothed_gap crosses above 0 -> open long  (bullish rotation)
    smoothed_gap crosses below 0 -> open short (bearish rotation)

Exits are percentage brackets around the fill price, or ATR-derived
levels when ``use_atr_stops`` is switched on.

Note: ``rsi_src`` is accepted for interface compatibility with sibling
templates; the momentum leg consistently reads the close column, which
matches the behaviour this template has always exhibited downstream.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.indicators.ema import EMA
from selene_space_strategy.indicators.rsi import RSI
from selene_space_strategy.indicators.sma import SMA
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class RSIMADeltaStrategy(BacktestStrategyTemplate):
    """Trades rotations of a twice-smoothed RSI-minus-MA gap.

    A rising RSI above its own average pushes the gap positive; the extra
    smoothing layer suppresses single-bar jitter so only sustained
    rotations fire entries. Bracket exits (fixed or ATR-scaled) manage
    the trade once armed.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        # momentum-leg knobs
        rsi_len: int = 14,
        ma_len1: int = 14,
        ma_len2: int = 7,
        ma_type: str = "ema",
        rsi_src: str = "close",
        # bracket percentages
        tp_pct: float = 0.05,
        sl_pct: float = 0.03,
        enable_long: bool = True,
        enable_short: bool = False,
        # ADX regime gate
        trend_filter_enabled: bool = False,
        adx_length: int = 14,
        adx_threshold: float = 25.0,
        # volatility-scaled brackets
        use_atr_stops: bool = False,
        atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0,
        atr_tp_mult: float = 3.0,
        # forwarded to the template base
        **kwargs,
    ) -> None:
        """Create the momentum-deviation strategy (see module header)."""
        super().__init__(
            symbol, initial_capital, leverage, fee_rate, max_bars, long_only,
            use_atr_stops=use_atr_stops,
            atr_stop_period=atr_stop_period,
            atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult,
            **kwargs,
        )

        (
            self.rsi_len, self.ma_len1, self.ma_len2,
            self.ma_type, self.rsi_src,
            self.tp_pct, self.sl_pct,
            self.enable_long, self.enable_short,
            self.trend_filter_enabled, self.adx_length, self.adx_threshold,
        ) = (
            rsi_len, ma_len1, ma_len2,
            ma_type.lower(), rsi_src.lower(),
            tp_pct, sl_pct,
            enable_long, enable_short,
            trend_filter_enabled, adx_length, adx_threshold,
        )

        # Armed bracket levels; 0.0 means disarmed.
        self._tp_price: float = 0.0
        self._sl_price: float = 0.0

    def get_parameters(self) -> dict[str, Any]:
        merged = {
            **super().get_parameters(),
            "rsi_len": self.rsi_len,
            "ma_len1": self.ma_len1,
            "ma_len2": self.ma_len2,
            "ma_type": self.ma_type,
            "rsi_src": self.rsi_src,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "enable_long": self.enable_long,
            "enable_short": self.enable_short,
            "trend_filter_enabled": self.trend_filter_enabled,
            "adx_length": self.adx_length,
            "adx_threshold": self.adx_threshold,
        }
        return merged

    def _average(self, series: pd.Series, span: int) -> pd.Series:
        """Average ``series`` with the configured smoothing family."""
        carrier = pd.DataFrame({"close": series})
        if self.ma_type == "ema":
            return EMA(span).calculate(carrier)
        return SMA(span).calculate(carrier)

    def _smoothed_gap(self, data: pd.DataFrame) -> pd.Series:
        """Full momentum-deviation chain, collapsed to the traded line."""
        momentum = RSI(self.rsi_len).calculate(data)
        baseline = self._average(momentum, self.ma_len1)
        return self._average(momentum - baseline, self.ma_len2)

    def _entry_intent(
        self, data: pd.DataFrame, gap_now: float, gap_before: float
    ) -> SignalType:
        """Rotation entries, optionally gated by the ADX regime filter."""
        if self.trend_filter_enabled and self.enable_long:
            trending, _ = self.check_trend_filter(
                data, self.adx_length, self.adx_threshold, direction="long"
            )
            if not trending:
                return SignalType.HOLD

        if self.enable_long and gap_before < 0.0 < gap_now:
            return SignalType.BUY

        if self.enable_short and not self.long_only:
            if self.trend_filter_enabled:
                trending, _ = self.check_trend_filter(
                    data, self.adx_length, self.adx_threshold, direction="short"
                )
                if not trending:
                    return SignalType.HOLD
            if gap_before > 0.0 > gap_now:
                return SignalType.SELL

        return SignalType.HOLD

    def _long_exit(self, data: pd.DataFrame) -> SignalType:
        """Bracket check for an open long: target first, then stop."""
        peak = data["high"].iloc[-1]
        trough = data["low"].iloc[-1]
        if self._tp_price > 0 and peak >= self._tp_price:
            return SignalType.CLOSE_LONG
        if self._sl_price > 0 and trough <= self._sl_price:
            return SignalType.CLOSE_LONG
        return SignalType.HOLD

    def _short_exit(self, data: pd.DataFrame) -> SignalType:
        """Bracket check for an open short: target first, then stop."""
        peak = data["high"].iloc[-1]
        trough = data["low"].iloc[-1]
        if self._tp_price > 0 and trough <= self._tp_price:
            return SignalType.CLOSE_SHORT
        if self._sl_price > 0 and peak >= self._sl_price:
            return SignalType.CLOSE_SHORT
        return SignalType.HOLD

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Decide the action for the newest bar from the smoothed gap."""
        warmup = max(self.rsi_len, self.ma_len1, self.ma_len2) + 2
        if len(data) < warmup:
            return SignalType.HOLD

        gap = self._smoothed_gap(data)
        gap_now = float(gap.iloc[-1])
        gap_before = float(gap.iloc[-2])
        if pd.isna(gap_now) or pd.isna(gap_before):
            return SignalType.HOLD

        if self._position == "flat":
            return self._entry_intent(data, gap_now, gap_before)
        if self._position == "long":
            return self._long_exit(data)
        if self._position == "short" and not self.long_only:
            return self._short_exit(data)
        return SignalType.HOLD

    def _arm_brackets(self, fill_price: float, side: str) -> None:
        """Fix the exit levels for a freshly opened position."""
        if self._use_atr_stops:
            self._sl_price, self._tp_price = self._calculate_atr_stop_levels(
                fill_price, side
            )
        elif side == "long":
            self._tp_price = fill_price * (1 + self.tp_pct)
            self._sl_price = fill_price * (1 - self.sl_pct)
        else:
            self._tp_price = fill_price * (1 - self.tp_pct)
            self._sl_price = fill_price * (1 + self.sl_pct)

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Dispatch through the base, then arm or clear bracket levels."""
        dispatched = super()._process_signal(event, signal_type)
        if dispatched is None:
            return None

        if signal_type == SignalType.BUY:
            self._arm_brackets(event.close, "long")
        elif signal_type == SignalType.SELL and not self.long_only and self._position == "flat":
            self._arm_brackets(event.close, "short")
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._tp_price = 0.0
            self._sl_price = 0.0

        return dispatched

    def reset(self) -> None:
        """Drop armed levels along with the base state."""
        super().reset()
        self._tp_price = 0.0
        self._sl_price = 0.0
