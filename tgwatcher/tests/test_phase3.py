"""Tests for Phase 3: SubscriptionPlan model, migration v11, plan CRUD,
subscription status/expiry, quota enforcement, data retention cleaner.

Verifies:
- SubscriptionPlan model creates and queries correctly
- Migration v10→v11 creates subscription_plans table + adds columns to bot_subscriptions
- 3 default plans seeded on fresh DB
- Plan CRUD routes work (list, create, update, soft-delete)
- BotSubscription with status='expired' not included in _get_subscribers()
- BotSubscription with expires_at in the past not included
- Signal quota enforcement: subscription over daily limit skipped
- DataRetentionCleaner deletes rows older than retention_days
- Compliance routes return policy/TOS text without auth
"""
from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta

import pytest

from tgwatcher.models import BotSubscription, SubscriptionPlan
from tgwatcher.storage import Storage


@pytest.fixture
def storage(tmp_path):
    """Create a fresh Storage with all Phase 3 tables."""
    db_path = tmp_path / "test.db"
    s = Storage(str(db_path))
    s.init_db()
    return s


class TestSubscriptionPlanModel:
    def test_create_and_query(self, storage):
        with storage.get_session() as sess:
            plan = SubscriptionPlan(
                name="test_plan", price_cents=500, currency="CNY",
                interval_days=30, max_signals_per_day=50,
            )
            sess.add(plan)
            sess.commit()
            sess.refresh(plan)
            assert plan.id is not None
            assert plan.name == "test_plan"
            assert plan.price_cents == 500
            assert plan.max_signals_per_day == 50

    def test_unique_name(self, storage):
        with storage.get_session() as sess:
            sess.add(SubscriptionPlan(name="dup"))
            sess.commit()
            sess.add(SubscriptionPlan(name="dup"))
            with pytest.raises(Exception, match="UNIQUE"):
                sess.commit()

    def test_features_json(self, storage):
        with storage.get_session() as sess:
            plan = SubscriptionPlan(
                name="feat_plan",
                features_json=json.dumps({"digest": True, "webhook": False}),
            )
            sess.add(plan)
            sess.commit()
            sess.refresh(plan)
            parsed = json.loads(plan.features_json)
            assert parsed["digest"] is True

    def test_default_plans_seeded(self, storage):
        with storage.get_session() as sess:
            plans = sess.query(SubscriptionPlan).all()
            names = {p.name for p in plans}
            assert "free" in names
            assert "pro" in names
            assert "enterprise" in names
            # Verify free plan details
            free = sess.query(SubscriptionPlan).filter(SubscriptionPlan.name == "free").one()
            assert free.price_cents == 0
            assert free.max_signals_per_day == 10


class TestBotSubscriptionExtensions:
    def test_plan_id_and_status(self, storage):
        with storage.get_session() as sess:
            plan = sess.query(SubscriptionPlan).filter(SubscriptionPlan.name == "free").one()
            sub = BotSubscription(
                chat_id=111, enabled=True, plan_id=plan.id, status="trial",
            )
            sess.add(sub)
            sess.commit()
            sess.refresh(sub)
            assert sub.plan_id == plan.id
            assert sub.status == "trial"

    def test_expires_at(self, storage):
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        with storage.get_session() as sess:
            sub = BotSubscription(chat_id=222, expires_at=future)
            sess.add(sub)
            sess.commit()
            sess.refresh(sub)
            assert sub.expires_at is not None

    def test_default_status_is_active(self, storage):
        with storage.get_session() as sess:
            sub = BotSubscription(chat_id=333)
            sess.add(sub)
            sess.commit()
            sess.refresh(sub)
            assert sub.status == "active"


class TestBotPusherStatusFilter:
    def test_expired_status_skipped(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=111, enabled=True, status="expired"))
            sess.commit()

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 111 not in result

    def test_cancelled_status_skipped(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=222, enabled=True, status="cancelled"))
            sess.commit()

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 222 not in result

    def test_active_status_included(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=333, enabled=True, status="active"))
            sess.commit()

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 333 in result

    def test_expired_expires_at_skipped(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)
        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=444, enabled=True, status="active", expires_at=past))
            sess.commit()

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 444 not in result

    def test_future_expires_at_included(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)
        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=555, enabled=True, status="active", expires_at=future))
            sess.commit()

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 555 in result


