"""Tests for half-life weighted digest aggregation.

Verifies:
- _signal_weight formula: 0.5^(age_minutes / halflife_min)
- Edge cases: missing halflife, zero halflife, missing message_date
- _aggregate weights newer signals higher than stale ones
- _aggregate high_conf ranking uses weight × confidence (not just confidence)
- _fetch_signals SQL JOINs messages table and filters by m.date (not created_at)
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from tgwatcher.digest import (
    DEFAULT_HALFLIFE_MIN,
    _aggregate,
    _fetch_signals,
    _signal_weight,
)


class TestSignalWeightFormula:
    def test_at_age_zero_weight_is_one(self):
        now = datetime(2026, 7, 25, 12, 0, 0)
        sig = {
            "message_date": now,
            "halflife_min": 60,
        }
        assert _signal_weight(sig, now) == pytest.approx(1.0)

    def test_at_halflife_weight_is_half(self):
        now = datetime(2026, 7, 25, 12, 0, 0)
        sig = {
            "message_date": now - timedelta(minutes=60),  # 1 halflife ago
            "halflife_min": 60,
        }
        assert _signal_weight(sig, now) == pytest.approx(0.5)

    def test_at_2x_halflife_weight_is_quarter(self):
        now = datetime(2026, 7, 25, 12, 0, 0)
        sig = {
            "message_date": now - timedelta(minutes=120),  # 2 halflives ago
            "halflife_min": 60,
        }
        assert _signal_weight(sig, now) == pytest.approx(0.25)

    def test_missing_halflife_uses_default(self):
        now = datetime(2026, 7, 25, 12, 0, 0)
        sig = {
            "message_date": now - timedelta(minutes=DEFAULT_HALFLIFE_MIN),
            "halflife_min": None,
        }
        assert _signal_weight(sig, now) == pytest.approx(0.5)

    def test_zero_halflife_uses_default_defensively(self):
        now = datetime(2026, 7, 25, 12, 0, 0)
        sig = {
            "message_date": now - timedelta(minutes=60),
            "halflife_min": 0,
        }
        # halflife=0 would cause divide-by-zero; fallback to default
        weight = _signal_weight(sig, now)
        # With default halflife=60 and age=60min, weight should be 0.5
        assert weight == pytest.approx(0.5)

    def test_message_date_as_iso_string(self):
        now = datetime(2026, 7, 25, 12, 0, 0)
        msg_date = now - timedelta(minutes=60)
        sig = {
            "message_date": msg_date.isoformat(),  # string, not datetime
            "halflife_min": 60,
        }
        assert _signal_weight(sig, now) == pytest.approx(0.5)

    def test_missing_message_date_falls_back_to_created_at(self):
        now = datetime(2026, 7, 25, 12, 0, 0)
        created = now - timedelta(minutes=60)
        sig = {
            "message_date": None,
            "created_at": created,
            "halflife_min": 60,
        }
        assert _signal_weight(sig, now) == pytest.approx(0.5)


class TestAggregateWeightedDirection:
    def test_old_low_dir_does_not_dominate_net_direction(self):
        """30h-old regulatory bearish signal (halflife=1440) should NOT be
        outweighed by 30min-old market noise (halflife=60) if both have same
        direction. Actually, old signal with long halflife has HIGHER weight
        than new signal with short halflife — that's the point."""
        now = datetime(2026, 7, 25, 12, 0, 0)
        # Old regulatory signal (30h ago, halflife 1440min = 24h)
        old_sig = {
            "message_date": now - timedelta(hours=30),
            "halflife_min": 1440,
            "direction": -1.0,
            "magnitude": 0.8,
            "urgency": 0.7,
            "confidence": 0.9,
            "symbols": '["BTC"]',
            "event_type": "regulatory",
            "reasoning": "regulatory crackdown",
            "created_at": now - timedelta(hours=30),
        }
        # New market signal (30min ago, halflife 60min)
        new_sig = {
            "message_date": now - timedelta(minutes=30),
            "halflife_min": 60,
            "direction": 1.0,
            "magnitude": 0.5,
            "urgency": 0.3,
            "confidence": 0.7,
            "symbols": '["BTC"]',
            "event_type": "market",
            "reasoning": "small bounce",
            "created_at": now - timedelta(minutes=30),
        }

        with patch("tgwatcher.digest.utc_now", return_value=now.replace(tzinfo=None)):
            agg = _aggregate([old_sig, new_sig], now - timedelta(hours=30), now)

        # Old sig weight: 0.5^(1800/1440) ≈ 0.42
        # New sig weight: 0.5^(30/60) = 0.707
        # New sig should dominate directionally
        assert agg["net_direction"] > 0  # positive because new bullish has more weight

    def test_equal_signals_different_age_weighted_differently(self):
        """Two identical-direction signals, one fresh one stale.
        Fresh should carry more weight in net_direction."""
        now = datetime(2026, 7, 25, 12, 0, 0)
        fresh = {
            "message_date": now - timedelta(minutes=10),
            "halflife_min": 60,
            "direction": 1.0,
            "magnitude": 0.5,
            "urgency": 0.3,
            "confidence": 0.5,
            "symbols": '["BTC"]',
            "event_type": "market",
            "reasoning": "fresh",
            "created_at": now - timedelta(minutes=10),
        }
        stale = {
            "message_date": now - timedelta(hours=20),
            "halflife_min": 60,
            "direction": -1.0,
            "magnitude": 0.5,
            "urgency": 0.3,
            "confidence": 0.5,
            "symbols": '["BTC"]',
            "event_type": "market",
            "reasoning": "stale",
            "created_at": now - timedelta(hours=20),
        }
        with patch("tgwatcher.digest.utc_now", return_value=now.replace(tzinfo=None)):
            agg = _aggregate([fresh, stale], now - timedelta(hours=20), now)
        # Fresh weight: 0.5^(10/60) ≈ 0.89
        # Stale weight: 0.5^(1200/60) ≈ 0 (essentially 0)
        # Net direction should be near +1.0 (fresh dominates)
        assert agg["net_direction"] > 0.8

    def test_total_weight_in_result(self):
        now = datetime(2026, 7, 25, 12, 0, 0)
        sig = {
            "message_date": now,
            "halflife_min": 60,
            "direction": 1.0,
            "magnitude": 0.5,
            "urgency": 0.3,
            "confidence": 0.5,
            "symbols": '["BTC"]',
            "event_type": "market",
            "reasoning": "x",
            "created_at": now,
        }
        with patch("tgwatcher.digest.utc_now", return_value=now.replace(tzinfo=None)):
            agg = _aggregate([sig], now - timedelta(hours=1), now)
        assert "total_weight" in agg
        assert agg["total_weight"] == pytest.approx(1.0)


