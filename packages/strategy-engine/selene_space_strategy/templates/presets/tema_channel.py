"""Volatility channel built on triple-smoothed extremes.

Construction:
    mah     = TEMA(high, ``length``)          # smoothed ceiling
    mal     = TEMA(low, ``length``)           # smoothed floor
    range   = true range or plain high-low    # per-bar volatility
    rangema = TEMA(range, ``length``)         # smoothed volatility
    upper   = mah + ``mult`` * rangema
    lower   = mal - ``mult`` * rangema
    midline = (upper + lower) / 2

Trading rules:
    close breaks above ``upper`` -> open long   (breakout confirmed)
    close breaks below ``lower`` -> open short  (breakdown confirmed)
    an open position closes when close crosses back through ``midline``
    percentage / ATR brackets provide the protective layer

The triple EMA removes lag aggressively, so the channel hugs price
while the volatility term keeps it honest during expansion phases.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


def _tema_smooth(series: pd.Series, span: int) -> pd.Series:
    """Triple exponential average: EMA applied three times in a row.

    Each pass uses ``adjust=False`` seeding from the first observation,
    matching the TradingView ``ta.tema`` construction.
    """
    first = series.ewm(span=span, adjust=False).mean()
    second = first.ewm(span=span, adjust=False).mean()
    return second.ewm(span=span, adjust=False).mean()


def _bar_range(data: pd.DataFrame, use_true_range: bool) -> pd.Series:
    """Per-bar volatility measure selected by configuration."""
    if not use_true_range:
        return data["high"] - data["low"]
    prior_close = data["close"].shift()
    legs = {
        "hl": data["high"] - data["low"],
        "hc": (data["high"] - prior_close).abs(),
        "lc": (data["low"] - prior_close).abs(),
    }
    return pd.concat(legs.values(), axis=1).max(axis=1)


class TEMAChannelStrategy(BacktestStrategyTemplate):
    """Channel breakout system with a mid-line retreat exit.

    Both channel edges derive from triple-smoothed extreme prices plus a
    volatility cushion; entries require a full close-through of an edge,
    exits fire when price surrenders the mid-line or hits a bracket.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        # TEMA Channel parameters
        length: int = 20,
        mult: float = 2.0,
        use_true_range: bool = True,
        # Exit parameters
        tp_pct: float = 0.05,
        sl_pct: float = 0.03,
        enable_long: bool = True,
        enable_short: bool = True,
        # ATR dynamic stops
        use_atr_stops: bool = False,
        atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0,
        atr_tp_mult: float = 3.0,
        **kwargs,
    ) -> None:
        """Create the TEMA channel strategy (see module header)."""
        super().__init__(
            symbol, initial_capital, leverage, fee_rate, max_bars, long_only,
            use_atr_stops=use_atr_stops,
            atr_stop_period=atr_stop_period,
            atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult,
            **kwargs,
        )

        (
            self.length, self.mult, self.use_true_range,
            self.tp_pct, self.sl_pct,
            self.enable_long, self.enable_short,
        ) = (length, mult, use_true_range, tp_pct, sl_pct, enable_long, enable_short)

        # Armed bracket levels; 0.0 means disarmed.
        self._tp_price: float = 0.0
        self._sl_price: float = 0.0

    def get_parameters(self) -> dict[str, Any]:
        merged = {
            **super().get_parameters(),
            "length": self.length,
            "mult": self.mult,
            "use_true_range": self.use_true_range,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "enable_long": self.enable_long,
            "enable_short": self.enable_short,
        }
        return merged

    def _channel_edges(self, data: pd.DataFrame) -> dict[str, pd.Series]:
        """Compute every channel line for the whole frame at once."""
        smoothed_high = _tema_smooth(data["high"], self.length)
        smoothed_low = _tema_smooth(data["low"], self.length)
        cushion = _tema_smooth(
            _bar_range(data, self.use_true_range), self.length
        ) * self.mult

        upper = smoothed_high + cushion
        lower = smoothed_low - cushion
        return {"upper": upper, "lower": lower, "mid": (upper + lower) * 0.5}

    @staticmethod
    def _crossed(current_price: float, prior_price: float,
                 line_now: float, line_before: float,
                 direction: str) -> bool:
        """True when price crossed ``direction`` relative to a moving line.

        ``direction="above"`` demands the pair (price, line) flip from
        price-at-or-below to price-above between consecutive bars; the
        mirror applies for ``"below"``.
        """
        if direction == "above":
            return current_price > line_now and prior_price <= line_before
        return current_price < line_now and prior_price >= line_before

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Decide the action for the newest bar from channel geometry."""
        warmup = self.length * 3 + 2
        if len(data) < warmup:
            return SignalType.HOLD

        edges = self._channel_edges(data)
        tip = len(data) - 1

        px_now = data["close"].iloc[tip]
        px_before = data["close"].iloc[tip - 1]
        peak = data["high"].iloc[tip]
        trough = data["low"].iloc[tip]

        upper_now = edges["upper"].iloc[tip]
        lower_now = edges["lower"].iloc[tip]
        mid_now = edges["mid"].iloc[tip]
        if pd.isna(upper_now) or pd.isna(lower_now) or pd.isna(mid_now):
            return SignalType.HOLD

        # === ENTRIES ===
        if self._position == "flat":
            if (self.enable_long
                    and self._crossed(px_now, px_before, upper_now,
                                      edges["upper"].iloc[tip - 1], "above")):
                return SignalType.BUY
            if (self.enable_short
                    and self._crossed(px_now, px_before, lower_now,
                                      edges["lower"].iloc[tip - 1], "below")):
                return SignalType.SELL

        # === EXITS ===
        elif self._position == "long":
            if self._tp_price > 0 and peak >= self._tp_price:
                return SignalType.CLOSE_LONG
            if self._sl_price > 0 and trough <= self._sl_price:
                return SignalType.CLOSE_LONG
            if self._crossed(px_now, px_before, mid_now,
                             edges["mid"].iloc[tip - 1], "below"):
                return SignalType.CLOSE_LONG

        elif self._position == "short" and not self.long_only:
            if self._tp_price > 0 and trough <= self._tp_price:
                return SignalType.CLOSE_SHORT
            if self._sl_price > 0 and peak >= self._sl_price:
                return SignalType.CLOSE_SHORT
            if self._crossed(px_now, px_before, mid_now,
                             edges["mid"].iloc[tip - 1], "above"):
                return SignalType.CLOSE_SHORT

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
