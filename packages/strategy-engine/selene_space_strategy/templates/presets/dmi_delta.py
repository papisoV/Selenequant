"""Directional Movement spread system ("DMI delta").

Core quantity: the signed spread between Wilder's directional indicators

    delta = +DI - (-DI)

interpreted as net directional pressure.  The raw spread is noisy, so it is
passed through a configurable smoother (EMA / SMA / RMA) before use.

Rules
-----
* long  : smoothed delta above zero AND still climbing (bulls in charge and
  strengthening)
* short : smoothed delta below zero AND still sinking (mirror case)
* long positions additionally fold when the spread flips negative; shorts
  when it flips positive -- direction loss ends the trade even if no bracket
  level was hit.
* percentage bracket from fill price guards every position unless ATR levels
  are enabled.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class DMIDeltaStrategy(BacktestStrategyTemplate):
    """Single-indicator trend strategy built on the +DI/-DI differential."""

    def __init__(
        self,
        symbol: str = "BTC/USDT", initial_capital: float = 100000.0,
        leverage: int = 1, fee_rate: float = 0.0005,
        max_bars: int = 500, long_only: bool = True,
        # DMI window
        dmi_len: int = 14,
        # spread smoother
        ma_len: int = 14, ma_type: str = "ema",
        # bracket percentages
        tp_pct: float = 0.05, sl_pct: float = 0.03,
        enable_long: bool = True, enable_short: bool = False,
        **kwargs,
    ) -> None:
        super().__init__(symbol, initial_capital, leverage, fee_rate, max_bars, long_only, **kwargs)

        (
            self.dmi_len, self.ma_len,
            self.tp_pct, self.sl_pct,
            self.enable_long, self.enable_short,
        ) = (
            dmi_len, ma_len,
            tp_pct, sl_pct,
            enable_long, enable_short,
        )
        self.ma_type = ma_type.lower()

        # Armed bracket levels
        self._tp_price = self._sl_price = 0.0

    def get_parameters(self) -> dict:
        merged = super().get_parameters()
        exposed = ("dmi_len", "ma_len", "ma_type", "tp_pct", "sl_pct", "enable_long", "enable_short")
        merged.update({key: getattr(self, key) for key in exposed})
        return merged

    def _smooth(self, series: pd.Series) -> pd.Series:
        """Apply the configured smoother to the DI spread.

        The SMA branch intentionally mirrors the package SMA indicator's
        prefix-sum arithmetic, which propagates the spread's NaN warm-up head
        through every later value (cumsum poisoning).  That yields an
        all-NaN smoothed line — and therefore a permanently flat book — when
        ``ma_type='sma'``; kept as-is for behavioural continuity.
        """
        import numpy as np

        if self.ma_type == "ema":
            from selene_space_strategy.indicators.ema import EMA
            return EMA(self.ma_len).calculate(pd.DataFrame({"close": series}))
        if self.ma_type == "rma":
            # RMA == Wilder smoothing == ewm with alpha=1/n
            return series.ewm(alpha=1.0 / self.ma_len, adjust=False).mean()

        values = series.to_numpy(dtype="float64")
        count = len(values)
        smoothed = np.full(count, np.nan)
        if count >= self.ma_len:
            prefix = np.concatenate(([0.0], np.cumsum(values)))
            smoothed[self.ma_len - 1:] = (
                prefix[self.ma_len:] - prefix[: count - self.ma_len + 1]
            ) / self.ma_len
        return pd.Series(smoothed, index=series.index)

    def _spread_series(self, data: pd.DataFrame) -> pd.Series:
        """Smoothed (+DI - -DI)."""
        from selene_space_strategy.indicators.adx import ADX

        parts = ADX(self.dmi_len).calculate_full(data)
        return self._smooth(parts.plus_di - parts.minus_di)

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Slope-and-sign rules on the smoothed DI spread."""
        warmup = max(self.dmi_len, self.ma_len) * 2 + 2
        if len(data) < warmup:
            return SignalType.HOLD

        n = len(data) - 1
        spread = self._spread_series(data)
        d_now, d_before = spread.iloc[n], spread.iloc[n - 1]
        if pd.isna(d_now) or pd.isna(d_before):
            return SignalType.HOLD

        rising, falling = d_now > d_before, d_now < d_before

        # === ENTRY ===
        if self._position == "flat":
            if self.enable_long and d_now > 0.0 and rising:
                return SignalType.BUY
            if self.enable_short and not self.long_only and d_now < 0.0 and falling:
                return SignalType.SELL

        # === EXIT ===
        elif self._position == "long":
            hi_t = data["high"].iloc[n]
            lo_t = data["low"].iloc[n]
            if self._tp_price > 0 and hi_t >= self._tp_price:
                return SignalType.CLOSE_LONG
            if self._sl_price > 0 and lo_t <= self._sl_price:
                return SignalType.CLOSE_LONG
            if d_now < 0.0:  # bulls lost the edge entirely
                return SignalType.CLOSE_LONG

        elif self._position == "short" and not self.long_only:
            hi_t = data["high"].iloc[n]
            lo_t = data["low"].iloc[n]
            if self._tp_price > 0 and lo_t <= self._tp_price:
                return SignalType.CLOSE_SHORT
            if self._sl_price > 0 and hi_t >= self._sl_price:
                return SignalType.CLOSE_SHORT
            if d_now > 0.0:  # bears lost the edge entirely
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
