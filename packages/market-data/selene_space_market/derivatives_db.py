"""SQLite-backed derivatives data storage (funding rates, open interest, taker ratios).

Follows the same pattern as kline_db.py: composite primary key, INSERT OR REPLACE
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
CREATE TABLE IF NOT EXISTS funding_rates (
    exchange          TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    timestamp         INTEGER NOT NULL,
    funding_rate      REAL    NOT NULL,
    mark_price        REAL,
    index_price       REAL,
    next_funding_time INTEGER,
    PRIMARY KEY (exchange, symbol, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_funding_rates_time
ON funding_rates (exchange, symbol, timestamp);

CREATE TABLE IF NOT EXISTS open_interest (
    exchange          TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    interval          TEXT    NOT NULL,
    timestamp         INTEGER NOT NULL,
    open_interest     REAL    NOT NULL,
    open_interest_usd REAL,
    PRIMARY KEY (exchange, symbol, interval, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_open_interest_time
ON open_interest (exchange, symbol, interval, timestamp);

CREATE TABLE IF NOT EXISTS taker_ratio (
    exchange          TEXT    NOT NULL,
    symbol            TEXT    NOT NULL,
    interval          TEXT    NOT NULL,
    timestamp         INTEGER NOT NULL,
    buy_vol           REAL    NOT NULL,
    sell_vol          REAL    NOT NULL,
    buy_sell_ratio    REAL    NOT NULL,
    PRIMARY KEY (exchange, symbol, interval, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_taker_ratio_time
ON taker_ratio (exchange, symbol, interval, timestamp);
"""

_INSERT_FUNDING = """
INSERT OR REPLACE INTO funding_rates
    (exchange, symbol, timestamp, funding_rate, mark_price, index_price, next_funding_time)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_INSERT_OI = """
INSERT OR REPLACE INTO open_interest
    (exchange, symbol, interval, timestamp, open_interest, open_interest_usd)
VALUES (?, ?, ?, ?, ?, ?)
"""

_INSERT_TAKER = """
INSERT OR REPLACE INTO taker_ratio
    (exchange, symbol, interval, timestamp, buy_vol, sell_vol, buy_sell_ratio)
VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_LATEST_TS_SQL = {
    "funding_rates": "SELECT MAX(timestamp) FROM funding_rates WHERE exchange = ? AND symbol = ?",
    "open_interest": "SELECT MAX(timestamp) FROM open_interest WHERE exchange = ? AND symbol = ? AND interval = ?",
    "taker_ratio": "SELECT MAX(timestamp) FROM taker_ratio WHERE exchange = ? AND symbol = ? AND interval = ?",
}


# ---------------------------------------------------------------------------
# DerivativesDB
# ---------------------------------------------------------------------------


class DerivativesDB:
    """SQLite-backed derivatives data storage."""

    def __init__(self, db_path: str | Path = "data/derivatives.db") -> None:
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
        logger.info("DerivativesDB opened: %s", self._db_path)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("DerivativesDB closed: %s", self._db_path)

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

    def put_funding_rates(self, rows: list[tuple]) -> int:
        """Bulk upsert funding rates. Each tuple: (exchange, symbol, timestamp, funding_rate, mark_price, index_price, next_funding_time)."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_FUNDING, rows)  # type: ignore[union-attr]
        return len(rows)

    def put_open_interest(self, rows: list[tuple]) -> int:
        """Bulk upsert open interest. Each tuple: (exchange, symbol, interval, timestamp, open_interest, open_interest_usd)."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_OI, rows)  # type: ignore[union-attr]
        return len(rows)

    def put_taker_ratio(self, rows: list[tuple]) -> int:
        """Bulk upsert taker ratios. Each tuple: (exchange, symbol, interval, timestamp, buy_vol, sell_vol, buy_sell_ratio)."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_TAKER, rows)  # type: ignore[union-attr]
        return len(rows)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_latest_timestamp(
        self,
        table: str,
        exchange: str,
        symbol: str,
        interval: str | None = None,
    ) -> int | None:
        """Return the most recent timestamp for a partition, or None if empty."""
        self.open()
        sql = _LATEST_TS_SQL.get(table)
        if sql is None:
            raise ValueError(f"Unknown table: {table}")
        if interval is not None:
            params: tuple = (exchange, symbol, interval)
        else:
            params = (exchange, symbol)
        cursor = self._conn.execute(sql, params)  # type: ignore[union-attr]
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None

    def get_funding_rates(
        self,
        exchange: str,
        symbol: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        """Retrieve funding rates within an optional time range."""
        self.open()
        query = "SELECT * FROM funding_rates WHERE exchange = ? AND symbol = ?"
        params: list = [exchange, symbol]
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        query += " ORDER BY timestamp ASC"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    def get_open_interest(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        self.open()
        query = "SELECT * FROM open_interest WHERE exchange = ? AND symbol = ? AND interval = ?"
        params: list = [exchange, symbol, interval]
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        query += " ORDER BY timestamp ASC"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    def get_taker_ratio(
        self,
        exchange: str,
        symbol: str,
        interval: str,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        self.open()
        query = "SELECT * FROM taker_ratio WHERE exchange = ? AND symbol = ? AND interval = ?"
        params: list = [exchange, symbol, interval]
        if start_ts is not None:
            query += " AND timestamp >= ?"
            params.append(start_ts)
        if end_ts is not None:
            query += " AND timestamp <= ?"
            params.append(end_ts)
        query += " ORDER BY timestamp ASC"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    def count(self, table: str, exchange: str, symbol: str, interval: str | None = None) -> int:
        self.open()
        if interval is not None:
            sql = f"SELECT COUNT(*) FROM {table} WHERE exchange = ? AND symbol = ? AND interval = ?"
            params: tuple = (exchange, symbol, interval)
        else:
            sql = f"SELECT COUNT(*) FROM {table} WHERE exchange = ? AND symbol = ?"
            params = (exchange, symbol)
        cursor = self._conn.execute(sql, params)  # type: ignore[union-attr]
        return cursor.fetchone()[0]  # type: ignore[index]
