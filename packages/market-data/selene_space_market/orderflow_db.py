"""SQLite-backed orderflow data storage (top position/account ratio, global LS ratio).

Follows the same pattern as derivatives_db.py: composite primary key, INSERT OR REPLACE
upsert, context manager lifecycle, and get_latest_timestamp for incremental sync.
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
CREATE TABLE IF NOT EXISTS top_position_ratio (
    exchange          TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    interval          TEXT    NOT NULL,
    timestamp         INTEGER NOT NULL,
    long_ratio        REAL    NOT NULL,
    short_ratio       REAL    NOT NULL,
    long_short_ratio  REAL    NOT NULL,
    PRIMARY KEY (exchange, symbol, interval, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_top_pos_ratio_time
ON top_position_ratio (exchange, symbol, interval, timestamp);

CREATE TABLE IF NOT EXISTS top_account_ratio (
    exchange          TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    interval          TEXT    NOT NULL,
    timestamp         INTEGER NOT NULL,
    long_ratio        REAL    NOT NULL,
    short_ratio       REAL    NOT NULL,
    long_short_ratio  REAL    NOT NULL,
    PRIMARY KEY (exchange, symbol, interval, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_top_acc_ratio_time
ON top_account_ratio (exchange, symbol, interval, timestamp);

CREATE TABLE IF NOT EXISTS global_ls_ratio (
    exchange          TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    interval          TEXT    NOT NULL,
    timestamp         INTEGER NOT NULL,
    long_short_ratio  REAL    NOT NULL,
    long_account      REAL,
    short_account     REAL,
    PRIMARY KEY (exchange, symbol, interval, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_global_ls_ratio_time
ON global_ls_ratio (exchange, symbol, interval, timestamp);

CREATE TABLE IF NOT EXISTS basis (
    exchange          TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    interval          TEXT    NOT NULL,
    timestamp         INTEGER NOT NULL,
    basis             REAL    NOT NULL,
    basis_rate        REAL    NOT NULL,
    index_price       REAL    DEFAULT 0,
    futures_price     REAL    DEFAULT 0,
    PRIMARY KEY (exchange, symbol, interval, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_basis_time
ON basis (exchange, symbol, interval, timestamp);
"""

_INSERT_TOP_POS = """
INSERT OR REPLACE INTO top_position_ratio
    (exchange, symbol, interval, timestamp, long_ratio, short_ratio, long_short_ratio)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_TOP_ACC = """
INSERT OR REPLACE INTO top_account_ratio
    (exchange, symbol, interval, timestamp, long_ratio, short_ratio, long_short_ratio)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_GLOBAL_LS = """
INSERT OR REPLACE INTO global_ls_ratio
    (exchange, symbol, interval, timestamp, long_short_ratio, long_account, short_account)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_BASIS = """
INSERT OR REPLACE INTO basis
    (exchange, symbol, interval, timestamp, basis, basis_rate, index_price, futures_price)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
"""

_LATEST_TS_SQL = {
    "top_position_ratio": "SELECT MAX(timestamp) FROM top_position_ratio WHERE exchange = ? AND symbol = ? AND interval = ?",
    "top_account_ratio": "SELECT MAX(timestamp) FROM top_account_ratio WHERE exchange = ? AND symbol = ? AND interval = ?",
    "global_ls_ratio": "SELECT MAX(timestamp) FROM global_ls_ratio WHERE exchange = ? AND symbol = ? AND interval = ?",
    "basis": "SELECT MAX(timestamp) FROM basis WHERE exchange = ? AND symbol = ? AND interval = ?",
}


# ---------------------------------------------------------------------------
# OrderflowDB
# ---------------------------------------------------------------------------


class OrderflowDB:
    """SQLite-backed orderflow data storage."""

    def __init__(self, db_path: str | Path = "data/orderflow.db") -> None:
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
        logger.info("OrderflowDB opened: %s", self._db_path)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("OrderflowDB closed: %s", self._db_path)

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

    def put_top_position_ratio(self, rows: list[tuple]) -> int:
        """Bulk upsert top position ratios. Each tuple: (exchange, symbol, interval, timestamp, long_ratio, short_ratio, long_short_ratio)."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_TOP_POS, rows)  # type: ignore[union-attr]
        return len(rows)

    def put_top_account_ratio(self, rows: list[tuple]) -> int:
        """Bulk upsert top account ratios. Each tuple: (exchange, symbol, interval, timestamp, long_ratio, short_ratio, long_short_ratio)."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_TOP_ACC, rows)  # type: ignore[union-attr]
        return len(rows)

    def put_global_ls_ratio(self, rows: list[tuple]) -> int:
        """Bulk upsert global LS ratios. Each tuple: (exchange, symbol, interval, timestamp, long_short_ratio, long_account, short_account)."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_GLOBAL_LS, rows)  # type: ignore[union-attr]
        return len(rows)

    def put_basis(self, rows: list[tuple]) -> int:
        """Bulk upsert basis data. Each tuple: (exchange, symbol, interval, timestamp, basis, basis_rate, index_price, futures_price)."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_BASIS, rows)  # type: ignore[union-attr]
        return len(rows)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_latest_timestamp(
        self,
        table: str,
        exchange: str,
        symbol: str,
        interval: str,
    ) -> int | None:
        """Return the most recent timestamp for a partition, or None if empty."""
        self.open()
        sql = _LATEST_TS_SQL.get(table)
        if sql is None:
            raise ValueError(f"Unknown table: {table}")
        params: tuple = (exchange, symbol, interval)
        cursor = self._conn.execute(sql, params)  # type: ignore[union-attr]
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None

    def get_top_position_ratio(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        self.open()
        query = "SELECT * FROM top_position_ratio WHERE exchange = ? AND symbol = ? AND interval = ?"
        params: list = [exchange, symbol, interval]
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        query += " ORDER BY timestamp ASC"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    def get_top_account_ratio(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        self.open()
        query = "SELECT * FROM top_account_ratio WHERE exchange = ? AND symbol = ? AND interval = ?"
        params: list = [exchange, symbol, interval]
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        query += " ORDER BY timestamp ASC"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    def get_global_ls_ratio(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        self.open()
        query = "SELECT * FROM global_ls_ratio WHERE exchange = ? AND symbol = ? AND interval = ?"
        params: list = [exchange, symbol, interval]
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        query += " ORDER BY timestamp ASC"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    def get_basis(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        self.open()
        query = "SELECT * FROM basis WHERE exchange = ? AND symbol = ? AND interval = ?"
        params: list = [exchange, symbol, interval]
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        query += " ORDER BY timestamp ASC"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    def count(self, table: str, exchange: str, symbol: str, interval: str) -> int:
        self.open()
        sql = f"SELECT COUNT(*) FROM {table} WHERE exchange = ? AND symbol = ? AND interval = ?"
        params: tuple = (exchange, symbol, interval)
        cursor = self._conn.execute(sql, params)  # type: ignore[union-attr]
        return cursor.fetchone()[0]  # type: ignore[index]
