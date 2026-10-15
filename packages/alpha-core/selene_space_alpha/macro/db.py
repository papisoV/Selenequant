"""SQLite-backed macro event + VIX/DXY persistence.

Follows the same pattern as SerenityScoreDB:
composite primary key, INSERT OR REPLACE upsert, context manager lifecycle,
and get_latest_timestamp for incremental sync.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS macro_events (
    event_type     TEXT    NOT NULL,
    date           TEXT    NOT NULL,
    release_time   TEXT    NOT NULL DEFAULT '',
    actual_value   REAL,
    forecast_value REAL,
    previous_value REAL,
    surprise       REAL,
    fred_series    TEXT    NOT NULL,
    timestamp_utc  INTEGER NOT NULL,
    PRIMARY KEY (event_type, date)
);

CREATE INDEX IF NOT EXISTS idx_macro_events_ts
ON macro_events (timestamp_utc);

CREATE TABLE IF NOT EXISTS vix_daily (
    date      TEXT    NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL    NOT NULL,
    timestamp INTEGER NOT NULL,
    PRIMARY KEY (date)
);

CREATE TABLE IF NOT EXISTS dxy_daily (
    date      TEXT    NOT NULL,
    close     REAL    NOT NULL,
    timestamp INTEGER NOT NULL,
    PRIMARY KEY (date)
);
"""

_INSERT_EVENT_SQL = """
INSERT OR REPLACE INTO macro_events (
    event_type, date, release_time, actual_value, forecast_value,
    previous_value, surprise, fred_series, timestamp_utc
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_VIX_SQL = """
INSERT OR REPLACE INTO vix_daily (date, open, high, low, close, timestamp)
VALUES (?, ?, ?, ?, ?, ?)
"""

_INSERT_DXY_SQL = """
INSERT OR REPLACE INTO dxy_daily (date, close, timestamp)
VALUES (?, ?, ?)
"""

_LATEST_TS_EVENTS_SQL = """
SELECT MAX(timestamp_utc) FROM macro_events
WHERE event_type = ?
"""

_LATEST_TS_VIX_SQL = """
SELECT MAX(timestamp) FROM vix_daily
"""

_LATEST_TS_DXY_SQL = """
SELECT MAX(timestamp) FROM dxy_daily
"""

_COUNT_SQL = """
SELECT COUNT(*) FROM {}
"""


# ---------------------------------------------------------------------------
# MacroEventDB
# ---------------------------------------------------------------------------


class MacroEventDB:
    """SQLite-backed macro event + VIX/DXY persistence."""

    def __init__(self, db_path: str | Path = "data/macro_events.db") -> None:
        self._db_path = Path(db_path)
        self._conn: sqlite3.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def open(self) -> None:
        if self._conn is not None:
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.executescript(_SCHEMA)
        self._conn.row_factory = sqlite3.Row
        logger.info("MacroEventDB opened: %s", self._db_path)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("MacroEventDB closed: %s", self._db_path)

    @contextmanager
    def connect(self) -> Iterator[None]:
        try:
            self.open()
            yield
        finally:
            self.close()

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def put_events(self, rows: list[tuple]) -> int:
        """Bulk upsert macro events. Tuple order matches _INSERT_EVENT_SQL."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_EVENT_SQL, rows)  # type: ignore[union-attr]
        return len(rows)

    def replace_events(self, rows: list[tuple], event_types: tuple[str, ...]) -> int:
        """Recreate the given event types from `rows` (delete stale, then insert).

        macro_events CPI/FOMC/NFP rows are a pure artifact of the sync scripts,
        so the table should equal the source's current output. INSERT OR REPLACE
        alone can never remove a stale row (e.g. a ghost date left by a fixed
        top-up bug), so this deletes the owned types first — re-running a sync
        after a code fix converges the DB to the corrected event set.
        """
        self.open()
        with self._conn:  # type: ignore[union-attr]
            placeholders = ",".join("?" for _ in event_types)
            self._conn.execute(  # type: ignore[union-attr]
                f"DELETE FROM macro_events WHERE event_type IN ({placeholders})",
                event_types,
            )
            self._conn.executemany(_INSERT_EVENT_SQL, rows)  # type: ignore[union-attr]
        return len(rows)

    def put_vix(self, rows: list[tuple]) -> int:
        """Bulk upsert VIX daily data. Tuple order matches _INSERT_VIX_SQL."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_VIX_SQL, rows)  # type: ignore[union-attr]
        return len(rows)

    def put_dxy(self, rows: list[tuple]) -> int:
        """Bulk upsert DXY daily data. Tuple order matches _INSERT_DXY_SQL."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_DXY_SQL, rows)  # type: ignore[union-attr]
        return len(rows)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_latest_timestamp(self, table: str, event_type: str | None = None) -> int | None:
        """Return the most recent timestamp for a table, or None if empty."""
        self.open()
        if table == "macro_events" and event_type:
            cursor = self._conn.execute(_LATEST_TS_EVENTS_SQL, (event_type,))  # type: ignore[union-attr]
        elif table == "vix_daily":
            cursor = self._conn.execute(_LATEST_TS_VIX_SQL)  # type: ignore[union-attr]
        elif table == "dxy_daily":
            cursor = self._conn.execute(_LATEST_TS_DXY_SQL)  # type: ignore[union-attr]
        else:
            return None
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None

    def get_events(
        self,
        event_type: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        """Retrieve macro events, optionally filtered by type and time range."""
        self.open()
        query = "SELECT * FROM macro_events WHERE 1=1"
        params: list = []
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)
        if start_ts is not None:
            query += " AND timestamp_utc >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND timestamp_utc <= ?"
            params.append(end_ts)
        query += " ORDER BY timestamp_utc ASC"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    def get_vix_daily(
        self,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        """Retrieve VIX daily data."""
        self.open()
        query = "SELECT * FROM vix_daily WHERE 1=1"
        params: list = []
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        query += " ORDER BY timestamp ASC"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    def get_dxy_daily(
        self,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        """Retrieve DXY daily data."""
        self.open()
        query = "SELECT * FROM dxy_daily WHERE 1=1"
        params: list = []
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        query += " ORDER BY timestamp ASC"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def count(self, table: str, event_type: str | None = None) -> int:
        """Count rows in a table."""
        self.open()
        if table == "macro_events" and event_type:
            cursor = self._conn.execute(  # type: ignore[union-attr]
                "SELECT COUNT(*) FROM macro_events WHERE event_type = ?",
                (event_type,),
            )
        else:
            cursor = self._conn.execute(_COUNT_SQL.format(table))  # type: ignore[union-attr]
        row = cursor.fetchone()
        return row[0] if row else 0

    def vacuum(self) -> None:
        """Reclaim disk space."""
        self.open()
        self._conn.execute("VACUUM")  # type: ignore[union-attr]
