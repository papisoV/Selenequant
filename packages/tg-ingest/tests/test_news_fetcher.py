"""Tests for selene_space_tg_ingest.news_fetcher.

Covers:
  - _normalize_entry produces {message_id, chat_id, text, date}
  - message_id is stable (same link → same id)
  - dedupe: re-fetch of same entry returns no new messages
  - fetch_once with mock feedparser returns normalized entries
  - run_forever disabled config exits immediately
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock


REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "packages" / "tg-ingest"))

from selene_space_tg_ingest.news_fetcher import (  # noqa: E402
    NewsConfig,
    NewsFetcher,
    _DEFAULT_SOURCES,
    load_news_config_from_env,
)


def _make_entry(link: str, title: str, summary: str = "", published: str = ""):
    """Create a feedparser-like entry object."""
    entry = MagicMock()
    entry.link = link
    entry.title = title
    entry.summary = summary
    entry.published = published
    entry.published_parsed = None
    return entry


def _make_feedparser(entries: list):
    """Return a mock feedparser module with parse() returning entries."""
    fp = MagicMock()
    parsed = MagicMock()
    parsed.entries = entries
    fp.parse.return_value = parsed
    return fp


# ---------- Test 1: _normalize_entry produces required fields ----------

def test_normalize_entry_produces_required_fields():
    cfg = NewsConfig(enabled=True)
    fetcher = NewsFetcher(cfg)
    fetcher._feedparser = _make_feedparser([])
    source = _DEFAULT_SOURCES[0]
    entry = _make_entry("https://example.com/post1", "BTC pumps 5%", "Bitcoin surged on ETF flows")
    msg = fetcher._normalize_entry(entry, source)
    assert msg is not None
    assert msg["chat_id"] == source.chat_id  # -1
    assert msg["message_id"] > 0
    assert "BTC pumps 5%" in msg["text"]
    assert "Bitcoin surged" in msg["text"]
    assert msg["source"] == source.name
    assert msg["link"] == "https://example.com/post1"


# ---------- Test 2: message_id is stable (same link → same id) ----------

def test_message_id_stable_across_normalizations():
    cfg = NewsConfig(enabled=True)
    fetcher = NewsFetcher(cfg)
    fetcher._feedparser = _make_feedparser([])
    source = _DEFAULT_SOURCES[0]
    e1 = _make_entry("https://example.com/post1", "Title A", "Summary A")
    e2 = _make_entry("https://example.com/post1", "Title B", "Summary B")
    m1 = fetcher._normalize_entry(e1, source)
    m2 = fetcher._normalize_entry(e2, source)
    assert m1["message_id"] == m2["message_id"]


# ---------- Test 3: dedupe (re-fetch returns no new messages) ----------

def test_dedupe_re_fetch_returns_no_new():
    cfg = NewsConfig(enabled=True)
    fetcher = NewsFetcher(cfg)
    fetcher._feedparser = _make_feedparser([
        _make_entry("https://example.com/post1", "BTC up"),
        _make_entry("https://example.com/post2", "ETH down"),
    ])
    source = _DEFAULT_SOURCES[0]
    first = fetcher.fetch_once(source)
    assert len(first) == 2
    second = fetcher.fetch_once(source)  # same entries, should dedupe
    assert len(second) == 0


# ---------- Test 4: fetch_once with empty feed ----------

def test_fetch_once_empty_feed():
    cfg = NewsConfig(enabled=True)
    fetcher = NewsFetcher(cfg)
    fetcher._feedparser = _make_feedparser([])
    msgs = fetcher.fetch_once(_DEFAULT_SOURCES[0])
    assert msgs == []


# ---------- Test 5: run_forever disabled config exits ----------

def test_run_forever_disabled_exits():
    cfg = NewsConfig(enabled=False)
    fetcher = NewsFetcher(cfg)
    # Should return immediately without calling process_message_fn
    called = []
    fetcher.run_forever(lambda m: called.append(m))
    assert called == []


# ---------- Test 6: load_news_config_from_env ----------

def test_load_news_config_from_env(monkeypatch):
    monkeypatch.setenv("SELENE_NEWS_ENABLED", "1")
    monkeypatch.setenv("SELENE_NEWS_POLL_INTERVAL", "60")
    cfg = load_news_config_from_env()
    assert cfg.enabled is True
    assert cfg.poll_interval == 60
    assert len(cfg.sources) == 3
    assert cfg.sources[0].chat_id == -1  # coindesk
    assert cfg.sources[1].chat_id == -2  # cointelegraph
    assert cfg.sources[2].chat_id == -3  # cryptoslate


# ---------- Test 7: entry with no link returns None ----------

def test_normalize_entry_no_link_returns_none():
    cfg = NewsConfig(enabled=True)
    fetcher = NewsFetcher(cfg)
    fetcher._feedparser = _make_feedparser([])
    entry = MagicMock()
    entry.link = None
    entry.id = None
    entry.title = "Some title"
    entry.summary = "Some summary"
    msg = fetcher._normalize_entry(entry, _DEFAULT_SOURCES[0])
    assert msg is None


# ---------- Test 8: synthetic chat_ids are negative ----------

def test_default_sources_have_negative_chat_ids():
    for src in _DEFAULT_SOURCES:
        assert src.chat_id < 0, f"{src.name} has non-negative chat_id {src.chat_id}"
