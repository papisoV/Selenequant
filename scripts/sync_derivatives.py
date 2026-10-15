#!/usr/bin/env python
"""衍生品历史数据同步脚本（Funding Rate, Open Interest, Taker Ratio）。

用法:
    python scripts/sync_derivatives.py                    # 同步默认配置
    python scripts/sync_derivatives.py --dry-run          # 只显示将要同步的内容
    python scripts/sync_derivatives.py --symbols BTC/USDT ETH/USDT
    python scripts/sync_derivatives.py --proxy http://<host>:<port>
    python scripts/sync_derivatives.py --days 365         # 首次同步1年历史

数据源: Binance Futures REST API (无需API Key)
  - Funding Rate: /fapi/v1/fundingRate (每8小时一条)
  - Open Interest: /futures/data/openInterestHist (1h/4h/1d聚合)
  - Taker Ratio:  /futures/data/takerlongshortRatio (1h/4h/1d聚合)
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

from selene_space_market.derivatives_db import DerivativesDB

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
DEFAULT_OI_PERIODS = ["1h", "4h", "1d"]
DEFAULT_TAKER_PERIODS = ["1h", "4h", "1d"]
DEFAULT_DAYS = 365

BINANCE_FAPI = "https://fapi.binance.com"

# Binance API limits per request
FUNDING_LIMIT = 1000
OI_LIMIT = 500
TAKER_LIMIT = 500

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("sync_derivatives")


# ---------------------------------------------------------------------------
# Sync logic
# ---------------------------------------------------------------------------


class DerivativesSync:
    """衍生品历史数据同步器。"""

    def __init__(
        self,
        db_path: str | Path = "data/derivatives.db",
        dry_run: bool = False,
        proxy_url: str | None = None,
    ) -> None:
        self._db = DerivativesDB(db_path)
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

    # -- Funding Rate --------------------------------------------------

    async def sync_funding_rate(self, symbol: str, days: int = DEFAULT_DAYS) -> int:
        binance_sym = symbol.replace("/", "").upper()
        latest_ts = self._db.get_latest_timestamp("funding_rates", "binance", symbol)

        if latest_ts:
            start_ts = latest_ts
            logger.info("  Funding Rate %s: 增量同步 from ts=%d", symbol, start_ts)
        else:
            start_dt = datetime.now(tz=timezone.utc) - timedelta(days=days)
            start_ts = int(start_dt.timestamp() * 1000)
            logger.info("  Funding Rate %s: 首次同步 from %s", symbol, start_dt.date())

        if self._dry_run:
            logger.info("  [DRY RUN] would sync funding rate for %s", symbol)
            return 0

        all_rows: list[tuple] = []
        current_start = start_ts

        while True:
            try:
                data = await self._fetch_json(
                    f"{BINANCE_FAPI}/fapi/v1/fundingRate",
                    {"symbol": binance_sym, "startTime": current_start, "limit": FUNDING_LIMIT},
                )
            except Exception as exc:
                logger.warning("  Funding Rate %s fetch failed: %s", symbol, exc)
                break

            if not data:
                break

            new_rows = []
            for row in data:
                ts = int(row["fundingTime"])
                if ts < start_ts:
                    continue
                new_rows.append((
                    "binance", symbol, ts,
                    float(row["fundingRate"]),
                    float(row.get("markPrice", 0)),
                    float(row.get("indexPrice", 0)),
                    int(row.get("nextFundingTime", 0)),
                ))
            all_rows.extend(new_rows)

            if len(data) < FUNDING_LIMIT:
                break
            latest = max(int(row["fundingTime"]) for row in data)
            if latest <= current_start:
                break
            current_start = latest

        if all_rows:
            count = self._db.put_funding_rates(all_rows)
            self._total_synced += count
            logger.info("  Funding Rate %s: +%d rows", symbol, count)
            return count

        logger.info("  Funding Rate %s: 无新数据", symbol)
        return 0

    # -- Open Interest -------------------------------------------------

    async def sync_open_interest(self, symbol: str, period: str, days: int = DEFAULT_DAYS) -> int:
        binance_sym = symbol.replace("/", "").upper()

        # Binance OI API only supports limit, no startTime/endTime pagination.
        # Fetch latest batch (500 records) and upsert.
        logger.info("  OI %s/%s: 拉取最新数据 (limit=%d)", symbol, period, OI_LIMIT)

        if self._dry_run:
            return 0

        try:
            data = await self._fetch_json(
                f"{BINANCE_FAPI}/futures/data/openInterestHist",
                {"symbol": binance_sym, "period": period, "limit": OI_LIMIT},
            )
        except Exception as exc:
            logger.warning("  OI %s/%s fetch failed: %s", symbol, period, exc)
            return 0

        if not data:
            logger.info("  OI %s/%s: 无数据", symbol, period)
            return 0

        rows = [
            (
                "binance", symbol, period, int(row["timestamp"]),
                float(row["sumOpenInterest"]),
                float(row.get("sumOpenInterestValue", 0)),
            )
            for row in data
        ]

        # Filter: only insert rows newer than what we have
        latest_ts = self._db.get_latest_timestamp("open_interest", "binance", symbol, period)
        if latest_ts:
            rows = [r for r in rows if r[3] > latest_ts]

        if rows:
            count = self._db.put_open_interest(rows)
            self._total_synced += count
            logger.info("  OI %s/%s: +%d rows", symbol, period, count)
            return count

        logger.info("  OI %s/%s: 无新数据", symbol, period)
        return 0

    # -- Taker Ratio ---------------------------------------------------

    async def sync_taker_ratio(self, symbol: str, period: str, days: int = DEFAULT_DAYS) -> int:
        binance_sym = symbol.replace("/", "").upper()

        # Binance Taker API only supports limit, no startTime/endTime pagination.
        # Fetch latest batch (500 records) and upsert.
        logger.info("  Taker %s/%s: 拉取最新数据 (limit=%d)", symbol, period, TAKER_LIMIT)

        if self._dry_run:
            return 0

        try:
            data = await self._fetch_json(
                f"{BINANCE_FAPI}/futures/data/takerlongshortRatio",
                {"symbol": binance_sym, "period": period, "limit": TAKER_LIMIT},
            )
        except Exception as exc:
            logger.warning("  Taker %s/%s fetch failed: %s", symbol, period, exc)
            return 0

        if not data:
            logger.info("  Taker %s/%s: 无数据", symbol, period)
            return 0

        rows = []
        for row in data:
            ts = int(row["timestamp"])
            buy_vol = float(row["buyVol"])
            sell_vol = float(row["sellVol"])
            ratio = buy_vol / sell_vol if sell_vol != 0 else 0.0
            rows.append(("binance", symbol, period, ts, buy_vol, sell_vol, ratio))

        # Filter: only insert rows newer than what we have
        latest_ts = self._db.get_latest_timestamp("taker_ratio", "binance", symbol, period)
        if latest_ts:
            rows = [r for r in rows if r[3] > latest_ts]

        if rows:
            count = self._db.put_taker_ratio(rows)
            self._total_synced += count
            logger.info("  Taker %s/%s: +%d rows", symbol, period, count)
            return count

        logger.info("  Taker %s/%s: 无新数据", symbol, period)
        return 0

    # -- Batch sync ----------------------------------------------------

    async def sync_all(
        self,
        symbols: list[str] | None = None,
        oi_periods: list[str] | None = None,
        taker_periods: list[str] | None = None,
        days: int = DEFAULT_DAYS,
    ) -> dict[str, int]:
        symbols = symbols or DEFAULT_SYMBOLS
        oi_periods = oi_periods or DEFAULT_OI_PERIODS
        taker_periods = taker_periods or DEFAULT_TAKER_PERIODS

        results: dict[str, int] = {}
        total = len(symbols) + len(symbols) * len(oi_periods) + len(symbols) * len(taker_periods)
        completed = 0

        logger.info("=" * 60)
        logger.info("衍生品数据同步开始")
        logger.info("  数据库: derivatives.db")
        logger.info("  交易对: %s", symbols)
        logger.info("  代理:   %s", self._proxy_url or "none")
        logger.info("  历史:   %d 天", days)
        logger.info("  总任务: %d", total)
        logger.info("=" * 60)

        for symbol in symbols:
            # Funding Rate
            completed += 1
            try:
                count = await self.sync_funding_rate(symbol, days=days)
                results[f"funding/{symbol}"] = count
            except Exception as exc:
                logger.error("  Funding %s 失败: %s", symbol, exc)
                results[f"funding/{symbol}"] = -1

            # Open Interest
            for period in oi_periods:
                completed += 1
                try:
                    count = await self.sync_open_interest(symbol, period, days=days)
                    results[f"oi/{symbol}/{period}"] = count
                except Exception as exc:
                    logger.error("  OI %s/%s 失败: %s", symbol, period, exc)
                    results[f"oi/{symbol}/{period}"] = -1

            # Taker Ratio
            for period in taker_periods:
                completed += 1
                try:
                    count = await self.sync_taker_ratio(symbol, period, days=days)
                    results[f"taker/{symbol}/{period}"] = count
                except Exception as exc:
                    logger.error("  Taker %s/%s 失败: %s", symbol, period, exc)
                    results[f"taker/{symbol}/{period}"] = -1

        return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="同步衍生品历史数据到本地 SQLite")
    parser.add_argument("--db", default="data/derivatives.db", help="SQLite 数据库路径")
    parser.add_argument("--dry-run", action="store_true", help="只显示将要同步的内容")
    parser.add_argument("--symbols", nargs="+", default=None, help="交易对列表")
    parser.add_argument("--periods", nargs="+", default=None, help="OI/Taker周期 (default: 1h 4h 1d)")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="首次同步历史天数")
    parser.add_argument("--proxy", default=None, help="HTTP代理URL (e.g. http://<host>:<port>)")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    proxy = args.proxy or None
    sync = DerivativesSync(db_path=args.db, dry_run=args.dry_run, proxy_url=proxy)

    try:
        start_time = datetime.now()

        async def _run():
            results = await sync.sync_all(
                symbols=args.symbols,
                oi_periods=args.periods,
                taker_periods=args.periods,
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
