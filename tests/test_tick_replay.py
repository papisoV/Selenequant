"""Tests for tick store and tick replay engine.

Covers:
  - TickStore: write/read trades to SQLite
  - TickReplay: chronological playback of trade stream
"""

from __future__ import annotations

import os


from selene_space_market.tick_store.tick_db import TickStore
from selene_space_market.tick_store.tick_replay import TickReplay


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------


def _make_trades():
    return [
        {
            "exchange": "binance",
            "symbol": "BTC/USDT",
            "price": 50000.0,
            "quantity": 0.1,
            "side": "buy",
            "timestamp_ms": 1_000_000_000_000,
        },
        {
            "exchange": "binance",
            "symbol": "BTC/USDT",
            "price": 50010.0,
            "quantity": 0.05,
            "side": "sell",
            "timestamp_ms": 1_000_000_001_000,
        },
        {
            "exchange": "binance",
            "symbol": "BTC/USDT",
            "price": 50020.0,
            "quantity": 0.2,
            "side": "buy",
            "timestamp_ms": 1_000_000_002_000,
        },
        {
            "exchange": "binance",
            "symbol": "ETH/USDT",
            "price": 3000.0,
            "quantity": 5.0,
            "side": "buy",
            "timestamp_ms": 1_000_000_000_500,
        },
    ]


def _make_temp_store() -> tuple[str, TickStore]:
    """Create a TickStore with a unique temp path to avoid accumulation across tests."""
    tmpdir = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..",
        ".pytest_temp",
    )
    os.makedirs(tmpdir, exist_ok=True)
    import uuid
    db_path = os.path.join(tmpdir, f"test_ticks_{uuid.uuid4().hex[:8]}.db")
    store = TickStore(db_path)
    return db_path, store


def _cleanup_store(db_path: str) -> None:
    try:
        os.remove(db_path)
    except OSError:
        pass
    # Also remove WAL/SHM files
    for suffix in ("-wal", "-shm"):
        try:
            os.remove(db_path + suffix)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# TickStore
# ---------------------------------------------------------------------------


class TestTickStore:
    def test_insert_and_query(self) -> None:
        db_path, store = _make_temp_store()
        try:
            trades = _make_trades()
            count = store.insert_trades(trades)
            assert count == 4

            results = store.query(
                symbol="BTC/USDT",
                start_ms=0,
                end_ms=9_999_999_999_999,
            )
            assert len(results) == 3
        finally:
            _cleanup_store(db_path)

    def test_query_by_symbol(self) -> None:
        db_path, store = _make_temp_store()
        try:
            store.insert_trades(_make_trades())
            btc = store.query(symbol="BTC/USDT")
            eth = store.query(symbol="ETH/USDT")
            assert len(btc) == 3
            assert len(eth) == 1
        finally:
            _cleanup_store(db_path)

    def test_query_time_range(self) -> None:
        db_path, store = _make_temp_store()
        try:
            store.insert_trades(_make_trades())
            results = store.query(
                symbol="BTC/USDT",
                start_ms=1_000_000_000_000,
                end_ms=1_000_000_001_500,
            )
            assert len(results) == 2
        finally:
            _cleanup_store(db_path)

    def test_query_returns_chronological(self) -> None:
        db_path, store = _make_temp_store()
        try:
            store.insert_trades(_make_trades())
            results = store.query(symbol="BTC/USDT")
            for i in range(len(results) - 1):
                assert results[i]["timestamp_ms"] <= results[i + 1]["timestamp_ms"]
        finally:
            _cleanup_store(db_path)

    def test_insert_empty_list(self) -> None:
        db_path, store = _make_temp_store()
        try:
            count = store.insert_trades([])
            assert count == 0
        finally:
            _cleanup_store(db_path)

    def test_count_returns_total(self) -> None:
        db_path, store = _make_temp_store()
        try:
            store.insert_trades(_make_trades())
            assert store.count(symbol="BTC/USDT") == 3
            assert store.count() == 4
        finally:
            _cleanup_store(db_path)


# ---------------------------------------------------------------------------
# TickReplay
# ---------------------------------------------------------------------------


class TestTickReplay:
    def test_replay_in_chronological_order(self) -> None:
        trades = _make_trades()
        replay = TickReplay(trades)
        replayed = list(replay.replay())
        assert len(replayed) == 4
        for i in range(len(replayed) - 1):
            assert replayed[i]["timestamp_ms"] <= replayed[i + 1]["timestamp_ms"]

    def test_replay_empty(self) -> None:
        replay = TickReplay([])
        assert list(replay.replay()) == []

    def test_replay_from_store(self) -> None:
        db_path, store = _make_temp_store()
        try:
            store.insert_trades(_make_trades())
            replay = TickReplay.from_store(
                store,
                symbol="BTC/USDT",
                start_ms=0,
                end_ms=9_999_999_999_999,
            )
            replayed = list(replay.replay())
            assert len(replayed) == 3
        finally:
            _cleanup_store(db_path)

    def test_replay_speed_multiplier(self) -> None:
        trades = _make_trades()
        replay = TickReplay(trades, speed=10.0)
        assert replay.speed == 10.0
        replayed = list(replay.replay())
        assert len(replayed) == 4

    def test_replay_can_seek(self) -> None:
        trades = _make_trades()
        replay = TickReplay(trades)
        replay.seek(1_000_000_001_000)
        replayed = list(replay.replay())
        # Sorted: 1_000_000_000_000, 1_000_000_000_500, 1_000_000_001_000, 1_000_000_002_000
        # Seek to >= 1_000_000_001_000 skips first two
        assert len(replayed) == 2
        assert replayed[0]["timestamp_ms"] == 1_000_000_001_000

    def test_replay_reset(self) -> None:
        trades = _make_trades()
        replay = TickReplay(trades)
        list(replay.replay())
        replay.reset()
        replayed = list(replay.replay())
        assert len(replayed) == 4
