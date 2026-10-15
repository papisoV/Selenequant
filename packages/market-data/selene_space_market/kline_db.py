"""SQLite persistence for candlesticks.

The Redis layer (``KlineStore``) only keeps a rolling hot window; this module
is the durable copy that offline backtests and research jobs read from. Rows
are keyed by the full partition tuple so the same file can hold many
exchange/segment/symbol/granularity series side by side.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import pandas as pd

from selene_space_types.market import KlineEvent

logger = logging.getLogger(__name__)

_TABLE_DDL = """
CREATE TABLE IF NOT EXISTS klines (
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    timestamp INTEGER NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    closed INTEGER DEFAULT 0,
    PRIMARY KEY (exchange, market_type, symbol, interval, timestamp)
)
"""

_TIME_INDEX_DDL = "CREATE INDEX IF NOT EXISTS idx_klines_time ON klines(timestamp)"

# Scan watermark: per-partition "verified through" stamp advanced only by the
# gap sweeper. Lives in this file's own DDL (not the migration system) so the
# durable candle store stays self-contained.
_SCAN_STATE_DDL = """
CREATE TABLE IF NOT EXISTS kline_scan_state (
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    verified_through_ts INTEGER,
    updated_at INTEGER NOT NULL,
    PRIMARY KEY (exchange, market_type, symbol, interval)
)
"""

# Open-hole ledger: one row per detected interior gap, carrying an attempt
# counter so an unfillable (source-side) hole flips to permanent and stops
# consuming REST budget. Cleared the moment the hole closes.
_HOLE_STATE_DDL = """
CREATE TABLE IF NOT EXISTS kline_hole_state (
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    hole_left_ts INTEGER NOT NULL,
    hole_right_ts INTEGER NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_attempt_ts INTEGER,
    permanent INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (exchange, market_type, symbol, interval, hole_left_ts, hole_right_ts)
)
"""

_HOLE_PERMANENT_AFTER = 3

# L1 snapshot outage ledger (S2): one row per interruption of the A-share
# intraday poll source, written at *open* and updated at *close* so a restart
# mid-outage leaves a visible open row instead of a silent hole. Lives in this
# file's own DDL for the same reason as the hole table: the durable store stays
# self-contained (the migration system targets a different DB).
#
# Keyed by the episode's *natural* identity — (source, opened_at_ms) — not a
# counter: a per-process counter resets to 1 on restart, so a later outage
# would overwrite an earlier row. Two outages of one source cannot open in the
# same millisecond (closing one takes a full poll cycle), so this is
# collision-free across restarts.
_L1_GAPS_DDL = """
CREATE TABLE IF NOT EXISTS l1_snapshot_gaps (
    source TEXT NOT NULL,
    opened_at_ms INTEGER NOT NULL,
    closed_at_ms INTEGER,
    duration_sec REAL,
    symbols TEXT NOT NULL,
    volume_before REAL,
    volume_after REAL,
    volume_symbols INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (source, opened_at_ms)
)
"""

# Columns added to the ledger after its first release (Option A′ volume
# bracket). Applied to a pre-existing table with ALTER TABLE ... ADD COLUMN,
# which is additive and safe to re-run, so an older klines.db upgrades in place.
_L1_GAPS_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("volume_before", "REAL"),
    ("volume_after", "REAL"),
    ("volume_symbols", "INTEGER NOT NULL DEFAULT 0"),
)

_MS_PER_SECOND = 1000


def _date_to_ms(value: datetime) -> int:
    """Normalize a datetime to epoch millis, assuming naive values are UTC."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * _MS_PER_SECOND)


def _kline_to_row(k: KlineEvent) -> tuple:
    return (
        k.exchange,
        k.market_type,
        k.symbol,
        k.interval,
        k.timestamp,
        k.open,
        k.high,
        k.low,
        k.close,
        k.volume,
        1 if k.closed else 0,
    )


@dataclass(frozen=True)
class ScanState:
    """Per-partition watermark: the last timestamp the sweeper has verified."""

    exchange: str
    market_type: str
    symbol: str
    interval: str
    verified_through_ts: int | None
    updated_at: int


@dataclass(frozen=True)
class HoleState:
    """One detected interior gap and its repair attempt history."""

    exchange: str
    market_type: str
    symbol: str
    interval: str
    hole_left_ts: int
    hole_right_ts: int
    attempts: int
    last_attempt_ts: int | None
    permanent: bool


