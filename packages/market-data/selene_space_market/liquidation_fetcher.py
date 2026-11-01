"""CoinGlass liquidation heatmap + aggregate stats.

``LiquidationFetcher`` is the only public object: it pulls the
``/liquidation/detail/chart`` series for one symbol/timeframe, keeps a short
TTL cache, and derives per-window totals (long vs short dollars, counts,
average ticket size) that the /liquidations endpoints serve.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

import httpx

from selene_space_core.resilience import OutboundGateway

logger = logging.getLogger(__name__)

COINGLASS_BASE_URL = "https://open-api.coinglass.com/public/v2"
COINGLASS_API_KEY = ""

CACHE_TTL_MS = 60_000


@dataclass(frozen=True)
class LiquidationHeatmapItem:
    timestamp: int
    short_amount_usd: float
    long_amount_usd: float
    total_amount_usd: float
    short_count: int
    long_count: int
    price_high: float
    price_low: float


@dataclass(frozen=True)
class LiquidationStats:
    symbol: str
    timeframe: str
    total_long_liquidations_usd: float
    total_short_liquidations_usd: float
    total_liquidations_usd: float
    long_count: int
    short_count: int
    avg_long_size_usd: float
    avg_short_size_usd: float
    timestamp: int


def _num(raw: dict, key: str) -> float:
    """Coinglass rows carry numbers as strings or nulls; coerce safely."""
    value = raw.get(key)
    try:
        return float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _row_to_item(row: dict) -> LiquidationHeatmapItem | None:
    try:
        stamp = int(row.get("timestamp", 0))
    except (TypeError, ValueError):
        return None
    long_usd = _num(row, "longAmount")
    short_usd = _num(row, "shortAmount")
    return LiquidationHeatmapItem(
        timestamp=stamp,
        short_amount_usd=short_usd,
        long_amount_usd=long_usd,
        total_amount_usd=long_usd + short_usd,
        short_count=int(_num(row, "shortCount")),
        long_count=int(_num(row, "longCount")),
        price_high=_num(row, "priceHigh"),
        price_low=_num(row, "priceLow"),
    )


def _summarize(symbol: str, timeframe: str, items: list[LiquidationHeatmapItem]) -> LiquidationStats | None:
    if not items:
        return None
    long_usd = sum(i.long_amount_usd for i in items)
    short_usd = sum(i.short_amount_usd for i in items)
    long_n = sum(i.long_count for i in items)
    short_n = sum(i.short_count for i in items)
    return LiquidationStats(
        symbol=symbol,
        timeframe=timeframe,
        total_long_liquidations_usd=long_usd,
        total_short_liquidations_usd=short_usd,
        total_liquidations_usd=long_usd + short_usd,
        long_count=long_n,
        short_count=short_n,
        avg_long_size_usd=(long_usd / long_n) if long_n else 0.0,
        avg_short_size_usd=(short_usd / short_n) if short_n else 0.0,
        timestamp=int(time.time() * 1000),
    )


class LiquidationFetcher:
    def __init__(self, *, proxy_url: str | None = None) -> None:
        self._proxy_url = proxy_url
        self._gateway: OutboundGateway | None = None
        # key "SYM:tf" -> (fetched_at_ms, heatmap rows)
        self._heat: dict[str, tuple[int, list[LiquidationHeatmapItem]]] = {}
        self._stats_by_key: dict[str, LiquidationStats] = {}
        self._refresh_lock = asyncio.Lock()

    async def _get_client(self) -> OutboundGateway:
        if self._gateway is None or self._gateway.is_closed:
            self._gateway = OutboundGateway(
                proxy=self._proxy_url,
                default_timeout=httpx.Timeout(30.0, connect=15.0),
                limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                default_circuit=True,
            )
        return self._gateway

    @staticmethod
    def _key(symbol: str, timeframe: str) -> str:
        return f"{symbol}:{timeframe}"

    async def get_liquidation_heatmap(
        self,
        symbol: str = "BTC",
        timeframe: str = "h4",
        *,
        force: bool = False,
    ) -> list[LiquidationHeatmapItem]:
        """Heatmap rows oldest-first; cached for CACHE_TTL_MS unless *force*."""
        key = self._key(symbol, timeframe)

        fresh = self._heat.get(key)
        if fresh and not force and self._is_fresh(fresh[0]):
            return fresh[1]

        async with self._refresh_lock:
            fresh = self._heat.get(key)
            if fresh and not force and self._is_fresh(fresh[0]):
                return fresh[1]

            items = await self._fetch_liquidation_chart(symbol, timeframe)
            now_ms = int(time.time() * 1000)
            self._heat[key] = (now_ms, items)
            stats = _summarize(symbol, timeframe, items)
            if stats is not None:
                self._stats_by_key[key] = stats
            return items

    async def get_liquidation_stats(
        self,
        symbol: str = "BTC",
        timeframe: str = "h4",
    ) -> LiquidationStats | None:
        key = self._key(symbol, timeframe)
        if key in self._stats_by_key:
            return self._stats_by_key[key]
        await self.get_liquidation_heatmap(symbol, timeframe)
        return self._stats_by_key.get(key)

    @staticmethod
    def _is_fresh(stamp_ms: int) -> bool:
        return (int(time.time() * 1000) - stamp_ms) < CACHE_TTL_MS

    async def _fetch_liquidation_chart(self, symbol: str, timeframe: str) -> list[LiquidationHeatmapItem]:
        client = await self._get_client()
        url = f"{COINGLASS_BASE_URL}/liquidation/detail/chart"
        params: dict[str, str] = {"symbol": symbol.upper(), "timeType": timeframe}

        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            body = resp.json()
        except httpx.HTTPStatusError as exc:
            logger.error("coinglass HTTP %s for %s/%s", exc.response.status_code, symbol, timeframe)
            return []
        except Exception as exc:
            logger.error("coinglass request failed for %s/%s: %s", symbol, timeframe, exc)
            return []

        if body.get("error"):
            logger.warning("coinglass rejected %s/%s: %s", symbol, timeframe, body["error"])
            return []

        rows = body.get("data", [])
        if not isinstance(rows, list):
            return []

        items: list[LiquidationHeatmapItem] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            item = _row_to_item(row)
            if item is not None:
                items.append(item)

        logger.info("coinglass heatmap %s/%s: %d points", symbol, timeframe, len(items))
        return items

    async def close(self) -> None:
        if self._gateway and not self._gateway.is_closed:
            await self._gateway.aclose()
            self._gateway = None
