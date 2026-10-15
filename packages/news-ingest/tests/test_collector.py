"""news_collector 测试: RSS 解析/清洗/拒非 http 链接/pubDate 回退/去重入库。"""

from __future__ import annotations

import asyncio

import pytest

from selene_space_news_ingest import news_collector as coll
from selene_space_news_ingest.news_store import NewsStore

_MIN = 60_000


@pytest.fixture
def store(tmp_path):
    s = NewsStore(db_path=tmp_path / "news.db")
    yield s
    s.close()


def _rss(items_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
        "<channel><title>test</title><link>https://feed</link>"
        f"{items_xml}</channel></rss>"
    )


def _item(
    link="https://panewslab.com/a/1",
    title="T",
    desc="D",
    pub="Thu, 04 Sep 2026 10:00:00 GMT",
):
    return (
        f"<item><link>{link}</link><title>{title}</title>"
        f"<description>{desc}</description><pubDate>{pub}</pubDate></item>"
    )


def test_parse_feed_basic_fields():
    xml = _rss(_item())
    items = coll.parse_feed(xml, lang="zh", fetched_at_ms=123)
    assert len(items) == 1
    it = items[0]
    assert it["link"] == "https://panewslab.com/a/1"
    assert it["title"] == "T" and it["content"] == "D"
    assert it["lang"] == "zh" and it["fetched_at_ms"] == 123
    assert it["published_at_ms"] > 0


def test_parse_feed_cleans_html_tags_and_whitespace():
    # RSS 正文里的 HTML 是转义文本(&lt;p&gt;),解析后 node.text 是带标签字符串,
    # _clean_text 负责去标签/反转义/压缩空白
    desc = "&lt;p&gt;正文  &amp;  空白&lt;/p&gt;\n\n&lt;b&gt;加粗&lt;/b&gt;"
    xml = _rss(_item(desc=desc))
    items = coll.parse_feed(xml, "en", 1)
    assert items[0]["content"] == "正文 & 空白 加粗"


def test_parse_feed_rejects_non_http_link():
    xml = _rss(
        _item(link="javascript:alert(1)")
        + _item(link="ftp://evil.com/x", title="ftp-item")
    )
    items = coll.parse_feed(xml, "en", 1)
    assert items == []  # javascript: 与 ftp: 都拒


def test_parse_feed_bad_pubdate_falls_back_to_now():
    import time as _t

    before = int(_t.time() * 1000)
    xml = _rss(_item(pub="not a date"))
    items = coll.parse_feed(xml, "en", 1)
    assert items[0]["published_at_ms"] >= before  # 回退当前时间


def test_parse_feed_single_item_failure_skipped():
    """单条解析失败只跳过该条,不炸整批: pubDate 非法值只 warn 不断流,此处
    用 link 为空(被非-http 拒)模拟单条被跳。"""
    good = _item(link="https://a.com/ok", title="ok")
    xml = _rss(f"<item><title>no-link</title></item>{good}")
    items = coll.parse_feed(xml, "en", 1)
    assert [i["link"] for i in items] == ["https://a.com/ok"]


def test_insert_dedup_roundtrip(store):
    """同一批 item 两次入库,新增数第二次为 0(link 去重闭环)。"""
    items = coll.parse_feed(_rss(_item()), "en", 1)
    assert store.insert_items(items) == 1
    assert store.insert_items(items) == 0


def test_lang_from_url():
    assert coll._lang_from_url("https://x/rss.xml?lang=zh") == "zh"
    assert coll._lang_from_url("https://x/rss.xml") == "en"


def test_fetch_rejects_oversized_feed(monkeypatch):
    """超 5MB 的 feed 中止读取(feed 源不可信)。"""

    class _Resp:
        status = 200

        class content:
            @staticmethod
            async def iter_chunked(size):
                for _ in range(100):
                    yield b"x" * (64 * 1024)  # 6.4MB

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def get(self, url, proxy=None):
            return _Resp()

    async def run():
        with pytest.raises(RuntimeError, match="too large"):
            await coll._fetch(_Session(), "https://feed", None)

    asyncio.run(run())
