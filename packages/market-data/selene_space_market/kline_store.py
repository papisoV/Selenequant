"""Redis-backed hot cache for candlesticks.

Layout: one sorted set per series, keyed ``kline:{exchange}:{market_type}:{symbol}:{interval}``,
scored by bar-open time so reads come back chronologically without sorting.
Keys expire and are trimmed to a bounded window to keep memory flat.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    import redis.asyncio as aioredis

from selene_space_types.market import KlineEvent

logger = logging.getLogger(__name__)

_TTL_DEFAULT = 86_400
_WINDOW_MAX = 5000


def _series_key(exchange: str, market_type: str, symbol: str, interval: str) -> str:
    return "/".join(("kline", exchange, market_type, symbol, interval))


T = TypeVar("T")


def _guarded(op_name: str, key: str, fn: Callable[[], Awaitable[T]]) -> Awaitable[T | None]:
    """Run *fn*, collapsing any Redis failure into None + a warning."""

    async def run() -> T | None:
        try:
            return await fn()
        except Exception:
            logger.warning("redis %s failed for %s", op_name, key, exc_info=True)
            return None

    return run()


class KlineStore:
    """Chronological candle cache over Redis sorted sets."""

    def __init__(self, redis: "aioredis.Redis", ttl: int = _TTL_DEFAULT) -> None:
        self._client = redis
        self._key_ttl = ttl

    # -- live path ---------------------------------------------------------

    async def append(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        candle: KlineEvent,
    ) -> None:
        """Upsert a single bar; one pipeline round-trip per WS tick."""
        key = _series_key(exchange, market_type, symbol, interval)
        score = float(candle.timestamp)

        async def op():
            pipe = self._client.pipeline(transaction=False)
            pipe.zremrangebyscore(key, score, score)
            pipe.zadd(key, {candle.model_dump_json(): score})
            pipe.expire(key, self._key_ttl)
            return await pipe.execute()

        await _guarded("append", key, op)

    # -- bulk path -----------------------------------------------------------

    async def put(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        candles: list[KlineEvent],
    ) -> int:
        """Write a batch of bars; returns how many members were new."""
        if not candles:
            return 0
        key = _series_key(exchange, market_type, symbol, interval)

        async def op():
            entries = {c.model_dump_json(): float(c.timestamp) for c in candles}
            pipe = self._client.pipeline(transaction=False)
            pipe.zadd(key, entries)
            pipe.expire(key, self._key_ttl)
            results = await pipe.execute()
            return int(results[0]) if results else 0

        added = await _guarded("put", key, op)
        if added is not None:
            await self._evict_oldest(key)
            return added
        return 0

    async def count(self, exchange: str, market_type: str, symbol: str, interval: str) -> int:
        """Number of bars currently cached for the series."""
        key = _series_key(exchange, market_type, symbol, interval)

        async def op():
            return int(await self._client.zcard(key))

        return (await _guarded("count", key, op)) or 0

    # -- read path -----------------------------------------------------------

    async def get(
        self,
        exchange: str,
        market_type: str,
        symbol: str,
        interval: str,
        *,
        since: int | None = None,
        limit: int = 500,
    ) -> list[KlineEvent]:
        """Read up to *limit* chronological bars, optionally from *since* onward."""
        key = _series_key(exchange, market_type, symbol, interval)

        async def op():
            if since is None:
                raw = await self._client.zrange(key, -limit, -1)
            else:
                raw = await self._client.zrangebyscore(
                    key, min=since, max="+inf", start=0, num=limit
                )
            return [KlineEvent.model_validate_json(item) for item in raw]

        return (await _guarded("get", key, op)) or []

    # -- internal ------------------------------------------------------------

    async def _evict_oldest(self, key: str) -> None:
        async def op():
            total = await self._client.zcard(key)
            excess = total - _WINDOW_MAX
            if excess > 0:
                await self._client.zremrangebyrank(key, 0, excess - 1)

        await _guarded("trim", key, op)
