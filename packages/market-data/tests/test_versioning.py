"""Tests for selene_space_market.versioning -- data snapshot hashing and storage."""

from __future__ import annotations


import pandas as pd
import pytest

from selene_space_market.versioning import (
    SnapshotStore,
    compute_data_hash,
    create_snapshot,
    verify_snapshot,
)


def _sample_df(rows: int = 5, base_ts: int = 1711000000000) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": [base_ts + i * 3600000 for i in range(rows)],
            "open": [100.0 + i for i in range(rows)],
            "high": [105.0 + i for i in range(rows)],
            "low": [95.0 + i for i in range(rows)],
            "close": [102.0 + i for i in range(rows)],
            "volume": [1000.0 for _ in range(rows)],
        }
    )


class TestComputeDataHash:
    def test_deterministic(self) -> None:
        df = _sample_df()
        h1 = compute_data_hash(df)
        h2 = compute_data_hash(df)
        assert h1 == h2

    def test_changes_with_data(self) -> None:
        df1 = _sample_df()
        df2 = _sample_df(rows=6)
        assert compute_data_hash(df1) != compute_data_hash(df2)

    def test_column_order_irrelevant(self) -> None:
        df1 = _sample_df()
        df2 = df1[sorted(df1.columns)]
        assert compute_data_hash(df1) == compute_data_hash(df2)


class TestCreateSnapshot:
    def test_correct_fields(self) -> None:
        df = _sample_df()
        snap = create_snapshot("BTC/USDT", "1h", df, metadata={"source": "test"})
        assert snap.symbol == "BTC/USDT"
        assert snap.interval == "1h"
        assert snap.bar_count == len(df)
        assert snap.content_hash == compute_data_hash(df)
        assert snap.metadata == {"source": "test"}
        assert snap.start_ts == df["timestamp"].min()
        assert snap.end_ts == df["timestamp"].max()

    def test_default_metadata(self) -> None:
        df = _sample_df()
        snap = create_snapshot("ETH/USDT", "4h", df)
        assert snap.metadata == {}


class TestVerifySnapshot:
    def test_matching_data_returns_true(self) -> None:
        df = _sample_df()
        snap = create_snapshot("BTC/USDT", "1h", df)
        assert verify_snapshot(df, snap) is True

    def test_modified_data_returns_false(self) -> None:
        df = _sample_df()
        snap = create_snapshot("BTC/USDT", "1h", df)
        modified = df.copy()
        modified.loc[0, "close"] = 99999.0
        assert verify_snapshot(modified, snap) is False


class TestSnapshotStore:
    @pytest.fixture()
    def store(self, tmp_path) -> SnapshotStore:
        return SnapshotStore(tmp_path / "snapshots")

    def test_save_load_roundtrip(self, store: SnapshotStore) -> None:
        df = _sample_df()
        snap = create_snapshot("BTC/USDT", "1h", df)
        path = store.save(snap)
        assert path.exists()

        loaded = store.load(snap.snapshot_id)
        assert loaded == snap

    def test_load_missing_raises(self, store: SnapshotStore) -> None:
        with pytest.raises(FileNotFoundError):
            store.load("nonexistent_id")

    def test_list_snapshots_all(self, store: SnapshotStore) -> None:
        df1 = _sample_df()
        df2 = _sample_df(rows=3)
        snap1 = create_snapshot("BTC/USDT", "1h", df1)
        snap2 = create_snapshot("ETH/USDT", "4h", df2)
        store.save(snap1)
        store.save(snap2)
        all_snaps = store.list_snapshots()
        assert len(all_snaps) == 2

    def test_list_snapshots_filter_by_symbol(self, store: SnapshotStore) -> None:
        df1 = _sample_df()
        df2 = _sample_df(rows=3)
        snap1 = create_snapshot("BTC/USDT", "1h", df1)
        snap2 = create_snapshot("ETH/USDT", "4h", df2)
        store.save(snap1)
        store.save(snap2)
        btc_snaps = store.list_snapshots(symbol="BTC/USDT")
        assert len(btc_snaps) == 1
        assert btc_snaps[0].symbol == "BTC/USDT"

    def test_find_matching_returns_correct(self, store: SnapshotStore) -> None:
        df = _sample_df()
        snap = create_snapshot("BTC/USDT", "1h", df)
        store.save(snap)
        found = store.find_matching(df, "BTC/USDT", "1h")
        assert found is not None
        assert found.snapshot_id == snap.snapshot_id

    def test_find_matching_returns_none_for_changed_data(self, store: SnapshotStore) -> None:
        df = _sample_df()
        snap = create_snapshot("BTC/USDT", "1h", df)
        store.save(snap)
        modified = df.copy()
        modified.loc[0, "close"] = 99999.0
        found = store.find_matching(modified, "BTC/USDT", "1h")
        assert found is None

    def test_find_matching_returns_none_for_wrong_interval(self, store: SnapshotStore) -> None:
        df = _sample_df()
        snap = create_snapshot("BTC/USDT", "1h", df)
        store.save(snap)
        found = store.find_matching(df, "BTC/USDT", "4h")
        assert found is None