class TestAggregateHighConfRanking:
    def test_old_high_conf_ranks_below_new_mid_conf(self):
        """Old signal with conf=0.95 should rank BELOW new signal with conf=0.6
        because weight × confidence is what matters, not just confidence."""
        now = datetime(2026, 7, 25, 12, 0, 0)
        old_high = {
            "message_date": now - timedelta(hours=20),
            "halflife_min": 60,
            "direction": 1.0,
            "magnitude": 0.5,
            "urgency": 0.3,
            "confidence": 0.95,
            "symbols": '["BTC"]',
            "event_type": "market",
            "reasoning": "old high conf",
            "created_at": now - timedelta(hours=20),
        }
        new_mid = {
            "message_date": now - timedelta(minutes=5),
            "halflife_min": 60,
            "direction": 1.0,
            "magnitude": 0.5,
            "urgency": 0.3,
            "confidence": 0.6,
            "symbols": '["ETH"]',
            "event_type": "market",
            "reasoning": "new mid conf",
            "created_at": now - timedelta(minutes=5),
        }
        with patch("tgwatcher.digest.utc_now", return_value=now.replace(tzinfo=None)):
            agg = _aggregate([old_high, new_mid], now - timedelta(hours=20), now)

        # Parse high_confidence_events — first line should mention ETH (new_mid)
        events_text = agg["high_confidence_events"]
        first_line = events_text.split("\n")[0]
        assert "ETH" in first_line
        assert "BTC" not in first_line

    def test_high_conf_events_include_weight_field(self):
        now = datetime(2026, 7, 25, 12, 0, 0)
        sig = {
            "message_date": now - timedelta(minutes=30),
            "halflife_min": 60,
            "direction": 1.0,
            "magnitude": 0.5,
            "urgency": 0.3,
            "confidence": 0.8,
            "symbols": '["BTC"]',
            "event_type": "market",
            "reasoning": "test",
            "created_at": now - timedelta(minutes=30),
        }
        with patch("tgwatcher.digest.utc_now", return_value=now.replace(tzinfo=None)):
            agg = _aggregate([sig], now - timedelta(hours=1), now)
        assert "w=0." in agg["high_confidence_events"]


