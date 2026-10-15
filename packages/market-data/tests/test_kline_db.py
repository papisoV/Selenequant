"""KlineDB coverage — connection lifecycle, partitioned put/get with date
windows, counts, timestamp bounds, has_data, and maintenance deletes."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from selene_space_market.kline_db import KlineDB
from selene_space_types.market import KlineEvent

BASE_TS = 1711000000000
HOUR_MS = 3_600_000


def _kline(
    ts: int,
    exchange: str = "binance",
    market_type: str = "futures",
    symbol: str = "BTC/USDT",
    interval: str = "1h",
    close: float = 67000.0,
) -> KlineEvent:
    return KlineEvent(
        exchange=exchange,
        market_type=market_type,  # type: ignore[arg-type]
        symbol=symbol,  # type: ignore[arg-type]
        timestamp=ts,
        interval=interval,
        open=close - 100,
        high=close + 100,
        low=close - 200,
        close=close,
        volume=1000.0,
        closed=True,
    )


def _hour_series(count: int, base_ts: int = BASE_TS) -> list[KlineEvent]:
    return [_kline(base_ts + i * HOUR_MS) for i in range(count)]


def _utc(ms: int) -> datetime:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc)


class TestConnectionLifecycle:
    @pytest.fixture
    def db(self) -> KlineDB:
        return KlineDB(":memory:")

    def test_open_is_idempotent(self, db: KlineDB) -> None:
        db.open()
        assert db._conn is not None
        db.open()  # second open must not raise or reset
        db.close()

    def test_connect_scope_binds_and_releases(self, db: KlineDB) -> None:
        with db.connect():
            assert db._conn is not None
        assert db._conn is None


class TestPutGetPartition:
    @pytest.fixture
    def db(self) -> KlineDB:
        return KlineDB(":memory:")

    def test_single_row_roundtrip(self, db: KlineDB) -> None:
        assert db.put_klines([_kline(BASE_TS)]) == 1

        df = db.get_klines("binance", "futures", "BTC/USDT", "1h")
        assert len(df) == 1
        assert df.iloc[0]["close"] == 67000.0

    def test_multi_row_order_preserved(self, db: KlineDB) -> None:
        series = [_kline(BASE_TS + i * HOUR_MS, close=67000 + i * 10) for i in range(5)]
        db.put_klines(series)

        df = db.get_klines("binance", "futures", "BTC/USDT", "1h")
        assert len(df) == 5
        assert list(df["close"]) == [67000.0 + i * 10 for i in range(5)]

    def test_same_timestamp_upserts(self, db: KlineDB) -> None:
        db.put_klines([_kline(BASE_TS, close=67000.0)])
        db.put_klines([_kline(BASE_TS, close=68000.0)])  # identical ts → replace

        df = db.get_klines("binance", "futures", "BTC/USDT", "1h")
        assert len(df) == 1
        assert df.iloc[0]["close"] == 68000.0


class TestDateWindows:
    @pytest.fixture
    def filled(self) -> KlineDB:
        db = KlineDB(":memory:")
        db.put_klines(_hour_series(10))
        return db

    def test_start_date_inclusive_tail(self, filled: KlineDB) -> None:
        df = filled.get_klines(
            "binance",
            "futures",
            "BTC/USDT",
            "1h",
            start_date=_utc(BASE_TS + 4 * HOUR_MS),
        )
        assert len(df) == 6  # indices 4..9

    def test_end_date_inclusive_head(self, filled: KlineDB) -> None:
        df = filled.get_klines(
            "binance",
            "futures",
            "BTC/USDT",
            "1h",
            end_date=_utc(BASE_TS + 4 * HOUR_MS),
        )
        assert len(df) == 5  # indices 0..4

    def test_window_slice_interior(self, filled: KlineDB) -> None:
        df = filled.get_klines(
            "binance",
            "futures",
            "BTC/USDT",
            "1h",
            start_date=_utc(BASE_TS + 2 * HOUR_MS),
            end_date=_utc(BASE_TS + 7 * HOUR_MS),
        )
        assert len(df) == 6  # indices 2..7

    def test_absent_partition_reads_empty(self) -> None:
        df = KlineDB(":memory:").get_klines("binance", "futures", "BTC/USDT", "1h")
        assert len(df) == 0


class TestCountsAndBounds:
    @pytest.fixture
    def db(self) -> KlineDB:
        return KlineDB(":memory:")

    def test_count_matches_rows(self, db: KlineDB) -> None:
        db.put_klines(_hour_series(7))
        assert db.count("binance", "futures", "BTC/USDT", "1h") == 7

    def test_count_zero_for_other_partition(self, db: KlineDB) -> None:
        assert db.count("binance", "spot", "ETH/USDT", "1h") == 0

    def test_latest_timestamp_is_max(self, db: KlineDB) -> None:
        db.put_klines(_hour_series(10))
        latest = db.get_latest_timestamp("binance", "futures", "BTC/USDT", "1h")
        assert latest == BASE_TS + 9 * HOUR_MS

    def test_earliest_timestamp_is_min(self, db: KlineDB) -> None:
        db.put_klines(_hour_series(10))
        assert (
            db.get_earliest_timestamp("binance", "futures", "BTC/USDT", "1h") == BASE_TS
        )

    def test_bounds_none_when_empty(self, db: KlineDB) -> None:
        partition = ("binance", "futures", "BTC/USDT", "1h")
        assert db.get_latest_timestamp(*partition) is None
        assert db.get_earliest_timestamp(*partition) is None


class TestHasDataGate:
    @pytest.fixture
    def db(self) -> KlineDB:
        return KlineDB(":memory:")

    def test_enough_candles_passes(self, db: KlineDB) -> None:
        db.put_klines(_hour_series(15))
        assert (
            db.has_data("binance", "futures", "BTC/USDT", "1h", min_candles=10) is True
        )

    def test_short_supply_fails(self, db: KlineDB) -> None:
        db.put_klines(_hour_series(5))
        assert (
            db.has_data("binance", "futures", "BTC/USDT", "1h", min_candles=10) is False
        )


class TestMaintenanceAndIsolation:
    @pytest.fixture
    def db(self) -> KlineDB:
        return KlineDB(":memory:")

    def test_delete_partition_wipes_only_that_partition(self, db: KlineDB) -> None:
        db.put_klines(_hour_series(5))
        assert db.delete_partition("binance", "futures", "BTC/USDT", "1h") == 5
        assert db.count("binance", "futures", "BTC/USDT", "1h") == 0

    def test_delete_before_keeps_recent_half(self, db: KlineDB) -> None:
        db.put_klines(_hour_series(10))
        cutoff = BASE_TS + 5 * HOUR_MS
        assert db.delete_before(cutoff) == 5
        assert db.count("binance", "futures", "BTC/USDT", "1h") == 5

    def test_symbol_and_interval_form_separate_partitions(self, db: KlineDB) -> None:
        db.put_klines([_kline(BASE_TS)])
        db.put_klines([_kline(BASE_TS, symbol="ETH/USDT", close=3500.0)])
        db.put_klines([_kline(BASE_TS, interval="4h", close=68000.0)])

        assert db.count("binance", "futures", "BTC/USDT", "1h") == 1
        assert db.count("binance", "futures", "ETH/USDT", "1h") == 1
        assert db.count("binance", "futures", "BTC/USDT", "4h") == 1


class TestL1SnapshotGaps:
    """S2: outage episodes for the A-share intraday poll source.

    Rows live in `kline_db.py`'s own DDL (not the storage migration system):
    the durable candle store self-manages its schema on `data/klines.db`, which
    is a different file from the migrations-targeted app DB.
    """

    @pytest.fixture
    def db(self) -> KlineDB:
        return KlineDB(":memory:")

    def test_open_then_close_updates_same_row(self, db: KlineDB) -> None:
        db.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=1_000_000,
            closed_at_ms=None,
            duration_sec=None,
            symbols=["600519.SH", "000001.SZ"],
        )
        open_rows = db.list_l1_gaps()
        assert len(open_rows) == 1
        assert open_rows[0].opened_at_ms == 1_000_000
        assert open_rows[0].closed_at_ms is None
        assert open_rows[0].symbols == ("000001.SZ", "600519.SH")

        # Close: same natural key → UPDATE, not a second INSERT.
        db.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=1_000_000,
            closed_at_ms=1_030_000,
            duration_sec=30.0,
            symbols=["600519.SH", "000001.SZ"],
        )
        rows = db.list_l1_gaps()
        assert len(rows) == 1  # still one row
        assert rows[0].closed_at_ms == 1_030_000
        assert rows[0].duration_sec == pytest.approx(30.0)

    def test_close_without_prior_open_upserts(self, db: KlineDB) -> None:
        """A close arriving with no prior open row must still persist.

        Happens when the poll loop starts before the sink is attached: the
        episode opens in memory only, then closes after wiring.
        """
        db.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=500,
            closed_at_ms=900,
            duration_sec=0.4,
            symbols=["600519.SH"],
        )
        rows = db.list_l1_gaps()
        assert len(rows) == 1
        assert rows[0].opened_at_ms == 500
        assert rows[0].closed_at_ms == 900

    def test_two_outages_across_a_restart_both_survive(self, tmp_path) -> None:
        """Regression: the second outage must not overwrite the first.

        Identity used to be a per-process counter, so a restarted process
        (counting from 1 again) clobbered the pre-restart row — one outage
        silently erasing another. The natural key makes each row addressable
        independently of process lifetime.
        """
        path = tmp_path / "klines.db"

        # Process 1: an outage at one instant.
        db1 = KlineDB(str(path))
        db1.open()
        db1.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=1_700_000_000_000,
            closed_at_ms=1_700_000_030_000,
            duration_sec=30.0,
            symbols=["600519.SH"],
        )
        db1.close()

        # Process 2 (restart): a *different* outage, same counter would be 1.
        db2 = KlineDB(str(path))
        db2.open()
        db2.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=1_700_020_000_000,
            closed_at_ms=1_700_020_060_000,
            duration_sec=60.0,
            symbols=["300750.SZ"],
        )
        rows = db2.list_l1_gaps()
        db2.close()

        assert len(rows) == 2, "restart must not overwrite the earlier outage"
        assert [r.opened_at_ms for r in rows] == [
            1_700_000_000_000,
            1_700_020_000_000,
        ]

    def test_legacy_counter_schema_is_migrated(self, tmp_path) -> None:
        """A DB written by the old per-counter build upgrades in place.

        The earlier schema keyed on ``episode_id INTEGER PRIMARY KEY``; opening
        such a file must re-key it to (source, opened_at_ms) without losing the
        outage rows it already holds.
        """
        import sqlite3

        path = tmp_path / "klines.db"
        con = sqlite3.connect(str(path))
        con.execute(
            "CREATE TABLE l1_snapshot_gaps ("
            "episode_id INTEGER PRIMARY KEY, source TEXT NOT NULL, "
            "opened_at_ms INTEGER NOT NULL, closed_at_ms INTEGER, "
            "duration_sec REAL, symbols TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO l1_snapshot_gaps VALUES (1,'astock_intraday',1000,1050,"
            "0.05,'600519.SH')"
        )
        con.commit()
        con.close()

        db = KlineDB(str(path))
        db.open()
        # The re-key must actually take: a write on the migrated table has to
        # satisfy the (source, opened_at_ms) upsert, which the legacy schema
        # would reject outright.
        db.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=2000,
            closed_at_ms=2030,
            duration_sec=0.03,
            symbols=["300750.SZ"],
        )
        rows = db.list_l1_gaps()
        db.close()

        assert [r.opened_at_ms for r in rows] == [1000, 2000]
        assert rows[0].closed_at_ms == 1050
        assert rows[1].symbols == ("300750.SZ",)

    def test_interrupted_legacy_rebuild_resumes(self, tmp_path) -> None:
        """A crash between RENAME and DROP must not lose the legacy rows.

        Models the half-migrated file a crash leaves behind: the legacy table
        already renamed, the new-shape table not yet created. Opening must
        finish the rebuild rather than treat the DB as done.
        """
        import sqlite3

        path = tmp_path / "klines.db"
        con = sqlite3.connect(str(path))
        con.execute(
            "CREATE TABLE _l1_gaps_legacy ("
            "episode_id INTEGER PRIMARY KEY, source TEXT NOT NULL, "
            "opened_at_ms INTEGER NOT NULL, closed_at_ms INTEGER, "
            "duration_sec REAL, symbols TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO _l1_gaps_legacy VALUES (1,'astock_intraday',1000,1050,"
            "0.05,'600519.SH')"
        )
        con.commit()
        con.close()

        db = KlineDB(str(path))
        db.open()
        rows = db.list_l1_gaps()
        db.close()

        assert len(rows) == 1
        assert rows[0].opened_at_ms == 1000
        assert rows[0].closed_at_ms == 1050

    def test_list_since_filters_by_open_time(self, db: KlineDB) -> None:
        for opened in (100, 200, 300):
            db.record_l1_gap(
                source="astock_intraday",
                opened_at_ms=opened,
                closed_at_ms=opened + 50,
                duration_sec=0.05,
                symbols=["600519.SH"],
            )
        assert [r.opened_at_ms for r in db.list_l1_gaps()] == [100, 200, 300]
        assert [r.opened_at_ms for r in db.list_l1_gaps(since_ms=250)] == [300]

    def test_open_rows_survive_a_reopen(self, tmp_path) -> None:
        path = tmp_path / "klines.db"
        db = KlineDB(str(path))
        with db.connect():
            db.record_l1_gap(
                source="astock_intraday",
                opened_at_ms=1000,
                closed_at_ms=None,
                duration_sec=None,
                symbols=["600519.SH"],
            )
        # Reopen the same file: the open row is durable (visible, not swallowed).
        db2 = KlineDB(str(path))
        with db2.connect():
            rows = db2.list_l1_gaps()
        assert len(rows) == 1
        assert rows[0].opened_at_ms == 1000
        assert rows[0].closed_at_ms is None

    def test_volume_bracket_roundtrips(self, db: KlineDB) -> None:
        """The open row has no bracket; the close row's bracket is persisted."""
        db.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=1000,
            closed_at_ms=None,
            duration_sec=None,
            symbols=["600519.SH"],
        )
        opened = db.list_l1_gaps()[0]
        assert opened.volume_before is None
        assert opened.volume_after is None
        assert opened.volume_delta is None
        assert opened.volume_symbols == 0

        db.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=1000,
            closed_at_ms=1050,
            duration_sec=0.05,
            symbols=["600519.SH"],
            volume_before=1000.0,
            volume_after=1250.0,
            volume_symbols=1,
        )
        closed = db.list_l1_gaps()[0]
        assert closed.volume_before == 1000.0
        assert closed.volume_after == 1250.0
        assert closed.volume_delta == 250.0
        assert closed.volume_symbols == 1

    def test_volume_defaults_to_unmeasured(self, db: KlineDB) -> None:
        """A caller that does not measure turnover writes a null bracket.

        Null means "not measured" and must survive the roundtrip as null —
        coercing it to 0.0 would turn "unknown" into the false claim "nothing
        traded".
        """
        db.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=1000,
            closed_at_ms=1050,
            duration_sec=0.05,
            symbols=["600519.SH"],
        )
        row = db.list_l1_gaps()[0]
        assert row.volume_before is None
        assert row.volume_after is None
        assert row.volume_delta is None
        assert row.volume_symbols == 0

    def test_natural_key_table_predating_the_volume_columns_is_extended(
        self, tmp_path
    ) -> None:
        """A ledger written before Option A′ gains the columns in place.

        The table already exists in the natural-key shape, so CREATE TABLE IF
        NOT EXISTS is a no-op for it; the volume columns have to be added by
        ALTER, and the pre-existing rows must read back as unmeasured.
        """
        import sqlite3

        path = tmp_path / "klines.db"
        con = sqlite3.connect(str(path))
        con.execute(
            "CREATE TABLE l1_snapshot_gaps ("
            "source TEXT NOT NULL, opened_at_ms INTEGER NOT NULL, "
            "closed_at_ms INTEGER, duration_sec REAL, symbols TEXT NOT NULL, "
            "PRIMARY KEY (source, opened_at_ms))"
        )
        con.execute(
            "INSERT INTO l1_snapshot_gaps VALUES "
            "('astock_intraday',1000,1050,0.05,'600519.SH')"
        )
        con.commit()
        con.close()

        db = KlineDB(str(path))
        db.open()
        rows = db.list_l1_gaps()
        assert len(rows) == 1
        assert rows[0].opened_at_ms == 1000
        assert rows[0].volume_before is None
        assert rows[0].volume_symbols == 0

        # And the extended table accepts a bracketed write.
        db.record_l1_gap(
            source="astock_intraday",
            opened_at_ms=2000,
            closed_at_ms=2050,
            duration_sec=0.05,
            symbols=["600519.SH"],
            volume_before=10.0,
            volume_after=20.0,
            volume_symbols=1,
        )
        rows = db.list_l1_gaps()
        db.close()
        assert [r.opened_at_ms for r in rows] == [1000, 2000]
        assert rows[1].volume_delta == 10.0

    def test_legacy_rebuild_carries_rows_with_unmeasured_volume(self, tmp_path) -> None:
        """A counter-era row survives the re-key with a null bracket.

        The legacy schema had no volume columns at all, so every migrated row
        is unmeasured by construction.
        """
        import sqlite3

        path = tmp_path / "klines.db"
        con = sqlite3.connect(str(path))
        con.execute(
            "CREATE TABLE l1_snapshot_gaps ("
            "episode_id INTEGER PRIMARY KEY, source TEXT NOT NULL, "
            "opened_at_ms INTEGER NOT NULL, closed_at_ms INTEGER, "
            "duration_sec REAL, symbols TEXT NOT NULL)"
        )
        con.execute(
            "INSERT INTO l1_snapshot_gaps VALUES "
            "(1,'astock_intraday',1000,1050,0.05,'600519.SH')"
        )
        con.commit()
        con.close()

        db = KlineDB(str(path))
        db.open()
        rows = db.list_l1_gaps()
        db.close()
        assert len(rows) == 1
        assert rows[0].volume_before is None
        assert rows[0].volume_after is None
        assert rows[0].volume_symbols == 0

    def test_reopening_an_extended_table_is_idempotent(self, tmp_path) -> None:
        """Re-running open() on an already-extended file must not error.

        ALTER ADD COLUMN is not idempotent on its own (a duplicate column is an
        error), so the guard around it matters: a second open of the same file
        is the common case, not an edge case.
        """
        path = tmp_path / "klines.db"
        db = KlineDB(str(path))
        with db.connect():
            db.record_l1_gap(
                source="astock_intraday",
                opened_at_ms=1000,
                closed_at_ms=1050,
                duration_sec=0.05,
                symbols=["600519.SH"],
                volume_before=1.0,
                volume_after=2.0,
                volume_symbols=1,
            )
        # Fresh instance, same file: this runs the schema pass again.
        db2 = KlineDB(str(path))
        with db2.connect():
            rows = db2.list_l1_gaps()
        assert len(rows) == 1
        assert rows[0].volume_delta == 1.0
