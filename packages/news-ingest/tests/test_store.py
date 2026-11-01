"""news_store 测试: claim 原子性/僵尸回收/release-failed 语义/健康账本/统计口径。"""

from __future__ import annotations

import threading

import pytest

from selene_space_news_ingest.news_store import NewsStore


@pytest.fixture
def store(tmp_path):
    s = NewsStore(db_path=tmp_path / "news.db")
    yield s
    s.close()


def _insert(store, n=3, status="pending"):
    for i in range(n):
        store._conn.execute(
            "INSERT INTO news_items (link, title, published_at_ms, fetched_at_ms, status) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"https://example.com/{i}", f"title {i}", 1000 + i, 2000, status),
        )
    store._conn.commit()


def test_insert_items_dedup_by_link(store):
    items = [
        {
            "link": "https://a.com/1",
            "title": "t",
            "content": "c",
            "lang": "en",
            "published_at_ms": 100,
            "fetched_at_ms": 200,
        },
    ]
    assert store.insert_items(items) == 1
    assert store.insert_items(items) == 0  # link 去重
    assert store.insert_items([{**items[0], "link": "https://a.com/2"}]) == 1


def test_claim_pending_marks_tagging_and_returns_rows(store):
    _insert(store, 3)
    claimed = store.claim_pending(10)
    assert len(claimed) == 3
    assert all(c["status"] == "tagging" for c in claimed)
    assert store.claim_pending(10) == []  # 已无 pending


def test_claim_atomic_two_workers_no_double_claim(store):
    """并发 claim: 同一行不被两个 worker 重复认领(TOCTOU 防护)。"""
    _insert(store, 20)
    results = []
    lock = threading.Lock()

    def worker():
        got = store.claim_pending(10)
        with lock:
            results.append([c["id"] for c in got])

    t1, t2 = threading.Thread(target=worker), threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    all_ids = [i for r in results for i in r]
    assert len(all_ids) == 20  # 20 行全部被认领
    assert len(set(all_ids)) == 20  # 无一重复


def test_reclaim_orphan_tagging_resets_to_pending(store):
    _insert(store, 2)
    store.claim_pending(10)
    assert store.reclaim_orphan_tagging() == 2
    # 回收后可再次认领
    assert len(store.claim_pending(10)) == 2


def test_release_to_pending_vs_failed_semantics(store):
    _insert(store, 2)
    claimed = store.claim_pending(10)

    # retryable: 退回 pending 可再认领
    store.release_to_pending(claimed[0]["id"], error="HTTP 429")
    assert len(store.claim_pending(10)) == 1

    # 终态 failed: 不再回到 pending
    store.update_tag(
        claimed[1]["id"],
        [],
        "mid",
        "analysis",
        "neutral",
        ok=False,
        error="JSON 解析失败",
    )
    assert store.claim_pending(10) == []
    row = store._conn.execute(
        "SELECT status, error FROM news_items WHERE id=?", (claimed[1]["id"],)
    ).fetchone()
    assert row[0] == "failed"
    assert "JSON" in row[1]


def test_update_tag_ok_writes_labels(store):
    _insert(store, 1)
    claimed = store.claim_pending(10)
    store.update_tag(claimed[0]["id"], ["BTC"], "high", "breaking", "bearish", ok=True)
    row = store._conn.execute(
        "SELECT symbols_json, importance, news_type, sentiment, status FROM news_items WHERE id=?",
        (claimed[0]["id"],),
    ).fetchone()
    assert row == ('["BTC"]', "high", "breaking", "bearish", "tagged")


def test_fetch_ready_to_check_window(store):
    """high+breaking+有symbol+满6分钟才可检;17分钟后不再复检。"""
    now = 10 * 60 * 1000
    # 符合条件: 8 分钟前发布,已打标带 symbol
    store._conn.execute(
        "INSERT INTO news_items (link, published_at_ms, fetched_at_ms, symbols_json, "
        "importance, news_type, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "https://a.com/ready",
            now - 8 * 60 * 1000,
            now,
            '["BTC"]',
            "high",
            "breaking",
            "tagged",
        ),
    )
    # 不符合: importance=mid
    store._conn.execute(
        "INSERT INTO news_items (link, published_at_ms, fetched_at_ms, symbols_json, "
        "importance, news_type, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "https://a.com/mid",
            now - 8 * 60 * 1000,
            now,
            '["BTC"]',
            "mid",
            "breaking",
            "tagged",
        ),
    )
    # 不符合: 距今不足 6 分钟
    store._conn.execute(
        "INSERT INTO news_items (link, published_at_ms, fetched_at_ms, symbols_json, "
        "importance, news_type, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "https://a.com/fresh",
            now - 3 * 60 * 1000,
            now,
            '["BTC"]',
            "high",
            "breaking",
            "tagged",
        ),
    )
    # 不符合: 18 分钟前,已 checked(超 17min 复检上界,不再取)
    store._conn.execute(
        "INSERT INTO news_items (link, published_at_ms, fetched_at_ms, symbols_json, "
        "importance, news_type, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "https://a.com/old",
            now - 18 * 60 * 1000,
            now,
            '["BTC"]',
            "high",
            "breaking",
            "checked",
        ),
    )
    store._conn.commit()
    ready = store.fetch_ready_to_check(now)
    assert [r["link"] for r in ready] == ["https://a.com/ready"]