class TestAggregateEmpty:
    def test_empty_signals_returns_empty_dict(self):
        now = datetime(2026, 7, 25, 12, 0, 0)
        agg = _aggregate([], now - timedelta(hours=1), now)
        assert agg == {"empty": True}


class TestFetchSignalsSqlJoin:
    """Verify _fetch_signals JOINs messages and filters by m.date not created_at."""

    def test_fetch_sql_joins_messages_and_filters_by_message_date(self, tmp_path, monkeypatch):
        # Create a SQLite DB with signal_factors + messages
        import sqlite3
        db_path = tmp_path / "test.db"
        con = sqlite3.connect(db_path)
        cur = con.cursor()
        cur.execute("""
            CREATE TABLE signal_factors (
                message_id INTEGER, chat_id INTEGER, direction REAL,
                magnitude REAL, urgency REAL, confidence REAL,
                halflife_min INTEGER, symbols TEXT, event_type TEXT,
                reasoning TEXT, created_at TEXT, is_signal INTEGER,
                llm_status TEXT
            )
        """)
        cur.execute("""
            CREATE TABLE messages (
                message_id INTEGER, chat_id INTEGER, date TEXT, text TEXT
            )
        """)
        # Insert: a message sent 2 days ago but LLM-processed today
        # (created_at=today, m.date=2 days ago). Should NOT appear in 36h window
        # when filtering by m.date.
        cur.execute(
            "INSERT INTO signal_factors "
            "(message_id, chat_id, direction, magnitude, urgency, confidence, "
            "halflife_min, symbols, event_type, reasoning, created_at, "
            "is_signal, llm_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (1, 100, 1.0, 0.5, 0.3, 0.8, 60, '["BTC"]', 'market', 'r',
             "2026-07-25T10:00:00", 1, "completed"),
        )
        cur.execute(
            "INSERT INTO messages VALUES (?,?,?,?)",
            (1, 100, "2026-07-23T10:00:00", "msg1"),  # 2 days old
        )
        # And a fresh message (within 36h)
        cur.execute(
            "INSERT INTO signal_factors "
            "(message_id, chat_id, direction, magnitude, urgency, confidence, "
            "halflife_min, symbols, event_type, reasoning, created_at, "
            "is_signal, llm_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (2, 100, -0.5, 0.6, 0.4, 0.7, 60, '["ETH"]', 'regulatory', 'r2',
             "2026-07-25T11:00:00", 1, "completed"),
        )
        cur.execute(
            "INSERT INTO messages VALUES (?,?,?,?)",
            (2, 100, "2026-07-25T08:00:00", "msg2"),  # 4h old
        )
        con.commit()
        con.close()

        storage = MagicMock()
        storage.engine.url = f"sqlite:///{db_path}"

        from datetime import datetime as dt
        from_at = dt(2026, 7, 24, 12, 0, 0)
        to_at = dt(2026, 7, 25, 12, 0, 0)

        signals = _fetch_signals(storage, from_at, to_at)

        # Only msg2 (m.date=2026-07-25T08:00:00) is in [from_at, to_at]
        # msg1 (m.date=2026-07-23T10:00:00) is OUTSIDE the window
        assert len(signals) == 1
        assert signals[0]["message_id"] == 2
        assert signals[0]["message_date"] == "2026-07-25T08:00:00"
