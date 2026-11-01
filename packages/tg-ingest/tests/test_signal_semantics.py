"""Tests for the absorbed signal semantics in SignalEngine (tgwatcher 2026-08-10).

Covers:
- _compute_is_signal (event gate: llm completed + event_type valid + magnitude>0 + confidence>=0.3;
  direction NOT required — neutral events pass; audit 2026-08-13: message = event sensor)
- _build_signal_payload (signal_score, expires_at, is_signal)
- process_message returns the full DB row (created_at present — downstream hooks
  depend on it; this was a latent bug before the read-back fix)
- process_new_message dedup wiring (signed signals deduped; neutral events and
  non-signals always emit — US-006 neutral bypass)
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))


class _FakeLLM:
    """Fixed factor stub. direction configurable to toggle signal_score (no longer gates is_signal)."""

    def __init__(self, direction=-0.8, confidence=0.85):
        self._direction = direction
        self._confidence = confidence

    def refine(self, text, preliminary_factors):
        return {
            "symbols": ["BTC"],
            "direction": self._direction,
            "magnitude": 0.7,
            "urgency": 0.9,
            "confidence": self._confidence,
            "halflife_min": 120,
            "event_type": "security",
            "reasoning": "SEC investigation triggers bearish spike",
        }

    def refine_batch(self, messages):
        return [self.refine(t, p) for (t, p) in messages]


def _build_engine(tmp_path, llm=None, on_signal_factor=None, deduper=None):
    from selene_space_tg_ingest.signal_engine import SignalEngine
    from selene_space_tg_ingest.signal_filter import KeywordFilter
    from selene_space_tg_ingest.storage import Storage

    storage = Storage(db_path=tmp_path / "tgwatcher.db")
    storage.init_db()
    kw_filter = KeywordFilter(config={"min_text_length": 10})
    config = {
        "filter": {"min_text_length": 10},
        "llm": {"model": "deepseek-chat"},
        "factor_version": 2,
    }
    return SignalEngine(
        storage=storage,
        keyword_filter=kw_filter,
        llm=llm or _FakeLLM(),
        config=config,
        on_signal_factor=on_signal_factor,
        deduper=deduper,
    )


def _msg(message_id):
    return {
        "message_id": message_id,
        "chat_id": 1001,
        "text": "SEC files lawsuit against exchange for compliance violations",
    }


# ---------- _compute_is_signal (event gate) ----------

def test_compute_is_signal_true():
    from selene_space_tg_ingest.signal_engine import SignalEngine
    assert SignalEngine._compute_is_signal(
        {"llm_status": "completed", "event_type": "security", "magnitude": 0.7, "confidence": 0.5}
    ) is True


def test_compute_is_signal_neutral_direction_true():
    """Event gate: direction=0 (neutral) events with valid event_type pass."""
    from selene_space_tg_ingest.signal_engine import SignalEngine
    assert SignalEngine._compute_is_signal(
        {"llm_status": "completed", "direction": 0.0, "event_type": "macro", "magnitude": 0.6, "confidence": 0.5}
    ) is True


def test_compute_is_signal_low_confidence_false():
    from selene_space_tg_ingest.signal_engine import SignalEngine
    assert SignalEngine._compute_is_signal(
        {"llm_status": "completed", "event_type": "macro", "magnitude": 0.6, "confidence": 0.2}
    ) is False


def test_compute_is_signal_missing_event_type_false():
    from selene_space_tg_ingest.signal_engine import SignalEngine
    assert SignalEngine._compute_is_signal(
        {"llm_status": "completed", "magnitude": 0.6, "confidence": 0.5}
    ) is False


def test_compute_is_signal_zero_magnitude_false():
    from selene_space_tg_ingest.signal_engine import SignalEngine
    assert SignalEngine._compute_is_signal(
        {"llm_status": "completed", "event_type": "macro", "magnitude": 0.0, "confidence": 0.5}
    ) is False


def test_compute_is_signal_not_completed_false():
    from selene_space_tg_ingest.signal_engine import SignalEngine
    assert SignalEngine._compute_is_signal(
        {"llm_status": "failed", "event_type": "macro", "magnitude": 0.6, "confidence": 0.9}
    ) is False
    assert SignalEngine._compute_is_signal(
        {"llm_status": "skipped", "event_type": "macro", "magnitude": 0.6, "confidence": 0.9}
    ) is False


# ---------- _build_signal_payload ----------

def test_build_signal_payload_score_and_expiry(tmp_path):
    engine = _build_engine(tmp_path)
    factor = {
        "llm_status": "completed",
        "direction": -0.8,
        "magnitude": 0.7,
        "confidence": 0.85,
        "urgency": 0.9,
        "halflife_min": 120,
        "event_type": "security",
        "symbols": '["BTC"]',
        "created_at": "2026-08-10 12:00:00",
    }
    payload = engine._build_signal_payload(factor)
    # score = -0.8 * 0.7 * 0.85 * (0.5 + 0.5*0.9) = -0.4522
    assert abs(payload["signal_score"] - (-0.4522)) < 1e-6
    assert payload["is_signal"] is True
    assert payload["expires_at"] == "2026-08-10T14:00:00"
    assert payload["symbols"] == '["BTC"]'  # unchanged shape for downstream hook


def test_build_signal_payload_neutral_event_is_signal(tmp_path):
    """Event gate: direction=0 (neutral) event with valid event_type IS a signal."""
    engine = _build_engine(tmp_path)
    factor = {
        "llm_status": "completed",
        "direction": 0.0,  # neutral — event gate passes it (direction no longer gates)
        "magnitude": 0.7,
        "confidence": 0.85,
        "urgency": 0.9,
        "halflife_min": 60,
        "event_type": "security",
        "created_at": "2026-08-10 12:00:00",
    }
    payload = engine._build_signal_payload(factor)
    assert payload["is_signal"] is True
    assert payload["signal_score"] == 0.0  # direction=0 → score 0, but still an event signal


def test_build_signal_payload_non_signal_missing_event_type(tmp_path):
    engine = _build_engine(tmp_path)
    factor = {
        "llm_status": "completed",
        "direction": -0.8,
        "magnitude": 0.7,
        "confidence": 0.85,
        "urgency": 0.9,
        "halflife_min": 60,
        "event_type": "",
        "created_at": "2026-08-10 12:00:00",
    }
    payload = engine._build_signal_payload(factor)
    assert payload["is_signal"] is False


# ---------- process_message read-back (created_at) ----------

def test_process_message_returns_created_at(tmp_path):
    engine = _build_engine(tmp_path)
    result = engine.process_message(_msg(700))
    assert result is not None
    assert result["llm_status"] == "completed"
    assert result.get("created_at") is not None
    # is_signal persisted with real semantics (non-neutral, high conf)
    assert result.get("is_signal") is True


def test_skipped_factor_persists_is_signal_false(tmp_path):
    engine = _build_engine(tmp_path)
    result = engine.process_message({
        "message_id": 800,
        "chat_id": 1001,
        "text": "Random text without any matching keywords here",
    })
    assert result is not None
    assert result["llm_status"] == "skipped"
    assert result["is_signal"] is False


# ---------- dedup wiring in process_new_message ----------

def test_process_new_message_dedups_duplicate_signals(tmp_path):
    from selene_space_tg_ingest.signal_dedup import SignalDeduper

    calls = []
    deduper = SignalDeduper()
    engine = _build_engine(tmp_path, on_signal_factor=calls.append, deduper=deduper)

    engine.process_new_message(_msg(900))
    assert len(calls) == 1

    # Same fake-LLM output (same symbols/direction/event_type) → same dedup key
    engine.process_new_message(_msg(901))
    assert len(calls) == 1  # deduped


def test_process_new_message_neutral_events_not_deduped(tmp_path):
    """Neutral events (direction=0) are real signals (event sensor) → they pass
    the deduper, and are NOT collapsed against each other (US-006: two distinct
    same-type neutrals within the window are distinct observations)."""
    from selene_space_tg_ingest.signal_dedup import SignalDeduper

    calls = []
    engine = _build_engine(
        tmp_path,
        llm=_FakeLLM(direction=0.0),
        on_signal_factor=calls.append,
        deduper=SignalDeduper(),
    )
    engine.process_new_message(_msg(910))
    engine.process_new_message(_msg(911))
    assert len(calls) == 2  # both are distinct observations — not deduped


def test_no_deduper_emits_everything(tmp_path):
    calls = []
    engine = _build_engine(tmp_path, on_signal_factor=calls.append)
    engine.process_new_message(_msg(920))
    engine.process_new_message(_msg(921))
    assert len(calls) == 2
