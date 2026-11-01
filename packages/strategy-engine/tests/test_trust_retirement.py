"""Tests for trust demotion/retirement paths (P0-2).

Covers: consecutive-failure demotion cascade, re-entry cooldown blocking
(auto-promotion and explicit upgrades), observe floor, success resetting the
failure streak, and idempotent schema migration for legacy DBs.
"""

from __future__ import annotations

import sqlite3
import time

import pytest

from selene_space_strategy.trust import (
    _DEMOTION_FAILURES,
    _PROMOTION_STREAK,
    _REENTRY_COOLDOWN_SECONDS,
    StrategyTrustTracker,
)


def _demote(tracker: StrategyTrustTracker, sid: str, start_ts: float) -> float:
    """Record _DEMOTION_FAILURES consecutive failures; returns next ts."""
    ts = start_ts
    for _ in range(_DEMOTION_FAILURES):
        tracker.record_strategy_outcome(sid, False, ts=ts)
        ts += 1.0
    return ts


class TestFreshDefaults:
    def test_unknown_strategy_is_observe_with_zero_counters(self, tmp_path) -> None:
        tracker = StrategyTrustTracker(db_path=tmp_path / "trust.db")
        rec = tracker.get_record("new-strategy")
        assert rec.level == "observe"
        assert rec.consecutive_failures == 0
        assert rec.consecutive_successes == 0
        assert rec.demoted_at is None
        assert rec.reentry_eligible_at is None


class TestFailureStreak:
    def test_four_failures_keep_level(self, tmp_path) -> None:
        tracker = StrategyTrustTracker(db_path=tmp_path / "trust.db")
        tracker.set_trust_level("s", "trusted")
        for i in range(_DEMOTION_FAILURES - 1):
            tracker.record_strategy_outcome("s", False, ts=1000.0 + i)
        assert tracker.get_trust_level("s") == "trusted"

    def test_fifth_failure_demotes_one_level(self, tmp_path) -> None:
        tracker = StrategyTrustTracker(db_path=tmp_path / "trust.db")
        tracker.set_trust_level("s", "trusted")
        ts = 1000.0
        for _ in range(_DEMOTION_FAILURES - 1):
            tracker.record_strategy_outcome("s", False, ts=ts)
            ts += 1.0
        rec = tracker.record_strategy_outcome("s", False, ts=ts)
        assert rec.level == "limited"
        assert rec.consecutive_failures == 0  # streak reset after demotion
        assert rec.demoted_at == ts
        assert rec.reentry_eligible_at == ts + _REENTRY_COOLDOWN_SECONDS

    def test_cascade_down_to_observe_then_floor(self, tmp_path) -> None:
        tracker = StrategyTrustTracker(db_path=tmp_path / "trust.db")
        sid = "cascade"
        tracker.set_trust_level(sid, "trusted")
        ts = _demote(tracker, sid, 10_000.0)
        assert tracker.get_trust_level(sid) == "limited"
        ts = _demote(tracker, sid, ts)
        assert tracker.get_trust_level(sid) == "confirm"
        ts = _demote(tracker, sid, ts)
        assert tracker.get_trust_level(sid) == "shadow"
        ts = _demote(tracker, sid, ts)
        assert tracker.get_trust_level(sid) == "observe"
        # observe is the floor: further failure streaks never drop below
        ts = _demote(tracker, sid, ts)
        assert tracker.get_trust_level(sid) == "observe"
        rec = tracker.get_record(sid)
        assert rec.consecutive_failures >= _DEMOTION_FAILURES  # saturates at floor

    def test_success_resets_failure_streak(self, tmp_path) -> None:
        tracker = StrategyTrustTracker(db_path=tmp_path / "trust.db")
        tracker.set_trust_level("s", "limited")
        ts = 2000.0
        for _ in range(_DEMOTION_FAILURES - 1):
            tracker.record_strategy_outcome("s", False, ts=ts)
            ts += 1.0
        assert tracker.get_record("s").consecutive_failures == _DEMOTION_FAILURES - 1
        rec = tracker.record_strategy_outcome("s", True, ts=ts)
        assert rec.consecutive_failures == 0
        assert rec.consecutive_successes == 1


