"""FeeLedger — persistent record of fee-rate snapshots for VIP-tier monitoring (#12)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class FeeSnapshot:
    """Point-in-time fee-rate state for one account (duck-typed across packages)."""

    ts: int
    account_id: str
    exchange: str
    taker_rate: float
    maker_rate: float
    effective_rate: float
    implied_tier: str
    source: str
    created_at: int


class FeeLedger:
    """Persistent ledger of fee snapshots at ~/.selenespace/risk/fee.db.

    Each snapshot is one observed effective fee rate. ``record`` is idempotent
    on (ts, account_id, exchange) — replay / duplicate polls are no-ops.
    """

    def __init__(self, db_path: Any | None = None) -> None:
        if db_path is None:
            db_path = Path.home() / ".selenespace" / "risk" / "fee.db"
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(str(self._db_path))
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=NORMAL")
        return con

    def _init_schema(self) -> None:
        with self._connect() as con:
            con.execute("""
                CREATE TABLE IF NOT EXISTS fee_snapshots (
                    ts INTEGER NOT NULL,
                    account_id TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    taker_rate REAL NOT NULL,
                    maker_rate REAL NOT NULL,
                    effective_rate REAL NOT NULL,
                    implied_tier TEXT NOT NULL,
                    source TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    PRIMARY KEY (ts, account_id, exchange)
                )
            """)
            con.execute("""
                CREATE INDEX IF NOT EXISTS idx_fee_account_ts
                ON fee_snapshots (account_id, ts)
            """)

    def record(self, snapshot: FeeSnapshot) -> None:
        """Idempotent insert of a fee snapshot (duplicate key → no-op)."""
        with self._connect() as con:
            con.execute(
                """
                INSERT OR IGNORE INTO fee_snapshots
                    (ts, account_id, exchange, taker_rate, maker_rate,
                     effective_rate, implied_tier, source, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot.ts,
                    snapshot.account_id,
                    snapshot.exchange,
                    float(snapshot.taker_rate),
                    float(snapshot.maker_rate),
                    float(snapshot.effective_rate),
                    snapshot.implied_tier,
                    snapshot.source,
                    int(snapshot.created_at),
                ),
            )

    def latest(self, account_id: str) -> FeeSnapshot | None:
        """Most recent snapshot for an account (None when empty)."""
        with self._connect() as con:
            cur = con.execute(
                """
                SELECT ts, account_id, exchange, taker_rate, maker_rate,
                       effective_rate, implied_tier, source, created_at
                FROM fee_snapshots
                WHERE account_id = ?
                ORDER BY ts DESC
                LIMIT 1
                """,
                (account_id,),
            )
            row = cur.fetchone()
        return self._row_to_snapshot(row) if row else None

    def query_range(
        self, account_id: str, start_ts: int, end_ts: int
    ) -> list[FeeSnapshot]:
        """Snapshots in [start_ts, end_ts) for an account, ordered by ts ASC."""
        with self._connect() as con:
            cur = con.execute(
                """
                SELECT ts, account_id, exchange, taker_rate, maker_rate,
                       effective_rate, implied_tier, source, created_at
                FROM fee_snapshots
                WHERE account_id = ? AND ts >= ? AND ts < ?
                ORDER BY ts ASC
                """,
                (account_id, start_ts, end_ts),
            )
            rows = cur.fetchall()
        return [self._row_to_snapshot(r) for r in rows]

    @staticmethod
    def _row_to_snapshot(row: tuple) -> FeeSnapshot:
        return FeeSnapshot(
            ts=row[0],
            account_id=row[1],
            exchange=row[2],
            taker_rate=row[3],
            maker_rate=row[4],
            effective_rate=row[5],
            implied_tier=row[6],
            source=row[7],
            created_at=row[8],
        )
