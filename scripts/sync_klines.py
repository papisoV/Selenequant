#!/usr/bin/env python
"""K线历史数据同步脚本。

用法:
    python scripts/sync_klines.py                    # 同步默认配置
    python scripts/sync_klines.py --dry-run          # 只显示将要同步的内容
    python scripts/sync_klines.py --symbols BTC/USDT ETH/USDT
    python scripts/sync_klines.py --days 365         # 同步1年数据

同步逻辑:
    1. 检查本地 SQLite 数据库中该 partition 的最新时间戳
    2. 从交易所 REST API 获取该时间戳之后的新数据
    3. 写入本地数据库
    4. 重复直到追上最新数据
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Add project root to path for imports
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from selene_space_core.kline_paths import default_klines_db_path
from selene_space_market.kline_db import KlineDB
from selene_space_market.service import MarketDataService
from selene_space_market.sources.binance_source import BinanceSource

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SYMBOLS = [
    "BTC/USDT",
    "ETH/USDT",
    "SOL/USDT",
    "BNB/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
]

DEFAULT_INTERVALS = ["1m", "5m", "15m", "1h", "4h", "8h", "1d"]

DEFAULT_EXCHANGES = [
    ("binance", "futures"),
    ("binance", "spot"),
]

DEFAULT_DAYS = 365

# 单次 REST 请求的 K 线上限（Binance 交易所限制）
BATCH_SIZE = 1500

# 每同步这么多 partition 打一次进度
PROGRESS_EVERY = 10

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_klines")


def _partition_key(exchange: str, market_type: str, symbol: str, interval: str) -> str:
    return f"{exchange}/{market_type}/{symbol}/{interval}"


def _resolve_proxy(explicit: str | None) -> str | None:
    """显式 --proxy 优先，否则回落到环境变量。"""
    return explicit or os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------


class KlineSync:
    """K线历史数据同步器。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        dry_run: bool = False,
        proxy_url: str | None = None,
    ) -> None:
        self._db = KlineDB(db_path)
        self._service = MarketDataService()
        self._proxy_url = _resolve_proxy(proxy_url)
        self._dry_run = dry_run
        self._total_synced = 0
        self._sources_registered = False

    def _resolve_start_ts(
        self, exchange: str, market_type: str, symbol: str, interval: str, days: int
    ) -> int:
        """起点时间戳：有本地数据就续传，否则回溯 N 天。"""
        latest = self._db.get_latest_timestamp(exchange, market_type, symbol, interval)
        if latest:
            return int(latest)
        origin = datetime.now(tz=timezone.utc) - timedelta(days=days)
        return int(origin.timestamp() * 1000)

    async def _drain_pages(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        start_ts: int,
        key: str,
    ) -> list:
        """按 start_time 翻页拉取，直到追平或翻不动；只保留 > start_ts 的行。"""
        collected: list = []
        cursor = start_ts
        page = 0

        while True:
            page += 1
            try:
                batch = await self._service.fetch_klines_rest(
                    symbol=symbol,
                    exchange=exchange,
                    market_type=market_type,
                    interval=interval,
                    limit=BATCH_SIZE,
                    start_time=cursor,
                )
            except Exception as exc:
                logger.warning("  %s: fetch failed (page %d): %s", key, page, exc)
                break

            if not batch:
                break

            collected.extend(k for k in batch if k.timestamp > start_ts)

            # 不满一页 = 已经追平
            if len(batch) < BATCH_SIZE:
                break

            newest = max(k.timestamp for k in batch)
            if newest <= cursor:
                break  # 交易所没有推进，避免死循环
            cursor = newest

        return collected

    async def sync_partition(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        days: int = DEFAULT_DAYS,
    ) -> int:
        """同步单个 partition (e.g. binance/futures/BTC/USDT/1h)。

        使用增量同步：先查本地最新时间戳，只获取新数据。
        """
        key = _partition_key(exchange, market_type, symbol, interval)
        start_ts = self._resolve_start_ts(exchange, market_type, symbol, interval, days)

        fresh = self._db.get_latest_timestamp(exchange, market_type, symbol, interval)
        if fresh:
            logger.info("  %s: 增量同步 from ts=%d", key, start_ts)
        else:
            logger.info(
                "  %s: 首次同步 from %s",
                key,
                datetime.fromtimestamp(start_ts / 1000, tz=timezone.utc).date(),
            )

        if self._dry_run:
            logger.info("  [DRY RUN] %s: would sync from ts=%d", key, start_ts)
            return 0

        klines = await self._drain_pages(
            exchange, market_type, symbol, interval, start_ts, key
        )
        if not klines:
            logger.info("  %s: 无新数据", key)
            return 0

        count = int(self._db.put_klines(klines))
        self._total_synced += count
        logger.info("  %s: +%d klines", key, count)
        return count

    def _register_sources(
        self, exchanges: list[tuple[str, str]], intervals: list[str]
    ) -> None:
        """幂等注册；目前只有 binance 有 REST source。"""
        if self._sources_registered:
            return
        for exchange, market_type in exchanges:
            if exchange != "binance":
                continue
            self._service.register_source(
                BinanceSource(
                    market_type=market_type,
                    proxy_url=self._proxy_url,
                    kline_intervals=intervals,
                )
            )
            logger.info(
                "注册数据源: binance/%s (proxy=%s)",
                market_type,
                self._proxy_url or "none",
            )
        self._sources_registered = True

    async def sync_all(
        self,
        symbols: list[str] | None = None,
        intervals: list[str] | None = None,
        exchanges: list[tuple[str, str]] | None = None,
        days: int = DEFAULT_DAYS,
    ) -> dict[str, int]:
        """批量同步多个 partition。"""
        symbols = symbols or DEFAULT_SYMBOLS
        intervals = intervals or DEFAULT_INTERVALS
        exchanges = exchanges or DEFAULT_EXCHANGES

        self._register_sources(exchanges, intervals)

        total = len(exchanges) * len(symbols) * len(intervals)
        logger.info(
            "开始同步: %d exchanges × %d symbols × %d intervals = %d partitions",
            len(exchanges),
            len(symbols),
            len(intervals),
            total,
        )

        results: dict[str, int] = {}
        done = 0
        for exchange, market_type in exchanges:
            for symbol in symbols:
                for interval in intervals:
                    done += 1
                    key = _partition_key(exchange, market_type, symbol, interval)
                    try:
                        results[key] = await self.sync_partition(
                            exchange=exchange,
                            market_type=market_type,
                            symbol=symbol,
                            interval=interval,
                            days=days,
                        )
                    except Exception as exc:
                        logger.error("  %s: 同步失败: %s", key, exc)
                        results[key] = -1

                    if done % PROGRESS_EVERY == 0:
                        logger.info("进度: %d/%d partitions", done, total)

        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


