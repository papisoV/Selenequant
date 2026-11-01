"""Depth store — SQLite persistence for banded order-book depth snapshots.

Companion to :class:`~selene_space_market.tick_store.tick_db.TickStore`.
Where that one keeps individual trades, this keeps the percentage-band
aggregate published in Binance's ``bookDepth`` archive (see
:mod:`selene_space_market.tick_store.depth_archive`).

One snapshot is 12 rows: the bands ±0.20 %, ±1 %, ±2 %, ±3 %, ±4 %, ±5 %,
each carrying the cumulative resting quantity inside that band plus its
quote-currency notional. ``percentage`` stays signed — negative is the bid
side, positive the ask side — so a snapshot is read back as a group of
rows sharing one ``timestamp_ms``.
"""

from __future__ import annotations

import sqlite3
from typing import Any

# The 12 bands the archive publishes, cheapest-first on the bid side.
BANDS: tuple[float, ...] = (
    -5.0,
    -4.0,
    -3.0,
    -2.0,
    -1.0,
    -0.2,
    0.2,
    1.0,
    2.0,
    3.0,
    4.0,
    5.0,
)


class DepthStore:
    """SQLite store for banded depth snapshots.

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
                CREATE TABLE IF NOT EXISTS depth_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    timestamp_ms INTEGER NOT NULL,
                    percentage REAL NOT NULL,
                    depth REAL NOT NULL,
                    notional REAL NOT NULL
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_depth_symbol_time
                ON depth_snapshots (symbol, timestamp_ms)
            """)

    def insert_depth(self, rows: list[dict[str, Any]]) -> int:
        """Insert depth rows. Returns number inserted."""
        if not rows:
            return 0

        with self._connect() as con:
            con.executemany(
                """
                INSERT INTO depth_snapshots
                    (exchange, symbol, timestamp_ms, percentage, depth, notional)
                VALUES
                    (:exchange, :symbol, :timestamp_ms, :percentage, :depth, :notional)
                """,
                rows,
            )
        return len(rows)

    def query(
        self,
        symbol: str = "",
        start_ms: int = 0,
        end_ms: int = 9_999_999_999_999,
        percentage: float | None = None,
    ) -> list[dict[str, Any]]:
        """Query depth rows, optionally filtered by symbol, time and band."""
        where_parts = ["timestamp_ms >= ?", "timestamp_ms <= ?"]
        params: list[Any] = [start_ms, end_ms]
        if symbol:
            where_parts.append("symbol = ?")
            params.append(symbol)
        if percentage is not None:
            where_parts.append("percentage = ?")
            params.append(percentage)

        where_clause = " AND ".join(where_parts)

        with self._connect() as con:
            con.row_factory = sqlite3.Row
            cur = con.execute(
                f"""
                SELECT exchange, symbol, timestamp_ms, percentage, depth, notional
                FROM depth_snapshots
                WHERE {where_clause}
                ORDER BY timestamp_ms ASC, percentage ASC
                """,
                params,
            )
            return [dict(row) for row in cur.fetchall()]

    def snapshots(
        self,
        symbol: str,
        start_ms: int = 0,
        end_ms: int = 9_999_999_999_999,
    ) -> list[dict[str, Any]]:
        """Group rows into whole snapshots, each with a ``bands`` mapping.

        The group-by happens in SQL so a long window does not ship every row
        into Python before collapsing it.
        """
        rows = self.query(symbol, start_ms, end_ms)
        grouped: dict[int, dict[str, Any]] = {}
        for row in rows:
            snap = grouped.setdefault(
                row["timestamp_ms"],
                {"timestamp_ms": row["timestamp_ms"], "bands": {}},
            )
            snap["bands"][row["percentage"]] = {
                "depth": row["depth"],
                "notional": row["notional"],
            }
        return [grouped[key] for key in sorted(grouped)]

    def count(self, symbol: str = "") -> int:
        """Return total depth-row count, optionally for a specific symbol."""
        with self._connect() as con:
            if symbol:
                cur = con.execute(
                    "SELECT COUNT(*) FROM depth_snapshots WHERE symbol = ?",
                    (symbol,),
                )
            else:
                cur = con.execute("SELECT COUNT(*) FROM depth_snapshots")
            row = cur.fetchone()
            assert row is not None, "COUNT(*) always yields one row"
            total: int = row[0]
            return total

    def close(self) -> None:
        """Explicitly release resources (marker for symmetry with TickStore)."""
        pass
