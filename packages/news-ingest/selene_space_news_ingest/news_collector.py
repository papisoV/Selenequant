"""RSS 新闻抓取 — 把订阅源的内容变成待打标的条目。

来源: 本模块的流程与解析逻辑源自作者本人的既有项目 `binance-quant` 的 news 模块
(同一作者 2026-04 建构、无第三方许可问题), 2026-10-05 首次独立重写, 同日再
cleanroom 一次(解析改为声明式字段抽取 + 统一的内存上限守卫)。
详见 `packages/news-ingest/PROVENANCE.md`。

订阅内容按不可信处理: feed 文本可能夹带针对下游模型的诱导指令, 或者编造
利好利空来操纵判断。眼下只做一层最粗的防护 —— 条目里出现的品种必须落在
:data:`SYMBOL_WHITELIST` 里, 否则不进异动检测。等到信号真的要驱动交易之前,
还必须补上正文消毒与来源评级。

抓回来的条目统一交给 :mod:`selene_space_news_ingest.news_store` 落库; 批量写
走线程, 避免阻塞事件循环。网络默认直连, 需要代理时经 ``SELENE_NI_HTTP_PROXY``
显式指定, 不写进全局环境。
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import time
from datetime import timezone
from email.utils import parsedate_to_datetime
from typing import List, Optional
from urllib.parse import parse_qs, urlparse
from xml.etree import ElementTree

import aiohttp

from selene_space_news_ingest.config import (
    NEWS_HTTP_PROXY,
    NEWS_POLL_INTERVAL,
    NEWS_RSS_URLS,
)
from selene_space_news_ingest.loops import run_forever
from selene_space_news_ingest.news_store import get_news_store

logger = logging.getLogger(__name__)

#: RSS 的 content 扩展模块命名空间。
_CONTENT_NS = {"content": "http://purl.org/rss/1.0/modules/content/"}

_TIMEOUT = aiohttp.ClientTimeout(total=30)

_TAG_RE = re.compile(r"<[^>]+>")
_BLANK_RE = re.compile(r"\s+")

#: 正文存库前的截断长度。
_CONTENT_LIMIT = 4000
#: 单个 feed 响应允许的最大字节数 —— 防超大或恶意响应把内存吃满。
_FEED_BYTE_CAP = 5 * 1024 * 1024
#: 流式读取的块大小。
_CHUNK_BYTES = 64 * 1024

#: 条目只接受这两种 scheme —— 挡掉 javascript: 之类的 URI 流到前端。
_SAFE_LINK_SCHEMES = ("http://", "https://")


def _clean_text(raw: Optional[str]) -> str:
    """剥标签、还原实体、空白压缩成一个空格, 再截断。"""
    if not raw:
        return ""
    stripped = _TAG_RE.sub("", raw)
    return _BLANK_RE.sub(" ", html.unescape(stripped)).strip()[:_CONTENT_LIMIT]


def _now_ms() -> int:
    return int(time.time() * 1000)


def _pubdate_to_ms(raw: Optional[str]) -> int:
    """RFC822 的 pubDate 转 epoch 毫秒。

    无时区的按 UTC 处理; 缺失或解析不了退回当前时刻。
    """
    if not raw:
        return _now_ms()
    try:
        stamp = parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        logger.warning("bad pubDate, fallback to now: %r", raw)
        return _now_ms()
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp() * 1000)


def _lang_from_url(url: str) -> str:
    """读 URL 上的 ``lang`` 查询参数, 缺省 en。"""
    values = parse_qs(urlparse(url).query).get("lang")
    return values[0] if values else "en"


def _node_text(item: ElementTree.Element, tag: str) -> str:
    """子元素文本; 元素缺失或空文本都返回空串。"""
    node = item.find(tag)
    return node.text.strip() if node is not None and node.text else ""


def _pick_body(item: ElementTree.Element) -> str:
    """条目正文: 优先 content:encoded, 没有才用 description。"""
    encoded = item.find("content:encoded", _CONTENT_NS)
    if encoded is not None and encoded.text:
        return encoded.text
    return _node_text(item, "description")


def _is_safe_link(link: str) -> bool:
    return link.startswith(_SAFE_LINK_SCHEMES)


def _entry_to_record(
    entry: ElementTree.Element, lang: str, fetched_at_ms: int
) -> Optional[dict]:
    """单个 <item> 转 dict; 链接不合法时返回 None。"""
    link = _node_text(entry, "link")
    if not _is_safe_link(link):
        logger.warning("rss item with non-http link %r, skipped", link[:80])
        return None
    return {
        "link": link,
        "title": _clean_text(_node_text(entry, "title")),
        "content": _clean_text(_pick_body(entry)),
        "lang": lang,
        "published_at_ms": _pubdate_to_ms(_node_text(entry, "pubDate")),
        "fetched_at_ms": fetched_at_ms,
    }


def parse_feed(xml_text: str, lang: str, fetched_at_ms: int) -> List[dict]:
    """把一段 RSS XML 解析成条目列表。

    逐条容错: 某条失败只跳过它自己, 不影响同批其余条目。
    """
    records: List[dict] = []
    for entry in ElementTree.fromstring(xml_text).iter("item"):
        try:
            record = _entry_to_record(entry, lang, fetched_at_ms)
        except Exception as exc:
            logger.warning("rss item parse failed, skipped: %s", exc)
            continue
        if record is not None:
            records.append(record)
    return records


async def _read_capped(resp: "aiohttp.ClientResponse", url: str) -> bytes:
    """边读边计数取回响应体; 超 :data:`_FEED_BYTE_CAP` 立即中止。

    订阅源不可信, 不能让它用超大响应把内存撑爆。
    """
    buf = bytearray()
    async for chunk in resp.content.iter_chunked(_CHUNK_BYTES):
        buf.extend(chunk)
        if len(buf) > _FEED_BYTE_CAP:
            raise RuntimeError(f"feed too large (>5MB): {url}")
    return bytes(buf)


async def _fetch(
    session: "aiohttp.ClientSession", url: str, proxy: Optional[str]
) -> str:
    """取回一个 feed 的正文。非 200 直接报错。``proxy=None`` 表示直连。"""
    async with session.get(url, proxy=proxy) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status} for {url}")
        body = await _read_capped(resp, url)
    return body.decode("utf-8", errors="replace")


async def poll_once() -> int:
    """全部订阅源各抓一轮并入库, 返回新增条数。"""
    store = get_news_store()
    fetched_at_ms = _now_ms()
    proxy = NEWS_HTTP_PROXY or None
    collected: List[dict] = []

    async with aiohttp.ClientSession(timeout=_TIMEOUT, trust_env=False) as session:
        for url in NEWS_RSS_URLS:
            try:
                xml_text = await _fetch(session, url, proxy)
                collected.extend(
                    parse_feed(xml_text, _lang_from_url(url), fetched_at_ms)
                )
            except Exception as exc:
                logger.error("feed fetch/parse failed (%s): %s", url, exc)

    if not collected:
        return 0
    return await asyncio.to_thread(store.insert_items, collected)


async def _touch_task(store, ok: bool) -> None:
    """记一次 collector 心跳。ok=False 时累加错误计数。"""
    recorder = store.record_task_ok if ok else store.record_task_error
    await asyncio.to_thread(recorder, "collector", _now_ms())


async def _collect_round() -> int:
    """抓一轮并落库; 有心跳信息时顺带打日志。"""
    added = await poll_once()
    if added:
        logger.info("news poll: +%d items", added)
    return added


async def poll_loop() -> None:
    """常驻轮询。循环不退出, 单轮失败只记一笔错误。"""
    store = get_news_store()
    # 心跳写在抓取之后 —— 否则 last_collector_ok_ms 会少算抓取耗时。
    await run_forever(
        tick=_collect_round,
        on_success=lambda: _touch_task(store, ok=True),
        on_failure=lambda: _touch_task(store, ok=False),
        interval=lambda: NEWS_POLL_INTERVAL,
        label="collector",
    )
