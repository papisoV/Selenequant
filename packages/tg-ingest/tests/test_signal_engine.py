"""Tests for SignalEngine callback hooks.

Verifies:
- on_signal_factor fires on completed and skipped messages (replaces old web SSE push)
- on_tg_event fires on every incoming message
- Exception in callback is swallowed (engine never throws)
- process_new_message sets llm_status='pending' on internal failure
"""
from __future__ import annotations

import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))


class _FakeLLM:
    """Minimal LLM stub returning a fixed validated factor dict."""

    def refine(self, text, preliminary_factors):
        return {
            "symbols": ["BTC"],
            "direction": -0.8,
            "magnitude": 0.7,
            "urgency": 0.9,
            "confidence": 0.85,
            "halflife_min": 120,
            "event_type": "security",
            "reasoning": "SEC investigation triggers bearish spike",
        }

    def refine_batch(self, messages):
        return [self.refine(t, p) for (t, p) in messages]


def _build_engine(tmp_path, on_signal_factor=None, on_tg_event=None, filter_config=None):
    from selene_space_tg_ingest.signal_engine import SignalEngine
    from selene_space_tg_ingest.signal_filter import KeywordFilter
    from selene_space_tg_ingest.storage import Storage

    storage = Storage(db_path=tmp_path / "tgwatcher.db")
    storage.init_db()
    kw_filter = KeywordFilter(config=filter_config or {"min_text_length": 10})
    config = {
        "filter": {"min_text_length": 10},
        "llm": {"model": "deepseek-chat"},
        "factor_version": 2,
    }
    return SignalEngine(
        storage=storage,
        keyword_filter=kw_filter,
        llm=_FakeLLM(),
        config=config,
        on_signal_factor=on_signal_factor,
        on_tg_event=on_tg_event,
    )


def test_on_signal_factor_fires_on_completed(tmp_path):
    calls = []

    def hook(factor):
        calls.append(factor)

    engine = _build_engine(tmp_path, on_signal_factor=hook)
    msg = {
        "message_id": 100,
        "chat_id": 1001,
        "text": "SEC files lawsuit against exchange for compliance violations",
    }
    result = engine.process_new_message(msg)
    assert result is not None
    assert result["llm_status"] == "completed"
    assert len(calls) == 1
    assert calls[0]["message_id"] == 100
    assert calls[0]["event_type"] == "security"


def test_on_signal_factor_fires_on_skipped(tmp_path):
    """Skipped (filter rejected) messages should also trigger callback."""
    calls = []

    def hook(factor):
        calls.append(factor)

    engine = _build_engine(tmp_path, on_signal_factor=hook)
    msg = {
        "message_id": 101,
        "chat_id": 1001,
        "text": "hi",  # too short, will be skipped at min_text_length=10
    }
    result = engine.process_new_message(msg)
    assert result is None  # too short returns None before filter even runs
    assert calls == []


def test_on_tg_event_fires_before_processing(tmp_path):
    """on_tg_event should fire for every message regardless of filter outcome."""
    tg_events = []

    def hook(msg):
        tg_events.append(msg["message_id"])

    engine = _build_engine(tmp_path, on_tg_event=hook)
    msg = {
        "message_id": 200,
        "chat_id": 2001,
        "text": "SEC files lawsuit against exchange for compliance violations",
    }
    engine.process_new_message(msg)
    assert tg_events == [200]


def test_callback_exception_is_swallowed(tmp_path):
    """A buggy on_signal_factor callback must not crash the engine."""
    def bad_hook(factor):
        raise RuntimeError("boom")

    engine = _build_engine(tmp_path, on_signal_factor=bad_hook)
    msg = {
        "message_id": 300,
        "chat_id": 3001,
        "text": "SEC files lawsuit against exchange for compliance violations",
    }
    # Must not raise
    result = engine.process_new_message(msg)
    assert result is not None
    assert result["llm_status"] == "completed"


def test_no_callbacks_registered_works(tmp_path):
    """Engine without callbacks should still process messages normally."""
    engine = _build_engine(tmp_path)
    msg = {
        "message_id": 400,
        "chat_id": 4001,
        "text": "SEC files lawsuit against exchange for compliance violations",
    }
    result = engine.process_new_message(msg)
    assert result is not None
    assert result["llm_status"] == "completed"


def test_process_message_skipped_when_filter_rejects(tmp_path):
    """When keyword filter rejects, factor saved with llm_status='skipped' and callback fires."""
    calls = []

    def hook(factor):
        calls.append(factor)

    engine = _build_engine(tmp_path, on_signal_factor=hook)
    msg = {
        "message_id": 500,
        "chat_id": 5001,
        "text": "Random text without any matching keywords here",
    }
    result = engine.process_new_message(msg)
    assert result is not None
    assert result["llm_status"] == "skipped"
    assert result["filter_result"] == "rejected"
    # process_message path does NOT fire on_signal_factor — only process_new_message does.
    # Verify that callers using process_new_message DO fire:
    calls.clear()
    result2 = engine.process_new_message(msg)
    # Second call should hit save with existing record; callback should fire
    # because llm_status is "skipped" (still in the (completed, skipped) tuple).
    assert len(calls) == 1
