"""SQLite persistence for grid execution engine snapshots.

Provides crash-safe save/load/delete of GridState snapshots so the
GridExecutionEngine can resume after a process restart.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from selene_space_paper.grid_types import GridState

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path("data") / "grid_state.db"


class GridStateRepository:
    """SQLite-backed persistence for grid state snapshots.

    One row per (exchange, market_id) — only the latest snapshot is kept.
    """

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path) if db_path else _DEFAULT_DB_PATH
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self._db_path))
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS grid_snapshots (
                    exchange  TEXT NOT NULL,
                    market_id INTEGER NOT NULL,
                    snapshot  TEXT NOT NULL,
                    saved_at  REAL NOT NULL,
                    PRIMARY KEY (exchange, market_id)
                )
                """
            )
            self._conn.commit()
        return self._conn

    def save(self, exchange: str, market_id: int, state: GridState) -> None:
        """Persist a grid state snapshot (upsert)."""
        conn = self._connect()
        snapshot_json = state.model_dump_json()
        import time
        conn.execute(
            """
            INSERT INTO grid_snapshots (exchange, market_id, snapshot, saved_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(exchange, market_id) DO UPDATE SET
                snapshot = excluded.snapshot,
                saved_at = excluded.saved_at
            """,
            (exchange, market_id, snapshot_json, time.time()),
        )
        conn.commit()

    def load(self, exchange: str, market_id: int) -> GridState | None:
        """Load the latest grid state snapshot, or None if not found."""
        conn = self._connect()
        row = conn.execute(
            "SELECT snapshot FROM grid_snapshots WHERE exchange = ? AND market_id = ?",
            (exchange, market_id),
        ).fetchone()
        if row is None:
            return None
        try:
            return GridState.model_validate_json(row[0])
        except Exception as exc:
            logger.warning(
                "Failed to parse grid snapshot for %s/%d: %s",
                exchange,
                market_id,
                exc,
            )
            return None

    def delete(self, exchange: str, market_id: int) -> bool:
        """Delete a grid state snapshot. Return True if it existed."""
        conn = self._connect()
        cursor = conn.execute(
            "DELETE FROM grid_snapshots WHERE exchange = ? AND market_id = ?",
            (exchange, market_id),
        )
        conn.commit()
        return cursor.rowcount > 0

    def list_snapshots(self) -> list[tuple[str, int]]:
        """Return all (exchange, market_id) pairs with saved snapshots."""
        conn = self._connect()
        rows = conn.execute(
            "SELECT exchange, market_id FROM grid_snapshots ORDER BY saved_at DESC"
        ).fetchall()
        return [(r[0], r[1]) for r in rows]

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