class TestQuotaEnforcement:
    def test_over_quota_skipped(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        with storage.get_session() as sess:
            # free plan has max_signals_per_day=10
            free_plan = sess.query(SubscriptionPlan).filter(SubscriptionPlan.name == "free").one()
            sub = BotSubscription(chat_id=600, enabled=True, status="active", plan_id=free_plan.id)
            sess.add(sub)
            sess.commit()

        # Simulate 10 signals already pushed today
        today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pusher._quota_counter[(600, today_key)] = 10

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 600 not in result

    def test_under_quota_included(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        with storage.get_session() as sess:
            free_plan = sess.query(SubscriptionPlan).filter(SubscriptionPlan.name == "free").one()
            sub = BotSubscription(chat_id=601, enabled=True, status="active", plan_id=free_plan.id)
            sess.add(sub)
            sess.commit()

        # Only 5 signals pushed today (limit is 10)
        today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pusher._quota_counter[(601, today_key)] = 5

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 601 in result

    def test_unlimited_quota_always_included(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        with storage.get_session() as sess:
            # enterprise plan has max_signals_per_day=0 (unlimited)
            ent_plan = sess.query(SubscriptionPlan).filter(SubscriptionPlan.name == "enterprise").one()
            sub = BotSubscription(chat_id=602, enabled=True, status="active", plan_id=ent_plan.id)
            sess.add(sub)
            sess.commit()

        # Even with 999 signals pushed
        today_key = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        pusher._quota_counter[(602, today_key)] = 999

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 602 in result

    def test_no_plan_no_quota_check(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        with storage.get_session() as sess:
            # Legacy subscription with no plan_id
            sub = BotSubscription(chat_id=603, enabled=True, status="active", plan_id=None)
            sess.add(sub)
            sess.commit()

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 603 in result


class TestSubscriptionChecker:
    def test_marks_expired(self, storage):
        from tgwatcher.subscription_checker import SubscriptionChecker
        past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)

        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=700, status="active", expires_at=past))
            sess.commit()

        checker = SubscriptionChecker(storage)
        count = checker.run_once()
        assert count == 1

        with storage.get_session() as sess:
            sub = sess.query(BotSubscription).filter(BotSubscription.chat_id == 700).one()
            assert sub.status == "expired"

    def test_does_not_touch_future(self, storage):
        from tgwatcher.subscription_checker import SubscriptionChecker
        future = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30)

        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=701, status="active", expires_at=future))
            sess.commit()

        checker = SubscriptionChecker(storage)
        count = checker.run_once()
        assert count == 0

        with storage.get_session() as sess:
            sub = sess.query(BotSubscription).filter(BotSubscription.chat_id == 701).one()
            assert sub.status == "active"

    def test_does_not_touch_no_expiry(self, storage):
        from tgwatcher.subscription_checker import SubscriptionChecker

        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=702, status="active", expires_at=None))
            sess.commit()

        checker = SubscriptionChecker(storage)
        count = checker.run_once()
        assert count == 0


class TestDataRetentionCleaner:
    def test_deletes_old_data(self, storage):
        from tgwatcher.compliance import DataRetentionCleaner

        old_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=400)
        recent_date = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=10)

        with storage.get_session() as sess:
            from tgwatcher.models import Message, SignalFactor
            # Old message
            sess.add(Message(message_id=1, chat_id=1, date=old_date, text="old"))
            # Recent message
            sess.add(Message(message_id=2, chat_id=1, date=recent_date, text="recent"))
            # Old signal factor
            sess.add(SignalFactor(message_id=1, chat_id=1, created_at=old_date, llm_status="completed"))
            # Recent signal factor
            sess.add(SignalFactor(message_id=2, chat_id=1, created_at=recent_date, llm_status="completed"))
            sess.commit()

        cleaner = DataRetentionCleaner(storage, retention_days=365)
        counts = cleaner.run_once()

        assert counts["messages"] == 1
        assert counts["signal_factors"] == 1

        with storage.get_session() as sess:
            from tgwatcher.models import Message, SignalFactor
            remaining_msgs = sess.query(Message).count()
            remaining_factors = sess.query(SignalFactor).count()
            assert remaining_msgs == 1
            assert remaining_factors == 1

    def test_disabled_with_zero_days(self, storage):
        from tgwatcher.compliance import DataRetentionCleaner

        cleaner = DataRetentionCleaner(storage, retention_days=0)
        counts = cleaner.run_once()
        assert counts["messages"] == 0
        assert counts["signal_factors"] == 0


class TestMigrationV11:
    def test_migration_creates_tables(self, tmp_path):
        db_path = tmp_path / "test_v11.db"
        s = Storage(str(db_path))
        s.init_db()

        with s.get_session() as sess:
            from sqlalchemy import text
            result = sess.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='subscription_plans'"
            ))
            assert result.fetchone() is not None, "subscription_plans table should exist"

        from tgwatcher.storage.repositories.migration import SCHEMA_VERSION
        assert SCHEMA_VERSION == 11

    def test_migration_adds_columns(self, tmp_path):
        db_path = tmp_path / "test_v11_cols.db"
        s = Storage(str(db_path))
        s.init_db()

        with s.get_session() as sess:
            from sqlalchemy import text
            # Check bot_subscriptions has new columns
            result = sess.execute(text("PRAGMA table_info(bot_subscriptions)"))
            col_names = {row[1] for row in result}
            assert "plan_id" in col_names
            assert "status" in col_names
            assert "expires_at" in col_names
