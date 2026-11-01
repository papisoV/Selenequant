"""Triple EMA trend capture with ATR-scaled momentum gate.

Indicator set
-------------
* price basis   -- close or typical price (h+l+c)/3, selected by ``price_src``
* three EMAs    -- short / mid / long spans on that basis (TradingView-style,
  seeded recursively, first span-1 values masked)
* ATR           -- simple rolling mean of true range over ``atr_period``
* separation    -- ema_short - ema_long, normalised by its own stddev over
  ``std_period`` bars and by ATR; the gate coefficient scales this ratio

Trade rules
-----------
Long entry requires ALL of:
  1. gold cross on the *previous* bar (short crossed above long one bar ago)
  2. close still above the long EMA (trend intact)
  3. abs(separation) exceeds the normalised threshold (real momentum, not chop)
  4. short above mid (structure guard against dead-cross traps)

Exits: fixed-percentage take profit, custom ATR stop (entry - stop_atr_mult *
ATR) unless base-class ATR stops are enabled, or a death cross of short under
mid.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class EMATripleCrossStrategy(BacktestStrategyTemplate):
    """Three-EMA stack with normalised-momentum confirmation and ATR stop."""

    def __init__(
        self,
        symbol: str = "BTC/USDT", initial_capital: float = 100000.0,
        leverage: int = 1, fee_rate: float = 0.0005,
        max_bars: int = 500, long_only: bool = True,
        # EMA stack spans
        short_period: int = 10, mid_period: int = 30, long_period: int = 200,
        # volatility window for the stop
        atr_period: int = 14,
        # momentum gate
        std_period: int = 20, threshold_coef: float = 1.5,
        stop_atr_mult: float = 2.0,
        # price basis selector
        price_src: str = "typical",
        # bracket percentages
        tp_pct: float = 0.05, sl_pct: float = 0.02,
        enable_long: bool = True, enable_short: bool = False,
        trend_filter_enabled: bool = False, adx_length: int = 14,
        adx_threshold: float = 25.0,
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
            self.short_period, self.mid_period, self.long_period,
            self.atr_period, self.std_period,
            self.threshold_coef, self.stop_atr_mult,
            self.tp_pct, self.sl_pct,
            self.enable_long, self.enable_short,
            self.trend_filter_enabled, self.adx_length, self.adx_threshold,
        ) = (
            short_period, mid_period, long_period,
            atr_period, std_period,
            threshold_coef, stop_atr_mult,
            tp_pct, sl_pct,
            enable_long, enable_short,
            trend_filter_enabled, adx_length, adx_threshold,
        )
        self.price_src = price_src

        # Position bookkeeping
        self._entry_price = self._sl_price = 0.0

    def get_parameters(self) -> dict:
        merged = super().get_parameters()
        exposed = (
            "short_period", "mid_period", "long_period", "atr_period",
            "std_period", "threshold_coef", "stop_atr_mult", "price_src",
            "tp_pct", "sl_pct", "enable_long", "enable_short",
            "trend_filter_enabled", "adx_length", "adx_threshold",
        )
        merged.update({key: getattr(self, key) for key in exposed})
        return merged

    def _basis(self, data: pd.DataFrame) -> pd.Series:
        """Price series the EMAs are computed on."""
        if self.price_src == "typical":
            return (data["high"] + data["low"] + data["close"]) / 3.0
        return data[self.price_src]

    def _true_range(self, data: pd.DataFrame) -> pd.Series:
        """Bar-by-bar true range with the previous close."""
        prev_close = data["close"].shift(1)
        legs = pd.concat(
            [
                data["high"] - data["low"],
                (data["high"] - prev_close).abs(),
                (data["low"] - prev_close).abs(),
            ],
            axis=1,
        )
        return legs.max(axis=1)

    def _indicator_frame(self, data: pd.DataFrame) -> pd.DataFrame:
        """Attach every derived column used by the rules to a copy of data."""
        out = data.copy()
        basis = self._basis(data)
        out["_basis"] = basis

        for tag, span in (("_ema_s", self.short_period), ("_ema_m", self.mid_period), ("_ema_l", self.long_period)):
            out[tag] = basis.ewm(span=span, adjust=False).mean()

        out["_atr"] = self._true_range(data).rolling(window=self.atr_period).mean()

        sep = out["_ema_s"] - out["_ema_l"]
        out["_sep"] = sep
        gate = (sep.rolling(window=self.std_period).std() / out["_atr"] * self.threshold_coef)
        out["_gate"] = gate.replace(float("inf"), pd.NA).replace(float("-inf"), pd.NA)
        return out

    def _fresh_atr_from_event(self, event: KlineEvent) -> float:
        """Single-bar ATR fallback used when arming the stop at fill time.

        With only one bar the rolling window cannot produce a value, so the
        frame's TR reduces to high-low and the ATR slot falls back to 1% of
        close — matching historical behaviour.
        """
        frame = self._indicator_frame(
            pd.DataFrame(
                {
                    "open": [event.open],
                    "high": [event.high],
                    "low": [event.low],
                    "close": [event.close],
                    "volume": [event.volume],
                }
            )
        )
        raw = frame["_atr"].iloc[0]
        if pd.isna(raw):
            return event.close * 0.01
        return float(raw)

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Gold/death cross rules plus bracket exits."""
        warmup = max(self.long_period, self.std_period) + 2
        if len(data) < warmup:
            return SignalType.HOLD

        n = len(data) - 1
        f = self._indicator_frame(data)

        px_now = data["close"].iloc[n]
        hi_now = data["high"].iloc[n]
        lo_now = data["low"].iloc[n]

        need = (f["_ema_s"].iloc[n], f["_ema_m"].iloc[n], f["_ema_l"].iloc[n], f["_atr"].iloc[n], f["_gate"].iloc[n])
        if any(pd.isna(v) for v in need):
            return SignalType.HOLD

        s_t, m_t, l_t = f["_ema_s"].iloc[n], f["_ema_m"].iloc[n], f["_ema_l"].iloc[n]
        s_p1, s_p2 = f["_ema_s"].iloc[n - 1], f["_ema_s"].iloc[n - 2]
        m_p1, m_p2 = f["_ema_m"].iloc[n - 1], f["_ema_m"].iloc[n - 2]
        l_p1, l_p2 = f["_ema_l"].iloc[n - 1], f["_ema_l"].iloc[n - 2]

        # === ENTRY ===
        if self._position == "flat":
            if self.trend_filter_enabled and self.enable_long:
                trending, _ = self.check_trend_filter(
                    data, self.adx_length, self.adx_threshold, direction="long"
                )
                if not trending:
                    return SignalType.HOLD

            if self.enable_long:
                crossed_up = s_p2 <= l_p2 and s_p1 > l_p1          # cross happened last bar
                above_long = px_now > l_t                          # trend intact now
                strong_move = abs(f["_sep"].iloc[n]) > f["_gate"].iloc[n]
                stacked = s_t > m_t                                # structure guard

                if crossed_up and above_long and strong_move and stacked:
                    return SignalType.BUY

        # === EXIT ===
        elif self._position == "long":
            if lo_now <= self._sl_price:
                return SignalType.CLOSE_LONG
            if hi_now >= self._entry_price * (1 + self.tp_pct):
                return SignalType.CLOSE_LONG
            if s_p2 >= m_p2 and s_p1 < m_p1:                       # death cross last bar
                return SignalType.CLOSE_LONG

        return SignalType.HOLD

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Execute via base class, then arm/disarm the stop."""
        executed = super()._process_signal(event, signal_type)
        if executed is None:
            return None

        if signal_type == SignalType.BUY:
            self._entry_price = event.close
            if self._use_atr_stops:
                self._sl_price, _ = self._calculate_atr_stop_levels(event.close, "long")
            else:
                self._sl_price = event.close - self._fresh_atr_from_event(event) * self.stop_atr_mult
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._sl_price = self._entry_price = 0.0

        return executed

    def reset(self) -> None:
        """Clear position bookkeeping."""
        super().reset()
        self._sl_price = self._entry_price = 0.0
