"""Weekly-anchored VWAP wrapped in golden-ratio deviation bands.

Anchor: the VWAP accumulator restarts whenever a bar stamps the
configured weekday and hour (Monday 08:00 by default). Within each
session:

    vwap    = cumsum(typical_price * volume) / cumsum(volume)
    sigma   = sqrt(cumsum((close - vwap)^2) / bars_seen)
    bands   = vwap +/- ``fibo_inner``/``fibo_outer`` * sigma

This is a fade system, not a breakout system: stretching beyond the
outer band is read as exhaustion, so shorts sell the upper stretch and
longs buy the lower one. Positions are surrendered when price trades
back through the anchor (VWAP), or at protective brackets.

Caveat inherited from the mean-reversion family: sustained trends walk
the outer band and bleed capital -- size accordingly.

The take-profit bracket deliberately points back toward the entry side
(price reverting toward the anchor), which is why longs place TP below
and SL above the fill.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class VWAPFiboBandsStrategy(BacktestStrategyTemplate):
    """Session-anchored VWAP fade strategy with Fibo-scaled envelopes.

    Outer envelope touches mark stretched conditions; reversion targets
    sit at the anchor itself. Inner coefficients exist for visualisation
    and partial-exit tuning but only the outer pair gates entries here.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        # VWAP anchored parameters
        reset_hour: int = 8,
        reset_day: int = 0,  # Monday = 0
        # Fibo band parameters
        fibo_inner: float = 1.618,
        fibo_outer: float = 2.618,
        # Trading safety
        min_bars: int = 4,
        # Exit parameters
        tp_pct: float = 0.03,
        sl_pct: float = 0.05,
        enable_long: bool = True,
        enable_short: bool = True,
        # ATR dynamic stops
        use_atr_stops: bool = False,
        atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0,
        atr_tp_mult: float = 3.0,
        **kwargs,
    ) -> None:
        """Create the anchored-VWAP fade strategy (see module header)."""
        super().__init__(
            symbol, initial_capital, leverage, fee_rate, max_bars, long_only,
            use_atr_stops=use_atr_stops,
            atr_stop_period=atr_stop_period,
            atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult,
            **kwargs,
        )

        self.reset_hour = reset_hour
        self.reset_day = reset_day
        self.fibo_inner = fibo_inner
        self.fibo_outer = fibo_outer
        self.min_bars = min_bars
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.enable_long = enable_long
        self.enable_short = enable_short

        # Armed bracket levels; 0.0 means disarmed.
        self._tp_price: float = 0.0
        self._sl_price: float = 0.0

    def get_parameters(self) -> dict[str, Any]:
        merged = {
            **super().get_parameters(),
            "reset_hour": self.reset_hour,
            "reset_day": self.reset_day,
            "fibo_inner": self.fibo_inner,
            "fibo_outer": self.fibo_outer,
            "min_bars": self.min_bars,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "enable_long": self.enable_long,
            "enable_short": self.enable_short,
        }
        return merged

    def _session_envelopes(self, data: pd.DataFrame) -> pd.DataFrame:
        """Attach per-session VWAP and both envelope pairs to a copy."""
        df = data.copy()

        boundary = (
            (df.index.dayofweek == self.reset_day)
            & (df.index.hour == self.reset_hour)
            & (df.index.minute == 0)
        )
        df["_epoch"] = boundary.cumsum()

        typical = (df["high"] + df["low"] + df["close"]) / 3
        df["_turnover"] = typical * df["volume"]

        within_session = df.groupby("_epoch", sort=False)
        spent = within_session["_turnover"].cumsum()
        shares = within_session["volume"].cumsum()
        anchor = spent / shares

        squared_miss = (df["close"] - anchor) ** 2
        accumulated_miss = squared_miss.groupby(df["_epoch"]).cumsum()
        seen = df.groupby("_epoch").cumcount() + 1
        sigma = np.sqrt(accumulated_miss / seen)

        df["_anchor"] = anchor
        df["_upper_far"] = anchor + self.fibo_outer * sigma
        df["_upper_near"] = anchor + self.fibo_inner * sigma
        df["_lower_near"] = anchor - self.fibo_inner * sigma
        df["_lower_far"] = anchor - self.fibo_outer * sigma
        return df

    # -- signal evaluation -------------------------------------------------

    def _entry_signal(
        self,
        px_now: float,
        px_before: float,
        upper_far: float,
        upper_far_before: float,
        lower_far: float,
        lower_far_before: float,
    ) -> SignalType:
        """Fade outer-envelope crossovers while flat."""
        stretched_high = px_now > upper_far and px_before <= upper_far_before
        stretched_low = px_now < lower_far and px_before >= lower_far_before

        if self._position != "flat":
            return SignalType.HOLD
        if self.enable_short and stretched_high:
            return SignalType.SELL
        if self.enable_long and stretched_low:
            return SignalType.BUY
        return SignalType.HOLD

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Decide the action for the newest bar from band geometry."""
        if len(data) < self.min_bars + 2:
            return SignalType.HOLD

        df = self._session_envelopes(data)
        tip = len(data) - 1

        px_now = data["close"].iloc[tip]
        px_before = data["close"].iloc[tip - 1]
        peak = data["high"].iloc[tip]
        trough = data["low"].iloc[tip]

        anchor_now = df["_anchor"].iloc[tip]
        upper_far = df["_upper_far"].iloc[tip]
        lower_far = df["_lower_far"].iloc[tip]
        if pd.isna(anchor_now) or pd.isna(upper_far) or pd.isna(lower_far):
            return SignalType.HOLD

        entry = self._entry_signal(
            px_now,
            px_before,
            upper_far,
            df["_upper_far"].iloc[tip - 1],
            lower_far,
            df["_lower_far"].iloc[tip - 1],
        )
        if entry is not SignalType.HOLD:
            return entry

        return self._exit_signal(px_now, px_before, anchor_now, df, tip, peak, trough)

    def _exit_signal(
        self,
        px_now: float,
        px_before: float,
        anchor_now: float,
        df: pd.DataFrame,
        tip: int,
        peak: float,
        trough: float,
    ) -> SignalType:
        """Bracket hits or a round-trip through the anchor surrender the position."""
        anchor_before = df["_anchor"].iloc[tip - 1]

        if self._position == "long":
            if self._tp_price > 0 and peak >= self._tp_price:
                return SignalType.CLOSE_LONG
            if self._sl_price > 0 and trough <= self._sl_price:
                return SignalType.CLOSE_LONG
            if px_now >= anchor_now and px_before < anchor_before:
                return SignalType.CLOSE_LONG

        elif self._position == "short" and not self.long_only:
            if self._tp_price > 0 and trough <= self._tp_price:
                return SignalType.CLOSE_SHORT
            if self._sl_price > 0 and peak >= self._sl_price:
                return SignalType.CLOSE_SHORT
            if px_now <= anchor_now and px_before > anchor_before:
                return SignalType.CLOSE_SHORT

        return SignalType.HOLD

    # -- bracket arming ------------------------------------------------------

    def _arm_brackets(self, fill_price: float, side: str) -> None:
        """Fix exit levels; TP sits on the reversion side by design."""
        if self._use_atr_stops:
            self._sl_price, self._tp_price = self._calculate_atr_stop_levels(
                fill_price, side
            )
            return

        drift_out = 1 + self.sl_pct if side == "long" else 1 - self.sl_pct
        revert_in = 1 - self.tp_pct if side == "long" else 1 + self.tp_pct
        self._sl_price = fill_price * drift_out
        self._tp_price = fill_price * revert_in

    def _disarm_brackets(self) -> None:
        self._tp_price = 0.0
        self._sl_price = 0.0

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Dispatch through the base, then arm or clear state."""
        dispatched = super()._process_signal(event, signal_type)
        if dispatched is None:
            return None

        entered_short = (
            signal_type == SignalType.SELL
            and not self.long_only
            and self._position == "flat"
        )
        if signal_type == SignalType.BUY:
            self._arm_brackets(event.close, "long")
        elif entered_short:
            self._arm_brackets(event.close, "short")
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._disarm_brackets()

        return dispatched

    def reset(self) -> None:
        """Drop armed levels along with the base state."""
        super().reset()
        self._disarm_brackets()
