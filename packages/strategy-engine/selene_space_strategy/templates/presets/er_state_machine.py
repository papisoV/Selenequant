"""Efficiency-Ratio regime machine preset (KOP-flavoured, low cadence).

Efficiency Ratio = net displacement over a window divided by the total
distance travelled. It answers one question: how straight did price walk?
A near-1.0 answer marks a clean directional leg; a near-0.0 answer marks
back-and-forth churn.

This template runs a two-state machine on that reading:

* CHAOS -> TREND: the reading reaches ``entry_threshold`` and, when
  ``direction_filter`` is on, the net displacement actually points the way
  the trade would go (longs need closes above where the window began);
* TREND -> CHAOS: the reading sags below ``exit_threshold`` — the walk has
  degenerated into noise, so standing positions are handed back.

The entry bar doubles as the exit criterion's mirror image, keeping the
machine honest: it never holds through conditions it would not re-enter.

Knob reference
--------------
er_period         ER window in bars (14)
entry_threshold   reading required to enter the trend state (0.5)
exit_threshold    reading under which the trend state is abandoned (0.3)
direction_filter  demand the displacement to point at the trade (True)
tp_pct / sl_pct   fixed profit-target / stop-loss distances (5% / 3%)
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.indicators.efficiency_ratio import EfficiencyRatio
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class ERStateMachineStrategy(BacktestStrategyTemplate):
    """Two-regime switch driven by movement efficiency.

    Flat: BUY/SELL once ER clears ``entry_threshold`` with displacement
    agreeing (filter permitting). In-market: brackets first; an ER reading
    below ``exit_threshold`` hands the position back.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        er_period: int = 14,
        entry_threshold: float = 0.5,
        exit_threshold: float = 0.3,
        direction_filter: bool = True,
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

        # One mapping holds every template-owned knob; normalisation happens
        # at this single storage point and get_parameters replays it.
        self._own_params: dict[str, Any] = {
            "er_period": er_period,
            "entry_threshold": float(entry_threshold),
            "exit_threshold": float(exit_threshold),
            "direction_filter": direction_filter,
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

    def _regime_reading(self, data: pd.DataFrame) -> tuple[float | None, float]:
        """(latest ER value or None while warming up, latest close)."""
        er_tip = EfficiencyRatio(self._own_params["er_period"]).calculate(data).iloc[-1]
        last_close = float(data["close"].iloc[-1])
        return (None if pd.isna(er_tip) else float(er_tip)), last_close

    def _displacement_confirms(self, data: pd.DataFrame, want_up: bool) -> bool:
        """Compare the close against its ``er_period``-ago level."""
        if not self._own_params["direction_filter"]:
            return True
        anchor = data["close"].iloc[max(0, len(data) - 1 - self._own_params["er_period"])]
        return anchor < data["close"].iloc[-1] if want_up else anchor > data["close"].iloc[-1]

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Trade only the efficient-trend state; abandon it when noisy."""
        knob = self._own_params
        if len(data) < knob["er_period"] + 2:
            return SignalType.HOLD

        er_now, close_now = self._regime_reading(data)
        if er_now is None:
            return SignalType.HOLD

        tip_high = float(data["high"].iloc[-1])
        tip_low = float(data["low"].iloc[-1])

        if self._position == "flat":
            if (
                knob["enable_long"]
                and er_now >= knob["entry_threshold"]
                and self._displacement_confirms(data, want_up=True)
            ):
                return SignalType.BUY
            if (
                knob["enable_short"]
                and not self.long_only
                and er_now >= knob["entry_threshold"]
                and self._displacement_confirms(data, want_up=False)
            ):
                return SignalType.SELL
            return SignalType.HOLD

        bracket_exit: SignalType | None = None
        if self._position == "long":
            if self._tp_price > 0 and tip_high >= self._tp_price:
                bracket_exit = SignalType.CLOSE_LONG
            elif self._sl_price > 0 and tip_low <= self._sl_price:
                bracket_exit = SignalType.CLOSE_LONG
            elif er_now < knob["exit_threshold"]:
                bracket_exit = SignalType.SELL
        elif self._position == "short" and not self.long_only:
            if self._tp_price > 0 and tip_low <= self._tp_price:
                bracket_exit = SignalType.CLOSE_SHORT
            elif self._sl_price > 0 and tip_high >= self._sl_price:
                bracket_exit = SignalType.CLOSE_SHORT
            elif er_now < knob["exit_threshold"]:
                bracket_exit = SignalType.CLOSE_SHORT

        return bracket_exit if bracket_exit is not None else SignalType.HOLD

    def _arm_brackets_for(self, fill_price: float, direction: str) -> None:
        """Set fixed or ATR-scaled exit levels around a fresh fill."""
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
        """Forward to the engine, then maintain this template's brackets."""
        signal = super()._process_signal(event, signal_type)
        if signal is None:
            return signal

        if signal_type is SignalType.BUY:
            self._arm_brackets_for(event.close, "long")
        elif signal_type is SignalType.SELL and not self.long_only and self._position == "flat":
            self._arm_brackets_for(event.close, "short")
        elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
            self._clear_bracket_levels()

        return signal

    def reset(self) -> None:
        """Wipe run history and any outstanding bracket levels."""
        super().reset()
        self._clear_bracket_levels()