class TestReentryCooldown:
    def test_cooldown_blocks_auto_promotion(self, tmp_path) -> None:
        tracker = StrategyTrustTracker(db_path=tmp_path / "trust.db")
        sid = "promo"
        tracker.set_trust_level(sid, "trusted")
        _ = _demote(tracker, sid, 30_000.0)
        assert tracker.get_trust_level(sid) == "limited"
        cooldown_end = tracker.get_record(sid).reentry_eligible_at
        assert cooldown_end is not None
        # streak accrues during cooldown but promotion stays blocked
        during = cooldown_end - 60.0
        for _ in range(_PROMOTION_STREAK):
            rec = tracker.record_strategy_outcome(sid, True, ts=during)
        assert rec.level == "limited"
        assert rec.consecutive_successes == _PROMOTION_STREAK
        # once the cooldown passes, the next success promotes
        rec = tracker.record_strategy_outcome(sid, True, ts=cooldown_end + 1.0)
        assert rec.level == "trusted"

    def test_cooldown_blocks_explicit_upgrade_but_keeps_downgrade(self, tmp_path) -> None:
        tracker = StrategyTrustTracker(db_path=tmp_path / "trust.db")
        sid = "explicit"
        tracker.set_trust_level(sid, "trusted")
        # base the demotion on real now so the cooldown window is in the future
        # relative to set_trust_level's internal time.time()
        _ = _demote(tracker, sid, time.time())
        assert tracker.get_trust_level(sid) == "limited"
        with pytest.raises(ValueError):
            tracker.set_trust_level(sid, "trusted")
        # explicit downgrade remains legal during cooldown
        tracker.set_trust_level(sid, "confirm")
        assert tracker.get_trust_level(sid) == "confirm"
        # a level change clears the cooldown, so upgrade is allowed again
        tracker.set_trust_level(sid, "trusted")
        assert tracker.get_trust_level(sid) == "trusted"

    def test_same_level_set_does_not_clear_cooldown(self, tmp_path) -> None:
        tracker = StrategyTrustTracker(db_path=tmp_path / "trust.db")
        sid = "same"
        tracker.set_trust_level(sid, "trusted")
        _ = _demote(tracker, sid, 50_000.0)
        cooldown_end = tracker.get_record(sid).reentry_eligible_at
        tracker.set_trust_level(sid, "limited")  # no-op: same level
        assert tracker.get_record(sid).reentry_eligible_at == cooldown_end


class TestSchemaMigration:
    def test_fresh_schema_has_all_columns(self, tmp_path) -> None:
        tracker = StrategyTrustTracker(db_path=tmp_path / "trust.db")
        with tracker._connect() as con:
            cols = {row[1] for row in con.execute("PRAGMA table_info(trust_state)")}
        assert {
            "strategy_id", "level", "consecutive_successes", "limited_run_days",
            "last_outcome_at", "updated_at", "consecutive_failures",
            "demoted_at", "reentry_eligible_at",
        } <= cols

    def test_legacy_db_migrates_idempotently(self, tmp_path) -> None:
        db = tmp_path / "trust.db"
        with sqlite3.connect(str(db)) as con:
            con.execute(
                """
                CREATE TABLE trust_state (
                    strategy_id TEXT PRIMARY KEY,
                    level TEXT NOT NULL,
                    consecutive_successes INTEGER NOT NULL DEFAULT 0,
                    limited_run_days INTEGER NOT NULL DEFAULT 0,
                    last_outcome_at REAL,
                    updated_at REAL NOT NULL
                )
                """
            )
            con.execute(
                "INSERT INTO trust_state VALUES (?, ?, ?, ?, ?, ?)",
                ("legacy", "limited", 7, 3, 100.0, 100.0),
            )
        tracker = StrategyTrustTracker(db_path=db)
        with tracker._connect() as con:
            cols = {row[1] for row in con.execute("PRAGMA table_info(trust_state)")}
        assert "consecutive_failures" in cols
        assert "demoted_at" in cols
        assert "reentry_eligible_at" in cols
        # existing row survives with defaults for the new columns
        rec = tracker.get_record("legacy")
        assert rec.level == "limited"
        assert rec.consecutive_successes == 7
        assert rec.consecutive_failures == 0
        assert rec.reentry_eligible_at is None
        # running the constructor again is harmless (idempotent)
        StrategyTrustTracker(db_path=db)
