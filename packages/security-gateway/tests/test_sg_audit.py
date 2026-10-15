"""Behavior locks for selene_space_security.audit: event defaults,
JSONL persistence, detail redaction, rotation, and queries."""

import json
import time
from pathlib import Path

from selene_space_security.audit import (
    AuditEvent,
    AuditEventType,
    AuditLogger,
    AuditSeverity,
)


def _records_in(tmp_path: Path) -> list[dict]:
    files = list(tmp_path.glob("audit-*.jsonl"))
    return [json.loads(line) for line in files[0].read_text(encoding="utf-8").strip().splitlines()]


def _tool_event(action: str, **extra) -> AuditEvent:
    return AuditEvent(event_type=AuditEventType.TOOL_CALL, actor="user", action=action, **extra)


# --- AuditEvent model -----------------------------------------------------------


def test_event_fills_sane_defaults() -> None:
    fresh = AuditEvent(event_type=AuditEventType.TOOL_CALL, actor="user", action="market_ticker")
    assert fresh.id  # UUID generated
    assert fresh.timestamp > 0
    assert (fresh.severity, fresh.outcome) == (AuditSeverity.INFO, "allowed")


def test_event_serializes_enum_values() -> None:
    blocked = AuditEvent(
        event_type=AuditEventType.TOOL_BLOCKED,
        severity=AuditSeverity.WARN,
        actor="agent",
        action="shell_exec",
        outcome="blocked",
    )
    wire = json.loads(blocked.model_dump_json())
    assert wire["event_type"] == "tool_blocked" and wire["severity"] == "warn"


# --- basic logging ------------------------------------------------------------------


def test_first_log_writes_one_jsonl_file(tmp_path: Path) -> None:
    AuditLogger(log_dir=tmp_path).log(_tool_event("market_ticker"))

    on_disk = list(tmp_path.glob("audit-*.jsonl"))
    assert len(on_disk) == 1
    record = json.loads(on_disk[0].read_text(encoding="utf-8").strip())
    assert record["event_type"] == "tool_call" and record["actor"] == "user"


def test_successive_logs_append_not_rotate(tmp_path: Path) -> None:
    sink = AuditLogger(log_dir=tmp_path)
    for n in range(5):
        sink.log(_tool_event(f"action_{n}"))

    written = list(tmp_path.glob("audit-*.jsonl"))
    assert len(written) == 1 and len(_records_in(tmp_path)) == 5


# --- redaction of details ---------------------------------------------------------------


def test_inline_secret_scrubbed_from_details(tmp_path: Path) -> None:
    sink = AuditLogger(log_dir=tmp_path)
    sink.log(
        AuditEvent(
            event_type=AuditEventType.SECRET_ACCESS,
            actor="system",
            action="resolve_secret",
            details={"key": "sk-abc123def456ghi789jkl012mno345"},
        )
    )
    # The sk- prefixed key should be redacted
    stored = _records_in(tmp_path)[0]
    assert "sk-abc123def456ghi789jkl012mno345" not in stored["details"]["key"]


def test_nested_secret_scrubbed_from_details(tmp_path: Path) -> None:
    sink = AuditLogger(log_dir=tmp_path)
    sink.log(
        AuditEvent(
            event_type=AuditEventType.CONFIG_CHANGE,
            actor="user",
            action="update_config",
            details={"config": {"api_key": "sk-nested_secret_key_value_long"}},
        )
    )
    assert "sk-nested_secret_key_value_long" not in json.dumps(_records_in(tmp_path))


# --- rotation ------------------------------------------------------------------------------


def test_small_size_cap_forces_rotation(tmp_path: Path) -> None:
    sink = AuditLogger(log_dir=tmp_path, max_file_size=500)
    for n in range(20):
        sink.log(_tool_event(f"action_{n}_with_some_padding_to_fill_space"))

    assert len(list(tmp_path.glob("audit-*.jsonl"))) >= 2


# --- query ------------------------------------------------------------------------------------


def _seed(count: int, tmp_path: Path) -> AuditLogger:
    sink = AuditLogger(log_dir=tmp_path)
    for n in range(count):
        sink.log(_tool_event(f"action_{n}"))
    return sink


def test_query_without_filters_returns_everything(tmp_path: Path) -> None:
    assert len(_seed(3, tmp_path).query()) == 3


def test_query_filter_by_event_type(tmp_path: Path) -> None:
    sink = AuditLogger(log_dir=tmp_path)
    sink.log(_tool_event("allowed"))
    sink.log(
        AuditEvent(event_type=AuditEventType.TOOL_BLOCKED, actor="agent", action="blocked")
    )

    hits = sink.query(event_type=AuditEventType.TOOL_BLOCKED)
    assert len(hits) == 1 and hits[0].event_type == AuditEventType.TOOL_BLOCKED


def test_query_respects_limit(tmp_path: Path) -> None:
    assert len(_seed(10, tmp_path).query(limit=3)) == 3


def test_query_since_excludes_old_entries(tmp_path: Path) -> None:
    sink = AuditLogger(log_dir=tmp_path)
    sink.log(AuditEvent(event_type=AuditEventType.TOOL_CALL, actor="user", action="old", timestamp=1000.0))
    sink.log(AuditEvent(event_type=AuditEventType.TOOL_CALL, actor="user", action="new", timestamp=time.time()))

    hits = sink.query(since=time.time() - 60)
    assert len(hits) == 1 and hits[0].action == "new"
