"""Month-boundary rotation system ("calendar effect").

A single mechanical rule pair exploiting recurring flows around month edges:

  * accumulate once per month on the configured buy date (default the 25th)
  * liquidate on the configured sell date of the following month (default the 6th)

Guard rails layered on top:

  * hard time cap -- a position older than ``hold_max_days`` is force-closed
  * percentage bracket -- fixed take-profit above / stop-loss below fill price
    (replaced by ATR levels when ``use_atr_stops`` is set)

Date handling note: the template's bar store keys bars by integer timestamps,
so the frame handed to :meth:`get_signal` carries a plain RangeIndex.  In that
case the current bar's wall-clock timestamp is unavailable and we fall back to
"now"; the previous-bar date then degrades to (now - 1 day).  With a real
DatetimeIndex both dates come from the index as expected.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal


class CalendarEffectStrategy(BacktestStrategyTemplate):
    """Buy late in the month, sell early in the next one, every cycle."""

    def __init__(
        self,
        symbol: str = "BTC/USDT", initial_capital: float = 100000.0,
        leverage: int = 1, fee_rate: float = 0.0005,
        max_bars: int = 500, long_only: bool = True,
        # month-boundary dates driving the cycle
        buy_day: int = 25, sell_day: int = 6, hold_max_days: int = 15,
        # bracket percentages
        tp_pct: float = 0.10, sl_pct: float = 0.05,
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

        self.buy_day, self.sell_day, self.hold_max_days = buy_day, sell_day, hold_max_days
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.enable_long = enable_long
        self.enable_short = enable_short

        # Position bookkeeping
        self._entry_date: datetime | None = None
        self._tp_price: float = 0.0
        self._sl_price: float = 0.0

    def get_parameters(self) -> dict:
        merged = super().get_parameters()
        exposed = ("buy_day", "sell_day", "hold_max_days", "tp_pct", "sl_pct", "enable_long", "enable_short")
        merged.update({name: getattr(self, name) for name in exposed})
        return merged

    @staticmethod
    def _resolve_bar_dates(data: pd.DataFrame) -> tuple[pd.Timestamp, pd.Timestamp]:
        """Return ``(current_bar_ts, previous_bar_ts)`` from the frame index."""
        tail = len(data) - 1
        if isinstance(data.index, pd.DatetimeIndex):
            return data.index[tail], data.index[tail - 1]
        # RangeIndex fallback: wall clock stands in for the current bar and
        # the previous bar is approximated by stepping back one day.
        now = pd.Timestamp.now()
        return now, now - pd.Timedelta(days=1)

    @staticmethod
    def _ts_from_event(event: KlineEvent) -> pd.Timestamp:
        """Convert an event's millisecond/second epoch stamp to a Timestamp."""
        raw = event.timestamp
        if isinstance(raw, (int, float)):
            unit = "ms" if raw > 1e10 else "s"
            return pd.Timestamp(raw, unit=unit)
        return pd.Timestamp.now()

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        """Calendar-driven entries plus bracket / age-based exits."""
        if len(data) < 2:
            return SignalType.HOLD

        cur_ts, prev_ts = self._resolve_bar_dates(data)
        hi_now = data["high"].iloc[-1]
        lo_now = data["low"].iloc[-1]

        # === ENTRY: first bar whose day-of-month equals buy_day ===
        if self._position == "flat":
            if self.enable_long and cur_ts.day == self.buy_day != prev_ts.day:
                return SignalType.BUY

        # === EXIT ===
        elif self._position == "long":
            # Bracket checks come first
            if self._tp_price > 0 and hi_now >= self._tp_price:
                return SignalType.CLOSE_LONG
            if self._sl_price > 0 and lo_now <= self._sl_price:
                return SignalType.CLOSE_LONG

            # Scheduled exit: sell_day inside a fresh month
            if cur_ts.day == self.sell_day and prev_ts.month != cur_ts.month:
                return SignalType.CLOSE_LONG

            # Age cap
            if self._entry_date is not None:
                held_days = (cur_ts - self._entry_date).days
                if held_days >= self.hold_max_days:
                    return SignalType.CLOSE_LONG

        return SignalType.HOLD

    def _process_signal(self, event: KlineEvent, signal_type: SignalType) -> Signal | None:
        """Execute via base class, then refresh entry bookkeeping."""
        executed = super()._process_signal(event, signal_type)

        if executed is not None:
            if signal_type == SignalType.BUY:
                self._entry_date = self._ts_from_event(event)
                if self._use_atr_stops:
                    self._sl_price, self._tp_price = self._calculate_atr_stop_levels(
                        event.close, "long"
                    )
                else:
                    fill = event.close
                    self._tp_price = fill * (1.0 + self.tp_pct)
                    self._sl_price = fill * (1.0 - self.sl_pct)

            elif signal_type in (SignalType.CLOSE_LONG, SignalType.CLOSE_SHORT):
                self._sl_price = 0.0
                self._tp_price = 0.0
                self._entry_date = None

        return executed

    def reset(self) -> None:
        """Clear position bookkeeping."""
        super().reset()
        self._entry_date = None
        self._sl_price = 0.0
        self._tp_price = 0.0
