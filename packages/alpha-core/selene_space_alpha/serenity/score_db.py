"""SQLite-backed Serenity score persistence.

Follows the same pattern as kline_db.py and derivatives_db.py:
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

from .models import SerenityScore, SerenitySnapshot

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS serenity_scores (
    symbol                TEXT    NOT NULL,
    timestamp             INTEGER NOT NULL,
    consensus_scarcity    REAL    NOT NULL,
    liquidity_scarcity    REAL    NOT NULL,
    trust_scarcity        REAL    NOT NULL,
    replication_difficulty REAL   NOT NULL,
    certification_progress REAL   NOT NULL,
    prepayment_signal     REAL    NOT NULL,
    decentralization REAL   NOT NULL,
    evidence_strength     REAL    NOT NULL,
    penalty_fork_risk     REAL    NOT NULL,
    penalty_tvl_concentration REAL NOT NULL,
    penalty_dev_exodus    REAL    NOT NULL,
    penalty_revenue_decline REAL NOT NULL,
    penalty_oi_divergence REAL    NOT NULL,
    penalty_whale_exodus  REAL    NOT NULL,
    penalty_governance_risk REAL  NOT NULL,
    penalty_liquidity_trap REAL   NOT NULL,
    raw_score             REAL    NOT NULL,
    normalized_score      REAL    NOT NULL,
    tier                  TEXT    NOT NULL,
    funding_rate          REAL,
    oi_change             REAL,
    ls_ratio              REAL,
    price_change_7d       REAL,
    volume_change_7d      REAL,
    PRIMARY KEY (symbol, timestamp)
);

CREATE INDEX IF NOT EXISTS idx_serenity_scores_time
ON serenity_scores (symbol, timestamp);
"""

