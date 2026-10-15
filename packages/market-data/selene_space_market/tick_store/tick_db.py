"""Tick store — SQLite-based persistence for trade tick data.

Stores raw trade ticks from exchange WebSocket streams for later
replay and analysis. Used by TickReplay for tick-level backtesting.
"""

from __future__ import annotations

import sqlite3
from typing import Any


class TickStore:
    """SQLite store for trade ticks.

    Args:
        db_path: Path to the SQLite database file.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._db_path)
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _init_db(self) -> None:
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS ticks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    side TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_ticks_symbol_time
                ON ticks (symbol, timestamp_ms)
            """)

    def insert_trades(self, trades: list[dict[str, Any]]) -> int:
        """Insert a list of trade dicts. Returns number inserted."""
        if not trades:
            return 0

        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO ticks (exchange, symbol, price, quantity, side, timestamp_ms)
                VALUES (:exchange, :symbol, :price, :quantity, :side, :timestamp_ms)
                """,
                trades,
            )
        return len(trades)

    def query(
        self,
        symbol: str = "",
        start_ms: int = 0,
        end_ms: int = 9_999_999_999_999,
    ) -> list[dict[str, Any]]:
        """Query ticks, optionally filtered by symbol and time range."""
        where_parts = ["timestamp_ms >= ?", "timestamp_ms <= ?"]
        params: list[Any] = [start_ms, end_ms]
        if symbol:
            where_parts.append("symbol = ?")
            params.append(symbol)

        where_clause = " AND ".join(where_parts)

        with self._connect() as con:
            con.row_factory = sqlite3.Row
            cur = con.execute(
                f"""
                SELECT exchange, symbol, price, quantity, side, timestamp_ms
                FROM ticks
                WHERE {where_clause}
                ORDER BY timestamp_ms ASC
                """,
                params,
            )
            return [dict(row) for row in cur.fetchall()]

    def count(self, symbol: str = "") -> int:
        """Return total tick count, optionally for a specific symbol."""
        with self._connect() as con:
            if symbol:
                cur = con.execute(
                    "SELECT COUNT(*) FROM ticks WHERE symbol = ?",
                    (symbol,),
                )
            else:
                cur = con.execute("SELECT COUNT(*) FROM ticks")
            return cur.fetchone()[0]

    def close(self) -> None:
        """Explicitly release resources."""
        # Each _connect() call opens and closes via context manager.
        # No persistent connection to close — marker for symmetry.
        pass
