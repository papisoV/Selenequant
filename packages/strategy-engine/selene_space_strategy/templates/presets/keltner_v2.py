"""Keltner envelope breakout system, second generation.

Channel construction:
  * centre   -- EMA(close, ``ma_length``)
  * range    -- EMA of true range over ``atr_length`` (not Wilder's ATR)
  * bands    -- centre ± ``mult`` * range

Participation filters (both required):
  * ADX above ``adx_threshold`` — only meaningful trends
  * bar open inside the channel — skip gap-driven fake breakouts

Trade rules:
  * long  : close pierces upper band from at-or-below + filters
  * short : close pierces lower band from at-or-above + filters
  * long exit : close falls back through the LOWER band (give trends room),
    or fixed bracket (stop_pct below / tp_pct above fill) if hit first
  * short exit: mirror with the upper band
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class KeltnerChannelV2Strategy(BacktestStrategyTemplate):
    """Trend-continuation breakouts of a smoothed Keltner envelope."""

    def __init__(
        self,
        symbol: str = "BTC/USDT", initial_capital: float = 100000.0,
        leverage: int = 1, fee_rate: float = 0.0005,
        max_bars: int = 500, long_only: bool = True,
        # channel geometry
        ma_length: int = 20, atr_length: int = 20, mult: float = 2.0,
        # ADX participation gate
        adx_length: int = 14, adx_threshold: float = 25.0,
        # fixed bracket
        stop_pct: float = 0.04, tp_pct: float = 0.10,
        enable_long: bool = True, enable_short: bool = True,
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
            self.ma_length, self.atr_length, self.mult,
            self.adx_length, self.adx_threshold,
            self.stop_pct, self.tp_pct,
            self.enable_long, self.enable_short,
        ) = (
            ma_length, atr_length, mult,
            adx_length, adx_threshold,
            stop_pct, tp_pct,
            enable_long, enable_short,
        )

        # Armed levels
        self._stop_price = self._tp_price = 0.0

    def get_parameters(self) -> dict:
        merged = super().get_parameters()
        exposed = (
            "ma_length", "atr_length", "mult", "adx_length", "adx_threshold",
            "stop_pct", "tp_pct", "enable_long", "enable_short",
        )
        merged.update({key: getattr(self, key) for key in exposed})
        return merged

    def _envelope(self, data: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
        """Return ``(upper, lower)`` band series."""
        centre = data["close"].ewm(span=self.ma_length, adjust=False).mean()

        prev_close = data["close"].shift(1)
        tr_legs = pd.concat(
            [
                data["high"] - data["low"],
                (data["high"] - prev_close).abs(),
                (data["low"] - prev_close).abs(),
            ],
            axis=1,
        )
        tr = tr_legs.max(axis=1)
        rng = tr.ewm(span=self.atr_length, adjust=False).mean()

        return centre + rng * self.mult, centre - rng * self.mult

    def _adx_line(self, data: pd.DataFrame) -> pd.Series:
        from selene_space_strategy.indicators.adx import ADX

        return ADX(self.adx_length).calculate_full(data).adx

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Filtered band-pierce entries; opposite-band and bracket exits."""
        warmup = max(self.ma_length, self.adx_length) + 5
        if len(data) < warmup:
            return SignalType.HOLD

        n = len(data) - 1
        px_t, px_p = data["close"].iloc[n], data["close"].iloc[n - 1]
        op_t = data["open"].iloc[n]
        hi_t, lo_t = data["high"].iloc[n], data["low"].iloc[n]

        upper, lower = self._envelope(data)
        adx = self._adx_line(data)

        up_t, up_p = upper.iloc[n], upper.iloc[n - 1]
        lo_t_band, lo_p_band = lower.iloc[n], lower.iloc[n - 1]

        if pd.isna(up_t) or pd.isna(lo_t_band) or pd.isna(adx.iloc[n]):
            return SignalType.HOLD

        strong_enough = adx.iloc[n] > self.adx_threshold

        # === ENTRY ===
        if self._position == "flat":
            if self.enable_long:
                pierced_up = px_p <= up_p and px_t > up_t
                no_gap_up = op_t < up_t
                if pierced_up and strong_enough and no_gap_up:
                    return SignalType.BUY

            if self.enable_short and not self.long_only:
                pierced_down = px_p >= lo_p_band and px_t < lo_t_band
                no_gap_down = op_t > lo_t_band
                if pierced_down and strong_enough and no_gap_down:
                    return SignalType.SELL

        # === EXIT ===
        elif self._position == "long":
            if lo_t <= self._stop_price:          # hard protective stop
                return SignalType.CLOSE_LONG
            if hi_t >= self._tp_price:            # profit target
                return SignalType.CLOSE_LONG
            if px_p >= lo_p_band and px_t < lo_t_band:   # band give-back
                return SignalType.CLOSE_LONG

        elif self._position == "short" and not self.long_only:
            if hi_t >= self._stop_price:
                return SignalType.CLOSE_SHORT
            if lo_t <= self._tp_price:
                return SignalType.CLOSE_SHORT
            if px_p <= up_p and px_t > up_t:
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
                self._stop_price, self._tp_price = self._calculate_atr_stop_levels(fill, "long")
            else:
                self._tp_price = fill * (1.0 + self.tp_pct)
                self._stop_price = fill * (1.0 - self.stop_pct)
        elif signal_type == SignalType.SELL and not self.long_only and self._position == "flat":
            if self._use_atr_stops:
                self._stop_price, self._tp_price = self._calculate_atr_stop_levels(fill, "short")
            else:
                self._tp_price = fill * (1.0 - self.tp_pct)
                self._stop_price = fill * (1.0 + self.stop_pct)
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._stop_price = self._tp_price = 0.0

        return executed

    def reset(self) -> None:
        """Clear armed levels."""
        super().reset()
        self._stop_price = self._tp_price = 0.0
