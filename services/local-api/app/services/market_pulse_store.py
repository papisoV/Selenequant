"""Hourly market-pulse snapshot store (SQLite) — real snapshots only.

The intelligence market-pulse endpoint appends one snapshot per UTC hour
(same hour overwritten via INSERT OR REPLACE). History is only ever
accumulated from complete real fetches — never backfilled or synthesized.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "market_pulse.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS market_pulse_hourly (
    hour_ts INTEGER PRIMARY KEY,
    payload TEXT NOT NULL
)
"""


class MarketPulseStore:
    """Accumulates complete real market-pulse snapshots, one row per UTC hour."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def save_snapshot(self, payload: dict[str, Any]) -> None:
        now = int(time.time())
        hour_ts = now - (now % 3600)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                "INSERT OR REPLACE INTO market_pulse_hourly (hour_ts, payload) VALUES (?, ?)",
                (hour_ts, json.dumps(payload)),
            )
            conn.commit()
        finally:
            conn.close()

    def load_recent(self, hours: int = 24) -> list[dict[str, Any]]:
        cutoff = int(time.time()) - hours * 3600
        conn = sqlite3.connect(self._db_path)
        try:
            rows = conn.execute(
                "SELECT hour_ts, payload FROM market_pulse_hourly WHERE hour_ts >= ?"
                " ORDER BY hour_ts ASC",
                (cutoff,),
            ).fetchall()
        finally:
            conn.close()
        out: list[dict[str, Any]] = []
        for hour_ts, raw in rows:
            try:
                entry = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning(
                    "market_pulse_hourly row %d has corrupt payload, skipped", hour_ts
                )
                continue
            entry["timestamp"] = hour_ts
            out.append(entry)
        return out

    def is_empty(self) -> bool:
        conn = sqlite3.connect(self._db_path)
        try:
            row = conn.execute("SELECT 1 FROM market_pulse_hourly LIMIT 1").fetchone()
        finally:
            conn.close()
        return row is None
