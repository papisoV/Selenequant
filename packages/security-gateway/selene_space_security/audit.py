"""Append-only JSONL audit logging with rotation and query.

Every :class:`AuditEvent` is serialized to one compact JSON line under
``log_dir``. Before hitting disk the event's ``details`` tree is swept by
the redaction engine, so credentials embedded in structured payloads are
masked in the *persisted* record while the in-memory object stays intact.

Files rotate when they exceed ``max_file_size``; queries read all
rotation generations back as typed events.
"""

from __future__ import annotations

import enum
import json
import time
import uuid
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from selene_space_security.redaction import redact_text

__all__ = [
    "AuditEvent",
    "AuditEventType",
    "AuditLogger",
    "AuditSeverity",
]

_FILE_STEM = "audit-"
_FILE_SUFFIX = ".jsonl"


class AuditEventType(str, enum.Enum):
    """Category of the audited action (serializes lowercase)."""

    TOOL_CALL = "tool_call"
    TOOL_BLOCKED = "tool_blocked"
    SECRET_ACCESS = "secret_access"
    CONFIG_CHANGE = "config_change"


class AuditSeverity(str, enum.Enum):
    """How serious an event is (serializes lowercase)."""

    INFO = "info"
    WARN = "warn"


class AuditEvent(BaseModel):
    """One auditable occurrence. ``id``/``timestamp`` auto-populate."""

    event_type: AuditEventType
    actor: str
    action: str
    severity: AuditSeverity = AuditSeverity.INFO
    outcome: str = "allowed"
    details: dict[str, Any] = Field(default_factory=dict)
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = Field(default_factory=time.time)


def _redact_details(value: Any) -> Any:
    """Recursively redact strings inside a details tree.

    Lists and dicts are traversed; every string is passed through
    :func:`redact_text` so provider tokens / JWTs / assignments inside
    detail values come out masked.
    """
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {k: _redact_details(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_redact_details(item) for item in value]
    return value


class AuditLogger:
    """Writes audit events as JSONL files with size-based rotation."""

    def __init__(
        self, log_dir: Path | str, max_file_size: int = 10 * 1024 * 1024
    ) -> None:
        self._log_dir = Path(log_dir)
        self._max_file_size = max_file_size
        self._log_dir.mkdir(parents=True, exist_ok=True)

    def _current_file(self) -> Path:
        """Newest generation file, creating one when none exists."""
        existing = sorted(self._log_dir.glob(f"{_FILE_STEM}*{_FILE_SUFFIX}"))
        if existing:
            return existing[-1]
        return self._next_file()

    def _next_file(self) -> Path:
        return (
            self._log_dir
            / f"{_FILE_STEM}{time.strftime('%Y%m%d-%H%M%S')}_{uuid.uuid4().hex[:8]}{_FILE_SUFFIX}"
        )

    def log(self, event: AuditEvent) -> None:
        """Persist one event; rotates when the active file is full."""
        target = self._current_file()
        if target.exists() and target.stat().st_size >= self._max_file_size:
            target = self._next_file()

        record = event.model_dump()
        record["event_type"] = event.event_type.value
        record["severity"] = event.severity.value
        if record.get("details"):
            record["details"] = _redact_details(record["details"])
        line = json.dumps(record, ensure_ascii=False, separators=(",", ":"))

        with open(target, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # -- read side -------------------------------------------------------------

    def query(
        self,
        event_type: Optional[AuditEventType] = None,
        since: Optional[float] = None,
        limit: Optional[int] = None,
    ) -> list[AuditEvent]:
        """Load recorded events (oldest first) with optional filters."""
        results: list[AuditEvent] = []
        for path in sorted(self._log_dir.glob(f"{_FILE_STEM}*{_FILE_SUFFIX}")):
            with open(path, encoding="utf-8") as fh:
                for raw_line in fh:
                    line = raw_line.strip()
                    if not line:
                        continue
                    try:
                        parsed = json.loads(line)
                    except json.JSONDecodeError:
                        continue  # torn final line from a crash — skip it
                    ts = float(parsed.get("timestamp", 0.0))
                    if since is not None and ts <= since:
                        continue
                    if (
                        event_type is not None
                        and parsed.get("event_type") != event_type.value
                    ):
                        continue
                    try:
                        results.append(AuditEvent.model_validate(parsed))
                    except Exception:
                        continue  # malformed record — never break the query
        return results[:limit] if limit is not None else results
