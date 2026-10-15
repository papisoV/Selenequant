"""Tests for BotSubscription model, migration, and BotPusher subscription filtering.

Verifies:
- BotSubscription model creates and queries correctly
- Migration v9→v10 creates bot_subscriptions table
- BotPusher._get_subscribers filters by min_score and event_types
- BotPusher falls back to static chat_ids when no storage
- Signal with score 0.3 not pushed to subscriber with min_score=0.5
- Signal with event_type='whale' not pushed to subscriber filtered to ['market']
"""
from __future__ import annotations

import json

import pytest

from tgwatcher.models import BotSubscription
from tgwatcher.storage import Storage


@pytest.fixture
def storage(tmp_path):
    """Create a fresh Storage with bot_subscriptions table."""
    db_path = tmp_path / "test.db"
    s = Storage(str(db_path))
    s.init_db()
    return s


class TestBotSubscriptionModel:
    def test_create_and_query(self, storage):
        with storage.get_session() as sess:
            sub = BotSubscription(chat_id=123456, enabled=True, min_score=0.3)
            sess.add(sub)
            sess.commit()
            sess.refresh(sub)
            assert sub.id is not None
            assert sub.chat_id == 123456
            assert sub.min_score == 0.3

    def test_unique_chat_id(self, storage):
        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=999))
            sess.commit()
            sess.add(BotSubscription(chat_id=999))
            with pytest.raises(Exception, match="UNIQUE"):
                sess.commit()

    def test_event_types_json(self, storage):
        with storage.get_session() as sess:
            sub = BotSubscription(
                chat_id=111,
                event_types=json.dumps(["market", "whale"]),
            )
            sess.add(sub)
            sess.commit()
            sess.refresh(sub)
            parsed = json.loads(sub.event_types)
            assert parsed == ["market", "whale"]

    def test_null_event_types_means_all(self, storage):
        with storage.get_session() as sess:
            sub = BotSubscription(chat_id=222, event_types=None)
            sess.add(sub)
            sess.commit()
            sess.refresh(sub)
            assert sub.event_types is None


class TestBotPusherSubscriptionFiltering:
    def test_filters_by_min_score(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        # Add subscriber with min_score=0.5
        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=111, enabled=True, min_score=0.5))
            sess.commit()

        # Signal with score 0.3 should NOT match
        payload_low = {"signal_score": 0.3, "event_type": "market"}
        result = pusher._get_subscribers(payload_low)
        assert 111 not in result

        # Signal with score 0.6 should match
        payload_high = {"signal_score": 0.6, "event_type": "market"}
        result = pusher._get_subscribers(payload_high)
        assert 111 in result

    def test_filters_by_event_types(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        # Add subscriber filtered to ['market']
        with storage.get_session() as sess:
            sess.add(BotSubscription(
                chat_id=222, enabled=True,
                event_types=json.dumps(["market"]),
            ))
            sess.commit()

        # Whale event should NOT match
        payload_whale = {"signal_score": 0.5, "event_type": "whale"}
        result = pusher._get_subscribers(payload_whale)
        assert 222 not in result

        # Market event should match
        payload_market = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload_market)
        assert 222 in result

    def test_null_event_types_accepts_all(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=333, enabled=True, event_types=None))
            sess.commit()

        for etype in ["market", "whale", "security", "other"]:
            payload = {"signal_score": 0.5, "event_type": etype}
            result = pusher._get_subscribers(payload)
            assert 333 in result, f"Should accept event_type={etype}"

    def test_disabled_subscriber_skipped(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=444, enabled=False))
            sess.commit()

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 444 not in result

    def test_falls_back_to_static_chat_ids(self):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc", "chat_ids": [100, 200]}})
        # No storage set — should use static chat_ids
        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert result == [100, 200]

    def test_multiple_subscribers(self, storage):
        from tgwatcher.bot_push import BotPusher
        pusher = BotPusher({"bot": {"enabled": True, "token": "test:abc"}})
        pusher.set_storage(storage)

        with storage.get_session() as sess:
            sess.add(BotSubscription(chat_id=501, enabled=True, min_score=0.0))
            sess.add(BotSubscription(chat_id=502, enabled=True, min_score=0.3))
            sess.add(BotSubscription(chat_id=503, enabled=True, min_score=0.7))
            sess.commit()

        payload = {"signal_score": 0.5, "event_type": "market"}
        result = pusher._get_subscribers(payload)
        assert 501 in result
        assert 502 in result
        assert 503 not in result  # min_score=0.7 > 0.5


class TestMigrationV10:
    def test_migration_creates_table(self, tmp_path):
        """Verify migration v9→v10 creates bot_subscriptions table."""
        db_path = tmp_path / "test_v10.db"
        s = Storage(str(db_path))
        s.init_db()

        with s.get_session() as sess:
            from sqlalchemy import text
            result = sess.execute(text(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_subscriptions'"
            ))
            assert result.fetchone() is not None, "bot_subscriptions table should exist"

        from tgwatcher.storage.repositories.migration import SCHEMA_VERSION
        assert SCHEMA_VERSION >= 10
