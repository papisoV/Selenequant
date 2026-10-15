"""Structured logging configuration for TGWatcher.

Two output modes, both stdlib-only (no structlog / python-json-logger):

- Human-readable (default): ``2026-07-24T10:00:00Z INFO tgwatcher.web.api message field1=val1 field2=val2``
- Full JSON (``TGWATCHER_LOG_FORMAT=json``): ``{"ts": "...", "level": "INFO", "logger": "...", "message": "...", "field1": "val1"}``

Both modes serialize ``extra`` fields passed via ``logger.info("msg", extra={"k": v})``.
Existing ``logger.info("msg %s", val)`` calls continue to work unchanged — ``extra`` is optional.

Designed for future ELK/Loki ingestion: key=value / JSON formats parse cleanly with
Logstash grok or Loki's logfmt parser.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

# Reserved attribute names used by logging.Formatter itself — these appear in
# every LogRecord and must NOT be echoed back as "extra" fields (would duplicate
# the primary line and leak internal bookkeeping like process IDs).
_RESERVED_LOGRECORD_KEYS = frozenset({
    "args", "asctime", "created", "exc_info", "exc_text", "filename",
    "funcName", "levelname", "levelno", "lineno", "message", "module",
    "msecs", "msg", "name", "pathname", "process", "processName",
    "relativeCreated", "stack_info", "thread", "threadName", "taskName",
    "extra",  # the user-supplied dict itself — its values are promoted to top-level
})


def _serialize_value(v: Any) -> str:
    """Render any value as a log-safe string.

    - str/ int/ float/ bool/ None pass through naturally
    - datetime -> ISO 8601 with Z suffix (matches _iso_z convention); aware
      datetimes in UTC are normalized to Z, other tz offsets preserved
    - dict/ list/ tuple -> compact JSON
    - objects with __dict__ -> compact JSON of that dict
    - fall back to repr() so we never raise from a log formatter
    """
    if v is None:
        return "None"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (str, int, float)):
        return str(v)
    if isinstance(v, datetime):
        s = v.isoformat()
        # Normalize UTC (+00:00) to Z suffix so downstream parsers see one shape.
        if s.endswith("+00:00"):
            s = s[:-6] + "Z"
        elif v.tzinfo is None and not s.endswith(("Z", "+00:00")):
            s = s + "Z"
        return s
    if isinstance(v, (dict, list, tuple)):
        try:
            return json.dumps(v, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            return repr(v)
    if hasattr(v, "__dict__") and not callable(v):
        try:
            return json.dumps(v.__dict__, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            return repr(v)
    return repr(v)


def _json_value(v: Any) -> Any:
    """Return a JSON-native representation for a value.

    Used by JsonFormatter so that int/float/bool/None survive as native JSON
    types (not stringified) — keeps numeric fields queryable in Loki/ELK.
    datetime/dict/list fall back to _serialize_value's string form.
    """
    if v is None or isinstance(v, (bool, int, float, str)):
        return v
    return _serialize_value(v)


def _extra_fields(record: logging.LogRecord, json_mode: bool = False) -> dict[str, Any]:
    """Extract user-supplied extra fields from a LogRecord.

    Any key in record.__dict__ that isn't a reserved LogRecord attribute is
    promoted to the output line. Returns a dict mapping key -> value
    (stringified for key=value mode, JSON-native for JSON mode).
    """
    out: dict[str, Any] = {}
    for key, value in record.__dict__.items():
        if key not in _RESERVED_LOGRECORD_KEYS and not key.startswith("_"):
            out[key] = _json_value(value) if json_mode else _serialize_value(value)
    return out


class KeyValueFormatter(logging.Formatter):
    """Human-readable formatter: ``asctime LEVEL logger message k1=v1 k2=v2``.

    The ``asctime`` is emitted in ISO 8601 with a trailing Z (UTC) so downstream
    parsers don't need to guess the timezone — matches the API serialization
    convention used throughout TGWatcher.
    """

    def format(self, record: logging.LogRecord) -> str:
        # UTC ISO 8601 timestamp with Z suffix
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        message = record.getMessage()
        extras = _extra_fields(record, json_mode=False)

        parts = [ts, record.levelname, record.name, message]
        for k, v in extras.items():
            # Quote the value if it contains whitespace — ensures logfmt parsers
            # treat it as a single token.
            if v and any(c.isspace() for c in v):
                parts.append(f'{k}="{v}"')
            else:
                parts.append(f"{k}={v}")

        line = " ".join(parts)
        if record.exc_info:
            # Preserve the standard "Traceback (most recent call last)" block
            # so error logs stay debuggable in both modes.
            line = line + "\n" + self.formatException(record.exc_info)
        return line


class JsonFormatter(logging.Formatter):
    """Full-JSON formatter: emits one JSON object per log line.

    Output shape: ``{"ts": "...", "level": "...", "logger": "...", "message": "...", <extra>: <value>, "exc": "..."}``
    """

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        extras = _extra_fields(record, json_mode=True)

        payload: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # In JSON mode, preserve native types (int stays int, not "503") so
        # numeric fields stay queryable in Loki/ELK.
        for k, v in extras.items():
            payload[k] = v

        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)

        # ensure_ascii=False keeps Chinese characters readable (project has
        # Chinese log messages like "正在停止..."). default=str guards against
        # exotic types that survive _serialize_value's net.
        return json.dumps(payload, default=str, ensure_ascii=False)


def setup_logging(level: str = "INFO") -> None:
    """Configure root logger with structured formatter.

    Selects between human-readable key=value and full JSON output based on
    the ``TGWATCHER_LOG_FORMAT`` env var (``json`` for JSON, anything else
    or unset for human-readable).

    Idempotent: safe to call multiple times — clears existing handlers on the
    root logger before installing the new one, so re-init during tests doesn't
    stack duplicate handlers.
    """
    log_format = os.environ.get("TGWATCHER_LOG_FORMAT", "").strip().lower()
    formatter = JsonFormatter() if log_format == "json" else KeyValueFormatter()

    root = logging.getLogger()
    # Remove existing handlers so repeat calls (tests, hot-reload) don't stack.
    for h in list(root.handlers):
        root.removeHandler(h)

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
