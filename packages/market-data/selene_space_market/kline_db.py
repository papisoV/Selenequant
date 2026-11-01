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
from functools import partial
from pathlib import Path
from typing import Callable, Iterator, Sequence

import pandas as pd

from selene_space_types.market import KlineEvent

logger = logging.getLogger(__name__)

# A predicate deciding whether the jump from ``left_ts`` to ``right_ts`` is
# larger than the series should ever produce. ``None`` keeps the fixed-step
# rule (any jump beyond two steps is a hole), which is correct for 24/7
# markets and wrong for anything with closed sessions.
GapPredicate = Callable[[int, int], bool]


def _fixed_step_hole(left_ts: int, right_ts: int, step_ms: int) -> bool:
    return right_ts - left_ts > 2 * step_ms


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

# Composite on the full partition tuple, not on ``timestamp`` alone: every
# read this table serves is scoped to one partition (``get_klines``,
# ``get_cross_section``, the gap scan), and a timestamp-only index makes the
# planner filter the rest by row. Under ``IF NOT EXISTS`` the name alone
# decides, so the earlier single-column DDL was frozen in place on existing
# databases and would only have bitten a *fresh* one — verified 2026-10-17:
# the live index is composite and the cross-section query uses it as a
# covering index for both the outer scan and the correlated subquery.
_TIME_INDEX_DDL = (
    "CREATE INDEX IF NOT EXISTS idx_klines_time ON klines"
    "(exchange, market_type, symbol, interval, timestamp)"
)

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

# Tail-probe memo: the forward tail advance asks the source for bars past the
# newest local one. A source that answers "nothing newer" (delisted symbol,
# market closed, exchange lagging) would otherwise be asked again every sweep,
# forever. Recording *where* and *when* we last probed lets the next round skip
# the same question until the retry window elapses (and immediately re-probe
# once local data moves past the recorded cursor). Additive columns, so an
# older klines.db upgrades in place.
_SCAN_STATE_ADDED_COLUMNS: tuple[tuple[str, str], ...] = (
    ("tail_probe_cursor_ts", "INTEGER"),
    ("tail_probe_at_ms", "INTEGER"),
)

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

