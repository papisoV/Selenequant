"""Smoke tests for selene_space_tg_ingest zero-dependency modules.

Verifies imports work and basic keyword filtering classifies messages correctly.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))


def test_schemas_parsed_message_to_dict_roundtrip():
    from selene_space_tg_ingest.schemas import ParsedMessage, ParsedChat

    msg = ParsedMessage(
        message_id=1,
        chat_id=100,
        chat_title="Test",
        chat_username="test",
        chat_type="channel",
        members=5000,
        sender_id=42,
        sender_name="Alice",
        text="hello",
    )
    d = msg.to_dict()
    assert d["message_id"] == 1
    assert d["chat_title"] == "Test"
    assert d["text"] == "hello"
    # chat property returns ParsedChat
    assert isinstance(msg.chat, ParsedChat)
    assert msg.chat.chat_id == 100


def test_tz_utils_utc_now_is_naive():
    from selene_space_tg_ingest.tz_utils import utc_now, tz_offset_hours, set_tz_offset

    now = utc_now()
    assert now.tzinfo is None, "utc_now must return naive datetime (SQLite convention)"
    assert isinstance(tz_offset_hours(), int)
    # Setting offset shouldn't break anything
    set_tz_offset(8)
    assert tz_offset_hours() == 8


def test_keyword_filter_passes_security_message():
    from selene_space_tg_ingest.signal_filter import KeywordFilter

    kf = KeywordFilter()
    result = kf.filter("SEC files lawsuit against major exchange for compliance violations")
    assert result.passed
    assert len(result.matched_keywords) > 0
    # Should match event_regulatory (SEC, 监管, 合规, etc.)
    assert "event_regulatory" in result.preliminary_factors["matched_categories"]


def test_keyword_filter_rejects_short_text():
    from selene_space_tg_ingest.signal_filter import KeywordFilter

    kf = KeywordFilter()
    result = kf.filter("hi")
    assert not result.passed
    assert result.matched_keywords == []


def test_keyword_filter_rejects_none_and_empty():
    from selene_space_tg_ingest.signal_filter import KeywordFilter

    kf = KeywordFilter()
    assert not kf.filter(None).passed
    assert not kf.filter("").passed


def test_keyword_filter_detects_high_urgency():
    from selene_space_tg_ingest.signal_filter import KeywordFilter

    kf = KeywordFilter()
    result = kf.filter("紧急: 暴跌! 黑客被盗!")
    assert result.passed
    assert "urgency_high" in result.preliminary_factors["matched_categories"]
    assert result.preliminary_factors["estimated_urgency"] == "high"


def test_keyword_filter_uses_custom_config():
    from selene_space_tg_ingest.signal_filter import KeywordFilter

    custom_config = {
        "min_text_length": 5,
        "keywords": {
            "custom_category": ["alpha", "beta"],
        },
    }
    kf = KeywordFilter(config=custom_config)
    result = kf.filter("this message has alpha keyword")
    assert result.passed
    assert "custom_category" in result.preliminary_factors["matched_categories"]
    assert "alpha" in result.matched_keywords
