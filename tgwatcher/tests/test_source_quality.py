"""Unit tests for SourceQualityTracker (Task D1-D3 skeleton).

Verifies:
- accumulate() ingests outcomes and updates per-chat stats
- stats(chat_id=...) returns single chat, stats() returns all
- direction_distribution counts -1/0/+1 correctly
- avg_magnitude_pct is mean of absolute values
- last_outcome_at normalized to ISO string
- to_dict() returns the /api/signals/source-quality response shape
- Missing chat_id is safely skipped
- Empty tracker returns zero stats (skeleton state — current production)
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tgwatcher.source_quality import SourceQualityTracker


def _make_outcome(
    chat_id: int = 2234543601,
    message_id: int = 100,
    actual_direction: int | None = 1,
    magnitude_pct: float | None = 2.5,
    reported_at: datetime | None = None,
) -> dict:
    return {
        "message_id": message_id,
        "chat_id": chat_id,
        "actual_direction": actual_direction,
        "magnitude_pct": magnitude_pct,
        "time_horizon_min": 60,
        "reported_at": reported_at or datetime(2026, 7, 20, 10, 0, tzinfo=timezone.utc),
    }


class TestAccumulate:
    def test_first_outcome_initializes_stats(self):
        t = SourceQualityTracker()
        t.accumulate(_make_outcome(actual_direction=1, magnitude_pct=2.5))
        s = t.stats(chat_id=2234543601)
        assert s["outcome_count"] == 1
        assert s["direction_distribution"]["1"] == 1
        assert s["avg_magnitude_pct"] == 2.5
        assert s["last_outcome_at"] is not None

    def test_multiple_outcomes_accumulate(self):
        t = SourceQualityTracker()
        for d, m in [(1, 2.0), (-1, 3.0), (1, 1.0), (0, 0.0)]:
            t.accumulate(_make_outcome(actual_direction=d, magnitude_pct=m))
        s = t.stats(chat_id=2234543601)
        assert s["outcome_count"] == 4
        assert s["direction_distribution"] == {"-1": 1, "0": 1, "1": 2}
        # |2|+|3|+|1|+|0| = 6, /4 = 1.5
        assert s["avg_magnitude_pct"] == pytest.approx(1.5, abs=1e-4)

    def test_missing_chat_id_skipped(self):
        t = SourceQualityTracker()
        o = _make_outcome()
        del o["chat_id"]
        t.accumulate(o)  # no crash
        assert t.to_dict()["total_outcomes"] == 0

    def test_none_magnitude_skipped(self):
        t = SourceQualityTracker()
        t.accumulate(_make_outcome(magnitude_pct=None))
        s = t.stats(chat_id=2234543601)
        assert s["outcome_count"] == 1
        assert s["avg_magnitude_pct"] == 0.0  # no magnitudes recorded

    def test_per_chat_isolation(self):
        t = SourceQualityTracker()
        t.accumulate(_make_outcome(chat_id=1, magnitude_pct=1.0))
        t.accumulate(_make_outcome(chat_id=2, magnitude_pct=5.0))
        t.accumulate(_make_outcome(chat_id=2, magnitude_pct=3.0))
        s1 = t.stats(chat_id=1)
        s2 = t.stats(chat_id=2)
        assert s1["outcome_count"] == 1
        assert s2["outcome_count"] == 2
        assert s2["avg_magnitude_pct"] == pytest.approx(4.0, abs=1e-4)  # (5+3)/2


class TestStatsOutput:
    def test_unknown_chat_returns_empty(self):
        t = SourceQualityTracker()
        assert t.stats(chat_id=999) == {}

    def test_empty_tracker_to_dict(self):
        """Skeleton state — current production (Selene not integrated)."""
        t = SourceQualityTracker()
        snap = t.to_dict()
        assert snap["tracked_chats"] == 0
        assert snap["total_outcomes"] == 0
        assert snap["per_chat"] == {}

    def test_to_dict_shape(self):
        t = SourceQualityTracker()
        t.accumulate(_make_outcome(chat_id=100, magnitude_pct=2.0))
        snap = t.to_dict()
        assert snap["tracked_chats"] == 1
        assert snap["total_outcomes"] == 1
        assert "100" in snap["per_chat"]
        per_chat = snap["per_chat"]["100"]
        assert per_chat["outcome_count"] == 1
        assert per_chat["avg_magnitude_pct"] == 2.0
        assert "direction_distribution" in per_chat
        assert "last_outcome_at" in per_chat

    def test_last_outcome_at_is_iso_string(self):
        t = SourceQualityTracker()
        ts = datetime(2026, 7, 20, 10, 30, tzinfo=timezone.utc)
        t.accumulate(_make_outcome(reported_at=ts))
        s = t.stats(chat_id=2234543601)
        assert isinstance(s["last_outcome_at"], str)
        assert "+00:00" in s["last_outcome_at"]

    def test_naive_reported_at_gets_utc_stamped(self):
        """Defensive: naive datetimes get tz-stamped (mirrors 7ffeb1e pattern)."""
        t = SourceQualityTracker()
        ts = datetime(2026, 7, 20, 10, 30)  # naive
        t.accumulate(_make_outcome(reported_at=ts))
        s = t.stats(chat_id=2234543601)
        assert s["last_outcome_at"].endswith("+00:00")


class TestReset:
    def test_reset_clears_stats(self):
        t = SourceQualityTracker()
        t.accumulate(_make_outcome())
        assert t.to_dict()["total_outcomes"] == 1
        t.reset()
        assert t.to_dict()["total_outcomes"] == 0
