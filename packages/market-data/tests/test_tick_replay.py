"""Tests for selene_space_market.tick_replay -- tick data replay and aggregation."""

from __future__ import annotations

import json

import pandas as pd

from selene_space_market.tick_replay import Tick, TickReplay, TickType


def _trade_tick(
    price: float = 100.0,
    quantity: float = 1.0,
    ts: int = 1000,
    side: str | None = "buy",
    symbol: str = "BTC/USDT",
) -> Tick:
    return Tick(
        tick_type=TickType.TRADE,
        symbol=symbol,
        price=price,
        quantity=quantity,
        timestamp_ms=ts,
        side=side,
    )


def _bid_tick(price: float, ts: int = 1000) -> Tick:
    return Tick(
        tick_type=TickType.BID_UPDATE,
        symbol="BTC/USDT",
        price=price,
        quantity=5.0,
        timestamp_ms=ts,
    )


class TestLoadFromDataFrame:
    def test_creates_correct_ticks(self) -> None:
        df = pd.DataFrame(
            {
                "timestamp": [1000, 2000, 3000],
                "price": [100.0, 101.0, 102.0],
                "quantity": [1.0, 2.0, 3.0],
                "type": ["trade", "trade", "trade"],
                "side": ["buy", "sell", "buy"],
            }
        )
        replay = TickReplay()
        replay.load_from_dataframe(df)
        assert replay.get_tick_count() == 3
        ticks = list(replay.replay(speed=0))
        assert ticks[0].price == 100.0
        assert ticks[1].price == 101.0
        assert ticks[2].price == 102.0
        assert ticks[0].side == "buy"
        assert ticks[1].side == "sell"


class TestReplayOrder:
    def test_yields_ticks_in_chronological_order(self) -> None:
        ticks = [
            _trade_tick(price=102.0, ts=3000),
            _trade_tick(price=100.0, ts=1000),
            _trade_tick(price=101.0, ts=2000),
        ]
        replay = TickReplay(ticks)
        yielded = list(replay.replay(speed=0))
        assert [t.timestamp_ms for t in yielded] == [1000, 2000, 3000]
        assert [t.price for t in yielded] == [100.0, 101.0, 102.0]

    def test_callback_called(self) -> None:
        ticks = [_trade_tick(ts=1000), _trade_tick(ts=2000)]
        replay = TickReplay(ticks)
        collected: list[Tick] = []
        list(replay.replay(speed=0, callback=collected.append))
        assert len(collected) == 2
        assert collected[0].timestamp_ms == 1000


class TestAggregateToKlines:
    def test_produces_correct_ohlcv(self) -> None:
        # 4 trades within the same 1-hour bucket (3600000 ms)
        ticks = [
            _trade_tick(price=100.0, quantity=1.0, ts=0),
            _trade_tick(price=105.0, quantity=2.0, ts=1000),
            _trade_tick(price=95.0, quantity=3.0, ts=2000),
            _trade_tick(price=102.0, quantity=4.0, ts=3000),
        ]
        replay = TickReplay(ticks)
        klines = replay.aggregate_to_klines(interval_ms=3600000)
        assert len(klines) == 1
        row = klines.iloc[0]
        assert row["open"] == 100.0
        assert row["high"] == 105.0
        assert row["low"] == 95.0
        assert row["close"] == 102.0
        assert row["volume"] == 10.0  # 1+2+3+4

    def test_multiple_bars(self) -> None:
        # Two separate 1-hour buckets
        ticks = [
            _trade_tick(price=100.0, ts=0),
            _trade_tick(price=101.0, ts=3600000),
        ]
        replay = TickReplay(ticks)
        klines = replay.aggregate_to_klines(interval_ms=3600000)
        assert len(klines) == 2

    def test_non_trade_ticks_excluded(self) -> None:
        ticks = [
            _trade_tick(price=100.0, ts=0),
            _bid_tick(price=99.0, ts=500),
        ]
        replay = TickReplay(ticks)
        klines = replay.aggregate_to_klines(interval_ms=3600000)
        assert len(klines) == 1
        assert klines.iloc[0]["volume"] == 1.0  # only the trade

    def test_empty_replay_produces_empty_df(self) -> None:
        replay = TickReplay()
        klines = replay.aggregate_to_klines()
        assert len(klines) == 0
        assert list(klines.columns) == ["timestamp", "open", "high", "low", "close", "volume"]


class TestGetTickCount:
    def test_correct_count(self) -> None:
        ticks = [_trade_tick(ts=i * 1000) for i in range(7)]
        replay = TickReplay(ticks)
        assert replay.get_tick_count() == 7

    def test_empty(self) -> None:
        replay = TickReplay()
        assert replay.get_tick_count() == 0


class TestGetTimeRange:
    def test_correct_range(self) -> None:
        ticks = [_trade_tick(ts=5000), _trade_tick(ts=1000), _trade_tick(ts=3000)]
        replay = TickReplay(ticks)
        assert replay.get_time_range() == (1000, 5000)

    def test_empty_returns_zeros(self) -> None:
        replay = TickReplay()
        assert replay.get_time_range() == (0, 0)


class TestFilterByTime:
    def test_returns_subset(self) -> None:
        ticks = [
            _trade_tick(ts=1000),
            _trade_tick(ts=2000),
            _trade_tick(ts=3000),
            _trade_tick(ts=4000),
        ]
        replay = TickReplay(ticks)
        filtered = replay.filter_by_time(2000, 3000)
        assert filtered.get_tick_count() == 2
        yielded = list(filtered.replay(speed=0))
        assert yielded[0].timestamp_ms == 2000
        assert yielded[1].timestamp_ms == 3000

    def test_does_not_mutate_original(self) -> None:
        ticks = [_trade_tick(ts=1000), _trade_tick(ts=2000), _trade_tick(ts=3000)]
        replay = TickReplay(ticks)
        filtered = replay.filter_by_time(1000, 2000)
        assert replay.get_tick_count() == 3
        assert filtered.get_tick_count() == 2


class TestEmptyReplay:
    def test_replay_yields_nothing(self) -> None:
        replay = TickReplay()
        assert list(replay.replay(speed=0)) == []

    def test_aggregate_empty(self) -> None:
        replay = TickReplay()
        assert len(replay.aggregate_to_klines()) == 0


class TestLoadFromFile:
    def test_load_json_file(self, tmp_path) -> None:
        data = [
            {"tick_type": "trade", "symbol": "BTC/USDT", "price": 100.0,
             "quantity": 1.0, "timestamp_ms": 1000, "side": "buy"},
            {"tick_type": "trade", "symbol": "BTC/USDT", "price": 101.0,
             "quantity": 2.0, "timestamp_ms": 2000, "side": "sell"},
        ]
        path = tmp_path / "ticks.json"
        path.write_text(json.dumps(data), encoding="utf-8")

        replay = TickReplay()
        replay.load_from_file(path)
        assert replay.get_tick_count() == 2
        ticks = list(replay.replay(speed=0))
        assert ticks[0].price == 100.0
        assert ticks[1].price == 101.0
