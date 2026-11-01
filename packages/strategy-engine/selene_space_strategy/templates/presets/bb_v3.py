"""Asymmetric Bollinger Band reversion system ("BB v3").

The two bands are deliberately decoupled from each other:

  * mid line        -- simple moving average over ``bb_len`` bars
  * upper envelope  -- mid + ``sell_mult`` * stddev(close, ``sell_len``)
  * lower envelope  -- mid - ``buy_mult`` * stddev(close, ``buy_len``)

Because the envelopes use independent windows and widths, the channel can be
tight on one side and wide on the other.  Trades fire on envelope re-entry:

  * long  : close climbs back above the lower envelope (dip exhausted)
  * short : close falls back below the upper envelope (rally exhausted)

Positions leave through percentage targets/stop levels measured from the fill
price, or through ATR-derived levels when ``use_atr_stops`` is on (the default
here, chosen so the narrow short stop is not choked at 1.5%).
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class BBV3Strategy(BacktestStrategyTemplate):
    """Reversion entries on an asymmetric Bollinger envelope pair.

    Upper side tuned for fast fade setups, lower side tuned for deep-pullback
    buys; each side carries its own window and multiplier plus dedicated
    take-profit / stop-loss percentages.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT", initial_capital: float = 100000.0,
        leverage: int = 1, fee_rate: float = 0.0005,
        max_bars: int = 500, long_only: bool = True,
        # asymmetric envelope knobs
        bb_len: int = 7, buy_len: int = 44, buy_mult: float = 2.664,
        sell_len: int = 20, sell_mult: float = 2.54,
        # per-side bracket percentages
        buy_tp_pct: float = 0.12704, buy_sl_pct: float = 0.07525,
        sell_tp_pct: float = 0.06112, sell_sl_pct: float = 0.0152,
        enable_long: bool = True, enable_short: bool = False,
        trend_filter_enabled: bool = False, adx_length: int = 14,
        adx_threshold: float = 25.0,
        # ATR stops default ON here: the tight 1.5% short SL choked otherwise
        use_atr_stops: bool = True, atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0, atr_tp_mult: float = 3.0,
        position_size_pct: float = 1.0, max_position_pct: float = 1.0,
        use_kelly_sizing: bool = False, kelly_win_rate: float = 0.5,
        kelly_win_loss_ratio: float = 2.0, cooldown_bars: int = 0,
        use_trailing_stop: bool = False, trailing_atr_mult: float = 2.5,
        **kwargs,
    ) -> None:
        """Create the strategy; see parameter meanings in the module header."""
        super().__init__(
            symbol, initial_capital, leverage, fee_rate, max_bars, long_only,
            position_size_pct=position_size_pct, max_position_pct=max_position_pct,
            use_kelly_sizing=use_kelly_sizing, kelly_win_rate=kelly_win_rate,
            kelly_win_loss_ratio=kelly_win_loss_ratio, cooldown_bars=cooldown_bars,
            use_atr_stops=use_atr_stops, atr_stop_period=atr_stop_period,
            atr_sl_mult=atr_sl_mult, atr_tp_mult=atr_tp_mult,
            use_trailing_stop=use_trailing_stop, trailing_atr_mult=trailing_atr_mult,
            **kwargs,
        )

        (
            self.bb_len, self.buy_len, self.buy_mult,
            self.sell_len, self.sell_mult,
            self.buy_tp_pct, self.buy_sl_pct,
            self.sell_tp_pct, self.sell_sl_pct,
            self.enable_long, self.enable_short,
            self.trend_filter_enabled, self.adx_length, self.adx_threshold,
        ) = (
            bb_len, buy_len, buy_mult,
            sell_len, sell_mult,
            buy_tp_pct, buy_sl_pct,
            sell_tp_pct, sell_sl_pct,
            enable_long, enable_short,
            trend_filter_enabled, adx_length, adx_threshold,
        )

        # Armed exit levels; 0.0 means "not armed"
        self._short_tp_price = self._short_sl_price = 0.0
        self._long_tp_price = self._long_sl_price = 0.0

    def get_parameters(self) -> dict[str, Any]:
        merged = super().get_parameters()
        exposed = (
            "bb_len", "buy_len", "buy_mult", "sell_len", "sell_mult",
            "buy_tp_pct", "buy_sl_pct", "sell_tp_pct", "sell_sl_pct",
            "enable_long", "enable_short", "trend_filter_enabled",
            "adx_length", "adx_threshold",
        )
        merged.update({key: getattr(self, key) for key in exposed})
        return merged

    def _band_geometry(self, data: pd.DataFrame) -> tuple[pd.Series, pd.Series, pd.Series]:
        """Return ``(upper, lower, mid)`` envelopes for the whole frame."""
        close = data["close"]

        mid = close.rolling(window=self.bb_len, min_periods=self.bb_len).mean()

        # Independent dispersion estimates per side (sample stddev, ddof=1)
        sell_sigma = close.rolling(window=self.sell_len, min_periods=self.sell_len).std(ddof=1)
        buy_sigma = close.rolling(window=self.buy_len, min_periods=self.buy_len).std(ddof=1)

        upper = mid + self.sell_mult * sell_sigma
        lower = mid - self.buy_mult * buy_sigma
        return upper, lower, mid

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Envelope re-entry logic plus armed-level exits."""
        needed = max(self.bb_len, self.buy_len, self.sell_len) + 2
        if len(data) < needed:
            return SignalType.HOLD

        n = len(data) - 1
        px, px_before = data["close"].iloc[n], data["close"].iloc[n - 1]
        hi, lo = data["high"].iloc[n], data["low"].iloc[n]

        upper, lower, _mid = self._band_geometry(data)
        up_t, up_before = upper.iloc[n], upper.iloc[n - 1]
        low_t, low_before = lower.iloc[n], lower.iloc[n - 1]

        if pd.isna(up_t) or pd.isna(low_t):
            return SignalType.HOLD

        # Distance of close from each envelope (sign encodes the side)
        gap_low_now, gap_low_before = px - low_t, px_before - low_before
        gap_up_now, gap_up_before = px - up_t, px_before - up_before

        # === ENTRY ===
        if self._position == "flat":
            if self.trend_filter_enabled and self.enable_long:
                trending, _ = self.check_trend_filter(
                    data, self.adx_length, self.adx_threshold, direction="long"
                )
                if not trending:
                    return SignalType.HOLD

            if self.enable_long and gap_low_before < 0.0 < gap_low_now:
                return SignalType.BUY

            if self.enable_short and not self.long_only:
                if self.trend_filter_enabled:
                    trending, _ = self.check_trend_filter(
                        data, self.adx_length, self.adx_threshold, direction="short"
                    )
                    if not trending:
                        return SignalType.HOLD
                if gap_up_before > 0.0 > gap_up_now:
                    return SignalType.SELL

        # === EXIT ===
        elif self._position == "long":
            if self._long_tp_price > 0 and hi >= self._long_tp_price:
                return SignalType.CLOSE_LONG
            if self._long_sl_price > 0 and lo <= self._long_sl_price:
                return SignalType.CLOSE_LONG

        elif self._position == "short" and not self.long_only:
            if self._short_tp_price > 0 and lo <= self._short_tp_price:
                return SignalType.CLOSE_SHORT
            if self._short_sl_price > 0 and hi >= self._short_sl_price:
                return SignalType.CLOSE_SHORT

        return SignalType.HOLD

    def _arm_exit_levels(self, fill: float, kind: SignalType) -> None:
        """Attach profit/loss levels to a freshly opened position."""
        if kind == SignalType.BUY:
            if self._use_atr_stops:
                self._long_sl_price, self._long_tp_price = self._calculate_atr_stop_levels(fill, "long")
            else:
                self._long_tp_price = fill * (1 + self.buy_tp_pct)
                self._long_sl_price = fill * (1 - self.buy_sl_pct)
        elif kind == SignalType.SELL and not self.long_only and self._position == "flat":
            if self._use_atr_stops:
                self._short_sl_price, self._short_tp_price = self._calculate_atr_stop_levels(fill, "short")
            else:
                self._short_tp_price = fill * (1 - self.sell_tp_pct)
                self._short_sl_price = fill * (1 + self.sell_sl_pct)

    def _disarm_exit_levels(self, kind: SignalType) -> None:
        """Clear levels once a position is gone."""
        if kind == SignalType.CLOSE_LONG:
            self._long_tp_price = 0.0
            self._long_sl_price = 0.0
        elif kind == SignalType.CLOSE_SHORT:
            self._short_tp_price = 0.0
            self._short_sl_price = 0.0

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Let the base class execute, then arm/disarm exit levels."""
        executed = super()._process_signal(event, signal_type)
        if executed is None:
            return None

        if signal_type in (SignalType.BUY, SignalType.SELL):
            self._arm_exit_levels(event.close, signal_type)
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._disarm_exit_levels(signal_type)

        return executed

    def reset(self) -> None:
        """Clear armed levels along with base state."""
        super().reset()
        self._short_tp_price = self._short_sl_price = 0.0
        self._long_tp_price = self._long_sl_price = 0.0
