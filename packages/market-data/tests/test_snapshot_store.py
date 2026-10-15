"""SnapshotStore coverage — keyed L2 snapshot bookkeeping."""

from __future__ import annotations

import pytest

from selene_space_market.snapshot_store import SnapshotStore
from selene_space_types.market import OrderBookL2Snapshot, PriceLevel

BASE_TS = 1711000000000
SYMBOLS = ("BTC/USDT", "ETH/USDT")


def _snapshot(symbol: str = "BTC/USDT", seq: int = 100) -> OrderBookL2Snapshot:
    return OrderBookL2Snapshot(
        exchange="binance",
        symbol=symbol,
        timestamp=BASE_TS,
        sequence_id=seq,
        bids=[PriceLevel(price=66999.0, quantity=2.0)],
        asks=[PriceLevel(price=67001.0, quantity=1.0)],
    )


@pytest.fixture
def store() -> SnapshotStore:
    return SnapshotStore()


class TestSnapshotReads:
    def test_stored_snapshot_fetches_back(self, store: SnapshotStore) -> None:
        snap = _snapshot()
        store.update("BTC/USDT", snap)
        assert store.get_snapshot("BTC/USDT") == snap

    def test_unknown_symbol_reads_none(self, store: SnapshotStore) -> None:
        assert store.get_snapshot("NONE") is None


class TestSnapshotWrites:
    def test_latest_write_wins(self, store: SnapshotStore) -> None:
        for seq in (100, 200):
            store.update("BTC/USDT", _snapshot(seq=seq))
        stored = store.get_snapshot("BTC/USDT")
        assert stored is not None
        assert stored.sequence_id == 200

    def test_remove_reports_then_clears(self, store: SnapshotStore) -> None:
        store.update("BTC/USDT", _snapshot())
        assert store.remove("BTC/USDT") is True
        assert store.get_snapshot("BTC/USDT") is None
        assert store.remove("BTC/USDT") is False


class TestInventory:
    def test_symbols_listing_sorted(self, store: SnapshotStore) -> None:
        for sym in SYMBOLS:
            store.update(sym, _snapshot(sym))
        assert sorted(store.symbols()) == list(SYMBOLS)

    def test_size_starts_at_zero(self, store: SnapshotStore) -> None:
        assert store.size == 0

    def test_size_counts_entries(self, store: SnapshotStore) -> None:
        store.update("BTC/USDT", _snapshot())
        assert store.size == 1

    def test_clear_resets_inventory(self, store: SnapshotStore) -> None:
        store.update("BTC/USDT", _snapshot())
        store.clear()
        assert store.size == 0
