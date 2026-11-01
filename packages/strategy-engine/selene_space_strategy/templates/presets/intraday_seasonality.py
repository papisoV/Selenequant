"""Intraday Seasonality Strategy — 隔夜时段效应策略.

Absorbed from ``intraday-seasonality-in-bitcoin.py``
(academic-strategy-catalog). Pure price data, no
fundamentals required.

Strategy logic:
  1. Go long at ``entry_hour`` (default 22:00 UTC) each day
  2. Close at ``exit_hour`` (default 00:00 UTC)
  3. Repeat daily — captures the systematic overnight/US-session drift

The window may wrap midnight (22:00 -> 00:00). Timestamps are read from
the ``timestamp`` column (epoch ms), which run_backtest() keeps as a
RangeIndex DataFrame, so the UTC hour must come from the column, not the
index. See the QC source: ``UtcTime.hour == 22`` open / ``== 0`` close.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType


class IntradaySeasonalityStrategy(BacktestStrategyTemplate):
    """Intraday seasonality / overnight effect strategy.

    Exploits recurring time-of-day patterns: long the asset during a
    historically favorable UTC window (default 22:00 -> 00:00) and flat
    otherwise.

    Args:
        symbol: Trading pair.
        entry_hour: UTC hour to open long (default 22).
        exit_hour: UTC hour to close long (default 0).
        max_hold_hours: Safety exit if the exit hour is missed (data gap).
        enable_long: Enable long entry signals.
    """

    def __init__(
        self,
        symbol: str = "BTC/USDT",
        initial_capital: float = 100000.0,
        leverage: int = 1,
        fee_rate: float = 0.0005,
        max_bars: int = 500,
        long_only: bool = True,
        entry_hour: int = 22,
        exit_hour: int = 0,
        max_hold_hours: int = 24,
        enable_long: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(
            symbol,
            initial_capital,
            leverage,
            fee_rate,
            max_bars,
            long_only,
            **kwargs,
        )
        self.entry_hour = entry_hour
        self.exit_hour = exit_hour
        self.max_hold_hours = max_hold_hours
        self.enable_long = enable_long

    def get_parameters(self) -> dict[str, Any]:
        params = super().get_parameters()
        params.update(
            {
                "entry_hour": self.entry_hour,
                "exit_hour": self.exit_hour,
                "max_hold_hours": self.max_hold_hours,
                "enable_long": self.enable_long,
            }
        )
        return params

    @classmethod
    def from_config(cls, config: EngineStrategyConfig) -> "IntradaySeasonalityStrategy":
        """Construct from an EngineStrategyConfig (factory integration)."""
        if not config.symbols:
            raise ValueError("IntradaySeasonalityStrategy requires >=1 symbol")
        params = config.parameters or {}
        return cls(
            symbol=config.symbols[0],
            entry_hour=int(params.get("entry_hour", 22)),
            exit_hour=int(params.get("exit_hour", 0)),
            max_hold_hours=int(params.get("max_hold_hours", 24)),
            enable_long=bool(params.get("enable_long", True)),
        )

    @staticmethod
    def _utc_ts(data: pd.DataFrame) -> pd.Timestamp:
        """Last bar's UTC timestamp from the backtest window.

        run_backtest() builds a RangeIndex DataFrame, so the wall-clock time
        lives in the "timestamp" column (epoch ms), not the index.
        """
        if "timestamp" in data.columns:
            return pd.Timestamp(int(data["timestamp"].iloc[-1]), unit="ms")
        if isinstance(data.index, pd.DatetimeIndex):
            return data.index[-1]
        return pd.Timestamp.now()

    def get_signal(self, data: pd.DataFrame) -> SignalType:
        if len(data) < 2:
            return SignalType.HOLD

        hour = self._utc_ts(data).hour

        if self._position == "flat":
            if self.enable_long and hour == self.entry_hour:
                return SignalType.BUY
            return SignalType.HOLD

        if self._position == "long":
            if hour == self.exit_hour:
                return SignalType.CLOSE_LONG
            # Safety: if the exit hour was missed (data gap), time out.
            if self._entry_time:
                now_ms = int(self._utc_ts(data).value // 1_000_000)
                if now_ms - self._entry_time >= self.max_hold_hours * 3600_000:
                    return SignalType.CLOSE_LONG
            return SignalType.HOLD

        return SignalType.HOLD
