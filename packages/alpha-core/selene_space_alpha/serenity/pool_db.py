"""SQLite-backed Serenity pool-scan persistence.

Mirrors the score_db self-managed-schema pattern (CREATE TABLE IF NOT EXISTS on
open, sync sqlite3, INSERT OR REPLACE). The pool ranking structure
(layer_summary / ranked / top_picks) lives here so pool state survives restarts;
per-candidate scores keep flowing through SerenityScoreDB.put_score.

CRITICAL: this must NOT go through the storage migrations_pkg framework — that
MigrationRunner (aiosqlite) targets the storage/market-analysis DB, not
serenity_scores.db. The schema is versioned via schema_version (default 1);
decode is tolerant so older rows (missing newer columns) still load.
"""

from __future__ import annotations

import dataclasses
import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .pool import PoolCandidate, PoolLayerSummary, SerenityPoolScan
from .score_db import canonical_score_db_path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS serenity_pool_scans (
    theme                TEXT    NOT NULL,
    theme_name           TEXT    NOT NULL,
    scanned_at           INTEGER NOT NULL,
    universe_size        INTEGER,
    weak_evidence_count  INTEGER,
    layer_summary        TEXT,
    top_picks            TEXT,
    ranked               TEXT,
    schema_version       INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (theme, scanned_at)
);
"""

_INSERT_SQL = """
INSERT OR REPLACE INTO serenity_pool_scans (
    theme, theme_name, scanned_at, universe_size, weak_evidence_count,
    layer_summary, top_picks, ranked, schema_version
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""

_LATEST_SQL = """
SELECT * FROM serenity_pool_scans
WHERE theme = ?
ORDER BY scanned_at DESC LIMIT 1
"""

_ALL_LATEST_SQL = """
SELECT s.* FROM serenity_pool_scans s
INNER JOIN (
    SELECT theme, MAX(scanned_at) AS max_ts
    FROM serenity_pool_scans
    GROUP BY theme
) latest ON s.theme = latest.theme AND s.scanned_at = latest.max_ts
ORDER BY s.theme
"""

_COUNT_SQL = """
SELECT COUNT(*) FROM serenity_pool_scans
"""


# ---------------------------------------------------------------------------
# (De)serialization
# ---------------------------------------------------------------------------


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_loads_list(raw: Any) -> list[Any]:
    """Parse a stored JSON array column, tolerating None / bad payloads."""
    if isinstance(raw, list):
        return raw
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return value if isinstance(value, list) else []
    except (TypeError, ValueError):
        return []


def _layer_summary_from_dict(item: dict) -> PoolLayerSummary:
    return PoolLayerSummary(
        layer=str(item.get("layer") or ""),
        count=int(item.get("count") or 0),
        top_scarce_symbols=tuple(str(s) for s in item.get("top_scarce_symbols") or ()),
    )


def _candidate_from_dict(item: dict) -> PoolCandidate:
    return PoolCandidate(
        symbol=str(item.get("symbol") or ""),
        name=str(item.get("name") or ""),
        category=str(item.get("category") or ""),
        chains=tuple(str(c) for c in item.get("chains") or ()),
        normalized_score=float(item.get("normalized_score") or 0.0),
        tier=str(item.get("tier") or ""),
        scarce_layer_score=float(item.get("scarce_layer_score") or 0.0),
        evidence_strength=float(item.get("evidence_strength") or 0.0),
        evidence_label=str(item.get("evidence_label") or ""),
        rationale=str(item.get("rationale") or ""),
        top_penalty=str(item.get("top_penalty") or ""),
        disproof=tuple(str(x) for x in item.get("disproof") or ()),
    )


def _scan_from_row(row: sqlite3.Row | dict) -> SerenityPoolScan:
    """Rebuild a SerenityPoolScan from a row, tolerating missing columns."""
    d = dict(row)
    layer_summary = tuple(
        _layer_summary_from_dict(item)
        for item in _json_loads_list(d.get("layer_summary"))
        if isinstance(item, dict)
    )
    ranked = tuple(
        _candidate_from_dict(item)
        for item in _json_loads_list(d.get("ranked"))
        if isinstance(item, dict)
    )
    theme = str(d.get("theme") or "")
    return SerenityPoolScan(
        theme=theme,
        theme_name=str(d.get("theme_name") or theme or ""),
        scanned_at=int(d.get("scanned_at") or 0),
        universe_size=int(d.get("universe_size") or 0),
        layer_summary=layer_summary,
        ranked=ranked,
        top_picks=tuple(str(s) for s in _json_loads_list(d.get("top_picks"))),
        weak_evidence_count=int(d.get("weak_evidence_count") or 0),
    )


# ---------------------------------------------------------------------------
# SerenityPoolDB
# ---------------------------------------------------------------------------


class SerenityPoolDB:
    """SQLite-backed Serenity pool-scan persistence (one row per scan)."""

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
        self._conn.row_factory = sqlite3.Row
        logger.info("SerenityPoolDB opened: %s", self._db_path)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            logger.info("SerenityPoolDB closed: %s", self._db_path)

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

    def put_scan(self, scan: SerenityPoolScan) -> None:
        """Upsert one pool scan (PK = theme + scanned_at)."""
        self.open()
        data = dataclasses.asdict(scan)
        row = (
            scan.theme,
            scan.theme_name,
            scan.scanned_at,
            scan.universe_size,
            scan.weak_evidence_count,
            _json_dumps(data["layer_summary"]) if scan.layer_summary else None,
            _json_dumps(list(scan.top_picks)) if scan.top_picks else None,
            _json_dumps(data["ranked"]) if scan.ranked else None,
            1,
        )
        with self._conn:  # type: ignore[union-attr]
            self._conn.execute(_INSERT_SQL, row)  # type: ignore[union-attr]

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_latest(self, theme: str) -> SerenityPoolScan | None:
        """Return the most recent scan for a theme, or None if never scanned."""
        self.open()
        cursor = self._conn.execute(_LATEST_SQL, (theme,))  # type: ignore[union-attr]
        row = cursor.fetchone()
        return _scan_from_row(row) if row else None

    def get_all_latest(self) -> list[SerenityPoolScan]:
        """Return the most recent scan for every theme, newest per theme."""
        self.open()
        cursor = self._conn.execute(_ALL_LATEST_SQL)  # type: ignore[union-attr]
        return [_scan_from_row(row) for row in cursor.fetchall()]

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def count(self) -> int:
        """Count persisted scan rows."""
        self.open()
        cursor = self._conn.execute(_COUNT_SQL)  # type: ignore[union-attr]
        row = cursor.fetchone()
        return row[0] if row else 0
