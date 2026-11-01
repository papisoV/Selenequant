"""Volume-anchored average versus plain EMA crossover system.

Two reference lines are compared bar by bar:

    * ``vw_line``  -- volume-weighted price (rolling VWAP over
      ``vw_period`` bars), smoothed by an EMA of length ``ema_len``
    * ``fast_line`` -- the same EMA applied directly to raw price

When the plain-price EMA climbs through its volume-weighted sibling,
participation is confirming the move -> go long; the mirror crossing
marks distribution -> exit or, with shorts enabled, go short. Brackets
(fixed percentage or ATR-scaled) protect every open position.

Line construction:
    weighted_px   = close * volume
    rolling_cost  = sum(weighted_px, ``vw_period``)
    rolling_shares= sum(volume, ``vw_period``)
    vw_price      = rolling_cost / rolling_shares
    vw_line       = EMA(vw_price, ``ema_len``)
    fast_line     = EMA(close, ``ema_len``)
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from selene_space_strategy.indicators.ema import EMA
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class VWEMACrossoverStrategy(BacktestStrategyTemplate):
    """Crossover of a raw-price EMA against an EMA of rolling VWAP.

    The volume-weighted line lags toward where real turnover happened;
    divergences between it and the plain EMA flag regime shifts driven
    by institutional-scale flow rather than noise.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        # VW-EMA parameters
        vw_period: int = 20,
        ema_len: int = 9,
        price_src: str = "close",
        # Exit parameters
        tp_pct: float = 0.05,
        sl_pct: float = 0.03,
        enable_long: bool = True,
        enable_short: bool = False,
        # ATR dynamic stops
        use_atr_stops: bool = False,
        atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0,
        atr_tp_mult: float = 3.0,
        **kwargs,
    ) -> None:
        """Create the volume-weighted crossover strategy (module header)."""
        super().__init__(
            symbol, initial_capital, leverage, fee_rate, max_bars, long_only,
            use_atr_stops=use_atr_stops,
            atr_stop_period=atr_stop_period,
            atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult,
            **kwargs,
        )

        (
            self.vw_period, self.ema_len, self.price_src,
            self.tp_pct, self.sl_pct,
            self.enable_long, self.enable_short,
        ) = (vw_period, ema_len, price_src, tp_pct, sl_pct, enable_long, enable_short)

        # Armed bracket levels; 0.0 means disarmed.
        self._tp_price: float = 0.0
        self._sl_price: float = 0.0

    def get_parameters(self) -> dict[str, Any]:
        merged = {
            **super().get_parameters(),
            "vw_period": self.vw_period,
            "ema_len": self.ema_len,
            "price_src": self.price_src,
            "tp_pct": self.tp_pct,
            "sl_pct": self.sl_pct,
            "enable_long": self.enable_long,
            "enable_short": self.enable_short,
        }
        return merged

    def _rolling_vwap(self, data: pd.DataFrame) -> pd.Series:
        """Volume-averaged traded price over the configured window.

        Division-by-zero windows (empty turnover) surface as NaN rather
        than infinity, keeping downstream smoothing well-behaved.
        """
        notional = data[self.price_src] * data["volume"]
        cost_window = notional.rolling(self.vw_period).sum()
        size_window = data["volume"].rolling(self.vw_period).sum()
        with np.errstate(divide="ignore", invalid="ignore"):
            quotient = cost_window / size_window
        return quotient.where(np.isfinite(quotient))

    def _both_lines(self, data: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Apply one shared smoother to raw price and to the VWAP line."""
        smoother = EMA(self.ema_len)
        weighted = smoother.calculate(
            pd.DataFrame({"close": self._rolling_vwap(data)})
        )
        plain = smoother.calculate(data)
        return plain, weighted

    def _bracket_hit(self, peak: float, trough: float) -> SignalType | None:
        """Protective-exit scan for whichever side is currently held.

        Longs surrender on a target touch from above or a stop touch from
        below; shorts run the mirrored pair.
        """
        if self._position == "long":
            if self._tp_price > 0 and peak >= self._tp_price:
                return SignalType.CLOSE_LONG
            if self._sl_price > 0 and trough <= self._sl_price:
                return SignalType.CLOSE_LONG
        elif self._position == "short":
            if self._tp_price > 0 and trough <= self._tp_price:
                return SignalType.CLOSE_SHORT
            if self._sl_price > 0 and peak >= self._sl_price:
                return SignalType.CLOSE_SHORT
        return None

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Decide the action for the newest bar from the line pair.

        The pair's relative standing (fast over/under weighted) is read
        as a boolean; transitions of that boolean drive both entries and
        counter-trend exits, so every branch reduces to "did it flip".
        """
        warmup = self.vw_period + self.ema_len + 2
        if len(data) < warmup:
            return SignalType.HOLD

        plain, weighted = self._both_lines(data)
        tip = len(data) - 1
        if pd.isna(plain.iloc[tip]) or pd.isna(weighted.iloc[tip]):
            return SignalType.HOLD
        if pd.isna(plain.iloc[tip - 1]) or pd.isna(weighted.iloc[tip - 1]):
            return SignalType.HOLD

        leading_now = bool(plain.iloc[tip] > weighted.iloc[tip])
        leading_before = bool(plain.iloc[tip - 1] > weighted.iloc[tip - 1])
        flipped_up = leading_now and not leading_before
        flipped_down = (not leading_now) and leading_before

        peak = data["high"].iloc[tip]
        trough = data["low"].iloc[tip]

        # === ENTRIES ===
        if self._position == "flat":
            if flipped_up and self.enable_long:
                return SignalType.BUY
            if flipped_down and self.enable_short and not self.long_only:
                return SignalType.SELL
            return SignalType.HOLD

        protective = self._bracket_hit(peak, trough)
        if protective is not None:
            return protective

        # === COUNTER-CROSSING EXITS ===
        if self._position == "long" and flipped_down:
            return SignalType.CLOSE_LONG
        if self._position == "short" and flipped_up and not self.long_only:
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
