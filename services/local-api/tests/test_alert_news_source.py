"""``voice`` rules: news keyword matching, and the window that keeps it honest.

``voice`` is a historical misnomer — the rule holds a ``keyword`` and matches
news text. Two failure modes matter more than the happy path:

* a store outage must read as "no data", never as "no news matched", or the
  rule silently stops monitoring behind a green health check;
* without a time window, one historical article matches every single round and
  the rule is armed forever.
"""

from __future__ import annotations

from typing import Any

from app.core.alert_sources import make_news_lookup


class _Store:
    def __init__(self, rows=None, raises: bool = False):
        self._rows = rows if rows is not None else []
        self._raises = raises
        self.calls: list[dict[str, Any]] = []

    def query_news(self, **kwargs):
        self.calls.append(kwargs)
        if self._raises:
            raise RuntimeError("db locked")
        return self._rows


def _rule(keyword="爆仓", rule_id="v1"):
    return {"id": rule_id, "type": "voice", "threshold": {"keyword": keyword}}


def test_keyword_match_returns_count_and_newest_title():
    store = _Store(
        [
            {"title": "某所爆仓 3 亿", "fetched_at_ms": 5000},
            {"title": "旧闻", "fetched_at_ms": 4000},
        ]
    )
    lookup = make_news_lookup(store, now_ms=lambda: 10_000)
    assert lookup(_rule()) == (2, "某所爆仓 3 亿")


def test_query_is_bounded_by_a_lookback_window():
    store = _Store()
    lookup = make_news_lookup(store, lookback_ms=3_600_000, now_ms=lambda: 10_000_000)
    lookup(_rule())
    assert store.calls[0]["fetched_after_ms"] == 10_000_000 - 3_600_000


def test_query_carries_the_keyword_and_a_bounded_row_cap():
    """A broad keyword like "BTC" matches most of the table; the per-round
    query must cap rows instead of pulling all of them every 30 seconds."""
    from app.core.alert_sources import NEWS_MATCH_LIMIT

    store = _Store()
    make_news_lookup(store)(_rule(keyword="  减半  "))
    assert store.calls[0]["keyword"] == "减半"
    assert 0 < store.calls[0]["limit"] <= NEWS_MATCH_LIMIT


def test_store_failure_is_none_not_zero():
    """``(0, None)`` would mean "searched, found nothing". A throw is not that."""
    lookup = make_news_lookup(_Store(raises=True))
    assert lookup(_rule()) is None


def test_empty_keyword_is_none_not_a_full_table_scan():
    store = _Store(rows=[{"title": "x"}])
    lookup = make_news_lookup(store)
    assert lookup(_rule(keyword="   ")) is None
    assert store.calls == []


def test_malformed_store_result_is_none():
    """A store returning a non-list must not become ``len()`` of something odd."""
    lookup = make_news_lookup(_Store(rows={"oops": 1}))
    assert lookup(_rule()) is None


def test_non_dict_threshold_is_none():
    lookup = make_news_lookup(_Store())
    assert lookup({"id": "v1", "type": "voice", "threshold": "爆仓"}) is None


def test_zero_title_is_reported_as_no_title_not_the_string_none():
    lookup = make_news_lookup(_Store(rows=[{"title": None}]))
    assert lookup(_rule()) == (1, None)
