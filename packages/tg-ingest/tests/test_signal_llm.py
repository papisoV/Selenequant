"""Tests for selene_space_tg_ingest.signal_llm hardening (v3 plan).

Covers:
  - REFUSAL_PATTERNS matching (Test 1-3)
  - _is_refusal lowercasing divergence (Test 4)
  - refine() None-content guard (Test 5)
  - refine_batch() refusal guard (Test 6)
  - raw logging on failure (Test 7)
  - WALSQLiteSession smoke test (Test 8)
"""
from __future__ import annotations

import logging
import sqlite3
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))

from selene_space_tg_ingest.signal_llm import (  # noqa: E402
    LLMConfig,
    LLMRefineError,
    REFUSAL_PATTERNS,
    SignalLLMClient,
    _is_refusal,
)
from selene_space_tg_ingest.tg_session import WALSQLiteSession  # noqa: E402


# ---------- Test 1: REFUSAL_PATTERNS is non-empty list of strings ----------

def test_refusal_patterns_is_non_empty_list():
    assert isinstance(REFUSAL_PATTERNS, list)
    assert len(REFUSAL_PATTERNS) >= 14
    for p in REFUSAL_PATTERNS:
        assert isinstance(p, str)
        assert len(p) > 0


# ---------- Test 2: _is_refusal matches known Chinese refusals ----------

@pytest.mark.parametrize("text", [
    "作为AI，我无法回答这个问题",
    "我不能提供涉及敏感内容的信息",
    "请换个话题吧",
    "我还没学习到这方面的知识",
])
def test_is_refusal_matches_chinese_patterns(text):
    assert _is_refusal(text) is True


# ---------- Test 3: _is_refusal does not match legitimate trading advice ----------

@pytest.mark.parametrize("text", [
    "BTC 看涨，建议逢低买入",
    "美联储加息 25 个基点，利空加密市场",
    "鲸鱼地址转账 5000 BTC 到交易所",
    "",
])
def test_is_refusal_does_not_match_legitimate(text):
    assert _is_refusal(text) is False


# ---------- Test 4: _is_refusal lowercases input (divergence from tgwatcher) ----------

def test_is_refusal_lowercases_input():
    assert _is_refusal("作为AI") is True
    assert _is_refusal("作为ai") is True


# ---------- Test 5: refine() guards None content ----------

def test_refine_raises_on_none_content():
    config = LLMConfig(base_url="http://localhost", api_key="test-key", max_retries=1)
    client = SignalLLMClient(config)
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = None
    mock_response.usage = None
    client._client.chat.completions.create = MagicMock(return_value=mock_response)
    with pytest.raises(LLMRefineError):
        client.refine("test message", {"matched_categories": [], "estimated_urgency": 0.5})


# ---------- Test 6: refine_batch() guards refusal ----------

def test_refine_batch_raises_on_refusal():
    config = LLMConfig(base_url="http://localhost", api_key="test-key", max_retries=1)
    client = SignalLLMClient(config)
    refusal_text = "作为AI，我无法分析这些消息"
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = refusal_text
    mock_response.usage = None
    client._client.chat.completions.create = MagicMock(return_value=mock_response)
    results = client.refine_batch(
        [("msg1", {"matched_categories": [], "estimated_urgency": 0.5})]
    )
    assert results == [None]


# ---------- Test 7: raw logging on failure includes raw content ----------

def test_refine_logs_raw_on_json_decode_error(caplog):
    config = LLMConfig(base_url="http://localhost", api_key="test-key", max_retries=1)
    client = SignalLLMClient(config)
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = "not valid json {{{"
    mock_response.usage = None
    client._client.chat.completions.create = MagicMock(return_value=mock_response)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(LLMRefineError):
            client.refine("test", {"matched_categories": [], "estimated_urgency": 0.5})
    assert any("raw=" in record.getMessage() for record in caplog.records)


# ---------- Test 8: WALSQLiteSession smoke test ----------

def test_wal_sqlite_session_smoke():
    with tempfile.TemporaryDirectory() as tmpdir:
        session_path = Path(tmpdir) / "test_session.db"
        session = WALSQLiteSession(str(session_path))
        cursor = session._cursor()
        assert isinstance(cursor, sqlite3.Cursor)
        cursor.execute("PRAGMA journal_mode")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"
        cursor.execute("PRAGMA busy_timeout")
        timeout = cursor.fetchone()[0]
        assert timeout == 30000
        cursor2 = session._cursor()
        assert isinstance(cursor2, sqlite3.Cursor)
        session.close()


# ---------- Test 9: semantic refusal in reasoning field (via _validate_response) ----------

def test_refine_raises_on_semantic_refusal_in_reasoning():
    """Tests that _validate_response catches refusal keywords in the reasoning field.

    Note: refine() pre-checks _is_refusal(raw) on the full JSON string before
    calling _validate_response, so this branch is only reachable when the
    refusal keyword appears in reasoning but nowhere else in the raw JSON
    (e.g. future English patterns like "I cannot" that aren't in the raw-text
    REFUSAL_PATTERNS list). This test directly invokes _validate_response to
    verify the reasoning-field check works in isolation.
    """
    config = LLMConfig(base_url="http://localhost", api_key="test-key", max_retries=1)
    client = SignalLLMClient(config)
    refusal_data = {
        "symbols": ["*"],
        "direction": 0,
        "magnitude": 0.1,
        "urgency": 0.1,
        "confidence": 0.3,
        "halflife_min": 60,
        "event_type": "other",
        "reasoning": "作为AI我无法分析这个话题",
    }
    with pytest.raises(LLMRefineError) as exc_info:
        client._validate_response(refusal_data)
    assert "semantic refusal" in str(exc_info.value).lower()
    assert exc_info.value.raw_response is not None


# ---------- Test 10: LLMRefineError carries raw_response attribute ----------

def test_llm_refine_error_carries_raw_response():
    err = LLMRefineError("test error", raw_response="raw text here")
    assert err.raw_response == "raw text here"
    err_no_raw = LLMRefineError("test error")
    assert err_no_raw.raw_response is None


# ---------- Test 11: refine_batch skips fallback on refusal ----------

def test_refine_batch_skips_fallback_on_refusal():
    config = LLMConfig(base_url="http://localhost", api_key="test-key", max_retries=1)
    client = SignalLLMClient(config)
    refusal_text = "作为AI，我无法分析"
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = refusal_text
    mock_response.usage = None
    client._client.chat.completions.create = MagicMock(return_value=mock_response)
    import time
    start = time.time()
    results = client.refine_batch([
        ("msg1", {"matched_categories": [], "estimated_urgency": 0.5}),
        ("msg2", {"matched_categories": [], "estimated_urgency": 0.5}),
    ])
    elapsed = time.time() - start
    assert results == [None, None]
    assert len(client._client.chat.completions.create.call_args_list) == 1, "Should not fall back to individual calls"


# ---------- Test 12: REFUSAL_PATTERNS loaded from env ----------

def test_refusal_patterns_loaded_from_env(monkeypatch):
    monkeypatch.setenv("SELENE_TG_REFUSAL_PATTERNS", "custom1,custom2,custom3")
    config = LLMConfig(base_url="http://localhost", api_key="test-key")
    client = SignalLLMClient(config)
    from selene_space_tg_ingest.signal_llm import REFUSAL_PATTERNS as current
    assert "custom1" in current
    assert "custom2" in current
    assert "custom3" in current
