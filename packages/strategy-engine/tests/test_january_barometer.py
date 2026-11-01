"""Tests for JanuaryBarometerStrategy (January barometer calendar effect).

Validates the month-read-from-timestamp logic and the year barometer:
positive January -> long held through February and beyond; negative
January -> long flattened on the first February bar and the rest of the
year stays flat (T-bill proxy); no entry outside January.
"""

from __future__ import annotations

import pandas as pd

from selene_space_strategy.factory import create_january_barometer_strategy
from selene_space_strategy.models import EngineStrategyConfig
from selene_space_strategy.template import BacktestResult
from selene_space_strategy.templates.presets.january_barometer import (
    JanuaryBarometerStrategy,
)
from selene_space_types.market import KlineEvent

_EPOCH = pd.Timestamp("2026-01-01", tz="UTC")
_DAY = 86_400_000
# Jan has 31 days: days 0-30 are January, day 31+ is February.
_FEB_1 = 31


def _ts(days: list[int]) -> list[int]:
    base = int(_EPOCH.value // 1_000_000)
    return [base + d * _DAY for d in days]


def _kline(close: float, ts: int) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=ts,
        interval="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=100.0,
        closed=True,
    )


def _run(close_by_day: dict[int, float], **kwargs) -> tuple[BacktestResult, JanuaryBarometerStrategy]:
    days = sorted(close_by_day)
    ts = _ts(days)
    strategy = JanuaryBarometerStrategy(symbol="BTC/USDT", **kwargs)
    events = [_kline(close_by_day[d], t) for d, t in zip(days, ts)]
    result = strategy.run_backtest(events)
    return result, strategy


class TestJanuaryBarometer:
    def test_positive_january_holds_through_february(self) -> None:
        """January climbs (+5%) then February keeps rising -> the long is
        held for the year: no close trade, position stays long."""
        result, strategy = _run(
            {0: 100.0, 1: 101.0, 2: 102.0, 3: 103.0, 4: 104.0, _FEB_1: 106.0, _FEB_1 + 1: 107.0}
        )
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 0
        assert strategy._position == "long"
        assert strategy._hold_after_january is True

    def test_negative_january_flattens_and_stays_flat(self) -> None:
        """January falls then February stays low -> CLOSE_LONG on the first
        February bar and the rest of the year stays flat."""
        result, strategy = _run(
            {0: 100.0, 1: 99.0, 2: 98.0, 3: 97.0, 4: 96.0, _FEB_1: 95.0, _FEB_1 + 1: 94.0}
        )
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 1
        assert result.trades[0]["action"] == "close_long"
        assert strategy._position == "flat"
        assert strategy._hold_after_january is False

    def test_latched_hold_survives_february_dip(self) -> None:
        """The barometer decision is latched on the first February bar: a
        positive January keeps the long even if February itself drops below
        the January baseline (the decision must not re-evaluate each bar)."""
        result, strategy = _run(
            {0: 100.0, 1: 101.0, 2: 102.0, 3: 103.0, 4: 104.0, _FEB_1: 106.0, _FEB_1 + 1: 99.0}
        )
        assert isinstance(result, BacktestResult)
        # Feb bar 1 resolves positive -> hold latched; Feb bar 2 at 99 is below
        # the Jan baseline (would be negative if recomputed) but must NOT close.
        assert result.total_trades == 0
        assert strategy._position == "long"
        assert strategy._hold_after_january is True

    def test_no_entry_outside_january(self) -> None:
        """A stream that starts in February/March never enters."""
        result, _ = _run(
            {_FEB_1: 100.0, _FEB_1 + 1: 101.0, _FEB_1 + 2: 102.0, 60: 103.0}
        )
        assert isinstance(result, BacktestResult)
        assert result.total_trades == 0

    def test_from_config(self) -> None:
        config = EngineStrategyConfig(
            id="janbar-e2e",
            name="January Barometer E2E",
            type="custom",
            symbols=["BTC/USDT"],
            interval="1d",
            parameters={
                "january_threshold": 0.01,
                "max_hold_days": 90,
            },
        )
        strategy = create_january_barometer_strategy(config)
        assert isinstance(strategy, JanuaryBarometerStrategy)
        assert strategy.symbol == "BTC/USDT"
        assert strategy.january_threshold == 0.01
        assert strategy.max_hold_days == 90
        assert strategy.long_only is True