@dataclass(frozen=True)
class L1SnapshotGap:
    """One recorded outage episode of the A-share intraday poll source (S2).

    ``closed_at_ms`` / ``duration_sec`` are ``None`` while the outage is live
    (the row is written at open), so an open row is itself the signal that the
    process went down mid-outage.

    Identity is the natural key ``(source, opened_at_ms)`` — the same pair the
    table is primary-keyed on. A per-process counter would collide across
    restarts (both processes count from 1), so it is deliberately not carried
    here.

    ``volume_before``/``volume_after`` bracket traded volume across the gap
    (Option A′) and are ``None`` when the episode could not be bracketed —
    a missing measurement, not a zero. ``volume_symbols`` is how many symbols
    the bracket covers; ``volume_delta`` is the difference when both sides
    exist, else ``None``.
    """

    source: str
    opened_at_ms: int
    closed_at_ms: int | None
    duration_sec: float | None
    symbols: tuple[str, ...]
    volume_before: float | None = None
    volume_after: float | None = None
    volume_symbols: int = 0

    @property
    def volume_delta(self) -> float | None:
        """Traded volume across the gap, or ``None`` when unbracketed."""
        if self.volume_before is None or self.volume_after is None:
            return None
        return self.volume_after - self.volume_before


class KlineDB:
    """Durable candle storage with per-partition upsert semantics."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            # Single source of truth (repo-root data/klines.db, env override).
            from selene_space_core.kline_paths import default_klines_db_path

            db_path = default_klines_db_path()
        self._path = str(db_path)
        self._conn: sqlite3.Connection | None = None
        # 多线程访问(app 主线程建连 + startup-sync 任务/to_thread 读写)需要跨线程共享连接;
        # SQLite 并发写靠这把锁串行化(check_same_thread=False 只解除亲和限制,不加锁)。
        # RLock:读路径持锁时 _ensure() 会重入 open(),非重入锁会自锁死。
        self._lock = threading.RLock()

    # -- lifecycle ---------------------------------------------------------

    def _ensure_l1_gaps_schema(self) -> None:
        """Create the ledger table, upgrading a legacy per-counter build.

        Databases written before this table was re-keyed to the natural
        ``(source, opened_at_ms)`` carry an ``episode_id INTEGER PRIMARY KEY``
        instead, which the upsert cannot target. Those rows are still real
        outages, so they are migrated across rather than dropped; the counter
        itself is discarded (it was meaningless once the process exited).
        """
        assert self._conn is not None
        existing = {
            row[0]
            for row in self._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "_l1_gaps_legacy" in existing:
            # A previous run crashed between RENAME and DROP; finish it.
            self._finish_l1_gaps_rebuild()
            return
        legacy_cols = {
            row[1] for row in self._conn.execute("PRAGMA table_info(l1_snapshot_gaps)")
        }
        self._conn.execute(_L1_GAPS_DDL)
        if "episode_id" in legacy_cols:
            # Rebuild without the counter. Rename-then-recreate is the SQLite
            # idiom for a key change; it runs inside one explicit transaction so
            # a crash leaves either the old table or the new one, never a
            # half-state. The rebuild writes the full new shape, so the volume
            # columns below need no separate pass.
            with self._conn:
                self._conn.execute(
                    "ALTER TABLE l1_snapshot_gaps RENAME TO _l1_gaps_legacy"
                )
                self._finish_l1_gaps_rebuild(commit=False)
            logger.info("KlineDB: migrated l1_snapshot_gaps to natural-key schema")
            return
        # Already natural-keyed, but possibly from before the volume bracket was
        # added. `_L1_GAPS_DDL` is CREATE IF NOT EXISTS, so it did not touch the
        # existing table — add whatever is missing. Re-read the columns here:
        # the DDL above may have just created the table in full shape.
        current_cols = {
            row[1] for row in self._conn.execute("PRAGMA table_info(l1_snapshot_gaps)")
        }
        to_add = [
            (name, decl)
            for name, decl in _L1_GAPS_ADDED_COLUMNS
            if name not in current_cols
        ]
        if not to_add:
            return
        with self._conn:
            for name, decl in to_add:
                self._conn.execute(
                    f"ALTER TABLE l1_snapshot_gaps ADD COLUMN {name} {decl}"
                )
        logger.info(
            "KlineDB: added l1_snapshot_gaps columns %s",
            ", ".join(name for name, _ in to_add),
        )

    def _finish_l1_gaps_rebuild(self, *, commit: bool = True) -> None:
        """Create the new-shape table and copy rows out of the legacy one."""
        assert self._conn is not None
        ctx = self._conn if commit else nullcontext()
        with ctx:
            self._conn.execute(_L1_GAPS_DDL)
            # INSERT OR REPLACE lets the last row win, so order closed rows last
            # (a healed episode is the more complete record of the two).
            self._conn.execute(
                "INSERT OR REPLACE INTO l1_snapshot_gaps "
                "(source, opened_at_ms, closed_at_ms, duration_sec, symbols) "
                "SELECT source, opened_at_ms, closed_at_ms, duration_sec, symbols "
                "FROM _l1_gaps_legacy ORDER BY (closed_at_ms IS NOT NULL), episode_id"
            )
            self._conn.execute("DROP TABLE _l1_gaps_legacy")

    def open(self) -> None:
        """Connect and ensure schema; repeated calls are harmless."""
        with self._lock:
            if self._conn is not None:
                return
            if self._path != ":memory:":
                Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(self._path, check_same_thread=False)
            self._conn.execute(_TABLE_DDL)
            self._conn.execute(_TIME_INDEX_DDL)
            self._conn.execute(_SCAN_STATE_DDL)
            self._conn.execute(_HOLE_STATE_DDL)
            self._ensure_l1_gaps_schema()
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None

    @contextmanager
    def connect(self) -> Iterator[KlineDB]:
        """Scoped connection: opened on entry, always closed on exit."""
        self.open()
        try:
            yield self
        finally:
            self.close()

    def _ensure(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    # -- write -------------------------------------------------------------

    def put_klines(self, klines: list[KlineEvent]) -> int:
        """Upsert candles; later writes win on identical timestamps."""
        if not klines:
            return 0
        with self._lock:
            conn = self._ensure()
            conn.executemany(
                "INSERT OR REPLACE INTO klines VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                [_kline_to_row(k) for k in klines],
            )
            conn.commit()
        return len(klines)

    # -- read ----------------------------------------------------------------

    def get_klines(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> pd.DataFrame:
        """Load one partition as a timestamp-ordered frame.

        ``start_date`` / ``end_date`` are inclusive bounds.
        """
        clauses = [
            "exchange = ?",
            "market_type = ?",
            "symbol = ?",
            "interval = ?",
        ]
        params: list[object] = [exchange, market_type, symbol, interval]
        if start_date is not None:
            clauses.append("timestamp >= ?")
            params.append(_date_to_ms(start_date))
        if end_date is not None:
            clauses.append("timestamp <= ?")
            params.append(_date_to_ms(end_date))
        sql = (
            f"SELECT * FROM klines WHERE {' AND '.join(clauses)} ORDER BY timestamp ASC"
        )
        with self._lock:
            frame = pd.read_sql_query(sql, self._ensure(), params=params)
        return frame

    def count(self, exchange: str, market_type: str, symbol: str, interval: str) -> int:
        with self._lock:
            cur = self._ensure().execute(
                "SELECT COUNT(*) FROM klines WHERE exchange=? AND market_type=? "
                "AND symbol=? AND interval=?",
                (exchange, market_type, symbol, interval),
            )
            n = int(cur.fetchone()[0])
        return n

    def get_latest_timestamp(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> int | None:
        with self._lock:
            cur = self._ensure().execute(
                "SELECT MAX(timestamp) FROM klines WHERE exchange=? AND market_type=? "
                "AND symbol=? AND interval=?",
                (exchange, market_type, symbol, interval),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def get_earliest_timestamp(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> int | None:
        with self._lock:
            cur = self._ensure().execute(
                "SELECT MIN(timestamp) FROM klines WHERE exchange=? AND market_type=? "
                "AND symbol=? AND interval=?",
                (exchange, market_type, symbol, interval),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def has_data(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        min_candles: int = 1,
    ) -> bool:
        return self.count(exchange, market_type, symbol, interval) >= min_candles

    # -- maintenance -----------------------------------------------------------

    def delete_partition(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> int:
        """Remove every row of one partition; returns rows deleted."""
        with self._lock:
            cur = self._ensure().execute(
                "DELETE FROM klines WHERE exchange=? AND market_type=? AND symbol=? AND interval=?",
                (exchange, market_type, symbol, interval),
            )
            self._ensure().commit()
        return cur.rowcount

    def delete_before(self, ts_ms: int) -> int:
        """Purge rows older than *ts_ms* across all partitions."""
        with self._lock:
            cur = self._ensure().execute(
                "DELETE FROM klines WHERE timestamp < ?", (ts_ms,)
            )
            self._ensure().commit()
        return cur.rowcount

    # -- gap scan / watermark --------------------------------------------------

    def list_partitions(self) -> list[tuple[str, str, str, str]]:
        """Every distinct (exchange, market_type, symbol, interval) in the store."""
        with self._lock:
            cur = self._ensure().execute(
                "SELECT DISTINCT exchange, market_type, symbol, interval FROM klines"
            )
            return [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]

    def find_gaps(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        step_ms: int,
        *,
        since_ts: int | None = None,
    ) -> list[tuple[int, int]]:
        """Interior holes: consecutive stamps more than two steps apart.

        Fast path: when ``count == (max-min)//step + 1`` the partition is
        contiguous and we return without touching every row. Only a mismatch
        pays for the pairwise scan. ``since_ts`` bounds the scan to a floor
        (``None`` scans the whole partition, so holes older than any rolling
        window are still caught).
        """
        if step_ms <= 0:
            raise ValueError("step_ms must be positive")
        clauses = [
            "exchange = ?",
            "market_type = ?",
            "symbol = ?",
            "interval = ?",
        ]
        params: list[object] = [exchange, market_type, symbol, interval]
        if since_ts is not None:
            clauses.append("timestamp >= ?")
            params.append(since_ts)
        where = " AND ".join(clauses)
        with self._lock:
            conn = self._ensure()
            row = conn.execute(
                f"SELECT MIN(timestamp), MAX(timestamp), COUNT(*) FROM klines WHERE {where}",
                params,
            ).fetchone()
            if not row or row[0] is None or row[2] < 2:
                return []
            min_ts, max_ts, total = int(row[0]), int(row[1]), int(row[2])
            expected = (max_ts - min_ts) // step_ms + 1
            if expected == total:
                return []
            cur = conn.execute(
                f"SELECT timestamp FROM klines WHERE {where} ORDER BY timestamp ASC",
                params,
            )
            stamps = [int(r[0]) for r in cur.fetchall()]
        return [
            (left, right)
            for left, right in zip(stamps, stamps[1:], strict=False)
            if right - left > 2 * step_ms
        ]

    def get_verified_through(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> int | None:
        with self._lock:
            cur = self._ensure().execute(
                "SELECT verified_through_ts FROM kline_scan_state WHERE exchange=? "
                "AND market_type=? AND symbol=? AND interval=?",
                (exchange, market_type, symbol, interval),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def set_verified_through(
        self, exchange: str, market_type: str, symbol: str, interval: str, ts_ms: int
    ) -> None:
        with self._lock:
            conn = self._ensure()
            conn.execute(
                "INSERT INTO kline_scan_state "
                "(exchange, market_type, symbol, interval, verified_through_ts, updated_at) "
                "VALUES (?,?,?,?,?,?) "
                "ON CONFLICT(exchange, market_type, symbol, interval) "
                "DO UPDATE SET verified_through_ts=excluded.verified_through_ts, "
                "updated_at=excluded.updated_at",
                (exchange, market_type, symbol, interval, ts_ms, _utcnow_ms()),
            )
            conn.commit()

    def list_holes(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        *,
        only_open: bool = True,
    ) -> list[HoleState]:
        """Recorded holes for a partition. ``only_open`` excludes permanent ones."""
        sql = (
            "SELECT hole_left_ts, hole_right_ts, attempts, last_attempt_ts, permanent "
            "FROM kline_hole_state WHERE exchange=? AND market_type=? AND symbol=? "
            "AND interval=?"
        )
        if only_open:
            sql += " AND permanent=0"
        with self._lock:
            cur = self._ensure().execute(sql, (exchange, market_type, symbol, interval))
            rows = cur.fetchall()
        return [
            HoleState(
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                interval=interval,
                hole_left_ts=int(r[0]),
                hole_right_ts=int(r[1]),
                attempts=int(r[2]),
                last_attempt_ts=int(r[3]) if r[3] is not None else None,
                permanent=bool(r[4]),
            )
            for r in rows
        ]

    def record_hole_attempt(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        hole_left_ts: int,
        hole_right_ts: int,
        *,
        now_ms: int,
    ) -> HoleState:
        """Bump a hole's attempt counter; >=3 attempts flips it permanent."""
        with self._lock:
            conn = self._ensure()
            conn.execute(
                "INSERT INTO kline_hole_state "
                "(exchange, market_type, symbol, interval, hole_left_ts, hole_right_ts, "
                "attempts, last_attempt_ts, permanent) VALUES (?,?,?,?,?,?,1,?,0) "
                "ON CONFLICT(exchange, market_type, symbol, interval, hole_left_ts, hole_right_ts) "
                "DO UPDATE SET attempts=attempts+1, last_attempt_ts=excluded.last_attempt_ts",
                (
                    exchange,
                    market_type,
                    symbol,
                    interval,
                    hole_left_ts,
                    hole_right_ts,
                    now_ms,
                ),
            )
            conn.execute(
                "UPDATE kline_hole_state SET permanent=1 WHERE exchange=? AND market_type=? "
                "AND symbol=? AND interval=? AND hole_left_ts=? AND hole_right_ts=? "
                "AND attempts>=?",
                (
                    exchange,
                    market_type,
                    symbol,
                    interval,
                    hole_left_ts,
                    hole_right_ts,
                    _HOLE_PERMANENT_AFTER,
                ),
            )
            conn.commit()
        for state in self.list_holes(
            exchange, market_type, symbol, interval, only_open=False
        ):
            if (
                state.hole_left_ts == hole_left_ts
                and state.hole_right_ts == hole_right_ts
            ):
                return state
        raise RuntimeError("hole row vanished after upsert")  # pragma: no cover

    def clear_hole(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        hole_left_ts: int,
        hole_right_ts: int,
    ) -> None:
        with self._lock:
            conn = self._ensure()
            conn.execute(
                "DELETE FROM kline_hole_state WHERE exchange=? AND market_type=? "
                "AND symbol=? AND interval=? AND hole_left_ts=? AND hole_right_ts=?",
                (exchange, market_type, symbol, interval, hole_left_ts, hole_right_ts),
            )
            conn.commit()

    # -- L1 snapshot outage ledger (S2) ------------------------------------

    def record_l1_gap(
        self,
        *,
        source: str,
        opened_at_ms: int,
        closed_at_ms: int | None,
        duration_sec: float | None,
        symbols: list[str] | tuple[str, ...],
        volume_before: float | None = None,
        volume_after: float | None = None,
        volume_symbols: int = 0,
    ) -> None:
        """Upsert one outage episode, keyed by ``(source, opened_at_ms)``.

        Called twice per episode — at *open* (``closed_at_ms=None``) and at
        *close*. The natural key means the close updates the open row rather
        than inserting a duplicate, and — unlike a per-process counter — it
        survives a restart without one process's rows clobbering another's.
        Tolerant of a close with no prior open row (the poll loop can open an
        episode before the sink is attached), hence INSERT-or-UPDATE rather
        than a bare UPDATE.

        ``volume_*`` carry the Option A′ bracket and default to "not measured"
        so the open row (which has no bracket yet) and any caller that does not
        measure turnover write the same row shape.
        """
        payload = ",".join(sorted(symbols))
        with self._lock:
            conn = self._ensure()
            conn.execute(
                "INSERT INTO l1_snapshot_gaps "
                "(source, opened_at_ms, closed_at_ms, duration_sec, symbols, "
                "volume_before, volume_after, volume_symbols) "
                "VALUES (?,?,?,?,?,?,?,?) "
                "ON CONFLICT(source, opened_at_ms) DO UPDATE SET "
                "closed_at_ms=excluded.closed_at_ms, "
                "duration_sec=excluded.duration_sec, "
                "symbols=excluded.symbols, "
                "volume_before=excluded.volume_before, "
                "volume_after=excluded.volume_after, "
                "volume_symbols=excluded.volume_symbols",
                (
                    source,
                    opened_at_ms,
                    closed_at_ms,
                    duration_sec,
                    payload,
                    volume_before,
                    volume_after,
                    volume_symbols,
                ),
            )
            conn.commit()

    def list_l1_gaps(self, *, since_ms: int | None = None) -> list[L1SnapshotGap]:
        """Recorded outage episodes, ordered by open time.

        ``since_ms`` filters on the episode's open timestamp.
        """
        sql = (
            "SELECT source, opened_at_ms, closed_at_ms, duration_sec, "
            "symbols, volume_before, volume_after, volume_symbols "
            "FROM l1_snapshot_gaps"
        )
        params: list[object] = []
        if since_ms is not None:
            sql += " WHERE opened_at_ms >= ?"
            params.append(since_ms)
        sql += " ORDER BY opened_at_ms ASC, source ASC"
        with self._lock:
            cur = self._ensure().execute(sql, params)
            rows = cur.fetchall()
        return [
            L1SnapshotGap(
                source=str(r[0]),
                opened_at_ms=int(r[1]),
                closed_at_ms=int(r[2]) if r[2] is not None else None,
                duration_sec=float(r[3]) if r[3] is not None else None,
                symbols=tuple(s for s in str(r[4]).split(",") if s),
                volume_before=float(r[5]) if r[5] is not None else None,
                volume_after=float(r[6]) if r[6] is not None else None,
                volume_symbols=int(r[7]) if r[7] is not None else 0,
            )
            for r in rows
        ]


def _utcnow_ms() -> int:
    return int(datetime.now(tz=timezone.utc).timestamp() * 1000)
