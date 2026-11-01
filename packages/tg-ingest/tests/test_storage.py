"""Smoke tests for selene_space_tg_ingest.storage.

Verifies SQLAlchemy ORM works: init_db, insert message, query, signal factor roundtrip.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))


def test_storage_init_creates_schema(tmp_path):
    from selene_space_tg_ingest.storage import Storage

    db = tmp_path / "tgwatcher.db"
    s = Storage(db_path=db)
    s.init_db()

    # Schema version row should be 7 (v7 = digests table)
    with s.engine.connect() as conn:
        from sqlalchemy import text
        row = conn.execute(text("SELECT version FROM schema_version")).fetchone()
        assert row[0] == 7

    # Tables exist
    with s.engine.connect() as conn:
        from sqlalchemy import inspect
        inspector = inspect(s.engine)
        tables = inspector.get_table_names()
    assert "chats" in tables
    assert "senders" in tables
    assert "messages" in tables
    assert "signal_factors" in tables
    assert "digests" in tables


def test_storage_insert_and_query_message(tmp_path):
    from selene_space_tg_ingest.storage import Storage
    from selene_space_tg_ingest.schemas import ParsedMessage

    db = tmp_path / "tgwatcher.db"
    s = Storage(db_path=db)
    s.init_db()

    msg = ParsedMessage(
        message_id=100,
        chat_id=1001,
        chat_title="Test Channel",
        chat_username="testchan",
        chat_type="channel",
        members=5000,
        sender_id=42,
        sender_name="Alice",
        sender_username="alice",
        text="SEC files lawsuit against exchange",
        date=datetime(2026, 7, 19, 12, 0, 0),
    )
    count = s.save_messages([msg])
    assert count == 1

    # Verify it was inserted
    result = s.query_messages(chat_id=1001, keyword="SEC")
    assert result["total"] == 1
    assert "SEC" in result["messages"][0]["text"]
    assert result["messages"][0]["chat_title"] == "Test Channel"


def test_storage_signal_factor_roundtrip(tmp_path):
    from selene_space_tg_ingest.storage import Storage

    db = tmp_path / "tgwatcher.db"
    s = Storage(db_path=db)
    s.init_db()

    factor = {
        "message_id": 200,
        "chat_id": 2001,
        "is_signal": True,
        "direction": -0.8,
        "magnitude": 0.7,
        "urgency": 0.9,
        "confidence": 0.85,
        "halflife_min": 120,
        "symbols": '["BTC"]',
        "event_type": "security",
        "reasoning": "SEC investigation triggers bearish spike",
        "llm_status": "completed",
        "llm_model": "deepseek-chat",
        "factor_version": 2,
    }
    s.save_signal_factor(factor)

    retrieved = s.get_signal_factor(200, 2001)
    assert retrieved is not None
    assert retrieved["direction"] == -0.8
    assert retrieved["event_type"] == "security"
    assert retrieved["is_signal"] is True


def test_storage_get_stats_empty(tmp_path):
    from selene_space_tg_ingest.storage import Storage

    db = tmp_path / "tgwatcher.db"
    s = Storage(db_path=db)
    s.init_db()

    stats = s.get_stats()
    assert stats["total_messages"] == 0
    assert stats["monitored_chats"] == 0


def test_storage_upsert_chat_is_idempotent(tmp_path):
    from selene_space_tg_ingest.storage import Storage
    from selene_space_tg_ingest.schemas import ParsedChat

    db = tmp_path / "tgwatcher.db"
    s = Storage(db_path=db)
    s.init_db()

    chat = ParsedChat(chat_id=3001, chat_title="First", chat_username="first")
    s.upsert_chat_from_parsed(chat)
    chat2 = ParsedChat(chat_id=3001, chat_title="Updated", chat_username="first")
    s.upsert_chat_from_parsed(chat2)

    chats = s.get_chats()
    assert len(chats) == 1
    assert chats[0]["chat_title"] == "Updated"
