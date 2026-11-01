"""Append-only audit trail backed by the ``audit_logs`` table.

Every event is persisted once: the caller may dictate its id, else one is
generated. Payloads travel as JSON inside ``details_json`` and are decoded
again on the way out; reads come back newest-first.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

_INSERT_SQL = """
INSERT INTO audit_logs (
    id, timestamp, event_type, severity,
    actor, action, resource, outcome, details_json
) VALUES (?, COALESCE(?, datetime('now')), ?, ?, ?, ?, ?, ?, ?)
"""

# column name -> (event key, fallback when key absent)
_DEFAULTS: dict[str, tuple[str, Any]] = {
    "event_type": ("event_type", ""),
    "severity": ("severity", "info"),
    "actor": ("actor", ""),
    "action": ("action", ""),
    "resource": ("resource", ""),
    "outcome": ("outcome", ""),
}


def _row_to_event(row: Any) -> dict[str, Any]:
    """Materialize one DB row as an event dict with ``details`` decoded."""
    event = dict(row)
    event["details"] = json.loads(event.pop("details_json"))
    return event


class AuditLogRepository:
    """Write-once access to the ``audit_logs`` table."""

    def __init__(self, db: Any) -> None:
        self._db = db

    async def append(self, event: dict[str, Any]) -> str:
        assigned_id = str(event.get("id") or uuid.uuid4().hex)
        values = [
            _DEFAULTS[column][1] if event.get(key) is None else event.get(key)
            for column, (key, _) in _DEFAULTS.items()
        ]
        await self._db.execute(
            _INSERT_SQL,
            (
                assigned_id,
                event.get("timestamp"),
                *values,
                json.dumps(event.get("details", {}), ensure_ascii=False),
            ),
        )
        return assigned_id

    async def query(
        self,
        event_type: str | None = None,
        since: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        spec: list[tuple[str, Any]] = []
        if event_type is not None:
            spec.append(("event_type = ?", event_type))
        if since is not None:
            spec.append(("timestamp >= ?", since))

        sql = "SELECT * FROM audit_logs"
        if spec:
            sql += " WHERE " + " AND ".join(predicate for predicate, _ in spec)
        sql += " ORDER BY timestamp DESC"
        if limit is not None:
            sql += f" LIMIT {int(limit)}"

        rows = await self._db.execute(sql, tuple(value for _, value in spec))
        return [_row_to_event(row) for row in rows]
