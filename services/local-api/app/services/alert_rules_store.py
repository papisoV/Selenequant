"""Alert-rule store (SQLite) — rules the background evaluator reads.

Rules used to live only in the browser's ``localStorage`` (``alerts.ts``), which
meant a rule existed only while the page was open. Monitoring has to outlive the
tab, so the rules move here and the evaluator loop reads them from this store.

Shape follows ``market_pulse_store``: a short-lived connection per operation, no
shared handle to get into a bad state. The whole rule is kept as JSON because
its threshold shape differs per ``type`` — columns for each variable field would
be mostly NULL and would have to be migrated whenever a type gains a field.

``enabled`` is mirrored into its own column so the evaluator's query is an index
read rather than a JSON scan. It is written from the same payload in one
statement, so the two cannot drift.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "alert_rules.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_rules (
    id TEXT PRIMARY KEY,
    payload TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
)
"""

# A key/value side table rather than a column: it describes the whole rule set,
# not any one rule.
_META_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
"""

# ``seeded`` distinguishes "never migrated" from "user deleted every rule". The
# frontend pushes its localStorage rules once and then treats the backend as the
# source of truth; without this flag an empty backend after a delete-all would
# look identical to a fresh install and the rules would be re-uploaded.
_SEEDED_KEY = "seeded"


def new_rule_id() -> str:
    """Server-generated rule id (the client's ids are not unique across devices)."""
    return str(uuid.uuid4())


class AlertRulesStore:
    """Persistent store for user-created alert rules."""

    def __init__(self, db_path: str | Path = _DEFAULT_DB_PATH) -> None:
        # Each operation opens its own connection (so no handle can be left in a
        # bad state), and an in-memory SQLite database lives and dies with its
        # connection. Accepting ":memory:" would therefore create the schema on
        # one connection and read from an empty database on the next — every
        # read returns nothing, silently. Reject it instead of pretending.
        if str(db_path) == ":memory:":
            raise ValueError(
                "AlertRulesStore opens a connection per operation and cannot back "
                "':memory:' — pass a file path (tests: use tmp_path)"
            )
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(_SCHEMA)
            conn.execute(_META_SCHEMA)
            conn.commit()
        finally:
            conn.close()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self._db_path)

    # -- writes ------------------------------------------------------------

    def upsert(self, rule: dict[str, Any]) -> dict[str, Any]:
        """Insert or replace one rule; returns the stored form.

        The caller owns the payload shape but not ``id``/``updated_at``: an
        existing id keeps its identity, a missing one is generated here so the
        API and the seed path agree on what an id is.
        """
        stored = dict(rule)
        rule_id = str(stored.get("id") or new_rule_id())
        stored["id"] = rule_id
        now = int(time.time())
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO alert_rules (id, payload, enabled, updated_at)"
                " VALUES (?, ?, ?, ?)",
                (
                    rule_id,
                    json.dumps(stored),
                    1 if stored.get("enabled", True) else 0,
                    now,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        return stored

    def patch(self, rule_id: str, fields: dict[str, Any]) -> dict[str, Any] | None:
        """Merge *fields* into an existing rule; ``None`` when it does not exist."""
        current = self.get(rule_id)
        if current is None:
            return None
        merged = {**current, **fields, "id": rule_id}
        return self.upsert(merged)

    def delete(self, rule_id: str) -> bool:
        conn = self._connect()
        try:
            cur = conn.execute("DELETE FROM alert_rules WHERE id = ?", (rule_id,))
            conn.commit()
            return cur.rowcount > 0
        finally:
            conn.close()

    def mark_seeded(self) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO alert_meta (key, value) VALUES (?, ?)",
                (_SEEDED_KEY, "1"),
            )
            conn.commit()
        finally:
            conn.close()

    # -- reads -------------------------------------------------------------

    def is_seeded(self) -> bool:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT value FROM alert_meta WHERE key = ?", (_SEEDED_KEY,)
            ).fetchone()
        finally:
            conn.close()
        return bool(row and row[0] == "1")

    def get(self, rule_id: str) -> dict[str, Any] | None:
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT id, payload FROM alert_rules WHERE id = ?", (rule_id,)
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return None
        return _decode(row[0], row[1])

    def list_all(self) -> list[dict[str, Any]]:
        return self._select("SELECT id, payload FROM alert_rules ORDER BY rowid ASC")

    def list_enabled(self) -> list[dict[str, Any]]:
        return self._select(
            "SELECT id, payload FROM alert_rules WHERE enabled = 1 ORDER BY rowid ASC"
        )

    def _select(self, sql: str) -> list[dict[str, Any]]:
        conn = self._connect()
        try:
            rows = conn.execute(sql).fetchall()
        finally:
            conn.close()
        out: list[dict[str, Any]] = []
        for rule_id, raw in rows:
            rule = _decode(rule_id, raw)
            if rule is not None:
                out.append(rule)
        return out


def _decode(rule_id: str, raw: str) -> dict[str, Any] | None:
    """Parse one payload row; a corrupt row is skipped, never fatal.

    One unparseable row must not take the whole rule list (and with it the
    evaluator) down — the remaining rules are still valid monitoring.
    """
    try:
        rule = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("alert_rules row %s has corrupt payload, skipped", rule_id)
        return None
    if not isinstance(rule, dict):
        logger.warning("alert_rules row %s is not an object, skipped", rule_id)
        return None
    rule["id"] = rule_id
    return rule


_alert_rules_store: AlertRulesStore | None = None


def get_alert_rules_store() -> AlertRulesStore:
    """Module-level singleton shared by the router and the evaluator loop."""
    global _alert_rules_store
    if _alert_rules_store is None:
        _alert_rules_store = AlertRulesStore()
    return _alert_rules_store