#: (flags, 关键字参数) —— 逐条对应原自写的 add_argument。
_ARG_SPECS: list[tuple[tuple[str, ...], dict[str, Any]]] = [
    (
        ("--db",),
        {
            "default": str(default_klines_db_path()),
            "help": "SQLite 数据库路径 (default: data/klines.db)",
        },
    ),
    (
        ("--dry-run",),
        {"action": "store_true", "help": "只显示将要同步的内容，不实际写入"},
    ),
    (
        ("--proxy",),
        {
            "default": None,
            "help": "HTTP代理URL (e.g. http://<host>:<port>), 默认读HTTPS_PROXY/HTTP_PROXY环境变量",
        },
    ),
    (
        ("--symbols",),
        {
            "nargs": "+",
            "default": None,
            "help": f"交易对列表 (default: {DEFAULT_SYMBOLS})",
        },
    ),
    (
        ("--intervals",),
        {
            "nargs": "+",
            "default": None,
            "help": f"时间周期列表 (default: {DEFAULT_INTERVALS})",
        },
    ),
    (
        ("--days",),
        {
            "type": int,
            "default": DEFAULT_DAYS,
            "help": f"首次同步的历史天数 (default: {DEFAULT_DAYS})",
        },
    ),
    (
        ("--exchange",),
        {
            "choices": ["binance", "okx"],
            "default": None,
            "help": "只同步指定交易所 (default: binance)",
        },
    ),
    (
        ("--market-type",),
        {
            "choices": ["spot", "futures"],
            "default": None,
            "help": "只同步指定市场类型 (default: both)",
        },
    ),
    (("-v", "--verbose"), {"action": "store_true", "help": "详细输出"}),
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步 K 线历史数据到本地 SQLite")
    for flags, kwargs in _ARG_SPECS:
        parser.add_argument(*flags, **kwargs)
    return parser.parse_args()


def _resolve_exchanges(args: argparse.Namespace) -> list[tuple[str, str]]:
    """--exchange / --market-type 组合成 (exchange, market_type) 列表。"""
    if not args.exchange:
        return DEFAULT_EXCHANGES
    market_types = [args.market_type] if args.market_type else ["spot", "futures"]
    return [(args.exchange, mt) for mt in market_types]


def main() -> None:
    args = parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    exchanges = _resolve_exchanges(args)
    sync = KlineSync(db_path=args.db, dry_run=args.dry_run, proxy_url=args.proxy)

    started = datetime.now()
    for line in (
        "=" * 60,
        "K线同步开始",
        f"  数据库: {args.db}",
        f"  交易对: {args.symbols or DEFAULT_SYMBOLS}",
        f"  周期:   {args.intervals or DEFAULT_INTERVALS}",
        f"  交易所: {exchanges}",
        f"  代理:   {sync._proxy_url or 'none'}",
        f"  历史:   {args.days} 天",
        "=" * 60,
    ):
        logger.info(line)

    results = asyncio.run(
        sync.sync_all(
            symbols=args.symbols,
            intervals=args.intervals,
            exchanges=exchanges,
            days=args.days,
        )
    )

    logger.info("=" * 60)
    logger.info("同步完成!")
    logger.info("  总耗时: %s", datetime.now() - started)
    logger.info("  总条数: %d", sync._total_synced)

    if sync._total_synced > 0 and not args.dry_run:
        logger.info("  各 partition 详情:")
        for key, count in sorted(results.items()):
            logger.info(
                "    %s: %s", key, f"+{count}" if count >= 0 else f"ERROR({count})"
            )

    logger.info("=" * 60)


if __name__ == "__main__":
    main()
