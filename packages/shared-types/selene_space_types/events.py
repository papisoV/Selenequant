"""Diagnostic and lifecycle hook event models.

These are the payloads flowing through the internal event bus: structured
diagnostics (log-like records with severity) and notifications fired when
platform hooks run. Both carry a monotonically increasing ``sequence_id`` plus
a millisecond ``timestamp`` so consumers can order and correlate them.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class DiagnosticLevel(str, Enum):
    """Severity scale for diagnostic records, ordered low to high."""

    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class DiagnosticEvent(BaseModel):
    """A single structured diagnostic record."""

    sequence_id: int
    timestamp: int
    category: str
    level: DiagnosticLevel
    message: str
    data: dict | None = None


class HookEvent(BaseModel):
    """Notification that a platform hook executed.

    ``payload`` defaults to an empty dict — hooks always have a payload slot,
    even when they carry no data.
    """

    sequence_id: int
    timestamp: int
    hook_name: str
    payload: dict = {}