def test_save_anomaly_and_mark_checked(store):
    _insert(store, 1)
    nid = store._conn.execute("SELECT id FROM news_items").fetchone()[0]
    store.save_anomaly(nid, "BTC", 3.5, True, False, 0.1, 0.2, 0.3, 12345)
    store.mark_checked(nid)
    row = store._conn.execute(
        "SELECT volume_ratio, triggered, insufficient_data, checked_at_ms "
        "FROM volume_anomalies WHERE news_id=?",
        (nid,),
    ).fetchone()
    assert row == (3.5, 1, 0, 12345)
    status = store._conn.execute(
        "SELECT status FROM news_items WHERE id=?", (nid,)
    ).fetchone()[0]
    assert status == "checked"


def test_status_ledger_records_ok_and_errors(store):
    store.record_task_ok("collector", 111)
    store.record_task_error("tagger", 222)
    store.record_task_error("tagger", 223)
    st = store.get_status()
    assert st["collector"] == {"last_ok_ms": 111, "errors": 0}
    assert st["tagger"] == {"last_ok_ms": 223, "errors": 2}
    assert st["detector"] == {"last_ok_ms": None, "errors": 0}
    with pytest.raises(KeyError):
        store.record_task_ok("evil_task", 0)  # 白名单兜底


def test_compute_stats_denominator_is_tested_not_total(store):
    """触发率分母 = 实际完成检测的新闻数,不是 high+breaking 总数。
    fetched_at_ms 用真实近今时间(compute_stats 内部以 now-7d 为界)。"""
    import time as _t

    now_ms = int(_t.time() * 1000)
    # 3 条 high+breaking,但只有 2 条完成有效检测(1 条 insufficient)
    for i, (nid_trig, insufficient) in enumerate([(1, 0), (1, 0), (0, 1)]):
        store._conn.execute(
            "INSERT INTO news_items (link, published_at_ms, fetched_at_ms, symbols_json, "
            "importance, news_type, status) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                f"https://a.com/{i}",
                now_ms - 1000,
                now_ms - 1000,
                '["BTC"]',
                "high",
                "breaking",
                "checked",
            ),
        )
        news_id = store._conn.execute(
            "SELECT id FROM news_items WHERE link=?", (f"https://a.com/{i}",)
        ).fetchone()[0]
        store._conn.execute(
            "INSERT INTO volume_anomalies (news_id, symbol, volume_ratio, triggered, "
            "insufficient_data, checked_at_ms) VALUES (?, 'BTC', 2.0, ?, ?, ?)",
            (news_id, nid_trig, insufficient, now_ms),
        )
    store._conn.commit()
    stats = store.compute_stats(days=7)
    assert stats["total_high_breaking"] == 3
    assert stats["tested_count"] == 2  # 1 条 insufficient 不进分母
    assert stats["triggered_count"] == 2
    assert stats["trigger_rate"] == 1.0  # 2/2 而非 2/3≈0.67


def test_query_news_symbol_filter_escapes_like(store):
    store._conn.execute(
        "INSERT INTO news_items (link, published_at_ms, fetched_at_ms, symbols_json) "
        "VALUES ('https://a.com/1', 1, 1, '[\"BTC\"]')"
    )
    store._conn.commit()
    rows = store.query_news(symbol="BTC")
    assert len(rows) == 1 and rows[0]["symbols"] == ["BTC"]
    assert store.query_news(symbol="BT%") == []  # % 不当通配符


def _insert_news(store, items):
    for i, (title, content, fetched) in enumerate(items):
        store._conn.execute(
            "INSERT INTO news_items (link, title, content, published_at_ms, fetched_at_ms) "
            "VALUES (?, ?, ?, ?, ?)",
            (f"https://k.com/{i}", title, content, fetched, fetched),
        )
    store._conn.commit()


def test_query_news_keyword_matches_title(store):
    _insert_news(store, [("某所爆仓 3 亿", "正文", 1), ("平常新闻", "正文", 2)])
    rows = store.query_news(keyword="爆仓")
    assert [r["title"] for r in rows] == ["某所爆仓 3 亿"]


def test_query_news_keyword_matches_content(store):
    _insert_news(store, [("标题", "交易所被清算 爆仓 5 亿", 1)])
    assert len(store.query_news(keyword="爆仓")) == 1


def test_query_news_keyword_escapes_like_wildcards(store):
    """A user typing ``%`` must search for a literal percent sign."""
    _insert_news(store, [("涨了 5%", "x", 1), ("涨了 5 块", "x", 2)])
    rows = store.query_news(keyword="5%")
    assert [r["title"] for r in rows] == ["涨了 5%"]


def test_query_news_keyword_escapes_underscore(store):
    _insert_news(store, [("a_b", "x", 1), ("axb", "x", 2)])
    rows = store.query_news(keyword="a_b")
    assert [r["title"] for r in rows] == ["a_b"]


def test_query_news_fetched_after_window_excludes_old_rows(store):
    """Without the window, one historical article matches every round forever."""
    _insert_news(store, [("旧闻 爆仓", "x", 1_000), ("新消息 爆仓", "x", 9_000)])
    rows = store.query_news(keyword="爆仓", fetched_after_ms=5_000)
    assert [r["title"] for r in rows] == ["新消息 爆仓"]


def test_query_news_without_keyword_or_window_is_unchanged(store):
    """The new parameters must not narrow existing callers."""
    _insert_news(store, [("a", "x", 1), ("b", "x", 2)])
    assert len(store.query_news()) == 2
