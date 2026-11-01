#!/usr/bin/env python
"""Orderflow历史数据同步脚本（Top Position/Account Ratio, Global LS Ratio）。

用法:
    python scripts/sync_orderflow.py                    # 同步默认配置
    python scripts/sync_orderflow.py --dry-run          # 只显示将要同步的内容
    python scripts/sync_orderflow.py --symbols BTC/USDT ETH/USDT
    python scripts/sync_orderflow.py --proxy http://<host>:<port>
    python scripts/sync_orderflow.py --days 365         # 首次同步1年历史

数据源: Binance Futures REST API (无需API Key)
  - Top Trader Position Ratio: /futures/data/topLongShortPositionRatio
  - Top Trader Account Ratio:  /futures/data/topLongShortAccountRatio
  - Global LS Account Ratio:   /futures/data/globalLongShortAccountRatio
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from selene_space_market.orderflow_db import OrderflowDB

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
DEFAULT_PERIODS = ["4h", "1d"]
DEFAULT_DAYS = 365

BINANCE_FAPI = "https://fapi.binance.com"

# Binance API limits per request
RATIO_LIMIT = 500

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_orderflow")


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------


class OrderflowSync:
    """Orderflow历史数据同步器。"""

    def __init__(
        self,
        db_path: str | Path = "data/orderflow.db",
        dry_run: bool = False,
        proxy_url: str | None = None,
    ) -> None:
        self._db = OrderflowDB(db_path)
        self._proxy_url = proxy_url or None
        self._dry_run = dry_run
        self._total_synced = 0
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            transport = None
            if self._proxy_url:
                transport = httpx.AsyncHTTPTransport(proxy=self._proxy_url)
            self._client = httpx.AsyncClient(
                transport=transport,
                timeout=httpx.Timeout(15.0, connect=10.0),
                follow_redirects=True,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def _fetch_json(self, url: str, params: dict) -> list[dict]:
        client = await self._get_client()
        r = await client.get(url, params=params)
        r.raise_for_status()
        data = r.json()
        return data if isinstance(data, list) else []

    # -- Top Position Ratio ---------------------------------------------

    async def sync_top_position_ratio(
        self, symbol: str, period: str, days: int = DEFAULT_DAYS,
    ) -> int:
        binance_sym = symbol.replace("/", "").upper()
        latest_ts = self._db.get_latest_timestamp("top_position_ratio", "binance", symbol, period)

        if latest_ts:
            logger.info("  TopPos %s/%s: 增量同步 from ts=%d", symbol, period, latest_ts)
        else:
            start_dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
            latest_ts = int(start_dt.timestamp() * 1000)
            logger.info("  TopPos %s/%s: 首次同步 from %s", symbol, period, start_dt.date())

        if self._dry_run:
            logger.info("  [DRY RUN] would sync top position ratio for %s/%s", symbol, period)
            return 0

        try:
            data = await self._fetch_json(
                f"{BINANCE_FAPI}/futures/data/topLongShortPositionRatio",
                {"symbol": binance_sym, "period": period, "limit": RATIO_LIMIT},
            )
        except Exception as exc:
            logger.warning("  TopPos %s/%s fetch failed: %s", symbol, period, exc)
            return 0

        if not data:
            logger.info("  TopPos %s/%s: 无数据", symbol, period)
            return 0

        rows = []
        for row in data:
            ts = int(row["timestamp"])
            if ts <= latest_ts:
                continue
            long_r = float(row["longShortRatio"])
            short_r = float(row.get("shortRatio", 1.0 - long_r))
            ratio = long_r / (short_r + 1e-9)
            rows.append(("binance", symbol, period, ts, long_r, short_r, ratio))

        if rows:
            count = self._db.put_top_position_ratio(rows)
            self._total_synced += count
            logger.info("  TopPos %s/%s: +%d rows", symbol, period, count)
            return count

        logger.info("  TopPos %s/%s: 无新数据", symbol, period)
        return 0

    # -- Top Account Ratio ----------------------------------------------

    async def sync_top_account_ratio(
        self, symbol: str, period: str, days: int = DEFAULT_DAYS,
    ) -> int:
        binance_sym = symbol.replace("/", "").upper()
        latest_ts = self._db.get_latest_timestamp("top_account_ratio", "binance", symbol, period)

        if latest_ts:
            logger.info("  TopAcc %s/%s: 增量同步 from ts=%d", symbol, period, latest_ts)
        else:
            start_dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
            latest_ts = int(start_dt.timestamp() * 1000)
            logger.info("  TopAcc %s/%s: 首次同步 from %s", symbol, period, start_dt.date())

        if self._dry_run:
            logger.info("  [DRY RUN] would sync top account ratio for %s/%s", symbol, period)
            return 0

        try:
            data = await self._fetch_json(
                f"{BINANCE_FAPI}/futures/data/topLongShortAccountRatio",
                {"symbol": binance_sym, "period": period, "limit": RATIO_LIMIT},
            )
        except Exception as exc:
            logger.warning("  TopAcc %s/%s fetch failed: %s", symbol, period, exc)
            return 0

        if not data:
            logger.info("  TopAcc %s/%s: 无数据", symbol, period)
            return 0

        rows = []
        for row in data:
            ts = int(row["timestamp"])
            if ts <= latest_ts:
                continue
            long_r = float(row["longShortRatio"])
            short_r = float(row.get("shortRatio", 1.0 - long_r))
            ratio = long_r / (short_r + 1e-9)
            rows.append(("binance", symbol, period, ts, long_r, short_r, ratio))

        if rows:
            count = self._db.put_top_account_ratio(rows)
            self._total_synced += count
            logger.info("  TopAcc %s/%s: +%d rows", symbol, period, count)
            return count

        logger.info("  TopAcc %s/%s: 无新数据", symbol, period)
        return 0

    # -- Global LS Ratio ------------------------------------------------

    async def sync_global_ls_ratio(
        self, symbol: str, period: str, days: int = DEFAULT_DAYS,
    ) -> int:
        binance_sym = symbol.replace("/", "").upper()
        latest_ts = self._db.get_latest_timestamp("global_ls_ratio", "binance", symbol, period)

        if latest_ts:
            logger.info("  GlobalLS %s/%s: 增量同步 from ts=%d", symbol, period, latest_ts)
        else:
            start_dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
            latest_ts = int(start_dt.timestamp() * 1000)
            logger.info("  GlobalLS %s/%s: 首次同步 from %s", symbol, period, start_dt.date())

        if self._dry_run:
            logger.info("  [DRY RUN] would sync global LS ratio for %s/%s", symbol, period)
            return 0

        try:
            data = await self._fetch_json(
                f"{BINANCE_FAPI}/futures/data/globalLongShortAccountRatio",
                {"symbol": binance_sym, "period": period, "limit": RATIO_LIMIT},
            )
        except Exception as exc:
            logger.warning("  GlobalLS %s/%s fetch failed: %s", symbol, period, exc)
            return 0

        if not data:
            logger.info("  GlobalLS %s/%s: 无数据", symbol, period)
            return 0

        rows = []
        for row in data:
            ts = int(row["timestamp"])
            if ts <= latest_ts:
                continue
            ratio = float(row["longShortRatio"])
            long_acc = float(row.get("longAccount", 0.0))
            short_acc = float(row.get("shortAccount", 0.0))
            rows.append(("binance", symbol, period, ts, ratio, long_acc, short_acc))

        if rows:
            count = self._db.put_global_ls_ratio(rows)
            self._total_synced += count
            logger.info("  GlobalLS %s/%s: +%d rows", symbol, period, count)
            return count

        logger.info("  GlobalLS %s/%s: 无新数据", symbol, period)
        return 0

    # -- Basis (spot-futures contango/backwardation) ---------------------

    async def sync_basis(
        self, symbol: str, period: str, days: int = DEFAULT_DAYS,
    ) -> int:
        """Sync spot-futures basis data (contango/backwardation indicator)."""
        binance_sym = symbol.replace("/", "").upper()
        # Basis API uses "pair" param (e.g. BTCUSDT) not "symbol"
        latest_ts = self._db.get_latest_timestamp("basis", "binance", symbol, period)

        if latest_ts:
            logger.info("  Basis %s/%s: 增量同步 from ts=%d", symbol, period, latest_ts)
        else:
            start_dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
            latest_ts = int(start_dt.timestamp() * 1000)
            logger.info("  Basis %s/%s: 首次同步 from %s", symbol, period, start_dt.date())

        if self._dry_run:
            logger.info("  [DRY RUN] would sync basis for %s/%s", symbol, period)
            return 0

        try:
            data = await self._fetch_json(
                f"{BINANCE_FAPI}/futures/data/basis",
                {"pair": binance_sym, "contractType": "PERPETUAL", "period": period, "limit": RATIO_LIMIT},
            )
        except Exception as exc:
            logger.warning("  Basis %s/%s fetch failed: %s", symbol, period, exc)
            return 0

        if not data:
            logger.info("  Basis %s/%s: 无数据", symbol, period)
            return 0

        rows = []
        for row in data:
            ts = int(row["timestamp"])
            if ts <= latest_ts:
                continue
            basis_val = float(row["basis"])
            basis_rate = float(row["basisRate"])
            index_price = float(row.get("indexPrice", 0.0))
            futures_price = float(row.get("futuresPrice", 0.0))
            rows.append(("binance", symbol, period, ts, basis_val, basis_rate, index_price, futures_price))

        if rows:
            count = self._db.put_basis(rows)
            self._total_synced += count
            logger.info("  Basis %s/%s: +%d rows", symbol, period, count)
            return count

        logger.info("  Basis %s/%s: 无新数据", symbol, period)
        return 0

    # -- Batch sync ----------------------------------------------------

    async def sync_all(
        self,
        symbols: list[str] | None = None,
        periods: list[str] | None = None,
        days: int = DEFAULT_DAYS,
    ) -> dict[str, int]:
        symbols = symbols or DEFAULT_SYMBOLS
        periods = periods or DEFAULT_PERIODS

        results: dict[str, int] = {}
        total = len(symbols) * len(periods) * 4  # 4 data types per symbol/period

        logger.info("=" * 60)
        logger.info("Orderflow数据同步开始")
        logger.info("  数据库: orderflow.db")
        logger.info("  交易对: %s", symbols)
        logger.info("  周期:   %s", periods)
        logger.info("  代理:   %s", self._proxy_url or "none")
        logger.info("  历史:   %d 天", days)
        logger.info("  总任务: %d", total)
        logger.info("=" * 60)

        for symbol in symbols:
            for period in periods:
                # Top Position Ratio
                try:
                    count = await self.sync_top_position_ratio(symbol, period, days=days)
                    results[f"top_pos/{symbol}/{period}"] = count
                except Exception as exc:
                    logger.error("  TopPos %s/%s 失败: %s", symbol, period, exc)
                    results[f"top_pos/{symbol}/{period}"] = -1

                # Top Account Ratio
                try:
                    count = await self.sync_top_account_ratio(symbol, period, days=days)
                    results[f"top_acc/{symbol}/{period}"] = count
                except Exception as exc:
                    logger.error("  TopAcc %s/%s 失败: %s", symbol, period, exc)
                    results[f"top_acc/{symbol}/{period}"] = -1

                # Global LS Ratio
                try:
                    count = await self.sync_global_ls_ratio(symbol, period, days=days)
                    results[f"global_ls/{symbol}/{period}"] = count
                except Exception as exc:
                    logger.error("  GlobalLS %s/%s 失败: %s", symbol, period, exc)
                    results[f"global_ls/{symbol}/{period}"] = -1

                # Basis
                try:
                    count = await self.sync_basis(symbol, period, days=days)
                    results[f"basis/{symbol}/{period}"] = count
                except Exception as exc:
                    logger.error("  Basis %s/%s 失败: %s", symbol, period, exc)
                    results[f"basis/{symbol}/{period}"] = -1

        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步Orderflow历史数据到本地 SQLite")
    parser.add_argument("--db", default="data/orderflow.db", help="SQLite 数据库路径")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要同步的内容")
    parser.add_argument("--symbols", nargs="+", default=None, help="交易对列表")
    parser.add_argument("--periods", nargs="+", default=None, help="数据周期 (default: 4h 1d)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="首次同步历史天数")
    parser.add_argument("--proxy", default=None, help="HTTP代理URL (e.g. http://<host>:<port>)")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    proxy = args.proxy or None
    sync = OrderflowSync(db_path=args.db, dry_run=args.dry_run, proxy_url=proxy)

    try:
        start_time = datetime.now()

        async def _run():
            results = await sync.sync_all(
                symbols=args.symbols,
                periods=args.periods,
                days=args.days,
            )
            await sync.close()
            return results

        results = asyncio.run(_run())
        elapsed = datetime.now() - start_time

        logger.info("=" * 60)
        logger.info("同步完成!")
        logger.info("  总耗时: %s", elapsed)
        logger.info("  总条数: %d", sync._total_synced)

        if sync._total_synced > 0 and not args.dry_run:
            for key, count in sorted(results.items()):
                status = f"+{count}" if count >= 0 else f"ERROR({count})"
                logger.info("    %s: %s", key, status)

        logger.info("=" * 60)
    except Exception:
        logger.error("同步失败", exc_info=True)


if __name__ == "__main__":
    main()
