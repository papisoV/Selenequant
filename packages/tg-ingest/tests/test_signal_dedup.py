"""Tests for SignalDeduper (ported from tgwatcher 2026-08-10).

Covers: first-in-window emit, duplicate dedup, high-confidence supersede,
order-independent symbols, JSON-string symbols (tg-ingest payload shape),
window expiry, and window_seconds=0 (disabled).
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))

from selene_space_tg_ingest.signal_dedup import SignalDeduper  # noqa: E402


def _payload(symbols='["BTC"]', direction=0.8, event_type="security", confidence=0.8):
    # symbols defaults to the JSON-array-string shape tg-ingest factors carry.
    return {
        "symbols": symbols,
        "direction": direction,
        "event_type": event_type,
        "confidence": confidence,
    }


def test_first_signal_in_window_emits():
    d = SignalDeduper()
    assert d.should_emit(_payload()) is True


def test_duplicate_within_window_is_deduped():
    d = SignalDeduper()
    assert d.should_emit(_payload(confidence=0.8)) is True
    # Same key, lower confidence — dedup
    assert d.should_emit(_payload(confidence=0.4)) is False


def test_higher_confidence_supersedes():
    d = SignalDeduper()
    assert d.should_emit(_payload(confidence=0.4)) is True
    # Higher confidence — emit (supersede the earlier lower-confidence push)
    assert d.should_emit(_payload(confidence=0.9)) is True
    # Back to lower — dedup again
    assert d.should_emit(_payload(confidence=0.5)) is False


def test_symbols_order_independent():
    d = SignalDeduper()
    assert d.should_emit(_payload(symbols=["BTC", "ETH"])) is True
    assert d.should_emit(_payload(symbols=["ETH", "BTC"])) is False


def test_symbols_accepts_json_string():
    """tg-ingest factors carry symbols as a JSON list string."""
    d = SignalDeduper()
    assert d.should_emit(_payload(symbols='["BTC","ETH"]')) is True
    assert d.should_emit(_payload(symbols='["BTC","ETH"]')) is False


def test_direction_sign_no_longer_partitions():
    """US-006: opposite-direction signed events of the same type share a key —
    the same news forwarded by sources that signed it differently is one event.
    The higher-confidence one supersedes; the lower-confidence one dedups."""
    d = SignalDeduper()
    assert d.should_emit(_payload(direction=0.8, confidence=0.4)) is True
    assert d.should_emit(_payload(direction=-0.8, confidence=0.3)) is False
    assert d.should_emit(_payload(direction=-0.8, confidence=0.9)) is True


def test_neutral_events_never_deduped():
    """US-006: two neutral (direction=0) same-type events within the window are
    distinct observations — never misjudged as duplicates."""
    d = SignalDeduper()
    assert d.should_emit(_payload(direction=0.0, confidence=0.8)) is True
    assert d.should_emit(_payload(direction=0.0, confidence=0.4)) is True
    assert d.should_emit(_payload(direction=0.0, confidence=0.9)) is True


def test_neutral_events_not_cached():
    """Neutral bypass must not pollute the key cache (no per-event keys)."""
    d = SignalDeduper()
    d.should_emit(_payload(direction=0.0))
    d.should_emit(_payload(direction=0.0))
    stats = d.stats()
    assert stats["keys"] == 0
    assert stats["total_entries"] == 0


def test_window_seconds_zero_disables_dedup():
    d = SignalDeduper(window_seconds=0)
    assert d.should_emit(_payload()) is True
    assert d.should_emit(_payload()) is True


def test_window_expiry(monkeypatch):
    d = SignalDeduper(window_seconds=300)
    fake_time = [1_000_000.0]

    monkeypatch.setattr("selene_space_tg_ingest.signal_dedup.time.time",
                        lambda: fake_time[0])
    assert d.should_emit(_payload()) is True

    # Advance past the window → the old entry expires → emits again
    fake_time[0] += 301
    assert d.should_emit(_payload()) is True


def test_stats():
    d = SignalDeduper()
    d.should_emit(_payload())
    d.should_emit(_payload(symbols='["ETH"]'))
    stats = d.stats()
    assert stats["keys"] == 2
    assert stats["total_entries"] == 2