_INSERT_SQL = """
INSERT OR REPLACE INTO serenity_scores (
    symbol, timestamp,
    consensus_scarcity, liquidity_scarcity, trust_scarcity,
    replication_difficulty, certification_progress, prepayment_signal,
    decentralization, evidence_strength,
    penalty_fork_risk, penalty_tvl_concentration, penalty_dev_exodus,
    penalty_revenue_decline, penalty_oi_divergence, penalty_whale_exodus,
    penalty_governance_risk, penalty_liquidity_trap,
    raw_score, normalized_score, tier,
    funding_rate, oi_change, ls_ratio, price_change_7d, volume_change_7d
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_LATEST_TS_SQL = """
SELECT MAX(timestamp) FROM serenity_scores WHERE symbol = ?
"""

_LATEST_SCORE_SQL = """
SELECT * FROM serenity_scores WHERE symbol = ?
ORDER BY timestamp DESC LIMIT 1
"""

_HISTORY_SQL = """
SELECT * FROM serenity_scores WHERE symbol = ?
ORDER BY timestamp DESC
"""

_ALL_LATEST_SQL = """
SELECT s.* FROM serenity_scores s
INNER JOIN (
    SELECT symbol, MAX(timestamp) AS max_ts
    FROM serenity_scores
    GROUP BY symbol
) latest ON s.symbol = latest.symbol AND s.timestamp = latest.max_ts
ORDER BY s.normalized_score DESC
"""

_COUNT_SQL = """
SELECT COUNT(*) FROM serenity_scores
"""

_COUNT_SYMBOL_SQL = """
SELECT COUNT(*) FROM serenity_scores WHERE symbol = ?
"""

_DELETE_BEFORE_SQL = """
DELETE FROM serenity_scores WHERE timestamp < ?
"""


# ---------------------------------------------------------------------------
# Canonical DB path
# ---------------------------------------------------------------------------


def canonical_score_db_path() -> Path:
    """Canonical Serenity score DB, shared by the API and CLI (P2-4).

    Resolved from the repo layout (services/local-api/data) so both writers hit
    the same file regardless of CWD. main.py previously used a CWD-relative
    "data/serenity_scores.db" and serenity_scan.py used root/data — two
    different DBs. klines.db / derivatives.db set the convention.
    """
    repo_root = Path(__file__).resolve().parents[4]
    return repo_root / "services" / "local-api" / "data" / "serenity_scores.db"


# ---------------------------------------------------------------------------
# SerenityScoreDB
# ---------------------------------------------------------------------------


class SerenityScoreDB:
    """SQLite-backed Serenity score persistence."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        self._db_path = (
            canonical_score_db_path() if db_path is None else Path(db_path)
        )
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
        self._ensure_column(self._conn, "decentralization", "REAL NOT NULL DEFAULT 0.0")
        self._conn.row_factory = sqlite3.Row
        logger.info("SerenityScoreDB opened: %s", self._db_path)

    def _ensure_column(self, con: sqlite3.Connection, name: str, decl: str) -> None:
        """Idempotent migration (D1). Old DBs predate the marco rename and still
        carry supplier_concentration NOT NULL — a bare ADD COLUMN would leave
        INSERTs violating that constraint, so RENAME the old column (preserving
        data) when present, falling back to ADD for schema-newer gaps."""
        existing = {row[1] for row in con.execute("PRAGMA table_info(serenity_scores)")}
        if name in existing:
            return
        if name == "decentralization" and "supplier_concentration" in existing:
            con.execute("ALTER TABLE serenity_scores RENAME COLUMN supplier_concentration TO decentralization")
            return
        con.execute(f"ALTER TABLE serenity_scores ADD COLUMN {name} {decl}")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("SerenityScoreDB closed: %s", self._db_path)

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

    def put_score(
        self,
        score: SerenityScore,
        snapshot: SerenitySnapshot | None = None,
    ) -> None:
        """Upsert a single SerenityScore. Enrichment fields come from score itself."""
        self.open()
        row = (
            score.symbol,
            score.timestamp,
            score.consensus_scarcity,
            score.liquidity_scarcity,
            score.trust_scarcity,
            score.replication_difficulty,
            score.certification_progress,
            score.prepayment_signal,
            score.decentralization,
            score.evidence_strength,
            score.penalty_fork_risk,
            score.penalty_tvl_concentration,
            score.penalty_dev_exodus,
            score.penalty_revenue_decline,
            score.penalty_oi_divergence,
            score.penalty_whale_exodus,
            score.penalty_governance_risk,
            score.penalty_liquidity_trap,
            score.raw_score,
            score.normalized_score,
            score.tier,
            score.funding_rate,
            score.oi_change,
            score.ls_ratio,
            score.price_change_7d,
            score.volume_change_7d,
        )
        with self._conn:  # type: ignore[union-attr]
            self._conn.execute(_INSERT_SQL, row)  # type: ignore[union-attr]

    def put_scores(self, rows: list[tuple]) -> int:
        """Bulk upsert. Each tuple must match _INSERT_SQL column order."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            self._conn.executemany(_INSERT_SQL, rows)  # type: ignore[union-attr]
        return len(rows)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_latest_timestamp(self, symbol: str) -> int | None:
        """Return the most recent timestamp for a symbol, or None if empty."""
        self.open()
        cursor = self._conn.execute(_LATEST_TS_SQL, (symbol,))  # type: ignore[union-attr]
        row = cursor.fetchone()
        return row[0] if row and row[0] is not None else None

    def get_latest_score(self, symbol: str) -> dict | None:
        """Return the most recent score row as a dict, or None."""
        self.open()
        cursor = self._conn.execute(_LATEST_SCORE_SQL, (symbol,))  # type: ignore[union-attr]
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_history(
        self,
        symbol: str,
        limit: int = 100,
        start_ts: int | None = None,
        end_ts: int | None = None,
    ) -> pd.DataFrame:
        """Retrieve historical scores for a symbol."""
        self.open()
        query = _HISTORY_SQL
        params: list = [symbol]
        if start_ts is not None:
            query = query.replace("ORDER BY", "AND timestamp >= ? ORDER BY")
            params.append(start_ts)
        if end_ts is not None:
            query = query.replace("ORDER BY", "AND timestamp <= ? ORDER BY")
            params.append(end_ts)
        query += f" LIMIT {min(limit, 1000)}"
        return pd.read_sql_query(query, self._conn, params=params)  # type: ignore[union-attr]

    def get_all_latest(self) -> pd.DataFrame:
        """Get the latest score for every symbol."""
        self.open()
        return pd.read_sql_query(_ALL_LATEST_SQL, self._conn)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def count(self, symbol: str | None = None) -> int:
        """Count rows, optionally filtered by symbol."""
        self.open()
        if symbol:
            cursor = self._conn.execute(_COUNT_SYMBOL_SQL, (symbol,))  # type: ignore[union-attr]
        else:
            cursor = self._conn.execute(_COUNT_SQL)  # type: ignore[union-attr]
        row = cursor.fetchone()
        return row[0] if row else 0

    def delete_before(self, before_ts: int) -> int:
        """Delete all rows with timestamp older than before_ts."""
        self.open()
        with self._conn:  # type: ignore[union-attr]
            cursor = self._conn.execute(_DELETE_BEFORE_SQL, (before_ts,))  # type: ignore[union-attr]
        return cursor.rowcount

    def vacuum(self) -> None:
        """Reclaim disk space after deletions."""
        self.open()
        self._conn.execute("VACUUM")  # type: ignore[union-attr]
