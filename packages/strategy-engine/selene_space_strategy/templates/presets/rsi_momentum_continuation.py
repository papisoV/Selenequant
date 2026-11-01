"""RSI momentum-continuation preset — overbought as fuel, not exhaustion.

Conventional wisdom fades extreme RSI readings. Community forward-testing
of this template's premise found the opposite skew at the strong end:
once RSI pins above ~70, upside continuation is far more common than
immediate reversal (roughly 83% of cases in that sample). The template
therefore trades *with* stretched momentum:

* entry: RSI climbs through ``continue_threshold`` (default 70) — join
  the move while buyers still dominate;
* exit: RSI sinks under ``exit_threshold`` (default 55) — distribution
  has taken over, hand the position back;
* fixed or ATR-scaled brackets can end the trade earlier on their own.

Shorts mirror everything through the complement (100 - threshold), and
stay dormant unless two-sided trading is enabled.

Knob reference
--------------
rsi_len              RSI lookback (14)
continue_threshold   reading required to enter with the move (70)
exit_threshold       reading under which the move is abandoned (55)
tp_pct / sl_pct      fixed profit-target / stop-loss distances (5% / 3%)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.indicators.rsi import RSI
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class RSIMomentumContinuationStrategy(BacktestStrategyTemplate):
    """Hold through overbought; leave only when momentum actually tires."""

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        rsi_len: int = 14,
        continue_threshold: float = 70.0,
        exit_threshold: float = 55.0,
        tp_pct: float = 0.05,
        sl_pct: float = 0.03,
        enable_long: bool = True,
        enable_short: bool = False,
        use_atr_stops: bool = False,
        atr_stop_period: int = 14,
        atr_sl_mult: float = 2.0,
        atr_tp_mult: float = 3.0,
        **kwargs,
    ) -> None:
        super().__init__(
            symbol=symbol,
            initial_capital=initial_capital,
            leverage=leverage,
            fee_rate=fee_rate,
            max_bars=max_bars,
            long_only=long_only,
            use_atr_stops=use_atr_stops,
            atr_stop_period=atr_stop_period,
            atr_sl_mult=atr_sl_mult,
            atr_tp_mult=atr_tp_mult,
            **kwargs,
        )

        # Every template-owned knob lives in one mapping — normalised at this
        # single storage point; get_parameters replays it verbatim.
        self._own_params: dict[str, Any] = {
            "rsi_len": rsi_len,
            "continue_threshold": float(continue_threshold),
            "exit_threshold": float(exit_threshold),
            "tp_pct": tp_pct,
            "sl_pct": sl_pct,
            "enable_long": enable_long,
            "enable_short": enable_short,
        }

        self._tp_price: float = 0.0
        self._sl_price: float = 0.0

    def get_parameters(self) -> dict[str, Any]:
        """Account-level base settings overlaid with this template's knobs."""
        return {**super().get_parameters(), **self._own_params}

    def _momentum_reading(self, data: pd.DataFrame) -> float | None:
        """Latest RSI value, or None during indicator warm-up."""
        tip = RSI(self._own_params["rsi_len"]).calculate(data).iloc[-1]
        return None if pd.isna(tip) else float(tip)

    def _entry_verdict(self, reading: float) -> SignalType:
        """Flat-state decision from one RSI print."""
        knob = self._own_params
        if knob["enable_long"] and reading >= knob["continue_threshold"]:
            return SignalType.BUY
        if (
            knob["enable_short"]
            and not self.long_only
            and reading <= 100.0 - knob["continue_threshold"]
        ):
            return SignalType.SELL
        return SignalType.HOLD

    def _exit_verdict(self, reading: float, high: float, low: float) -> SignalType | None:
        """In-market decision: bracket levels first, fading momentum second."""
        exit_floor = self._own_params["exit_threshold"]
        if self._position == "long":
            if self._tp_price > 0 and high >= self._tp_price:
                return SignalType.CLOSE_LONG
            if self._sl_price > 0 and low <= self._sl_price:
                return SignalType.CLOSE_LONG
            if reading < exit_floor:
                return SignalType.SELL
            return None
        if self._position == "short":
            if self._tp_price > 0 and low <= self._tp_price:
                return SignalType.CLOSE_SHORT
            if self._sl_price > 0 and high >= self._sl_price:
                return SignalType.CLOSE_SHORT
            if reading > 100.0 - exit_floor:
                return SignalType.CLOSE_SHORT
        return None

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """One RSI print decides: chase strength flat, retreat when it fades."""
        if len(data) < self._own_params["rsi_len"] + 2:
            return SignalType.HOLD

        reading = self._momentum_reading(data)
        if reading is None:
            return SignalType.HOLD

        tip_high = float(data["high"].iloc[-1])
        tip_low = float(data["low"].iloc[-1])

        if self._position == "flat":
            return self._entry_verdict(reading)

        decided = self._exit_verdict(reading, tip_high, tip_low)
        return decided if decided is not None else SignalType.HOLD

    def _apply_brackets(self, fill_price: float, direction: str) -> None:
        """Choose ATR-scaled or fixed brackets for a fresh position."""
        if self._use_atr_stops:
            self._sl_price, self._tp_price = self._calculate_atr_stop_levels(fill_price, direction)
        elif direction == "long":
            self._tp_price = fill_price * (1 + self._own_params["tp_pct"])
            self._sl_price = fill_price * (1 - self._own_params["sl_pct"])
        else:
            self._tp_price = fill_price * (1 - self._own_params["tp_pct"])
            self._sl_price = fill_price * (1 + self._own_params["sl_pct"])

    def _clear_bracket_levels(self) -> None:
        """Retire live exit levels; shared by close-out handling and reset."""
        self._tp_price = 0.0
        self._sl_price = 0.0

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Run the base execution, then sync this template's brackets."""
        signal = super()._process_signal(event, signal_type)
        if signal is None:
            return signal

        if signal_type is SignalType.BUY:
            self._apply_brackets(event.close, "long")
        elif signal_type is SignalType.SELL and not self.long_only and self._position == "flat":
            self._apply_brackets(event.close, "short")
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._clear_bracket_levels()

        return signal

    def reset(self) -> None:
        """Restore just-constructed state including cleared brackets."""
        super().reset()
        self._clear_bracket_levels()
