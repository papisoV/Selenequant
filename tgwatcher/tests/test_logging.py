"""Tests for tgwatcher.logging_config — structured logging for ELK/Loki."""
from __future__ import annotations

import json
import logging

import pytest

from tgwatcher.logging_config import (
    JsonFormatter,
    KeyValueFormatter,
    _extra_fields,
    _serialize_value,
    setup_logging,
)
from datetime import datetime, timezone


@pytest.fixture(autouse=True)
def _reset_root_logger():
    """Restore root logger state around each test.

    setup_logging mutates the root logger (clears + adds handlers). Without
    this fixture, handler stacking across tests would corrupt downstream
    test output capture.
    """
    snap_handlers = logging.getLogger().handlers[:]
    snap_level = logging.getLogger().level
    yield
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    for h in snap_handlers:
        root.addHandler(h)
    root.setLevel(snap_level)


def test_setup_logging_does_not_raise():
    # Default invocation — no env var set, no args.
    setup_logging()
    # Explicit level string.
    setup_logging(level="DEBUG")
    # Bogus level falls back to INFO (getattr default), should not raise.
    setup_logging(level="not-a-real-level")


def test_setup_logging_is_idempotent():
    setup_logging()
    root = logging.getLogger()
    first_count = len(root.handlers)
    setup_logging()
    second_count = len(root.handlers)
    assert second_count == first_count, "setup_logging must not stack handlers on repeat calls"


def test_keyvalue_formatter_serializes_extra_fields():
    rec = logging.LogRecord(
        name="tgwatcher.web.api",
        level=logging.INFO,
        pathname="api.py",
        lineno=298,
        msg="Auto-poll: triggering crawl",
        args=(),
        exc_info=None,
    )
    # Simulate logger.info(..., extra={"chat_id": -100123, "action": "crawl_start"})
    rec.chat_id = -100123
    rec.action = "crawl_start"

    line = KeyValueFormatter().format(rec)
    assert "INFO" in line
    assert "tgwatcher.web.api" in line
    assert "Auto-poll: triggering crawl" in line
    assert "chat_id=-100123" in line
    assert "action=crawl_start" in line
    # ISO 8601 Z-suffixed timestamp at the start
    assert line.split()[0].endswith("Z"), "timestamp must be UTC ISO 8601 with Z suffix"


def test_keyvalue_formatter_quotes_values_with_whitespace():
    rec = logging.LogRecord(
        name="t",
        level=logging.WARNING,
        pathname="x.py",
        lineno=1,
        msg="legacy call still works",
        args=(),
        exc_info=None,
    )
    rec.group_name = "Some Group With Spaces"

    line = KeyValueFormatter().format(rec)
    assert 'group_name="Some Group With Spaces"' in line


def test_keyvalue_formatter_preserves_legacy_no_extra_calls():
    """logger.info('msg %s', val) without extra must still render correctly."""
    rec = logging.LogRecord(
        name="t",
        level=logging.INFO,
        pathname="x.py",
        lineno=1,
        msg="Saved %d/%d messages from %s",
        args=(5, 10, "GroupA"),
        exc_info=None,
    )
    line = KeyValueFormatter().format(rec)
    assert "Saved 5/10 messages from GroupA" in line
    # No spurious key=value pairs leaked from LogRecord internals
    assert "levelname=" not in line
    assert "args=" not in line


def test_json_formatter_outputs_valid_json():
    rec = logging.LogRecord(
        name="tgwatcher.signal_llm",
        level=logging.WARNING,
        pathname="signal_llm.py",
        lineno=500,
        msg="OpenAI call failed",
        args=(),
        exc_info=None,
    )
    rec.provider = "deepseek"
    rec.model = "deepseek-chat"
    rec.status = 503

    raw = JsonFormatter().format(rec)
    payload = json.loads(raw)  # raises if not valid JSON
    assert payload["level"] == "WARNING"
    assert payload["logger"] == "tgwatcher.signal_llm"
    assert payload["message"] == "OpenAI call failed"
    assert payload["provider"] == "deepseek"
    assert payload["model"] == "deepseek-chat"
    assert payload["status"] == 503
    assert payload["ts"].endswith("Z")


def test_json_formatter_env_switch_via_setup():
    import os
    from tgwatcher import logging_config as lc

    prev = os.environ.get("TGWATCHER_LOG_FORMAT")
    os.environ["TGWATCHER_LOG_FORMAT"] = "json"
    try:
        lc.setup_logging()
        handler = logging.getLogger().handlers[-1]
        assert isinstance(handler.formatter, JsonFormatter), \
            "TGWATCHER_LOG_FORMAT=json must configure JsonFormatter"
    finally:
        if prev is None:
            os.environ.pop("TGWATCHER_LOG_FORMAT", None)
        else:
            os.environ["TGWATCHER_LOG_FORMAT"] = prev
        lc.setup_logging()


def test_extra_fields_excludes_reserved_logrecord_keys():
    """Internal LogRecord attrs (process, thread, pathname...) must not leak as extras."""
    rec = logging.LogRecord(
        name="t", level=logging.INFO, pathname="x.py", lineno=1,
        msg="hi", args=(), exc_info=None,
    )
    rec.process = 12345  # type: ignore[attr-defined]
    rec.my_field = "value"  # type: ignore[attr-defined]
    extras = _extra_fields(rec)
    assert "process" not in extras
    assert "my_field" in extras
    assert extras["my_field"] == "value"


def test_serialize_value_handles_datetime_and_natives():
    assert _serialize_value(None) == "None"
    assert _serialize_value(True) == "true"
    assert _serialize_value(42) == "42"
    assert _serialize_value(3.14) == "3.14"
    assert _serialize_value("plain") == "plain"
    # naive datetime -> Z suffix appended (matches project _iso_z convention)
    dt = datetime(2026, 7, 24, 10, 0, 0)
    assert _serialize_value(dt) == "2026-07-24T10:00:00Z"
    # aware datetime -> preserved with Z suffix
    dt_aware = datetime(2026, 7, 24, 10, 0, 0, tzinfo=timezone.utc)
    assert _serialize_value(dt_aware) == "2026-07-24T10:00:00Z"
    # dict -> compact JSON
    assert _serialize_value({"k": "v"}) == '{"k": "v"}'
    assert _serialize_value([1, 2, 3]) == "[1, 2, 3]"
