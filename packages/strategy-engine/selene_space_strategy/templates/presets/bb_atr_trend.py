"""Bollinger + ATR + RSI trend participation filter ("BB ATR trend").

One shared idea drives every rule: volatility expansion marks the birth of a
trend, so only participate while expansion persists.

Opening gate (deliberately strict, all must hold):
  1. envelope widening -- upper band rising AND lower band sinking
  2. band spread (upper - lower) growing bar over bar
  3. ATR growing bar over bar
  4. mid EMA sloping in the trade direction
  5. RSI inside the safe corridor for that direction

Closing gate (deliberately loose -- all of):
  * any one band edge folding back toward the mid line
  * band spread contracting
  * mid EMA slope flipping against the position
must hold together before a position is surrendered, keeping entries rare but
exits unhurried.  A fixed percentage bracket guards both sides unless ATR
levels are enabled.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class BBATRTrendStrategy(BacktestStrategyTemplate):
    """Trend rider gated by Bollinger geometry, ATR slope and RSI corridor."""

    def __init__(
        self,
        symbol: str = "BTC/USDT", initial_capital: float = 100000.0,
        leverage: int = 1, fee_rate: float = 0.0005,
        max_bars: int = 500, long_only: bool = True,
        # envelope knobs
        bb_len: int = 20, bb_mult: float = 2.0, bb_src: str = "close",
        # volatility window
        atr_len: int = 14,
        # momentum corridor
        rsi_len: int = 14, rsi_src: str = "close",
        rsi_long_min: float = 30.0, rsi_long_max: float = 70.0,
        rsi_short_min: float = 30.0, rsi_short_max: float = 70.0,
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

        (
            self.bb_len, self.bb_mult,
            self.atr_len, self.rsi_len,
            self.rsi_long_min, self.rsi_long_max,
            self.rsi_short_min, self.rsi_short_max,
            self.tp_pct, self.sl_pct,
            self.enable_long, self.enable_short,
        ) = (
            bb_len, bb_mult,
            atr_len, rsi_len,
            rsi_long_min, rsi_long_max,
            rsi_short_min, rsi_short_max,
            tp_pct, sl_pct,
            enable_long, enable_short,
        )
        self.bb_src = bb_src.lower()
        self.rsi_src = rsi_src.lower()

        # Armed bracket levels
        self._sl_price = self._tp_price = 0.0

    def get_parameters(self) -> dict:
        merged = super().get_parameters()
        exposed = (
            "bb_len", "bb_mult", "bb_src", "atr_len", "rsi_len", "rsi_src",
            "rsi_long_min", "rsi_long_max", "rsi_short_min", "rsi_short_max",
            "tp_pct", "sl_pct", "enable_long", "enable_short",
        )
        merged.update({key: getattr(self, key) for key in exposed})
        return merged

    @staticmethod
    def _pick_source(data: pd.DataFrame, name: str) -> pd.Series:
        """Map ``close`` / ``hl2`` / ``hlc3`` to their series."""
        if name == "hl2":
            return data[["high", "low"]].sum(axis=1) / 2.0
        if name == "hlc3":
            return data[["high", "low", "close"]].sum(axis=1) / 3.0
        return data["close"]

    def _indicator_set(self, data: pd.DataFrame) -> tuple[pd.Series, ...]:
        """Return ``(upper, lower, mid, spread, atr, rsi)`` series."""
        from selene_space_strategy.indicators.atr import ATR
        from selene_space_strategy.indicators.ema import EMA
        from selene_space_strategy.indicators.rsi import RSI

        src = self._pick_source(data, self.bb_src)
        mid = EMA(self.bb_len).calculate(pd.DataFrame({"close": src}))
        sigma = src.rolling(window=self.bb_len, min_periods=self.bb_len).std(ddof=1)
        upper = mid + sigma * self.bb_mult
        lower = mid - sigma * self.bb_mult
        spread = upper - lower

        atr = ATR(self.atr_len).calculate(data)
        rsi = RSI(self.rsi_len).calculate(
            pd.DataFrame({"close": self._pick_source(data, self.rsi_src)})
        )
        return upper, lower, mid, spread, atr, rsi

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Strict expansion-gated entries; loose contraction-based exits."""
        warmup = max(self.bb_len, self.atr_len, self.rsi_len) + 2
        if len(data) < warmup:
            return SignalType.HOLD

        n = len(data) - 1

        upper, lower, mid, spread, atr, rsi = self._indicator_set(data)

        hi_t = data["high"].iloc[n]
        lo_t = data["low"].iloc[n]

        if any(
            pd.isna(v)
            for v in (upper.iloc[n], lower.iloc[n], mid.iloc[n], atr.iloc[n], rsi.iloc[n], spread.iloc[n])
        ):
            return SignalType.HOLD

        # Geometry deltas between this bar and the last
        up_slope = upper.iloc[n] > upper.iloc[n - 1]
        low_slope = lower.iloc[n] < lower.iloc[n - 1]
        up_folds = upper.iloc[n] < upper.iloc[n - 1]
        low_folds = lower.iloc[n] > lower.iloc[n - 1]
        spread_grows = spread.iloc[n] > spread.iloc[n - 1]
        spread_shrinks = spread.iloc[n] < spread.iloc[n - 1]
        atr_grows = atr.iloc[n] > atr.iloc[n - 1]

        # === OPEN (strict) ===
        if self._position == "flat":
            widening = up_slope and low_slope

            if self.enable_long:
                long_ok = (
                    widening
                    and spread_grows
                    and atr_grows
                    and mid.iloc[n] > mid.iloc[n - 1]
                    and self.rsi_long_min <= rsi.iloc[n] <= self.rsi_long_max
                )
                if long_ok:
                    return SignalType.BUY

            if self.enable_short and not self.long_only:
                short_ok = (
                    widening
                    and spread_grows
                    and atr_grows
                    and mid.iloc[n] < mid.iloc[n - 1]
                    and self.rsi_short_min <= rsi.iloc[n] <= self.rsi_short_max
                )
                if short_ok:
                    return SignalType.SELL

        # === CLOSE (loose) ===
        elif self._position == "long":
            if self._tp_price > 0 and hi_t >= self._tp_price:
                return SignalType.CLOSE_LONG
            if self._sl_price > 0 and lo_t <= self._sl_price:
                return SignalType.CLOSE_LONG

            collapsing = (up_folds or low_folds) and spread_shrinks
            if collapsing and mid.iloc[n] < mid.iloc[n - 1]:
                return SignalType.CLOSE_LONG

        elif self._position == "short" and not self.long_only:
            if self._tp_price > 0 and lo_t <= self._tp_price:
                return SignalType.CLOSE_SHORT
            if self._sl_price > 0 and hi_t >= self._sl_price:
                return SignalType.CLOSE_SHORT

            collapsing = (up_folds or low_folds) and spread_shrinks
            if collapsing and mid.iloc[n] > mid.iloc[n - 1]:
                return SignalType.CLOSE_SHORT

        return SignalType.HOLD

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Execute via base class, then arm/disarm the bracket."""
        executed = super()._process_signal(event, signal_type)
        if executed is None:
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

        return executed

    def reset(self) -> None:
        """Clear armed levels."""
        super().reset()
        self._tp_price = self._sl_price = 0.0
