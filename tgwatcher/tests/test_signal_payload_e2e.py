"""End-to-end payload verification for commit 1508a5e + 7ffeb1e.

These tests verify that SignalEngine._build_signal_payload produces the
new downstream-facing fields (signal_score, expires_at) with the correct
semantics, and that SignalDeduper correctly filters same-key payloads.

This is the "end-to-end" verification of the payload contract without
needing to run the full Flask + Telethon + LLM stack. _build_signal_payload
is a staticmethod, so we can call it directly with synthesized inputs.

Covers:
- signal_score formula: direction * magnitude * confidence * (0.5 + 0.5 * urgency)
- expires_at = date + 2 * halflife_min, ISO8601 with timezone suffix
- Naive datetime gets UTC stamped (defense from 7ffeb1e)
- SignalDeduper: same key dedups, higher confidence supersedes, different key emits
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tgwatcher.signal_engine import SignalEngine
from tgwatcher.signal_dedup import SignalDeduper


def _make_msg(date: datetime, text: str = "test", message_id: int = 1) -> dict:
    return {
        "message_id": message_id,
        "chat_id": 2234543601,
        "chat_title": "test-chat",
        "sender_name": "tester",
        "text": text,
        "date": date,
    }


def _make_factor(
    direction: float = 0.8,
    magnitude: float = 0.7,
    urgency: float = 0.9,
    confidence: float = 0.85,
    halflife_min: int = 60,
    symbols: list[str] | None = None,
    event_type: str = "market",
) -> dict:
    import json
    return {
        "direction": direction,
        "magnitude": magnitude,
        "urgency": urgency,
        "confidence": confidence,
        "halflife_min": halflife_min,
        "symbols": json.dumps(symbols or ["BTC"], ensure_ascii=False),
        "event_type": event_type,
        "reasoning": "test reasoning",
    }


# ============================================================
# signal_score tests
# ============================================================

class TestSignalScore:
    def test_bullish_score_formula(self):
        """direction=0.8, magnitude=0.7, urgency=0.9, confidence=0.85
        → 0.8 * 0.7 * 0.85 * (0.5 + 0.5*0.9) = 0.4522"""
        msg = _make_msg(datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
        factor = _make_factor(direction=0.8, magnitude=0.7, urgency=0.9, confidence=0.85)
        payload = SignalEngine._build_signal_payload(msg, factor)
        assert payload["signal_score"] == pytest.approx(0.4522, abs=1e-4)

    def test_bearish_score_negative(self):
        """direction=-0.9, magnitude=0.7, urgency=0.9, confidence=0.85
        → -0.9 * 0.7 * 0.85 * 0.95 = -0.5087"""
        msg = _make_msg(datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
        factor = _make_factor(direction=-0.9, magnitude=0.7, urgency=0.9, confidence=0.85)
        payload = SignalEngine._build_signal_payload(msg, factor)
        assert payload["signal_score"] < 0
        assert payload["signal_score"] == pytest.approx(-0.5087, abs=1e-4)

    def test_score_in_valid_range(self):
        """score must be in [-1, 1] for any reasonable inputs"""
        msg = _make_msg(datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
        for d in (-1.0, -0.5, 0.0, 0.5, 1.0):
            for u in (0.0, 0.5, 1.0):
                for c in (0.0, 0.3, 1.0):
                    factor = _make_factor(direction=d, urgency=u, confidence=c)
                    payload = SignalEngine._build_signal_payload(msg, factor)
                    assert -1.0 <= payload["signal_score"] <= 1.0, (
                        f"score {payload['signal_score']} out of range for d={d}, u={u}, c={c}"
                    )

    def test_zero_direction_zero_score(self):
        msg = _make_msg(datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
        factor = _make_factor(direction=0.0, magnitude=0.9, urgency=1.0, confidence=1.0)
        payload = SignalEngine._build_signal_payload(msg, factor)
        assert payload["signal_score"] == 0.0

    def test_low_urgency_halves_weight(self):
        """urgency=0 → weight (0.5 + 0.5*0) = 0.5, so score is half of max."""
        msg = _make_msg(datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
        factor = _make_factor(direction=1.0, magnitude=1.0, urgency=0.0, confidence=1.0)
        payload = SignalEngine._build_signal_payload(msg, factor)
        assert payload["signal_score"] == pytest.approx(0.5, abs=1e-4)


# ============================================================
# expires_at tests (timezone defense from 7ffeb1e)
# ============================================================

class TestExpiresAt:
    def test_aware_datetime_preserves_tz(self):
        """Aware UTC datetime → expires_at has +00:00 suffix."""
        date_dt = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
        msg = _make_msg(date_dt)
        factor = _make_factor(halflife_min=60)
        payload = SignalEngine._build_signal_payload(msg, factor)
        assert payload["expires_at"] is not None
        assert payload["expires_at"].endswith("+00:00")
        # 10:00 + 120 min = 12:00
        assert "12:00:00" in payload["expires_at"]

    def test_naive_datetime_gets_utc_stamped(self):
        """Naive datetime (test/replay path) → gets UTC stamped, expires_at has +00:00."""
        date_dt = datetime(2026, 7, 20, 10, 0)  # naive — no tzinfo
        msg = _make_msg(date_dt)
        factor = _make_factor(halflife_min=60)
        payload = SignalEngine._build_signal_payload(msg, factor)
        assert payload["expires_at"] is not None
        assert payload["expires_at"].endswith("+00:00"), (
            "naive datetime must still produce tz-suffixed expires_at (7ffeb1e defense)"
        )

    def test_expires_at_is_date_plus_2x_halflife(self):
        """expires_at = date + 2 * halflife_min (signal decays to 25%)."""
        date_dt = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
        msg = _make_msg(date_dt)
        # halflife=45 → expires_at = 10:00 + 90min = 11:30
        factor = _make_factor(halflife_min=45)
        payload = SignalEngine._build_signal_payload(msg, factor)
        assert "11:30:00" in payload["expires_at"]

    def test_expires_at_none_if_no_date(self):
        """If msg has no date, expires_at is None (graceful)."""
        msg = _make_msg(datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
        msg["date"] = None
        factor = _make_factor(halflife_min=60)
        payload = SignalEngine._build_signal_payload(msg, factor)
        assert payload["expires_at"] is None


# ============================================================
# Payload contract completeness
# ============================================================

class TestPayloadContract:
    def test_all_expected_fields_present(self):
        """Payload must contain all fields Selene expects (per 1508a5e spec)."""
        msg = _make_msg(datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
        factor = _make_factor()
        payload = SignalEngine._build_signal_payload(msg, factor)

        required = {
            "message_id", "chat_id", "chat_title", "sender_name", "text", "date",
            "direction", "magnitude", "urgency", "confidence", "halflife_min",
            "symbols", "event_type", "reasoning",
            "signal_score", "expires_at",  # new fields
        }
        assert required.issubset(payload.keys()), (
            f"missing fields: {required - set(payload.keys())}"
        )

    def test_symbols_parsed_from_json_string(self):
        """factor['symbols'] is JSON string; payload['symbols'] must be a list."""
        msg = _make_msg(datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc))
        factor = _make_factor(symbols=["BTC", "ETH"])
        payload = SignalEngine._build_signal_payload(msg, factor)
        assert isinstance(payload["symbols"], list)
        assert set(payload["symbols"]) == {"BTC", "ETH"}


# ============================================================
# SignalDeduper behavior on real payloads
# ============================================================

class TestDeduperOnPayload:
    def test_first_signal_emits(self):
        d = SignalDeduper(window_seconds=300)
        msg = _make_msg(datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc), message_id=1)
        payload = SignalEngine._build_signal_payload(msg, _make_factor(confidence=0.7))
        assert d.should_emit(payload) is True

    def test_same_key_lower_confidence_dedups(self):
        d = SignalDeduper(window_seconds=300)
        base_time = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
        p1 = SignalEngine._build_signal_payload(
            _make_msg(base_time, message_id=1), _make_factor(confidence=0.8)
        )
        p2 = SignalEngine._build_signal_payload(
            _make_msg(base_time, message_id=2), _make_factor(confidence=0.5)
        )
        assert d.should_emit(p1) is True
        assert d.should_emit(p2) is False  # same key, lower confidence → dedup

    def test_same_key_higher_confidence_supersedes(self):
        d = SignalDeduper(window_seconds=300)
        base_time = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
        p1 = SignalEngine._build_signal_payload(
            _make_msg(base_time, message_id=1), _make_factor(confidence=0.5)
        )
        p2 = SignalEngine._build_signal_payload(
            _make_msg(base_time, message_id=2), _make_factor(confidence=0.9)
        )
        assert d.should_emit(p1) is True
        assert d.should_emit(p2) is True  # higher confidence → supersede, emit

    def test_different_symbols_emit_independently(self):
        d = SignalDeduper(window_seconds=300)
        base_time = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
        p1 = SignalEngine._build_signal_payload(
            _make_msg(base_time, message_id=1), _make_factor(symbols=["BTC"], confidence=0.7)
        )
        p2 = SignalEngine._build_signal_payload(
            _make_msg(base_time, message_id=2), _make_factor(symbols=["ETH"], confidence=0.7)
        )
        assert d.should_emit(p1) is True
        assert d.should_emit(p2) is True  # different symbols → independent key

    def test_opposite_direction_emit_independently(self):
        """Bullish + bearish on same symbol are different signals (direction_sign)."""
        d = SignalDeduper(window_seconds=300)
        base_time = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
        p_bull = SignalEngine._build_signal_payload(
            _make_msg(base_time, message_id=1),
            _make_factor(direction=0.8, confidence=0.7),
        )
        p_bear = SignalEngine._build_signal_payload(
            _make_msg(base_time, message_id=2),
            _make_factor(direction=-0.8, confidence=0.7),
        )
        assert d.should_emit(p_bull) is True
        assert d.should_emit(p_bear) is True

    def test_disabled_dedup_always_emits(self):
        """window_seconds=0 → dedup is off, always emit (backward-compat escape)."""
        d = SignalDeduper(window_seconds=0)
        base_time = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
        for i in range(5):
            p = SignalEngine._build_signal_payload(
                _make_msg(base_time, message_id=i), _make_factor(confidence=0.7)
            )
            assert d.should_emit(p) is True

    def test_symbols_order_independent(self):
        """["BTC","ETH"] and ["ETH","BTC"] must produce the same dedup key."""
        d = SignalDeduper(window_seconds=300)
        base_time = datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc)
        p1 = SignalEngine._build_signal_payload(
            _make_msg(base_time, message_id=1),
            _make_factor(symbols=["BTC", "ETH"], confidence=0.7),
        )
        p2 = SignalEngine._build_signal_payload(
            _make_msg(base_time, message_id=2),
            _make_factor(symbols=["ETH", "BTC"], confidence=0.5),  # lower confidence
        )
        assert d.should_emit(p1) is True
        # Same key (sorted symbols match), lower confidence → dedup
        assert d.should_emit(p2) is False