# Write-lock wait. This DB is shared by the local-api service and by offline
# backfills, and SQLite allows exactly one writer at a time — so a collision
# must *wait*, not fail. The sqlite3 default (5s) was short enough that a
# whole-market backfill died at symbol 5250 with "database is locked"; 60s
# rides out the service's short write bursts while still surfacing a genuinely
# stuck lock.
_BUSY_TIMEOUT_S = 60.0


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

    def _ensure_scan_state_columns(self) -> None:
        """Add the tail-probe memo columns to a pre-existing scan-state table."""
        assert self._conn is not None
        current = {
            row[1] for row in self._conn.execute("PRAGMA table_info(kline_scan_state)")
        }
        to_add = [
            (name, decl)
            for name, decl in _SCAN_STATE_ADDED_COLUMNS
            if name not in current
        ]
        if not to_add:
            return
        with self._conn:
            for name, decl in to_add:
                self._conn.execute(
                    f"ALTER TABLE kline_scan_state ADD COLUMN {name} {decl}"
                )
        logger.info(
            "KlineDB: added kline_scan_state columns %s",
            ", ".join(name for name, _ in to_add),
        )

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
            self._conn = sqlite3.connect(
                self._path, check_same_thread=False, timeout=_BUSY_TIMEOUT_S
            )
            # Busy timeout is per-connection, not a file setting: without it a
            # writer that collides with another process fails instantly with
            # "database is locked" instead of waiting for the other to commit.
            # A whole-market backfill holds the write lock for minutes at a
            # time and shares this file with the local-api service, so the
            # default 5s is far too short.
            self._conn.execute(f"PRAGMA busy_timeout={int(_BUSY_TIMEOUT_S * 1000)}")
            self._conn.execute(_TABLE_DDL)
            self._conn.execute(_TIME_INDEX_DDL)
            self._conn.execute(_SCAN_STATE_DDL)
            self._ensure_scan_state_columns()
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

    def get_bar(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        timestamp: int,
    ) -> dict[str, object] | None:
        """One stored bar by primary key, or ``None`` when absent.

        Exists for the restatement check: a daily bar is upserted under a stable
        session-date timestamp, so "is the incoming row news?" cannot be answered
        from timestamps alone. Reading the stored row is one indexed lookup on
        the composite primary key, which is what the caller needs to tell an
        unchanged repoll from the post-close restatement that replaces an
        intraday snapshot.
        """
        with self._lock:
            cur = self._ensure().execute(
                "SELECT open, high, low, close, volume, closed FROM klines "
                "WHERE exchange=? AND market_type=? AND symbol=? AND interval=? "
                "AND timestamp=?",
                (exchange, market_type, symbol, interval, timestamp),
            )
            row = cur.fetchone()
        if row is None:
            return None
        return {
            "open": float(row[0]),
            "high": float(row[1]),
            "low": float(row[2]),
            "close": float(row[3]),
            "volume": float(row[4]),
            "closed": bool(row[5]),
        }

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

    def list_open_bars(
        self,
        *,
        allowed_sources: set[tuple[str, str]] | None = None,
    ) -> list[tuple[str, str, str, str, int]]:
        """Every row still flagged ``closed=0``.

        A bar is written by a REST read while it is still forming, then is never
        asked for again: ``_advance_tail`` starts at ``max(timestamp)+1`` and
        ``sync_incremental`` filters with ``timestamp > latest``. So a partial
        bar that is no longer the newest row keeps its truncated close and
        volume forever. Measured on the live store: 3,345 such rows across 897
        partitions, up to 63 days old.

        The open set is small (thousands of rows), so the caller filters by
        interval duration in Python rather than encoding per-interval steps in
        SQL.
        """
        clauses = ["closed = 0"]
        params: list[object] = []
        if allowed_sources:
            placeholders = ",".join("(?,?)" for _ in allowed_sources)
            clauses.append(f"(exchange, market_type) IN ({placeholders})")
            for source in sorted(allowed_sources):
                params.extend(source)
        sql = (
            "SELECT exchange, market_type, symbol, interval, timestamp "
            "FROM klines WHERE " + " AND ".join(clauses) + " ORDER BY timestamp ASC"
        )
        with self._lock:
            rows = self._ensure().execute(sql, params).fetchall()
        return [
            (str(ex), str(mt), str(sym), str(iv), int(ts))
            for ex, mt, sym, iv, ts in rows
        ]

    def get_partition_max_timestamp(
        self, exchange: str, market_type: str, interval: str
    ) -> int | None:
        """Newest timestamp anywhere in a partition, or ``None`` when empty.

        Distinct from :meth:`get_latest_timestamp`, which is scoped to one
        symbol. This one answers "how current is the market as a whole" in a
        single query — the check an EOD loop makes to decide whether today's bar
        has landed yet, and what the acceptance SQL in the daily-base plan
        measures.
        """
        with self._lock:
            cur = self._ensure().execute(
                "SELECT MAX(timestamp) FROM klines WHERE exchange=? AND "
                "market_type=? AND interval=?",
                (exchange, market_type, interval),
            )
            row = cur.fetchone()
        return int(row[0]) if row and row[0] is not None else None

    def get_cross_section(
        self,
        exchange: str,
        market_type: str,
        interval: str,
        *,
        end_ms: int,
    ) -> pd.DataFrame:
        """One row per symbol: its latest bar at or before ``end_ms``, plus prev close.

        The whole-market read a screener starts from — one query instead of one
        per symbol, which for 5574 symbols would be 5574 cursor round trips over
        the same connection.

        ``prev_close`` is the symbol's own previous session close, resolved in
        the same statement. It has to come from here rather than from the source
        endpoint: Sina does not publish it (unlike Tencent's ``prec``), and
        mixing one source's OHLC with another's previous close would silently
        blend two adjustment conventions on the one number limit-up math is
        computed from.

        Note the per-symbol ``timestamp`` is the latest bar *available*, which
        for a suspended symbol is earlier than ``end_ms``. That is the point:
        the row reports when the symbol last traded, not when it was asked
        about. A caller filtering on ``timestamp == end_ms`` would drop every
        halted name from the cross-section and quietly shrink the universe.
        """
        sql = """
            SELECT k.symbol,
                   k.timestamp,
                   k.open,
                   k.high,
                   k.low,
                   k.close,
                   k.volume,
                   (
                       SELECT p.close FROM klines p
                       WHERE p.exchange = k.exchange
                         AND p.market_type = k.market_type
                         AND p.symbol = k.symbol
                         AND p.interval = k.interval
                         AND p.timestamp < k.timestamp
                       ORDER BY p.timestamp DESC
                       LIMIT 1
                   ) AS prev_close
            FROM klines k
            WHERE k.exchange = ?
              AND k.market_type = ?
              AND k.interval = ?
              AND k.timestamp = (
                  SELECT MAX(m.timestamp) FROM klines m
                  WHERE m.exchange = k.exchange
                    AND m.market_type = k.market_type
                    AND m.symbol = k.symbol
                    AND m.interval = k.interval
                    AND m.timestamp <= ?
              )
            ORDER BY k.symbol ASC
        """
        params = [exchange, market_type, interval, end_ms]
        with self._lock:
            frame = pd.read_sql_query(sql, self._ensure(), params=params)
        return frame

    # -- maintenance -----------------------------------------------------------

    def get_partition_range(
        self,
        exchange: str,
        market_type: str,
        interval: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> pd.DataFrame:
        """Every bar of one partition inside ``[start_ms, end_ms]``, all symbols.

        The multi-day companion to :meth:`get_cross_section`: rotation needs a
        window of closes per symbol, and calling :meth:`get_klines` once per
        symbol would be 5574 round trips over the same connection.

        Both bounds are inclusive. Returns an empty frame (with the table's
        columns) rather than None when the window holds no bars, so a caller
        can group on ``symbol``/``timestamp`` without a KeyError.
        """
        sql = """
            SELECT symbol, timestamp, open, high, low, close, volume
            FROM klines
            WHERE exchange = ?
              AND market_type = ?
              AND interval = ?
              AND timestamp >= ?
              AND timestamp <= ?
            ORDER BY symbol ASC, timestamp ASC
        """
        params = [exchange, market_type, interval, start_ms, end_ms]
        with self._lock:
            frame = pd.read_sql_query(sql, self._ensure(), params=params)
        return frame

    def get_session_stamps(
        self,
        exchange: str,
        market_type: str,
        interval: str,
        *,
        start_ms: int | None = None,
        end_ms: int,
        limit: int | None = None,
        anchors: Sequence[str] | None = None,
    ) -> list[int]:
        """Distinct bar timestamps in ``[start_ms, end_ms]``, ascending — the session calendar.

        Daily bars are stamped at Beijing midnight (one row per symbol per
        session), so the distinct timestamps of a partition *are* the exchanges'
        trading calendar, holidays included. Deriving the calendar from a
        ``timedelta`` instead would invent sessions the exchange never held —
        the National Day block alone would add five phantom days.

        ``limit`` keeps the *most recent* ``limit`` sessions (still returned
        ascending), which is what a "last N sessions ending at ``end_ms``"
        caller wants.

        ``anchors`` switches to a cheaper strategy. Scanning the whole
        partition for ``DISTINCT timestamp`` costs ~0.8 s on a 3.9 M-row base
        no matter how few sessions are wanted, because the composite index is
        keyed on symbol first: the LIMIT cannot stop the dedupe early. Walking
        a handful of liquid symbols' index tails instead costs ~0 ms, but a
        halted anchor contributes no stamp for the sessions it missed. Both
        effects are absorbed by over-reading (``limit * 4`` per anchor) and
        checking the union actually produced ``limit`` stamps — if it did not,
        the call falls back to the exact whole-partition scan. Callers get
        correctness first and the fast path when it holds.
        """
        wanted = int(limit) if limit is not None else None
        if anchors and wanted is not None:
            found = self._session_stamps_from_anchors(
                exchange,
                market_type,
                interval,
                end_ms=end_ms,
                start_ms=start_ms,
                anchors=anchors,
                limit=wanted,
            )
            if len(found) >= wanted:
                return found[-wanted:]
        sql = """
            SELECT DISTINCT timestamp FROM klines
            WHERE exchange = ?
              AND market_type = ?
              AND interval = ?
              AND timestamp <= ?
        """
        params: list[object] = [exchange, market_type, interval, end_ms]
        if start_ms is not None:
            sql += " AND timestamp >= ?"
            params.append(start_ms)
        # DISTINCT + ORDER BY reads the whole slice; selecting the newest N
        # first and sorting after is cheaper when N is small.
        sql += " ORDER BY timestamp DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        with self._lock:
            rows = self._ensure().execute(sql, params).fetchall()
        stamps = sorted(int(row[0]) for row in rows if row[0] is not None)
        return stamps

    def get_session_counts(
        self,
        exchange: str,
        market_type: str,
        interval: str,
        *,
        start_ms: int,
        end_ms: int,
    ) -> list[tuple[int, int]]:
        """``[(timestamp, rows)]`` for every session in ``[start_ms, end_ms]``, newest first.

        The cheap answer to "how complete is each recent session?". A caller
        deciding *which* session to score would otherwise read each candidate's
        full cross-section (~6 s apiece on the 3.9 M-row base) just to count
        rows. One grouped pass over the same slice costs ~0.5 s for a month of
        sessions, because the count comes from the covering index without
        touching the row payload.

        Daily bars sit at Beijing midnight, so each ``timestamp`` is one
        session. Sessions with no rows simply do not appear — an empty list
        means the window is empty, not that counting failed.
        """
        sql = """
            SELECT timestamp, COUNT(*) FROM klines
            WHERE exchange = ?
              AND market_type = ?
              AND interval = ?
              AND timestamp >= ?
              AND timestamp <= ?
            GROUP BY timestamp
            ORDER BY timestamp DESC
        """
        params = [exchange, market_type, interval, start_ms, end_ms]
        with self._lock:
            rows = self._ensure().execute(sql, params).fetchall()
        return [(int(row[0]), int(row[1])) for row in rows if row[0] is not None]

    def _session_stamps_from_anchors(
        self,
        exchange: str,
        market_type: str,
        interval: str,
        *,
        end_ms: int,
        start_ms: int | None,
        anchors: Sequence[str],
        limit: int,
    ) -> list[int]:
        """Union of a few symbols' recent stamps — the fast path of :meth:`get_session_stamps`.

        Each anchor is a separate range scan on the (symbol, timestamp) arm of
        the composite index, so the work is proportional to ``limit`` rather
        than to the partition size.
        """
        per_anchor = max(limit * 4, limit + 8)
        marks = ", ".join("?" for _ in anchors)
        # A per-anchor LIMIT is illegal directly under UNION, so the "newest
        # per_anchor stamps of each anchor" cut is expressed as a window
        # function instead — same result, one statement.
        sql = f"""
            SELECT DISTINCT timestamp FROM (
                SELECT timestamp,
                       ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY timestamp DESC) AS rn
                FROM klines
                WHERE exchange = ? AND market_type = ? AND interval = ?
                  AND symbol IN ({marks}) AND timestamp <= ?
            )
            WHERE rn <= ?
            ORDER BY timestamp DESC
            LIMIT ?
        """
        params: list[object] = [
            exchange,
            market_type,
            interval,
            *anchors,
            end_ms,
            per_anchor,
            limit,
        ]
        with self._lock:
            rows = self._ensure().execute(sql, params).fetchall()
        stamps = sorted(int(row[0]) for row in rows if row[0] is not None)
        if start_ms is not None:
            stamps = [stamp for stamp in stamps if stamp >= start_ms]
        return stamps

    def get_last_before(
        self,
        exchange: str,
        market_type: str,
        interval: str,
        *,
        before_ms: int,
    ) -> pd.DataFrame:
        """Each symbol's last bar strictly before ``before_ms`` — one query.

        The per-symbol form (``get_klines`` in a loop) would be thousands of
        round trips. This resolves the prior close for a whole cross-section at
        once, which is what a windowed return calculation needs at its left
        edge: session k's return uses session k-1's close, and k-1 may sit
        before the window.

        Returns ``symbol`` / ``timestamp`` / ``close``, one row per symbol that
        has any earlier bar. A symbol absent from the result has no history
        before the bound — the caller must treat that as "unknown", not as a
        zero return.
        """
        sql = """
            SELECT k.symbol, k.timestamp, k.close
            FROM klines k
            WHERE k.exchange = ?
              AND k.market_type = ?
              AND k.interval = ?
              AND k.timestamp = (
                  SELECT MAX(m.timestamp) FROM klines m
                  WHERE m.exchange = k.exchange
                    AND m.market_type = k.market_type
                    AND m.symbol = k.symbol
                    AND m.interval = k.interval
                    AND m.timestamp < ?
              )
            ORDER BY k.symbol ASC
        """
        params = [exchange, market_type, interval, before_ms]
        with self._lock:
            frame = pd.read_sql_query(sql, self._ensure(), params=params)
        return frame

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
        """Every distinct (exchange, market_type, symbol, interval) in the store.

        Ordered so callers that slice the list (the sweeper's budget) get a
        stable sequence across rounds rather than whatever order SQLite
        happens to return.
        """
        with self._lock:
            cur = self._ensure().execute(
                "SELECT DISTINCT exchange, market_type, symbol, interval FROM klines "
                "ORDER BY exchange, market_type, symbol, interval"
            )
            return [(r[0], r[1], r[2], r[3]) for r in cur.fetchall()]

    def list_timestamps(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> list[int]:
        """Ordered label series for one partition."""
        with self._lock:
            cur = self._ensure().execute(
                "SELECT timestamp FROM klines WHERE exchange=? AND market_type=? "
                "AND symbol=? AND interval=? ORDER BY timestamp ASC",
                (exchange, market_type, symbol, interval),
            )
            return [int(r[0]) for r in cur.fetchall()]

    def find_gaps(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        step_ms: int,
        *,
        since_ts: int | None = None,
        is_hole: GapPredicate | None = None,
    ) -> list[tuple[int, int]]:
        """Interior holes: consecutive stamps the series should never jump across.

        Fast path: when ``count == (max-min)//step + 1`` the partition is
        contiguous and we return without touching every row. Only a mismatch
        pays for the pairwise scan. ``since_ts`` bounds the scan to a floor
        (``None`` scans the whole partition, so holes older than any rolling
        window are still caught).

        ``is_hole`` overrides the fixed-step rule for markets with closed
        sessions; see ``kline_calendar.gap_predicate``. The count fast path is
        skipped when a predicate is supplied: it assumes wall-clock
        contiguity, and a closed-session series can coincidentally have
        ``count == (max-min)//step + 1`` while still missing a bar.
        """
        if step_ms <= 0:
            raise ValueError("step_ms must be positive")
        custom_rule = is_hole is not None
        if is_hole is None:
            is_hole = partial(_fixed_step_hole, step_ms=step_ms)
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
            if expected == total and not custom_rule:
                return []
            cur = conn.execute(
                f"SELECT timestamp FROM klines WHERE {where} ORDER BY timestamp ASC",
                params,
            )
            stamps = [int(r[0]) for r in cur.fetchall()]
        return [
            (left, right)
            for left, right in zip(stamps, stamps[1:], strict=False)
            if is_hole(left, right)
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

    def get_tail_probe(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> tuple[int | None, int | None]:
        """``(cursor_ts, probe_at_ms)`` of the last empty tail probe, if any."""
        with self._lock:
            cur = self._ensure().execute(
                "SELECT tail_probe_cursor_ts, tail_probe_at_ms FROM kline_scan_state "
                "WHERE exchange=? AND market_type=? AND symbol=? AND interval=?",
                (exchange, market_type, symbol, interval),
            )
            row = cur.fetchone()
        if not row:
            return (None, None)
        cursor = int(row[0]) if row[0] is not None else None
        at_ms = int(row[1]) if row[1] is not None else None
        return (cursor, at_ms)

    def set_tail_probe(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        *,
        cursor_ts: int,
        now_ms: int,
    ) -> None:
        """Memo that a probe at ``cursor_ts`` found nothing newer."""
        with self._lock:
            conn = self._ensure()
            conn.execute(
                "INSERT INTO kline_scan_state "
                "(exchange, market_type, symbol, interval, tail_probe_cursor_ts, "
                "tail_probe_at_ms, updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(exchange, market_type, symbol, interval) "
                "DO UPDATE SET tail_probe_cursor_ts=excluded.tail_probe_cursor_ts, "
                "tail_probe_at_ms=excluded.tail_probe_at_ms",
                (exchange, market_type, symbol, interval, cursor_ts, now_ms, now_ms),
            )
            conn.commit()

    def clear_tail_probe(
        self, exchange: str, market_type: str, symbol: str, interval: str
    ) -> None:
        with self._lock:
            conn = self._ensure()
            conn.execute(
                "UPDATE kline_scan_state SET tail_probe_cursor_ts=NULL, "
                "tail_probe_at_ms=NULL WHERE exchange=? AND market_type=? "
                "AND symbol=? AND interval=?",
                (exchange, market_type, symbol, interval),
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

    def prune_filled_holes(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        step_ms: int,
    ) -> int:
        """Drop ledger rows whose gap no longer exists in the data.

        The ledger is cleared only on the sweeper's own fill path
        (``_sweep_partition``). Every other writer — the incremental sync, the
        backfill scripts, ``_seal_stale_open_bars`` — writes bars without
        touching it, so a hole they happened to fill keeps a ``permanent=0``
        row forever. That row is not cosmetic: ``_advance_tail`` skips any
        partition with an open hole, so the partition's tail stalls behind a
        gap that is already gone. Measured in production on the live store:
        BTC futures 5m sat 13 bars behind on three such rows whose ranges were
        already fully filled (36,128 expected == 36,128 present).

        A row counts as filled when every bar strictly inside
        ``(hole_left_ts, hole_right_ts)`` is present. Returns how many rows were
        removed.
        """
        if step_ms <= 0:
            raise ValueError("step_ms must be positive")
        with self._lock:
            conn = self._ensure()
            rows = conn.execute(
                "SELECT hole_left_ts, hole_right_ts FROM kline_hole_state "
                "WHERE exchange=? AND market_type=? AND symbol=? AND interval=? "
                "AND permanent=0",
                (exchange, market_type, symbol, interval),
            ).fetchall()
            filled = []
            for left, right in rows:
                expected = (int(right) - int(left)) // step_ms - 1
                if expected <= 0:
                    filled.append((left, right))
                    continue
                present = conn.execute(
                    "SELECT COUNT(*) FROM klines WHERE exchange=? AND market_type=? "
                    "AND symbol=? AND interval=? AND timestamp > ? AND timestamp < ?",
                    (exchange, market_type, symbol, interval, left, right),
                ).fetchone()[0]
                if int(present) >= expected:
                    filled.append((left, right))
            if not filled:
                return 0
            conn.executemany(
                "DELETE FROM kline_hole_state WHERE exchange=? AND market_type=? "
                "AND symbol=? AND interval=? AND hole_left_ts=? AND hole_right_ts=?",
                [
                    (exchange, market_type, symbol, interval, left, right)
                    for left, right in filled
                ],
            )
            conn.commit()
        return len(filled)

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

    def prune_stale_holes(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        live_holes: list[tuple[int, int]],
    ) -> int:
        """Drop ledger rows for jumps the current gap rule no longer sees.

        A hole ledger outlives the rule that wrote it: rows recorded while a
        non-24/7 partition was judged by the fixed-step rule (every weekend a
        hole) stay ``permanent`` forever and keep the health check degraded
        even after detection is fixed. Anything not in ``live_holes`` is no
        longer a hole by definition. Returns rows deleted.
        """
        live = set(live_holes)
        stale = [
            (h.hole_left_ts, h.hole_right_ts)
            for h in self.list_holes(
                exchange, market_type, symbol, interval, only_open=False
            )
            if (h.hole_left_ts, h.hole_right_ts) not in live
        ]
        if not stale:
            return 0
        with self._lock:
            conn = self._ensure()
            conn.executemany(
                "DELETE FROM kline_hole_state WHERE exchange=? AND market_type=? "
                "AND symbol=? AND interval=? AND hole_left_ts=? AND hole_right_ts=?",
                [
                    (exchange, market_type, symbol, interval, left, right)
                    for left, right in stale
                ],
            )
            conn.commit()
        return len(stale)

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
