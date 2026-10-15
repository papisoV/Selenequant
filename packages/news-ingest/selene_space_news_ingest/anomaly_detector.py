"""成交量异动检测 — 对刚发布的新闻做市场反应度量。

来源: 本模块的检测流程源自作者本人的既有项目 `binance-quant` 的 news 模块
(同一作者 2026-04 建构、无第三方许可问题), 2026-10-05 首次独立重写, 同日再
cleanroom 一次(切窗/取价/基线改为显式数据流并抽出通用「跑一轮」骨架)。
详见 `packages/news-ingest/PROVENANCE.md`。

检测对象是已打标为 high/breaking 且带 symbol 的新闻。做法: 取该 symbol 的
1 分钟 K 线, 用新闻发布前的若干根收盘量作为"平常水平", 再看发布后短窗口内
成交量放大到什么程度; 同时记录发布后若干分钟的价格偏移。

实现边界(踩过的坑, 改动前先读):

* 毫秒是唯一时间单位。上游行情接口给的就是毫秒, 内部不再换算; 只有"对齐到
  整分钟"时才除以 :data:`_MINUTE_MS`。混用秒会把基线和窗口整体错位。
* 取数必须现拉。任何带 TTL 的缓存都会让 ratio 失真 —— 旧 K 线算出来的放大
  倍数没有意义。
* 未收盘的 K 线一律不用。收盘判据是"该根结束时刻已过 now"。
* 数据不够就承认不够。基线或窗口根数不足时, 只标记 insufficient_data, 不拿
  残缺数据凑一个结论出来。

相对上游项目的差异: 不依赖 ccxt, 直接请求公开行情端点; 代理经
``SELENE_NI_HTTP_PROXY`` 显式传入, 不写进进程环境。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Dict, List, Optional, Tuple

import requests

from selene_space_news_ingest.config import (
    ANOMALY_BASELINE_PERIODS,
    ANOMALY_RATIO_THRESHOLD,
    ANOMALY_WINDOW_MINUTES,
    BINANCE_KLINES_URL,
    DETECTOR_POLL_INTERVAL,
    NEWS_HTTP_PROXY,
)
from selene_space_news_ingest.loops import run_forever
from selene_space_news_ingest.news_store import get_news_store

logger = logging.getLogger(__name__)

_MINUTE_MS = 60 * 1000
_KLINES_TIMEOUT_S = 30

#: 上游单次最多返回的 K 线根数。
_MAX_KLINES = 5000
#: 请求根数下限 —— 太小的 limit 会让接口把新闻附近那几根截掉。
_MIN_REQUEST_LIMIT = 40
#: 推算请求根数时多要的余量(分钟)。
_REQUEST_SLACK = 10

#: ``_analyze_symbol`` 的结果: (ratio, triggered, insufficient, p1, p5, p15)。
Analysis = Tuple[
    Optional[float], bool, bool, Optional[float], Optional[float], Optional[float]
]
#: 数据不足时的统一返回 —— 其余字段保持默认。
_INSUFFICIENT: Analysis = (None, False, True, None, None, None)

#: 价格跟踪的观察点(分钟)。
_PRICE_OFFSETS_MIN = (1, 5, 15)


def _pct_change(close: float, base: float) -> float:
    return (close / base - 1.0) * 100.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def fetch_klines_1m(symbol: str, limit: int, since_ms: int) -> List[Dict]:
    """拉一个 symbol 的 1 分钟 K 线(同步, 调用方负责放进线程)。

    返回按时间升序的 ``[{time, open, high, low, close, volume}]``, 时间单位毫秒。
    刻意不做任何缓存 —— 检测端每次都要最新已收盘数据。
    """
    proxies = (
        {"http": NEWS_HTTP_PROXY, "https": NEWS_HTTP_PROXY} if NEWS_HTTP_PROXY else None
    )
    resp = requests.get(
        BINANCE_KLINES_URL,
        params={
            "symbol": f"{symbol.upper()}USDT",  # 现货符号 -> U 本位永续
            "interval": "1m",
            "limit": min(limit, _MAX_KLINES),
            "startTime": since_ms,
        },
        proxies=proxies,
        timeout=_KLINES_TIMEOUT_S,
    )
    resp.raise_for_status()
    return [
        {
            "time": int(row[0]),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in resp.json()
    ]


def _request_limit(since_ms: int, now_ms: int) -> int:
    """覆盖 since->now 全区间所需的根数。

    接口给了 startTime 之后是"向后翻页"语义: limit 取固定小值会让真正靠近新闻
    的那几根被丢掉。故按区间跨度推算并夹在上下限之间; 超过上限的积压新闻只能
    拿到部分基线 —— 那属于 insufficient 的情形。
    """
    span_minutes = (now_ms - since_ms) // _MINUTE_MS + _REQUEST_SLACK
    return min(max(_MIN_REQUEST_LIMIT, span_minutes), _MAX_KLINES)


def _closed_before(klines: List[Dict], now_ms: int) -> List[Dict]:
    """只留已收盘的根 —— 一根结束时刻已过 now 才算收盘。"""
    return [k for k in klines if k["time"] + _MINUTE_MS <= now_ms]


def _split_baseline_and_window(
    closed: List[Dict], news_minute_ms: int
) -> Tuple[List[Dict], List[Dict]]:
    """把已收盘序列切成 (基线, 窗口) 两段。

    基线 = 新闻分钟之前最近至多 ``ANOMALY_BASELINE_PERIODS`` 根;
    窗口 = 从新闻分钟起、覆盖 ``ANOMALY_WINDOW_MINUTES`` 分钟内的根。
    """
    baseline = [k for k in closed if k["time"] < news_minute_ms][
        -ANOMALY_BASELINE_PERIODS:
    ]
    window_end_ms = news_minute_ms + ANOMALY_WINDOW_MINUTES * _MINUTE_MS
    window = [k for k in closed if news_minute_ms <= k["time"] < window_end_ms][
        :ANOMALY_WINDOW_MINUTES
    ]
    return baseline, window


def _price_track(
    closed: List[Dict], news_minute_ms: int, fallback_open: float
) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """+1 / +5 / +15 分钟相对基准价的涨跌幅。

    基准价取新闻那一根的收盘; 该根缺失时退化为窗口首根的开盘。
    """
    by_time = {k["time"]: k for k in closed}
    anchor = by_time.get(news_minute_ms)
    base_price = anchor["close"] if anchor is not None else fallback_open

    def offset_pct(minutes: int) -> Optional[float]:
        candle = by_time.get(news_minute_ms + minutes * _MINUTE_MS)
        return None if candle is None else _pct_change(candle["close"], base_price)

    return tuple(offset_pct(m) for m in _PRICE_OFFSETS_MIN)  # type: ignore[return-value]


async def _analyze_symbol(symbol: str, published_at_ms: int, now_ms: int) -> Analysis:
    """单个 symbol 的异动分析。

    数据不足时 ``insufficient_data=True``, 其余字段保持默认值。
    """
    news_minute_ms = published_at_ms // _MINUTE_MS * _MINUTE_MS
    # 基线需要的分钟数 + 新闻所在分钟 + 一点余量
    since_ms = published_at_ms - (ANOMALY_BASELINE_PERIODS + 2) * _MINUTE_MS

    klines = await asyncio.to_thread(
        fetch_klines_1m, symbol, _request_limit(since_ms, now_ms), since_ms
    )
    closed = _closed_before(klines, now_ms)
    baseline, window = _split_baseline_and_window(closed, news_minute_ms)
    if len(baseline) < ANOMALY_BASELINE_PERIODS or len(window) < ANOMALY_WINDOW_MINUTES:
        return _INSUFFICIENT

    mean_volume = sum(k["volume"] for k in baseline) / len(baseline)
    if mean_volume <= 0:
        return _INSUFFICIENT

    peak_ratio = max(k["volume"] for k in window) / mean_volume
    p1, p5, p15 = _price_track(closed, news_minute_ms, window[0]["open"])
    return peak_ratio, peak_ratio >= ANOMALY_RATIO_THRESHOLD, False, p1, p5, p15


async def _store_symbol_anomaly(store, item: dict, symbol: str, now_ms: int) -> None:
    """算一个 symbol 并把结果写库。异常向上抛, 由调用方决定处置。"""
    ratio, triggered, insufficient, p1, p5, p15 = await _analyze_symbol(
        symbol, item["published_at_ms"], now_ms
    )
    await asyncio.to_thread(
        store.save_anomaly,
        item["id"],
        symbol,
        ratio,
        triggered,
        insufficient,
        p1,
        p5,
        p15,
        now_ms,
    )


async def _process_item(item: dict, now_ms: int) -> int:
    """跑完一条新闻的所有 symbol, 返回落库的 anomaly 行数。

    只要有一个 symbol 失败就不 mark_checked, 留给下一轮重试(重试窗口上有界,
    不会无限循环)。
    """
    store = get_news_store()
    symbols = item.get("symbols") or []
    stored = 0

    for symbol in symbols:
        try:
            await _store_symbol_anomaly(store, item, symbol, now_ms)
            stored += 1
        except Exception:
            # 丢一个 symbol 只丢它自己那一行, 其余照常
            logger.exception(
                "anomaly analyze failed news_id=%s symbol=%s", item.get("id"), symbol
            )

    if symbols and stored == len(symbols):
        await asyncio.to_thread(store.mark_checked, item["id"])
    return stored


async def detect_ready() -> int:
    """处理一轮到期新闻, 返回其中产出了结果的条数。供测试直接调用。"""
    store = get_news_store()
    now_ms = _now_ms()
    ready: List[dict] = await asyncio.to_thread(store.fetch_ready_to_check, now_ms)

    done = 0
    for item in ready:
        try:
            if await _process_item(item, now_ms) > 0:
                done += 1
        except Exception:
            # 一条炸了不影响这一轮其余条目
            logger.exception("anomaly detection failed news_id=%s", item.get("id"))
    return done


async def _record_task(store, ok: bool) -> None:
    """把这一轮的结果记进任务账本(detector 槽位)。"""
    recorder = store.record_task_ok if ok else store.record_task_error
    await asyncio.to_thread(recorder, "detector", _now_ms())


async def _detector_round(store) -> None:
    """跑一轮检测并按产出记账。

    与 collector/tagger 不同: detector 的"成功"要求本轮**确实产出了结果**
    (detect_ready() > 0)。空轮记错误 —— 长期无产出说明取数链路有问题,
    得在心跳上看得见。单轮抛异常则由 :func:`run_forever` 走 on_failure。
    """
    await _record_task(store, ok=await detect_ready() > 0)


async def detector_loop() -> None:
    """常驻轮询。循环本身不退出, 单轮异常只落到错误计数。"""
    store = get_news_store()
    await run_forever(
        tick=lambda: _detector_round(store),
        on_failure=lambda: _record_task(store, ok=False),
        interval=lambda: DETECTOR_POLL_INTERVAL,
        label="detector",
    )
