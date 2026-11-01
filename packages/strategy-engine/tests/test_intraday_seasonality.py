"""Tests for IntradaySeasonalityStrategy (overnight BTC effect).

Validates UTC-hour entry/exit against the QC source logic
(intraday-seasonality-in-bitcoin: 22:00 UTC long, 00:00 UTC close).
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.factory import create_intraday_seasonality_strategy
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestResult
from selene_space_strategy.templates.presets.intraday_seasonality import (
    IntradaySeasonalityStrategy,
)
from selene_space_types.market import KlineEvent

_EPOCH = pd.Timestamp("2026-01-01", tz="UTC")


def _hour_ts(day: int, hour: int) -> int:
    return int((_EPOCH + pd.Timedelta(days=day, hours=hour)).value // 1_000_000)


def _kline(close: float, ts: int) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=ts,
        interval="1h",
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=100.0,
        closed=True,
    )


class TestIntradaySeasonality:
    def test_long_enters_22_utc_closes_0_utc(self) -> None:
        """Two overnight windows over 2 days -> 2 long round-trips."""
        strategy = IntradaySeasonalityStrategy(
            symbol="BTC/USDT", entry_hour=22, exit_hour=0
        )
        # Chronological hours: day0 20-23h, day1 00-01h, day1 20-23h, day2 00-01h.
        hours = [
            (0, 20), (0, 21), (0, 22), (0, 23),
            (1, 0), (1, 1),
            (1, 20), (1, 21), (1, 22), (1, 23),
            (2, 0), (2, 1),
        ]
        events = [
            _kline(100.0 + day * 10 + hour, _hour_ts(day, hour))
            for day, hour in hours
        ]

        result = strategy.run_backtest(events)
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 2
        assert all(t["action"] == "close_long" for t in result.trades)

    def test_no_trade_when_entry_hour_absent(self) -> None:
        strategy = IntradaySeasonalityStrategy(
            symbol="BTC/USDT", entry_hour=22, exit_hour=0
        )
        hours = [(0, 10), (0, 11), (0, 12), (1, 10), (1, 11)]
        events = [_kline(100.0, _hour_ts(d, h)) for d, h in hours]
        result = strategy.run_backtest(events)
        assert result.total_trades == 0

    def test_from_config(self) -> None:
        config = EngineStrategyConfig(
            id="overnight-e2e",
            name="Overnight E2E",
            type="custom",
            symbols=["BTC/USDT"],
            interval="1h",
            parameters={"entry_hour": 20, "exit_hour": 2, "max_hold_hours": 6},
        )
        strategy = create_intraday_seasonality_strategy(config)
        assert isinstance(strategy, IntradaySeasonalityStrategy)
        assert strategy.symbol == "BTC/USDT"
        assert strategy.entry_hour == 20
        assert strategy.exit_hour == 2
        assert strategy.max_hold_hours == 6
