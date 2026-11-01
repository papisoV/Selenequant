"""January Barometer — 一月晴雨表策略.

Absorbed from ``january-barometer.py`` (paperswithbacktest/
awesome-systematic-trading). The market-timing indicator:
  - invest in January
  - if January's return is positive, stay invested for the rest of the year
  - if January's return is negative, switch to T-bills (i.e. go flat)

Transferred to a single-asset template. The month is read from the
"timestamp" column (epoch ms), so it works on the RangeIndex backtest
DataFrame built by run_backtest() — unlike calendar templates that assume
a DatetimeIndex. Being flat after a negative January is the T-bill
equivalent (cash); the source strategy has no short leg.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType

_DAY_MS = 86_400_000


class JanuaryBarometerStrategy(BacktestStrategyTemplate):
    """January barometer: long in January, then hold the year iff January was up.

    The barometer resolves on the first February bar: if the move since the
    first January close is above ``january_threshold`` the long is kept for
    the year, otherwise it is closed and the strategy stays flat (T-bill
    proxy) until the next January.

    Args:
        symbol: Trading pair.
        january_threshold: January return must exceed this to keep the long.
        max_hold_days: Safety exit if the January entry is never resolved
            (e.g. a February gap) and stays long this many days.
        enable_long: Allow long entries.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        january_threshold: float = 0.0,
        max_hold_days: int = 60,
        enable_long: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(
            symbol,
            initial_capital,
            leverage,
            fee_rate,
            max_bars,
            long_only=True,
            **kwargs,
        )
        self.january_threshold = january_threshold
        self.max_hold_days = max_hold_days
        self.enable_long = enable_long
        self._jan_year: int | None = None
        self._jan_start: float | None = None
        self._hold_after_january: bool | None = None
        self._entry_ts: int | None = None

    def reset(self) -> None:
        super().reset()
        self._jan_year = None
        self._jan_start = None
        self._hold_after_january = None
        self._entry_ts = None

    def get_parameters(self) -> dict[str, Any]:
        params = super().get_parameters()
        params.update(
            {
                "january_threshold": self.january_threshold,
                "max_hold_days": self.max_hold_days,
                "enable_long": self.enable_long,
            }
        )
        return params

    @classmethod
    def from_config(cls, config: EngineStrategyConfig) -> "JanuaryBarometerStrategy":
        """Construct from an EngineStrategyConfig (factory integration)."""
        if not config.symbols:
            raise ValueError("JanuaryBarometerStrategy requires >=1 symbol")
        params = config.parameters or {}
        return cls(
            symbol=config.symbols[0],
            january_threshold=float(params.get("january_threshold", 0.0)),
            max_hold_days=int(params.get("max_hold_days", 60)),
            enable_long=bool(params.get("enable_long", True)),
        )

    def _ym(self, data: pd.DataFrame) -> tuple[int, int] | None:
        """(year, month) of the current bar, read from the timestamp column."""
        if "timestamp" not in data.columns:
            return None
        try:
            ts = int(data["timestamp"].iloc[-1])
        except (TypeError, ValueError):
            return None
        dt = pd.Timestamp(ts, unit="ms")
        return dt.year, dt.month

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        if len(data) < 2:
            return SignalType.HOLD
        ym = self._ym(data)
        if ym is None:
            return SignalType.HOLD
        year, month = ym
        close = float(data["close"].iloc[-1])
        cur_ts = int(data["timestamp"].iloc[-1])

        if month == 1:
            # January: re-baseline the barometer for the new year, then long.
            if self._jan_year != year:
                self._jan_year = year
                self._jan_start = close
                self._hold_after_january = None
                self._entry_ts = cur_ts
            if self.enable_long and self._position == "flat":
                return SignalType.BUY
            return SignalType.HOLD

        if month == 2 and self._jan_start is not None and self._hold_after_january is None:
            # Resolve the barometer once, on the first February bar, and
            # latch the decision: holding/flattening for the year must not
            # flip with February's own price action.
            jan_return = close / self._jan_start - 1.0
            self._hold_after_january = jan_return > self.january_threshold
            if self._position == "long" and not self._hold_after_january:
                return SignalType.CLOSE_LONG
            return SignalType.HOLD

        # Safety: an unresolved January entry (e.g. a February data gap)
        # shouldn't be held forever.
        if (
            self._position == "long"
            and self._hold_after_january is None
            and self._entry_ts is not None
            and cur_ts - self._entry_ts > self.max_hold_days * _DAY_MS
        ):
            return SignalType.CLOSE_LONG

        return SignalType.HOLD
