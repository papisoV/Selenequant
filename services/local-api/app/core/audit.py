"""Lightweight audit logging for critical local-api operations.

Writes structured events to the SQLite audit_logs table via
the existing ``AuditLogRepository``. Non-blocking: logs a warning
on failure but never raises to avoid disrupting business logic.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

logger = logging.getLogger(__name__)

_audit_repo: Any = None


def set_audit_repo(repo: Any) -> None:
    global _audit_repo
    _audit_repo = repo


async def _audit_write(data: dict[str, Any]) -> None:
    """Module-level fire-and-forget writer. Exceptions logged, never raised."""
    try:
        await _audit_repo.append(data)
    except Exception:
        logger.warning(
            "Failed to write audit event: %s/%s",
            data.get("event_type"),
            data.get("action"),
            exc_info=True,
        )


async def audit_event(
    *,
    event_type: str,
    actor: str,
    action: str,
    resource: str = "",
    outcome: str = "success",
    severity: str = "info",
    details: dict[str, Any] | None = None,
) -> None:
    """Append an audit event. Fire-and-forget — never blocks the caller."""
    if _audit_repo is None:
        return
    asyncio.create_task(_audit_write(
        {
            "event_type": event_type,
            "actor": actor,
            "action": action,
            "resource": resource,
            "outcome": outcome,
            "severity": severity,
            "details": details or {},
        }
    ))
