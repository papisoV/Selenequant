"""Redis connection manager with health check and graceful degradation.

When Redis is unavailable the application continues to work — callers
get ``None`` from ``get_redis()`` and should fall through to the
exchange REST API directly.
"""

from __future__ import annotations

import logging
import os

import redis.asyncio as aioredis

logger = logging.getLogger(__name__)

_redis_client: aioredis.Redis | None = None

#: 连接池与超时参数(秒)。超时短, 避免 Redis 掉线拖慢请求。
_MAX_CONNECTIONS = 200
_SOCKET_TIMEOUT = 5

_CONNECTION_OPTIONS: dict[str, object] = {
    "decode_responses": True,
    "max_connections": _MAX_CONNECTIONS,
    "socket_connect_timeout": _SOCKET_TIMEOUT,
    "socket_timeout": _SOCKET_TIMEOUT,
    "retry_on_timeout": True,
}


def _host_only(url: str) -> str:
    """URL 只取 @ 之后的部分, 免得把带密码的连接串写进日志。"""
    return url.split("@")[-1]


async def _connect(url: str) -> aioredis.Redis:
    """建连接池并 ping 一次确认可用。"""
    client = aioredis.from_url(url, **_CONNECTION_OPTIONS)
    await client.ping()  # type: ignore[misc]  # redis-py stub: ping -> Awaitable[bool] | bool
    return client


async def init_redis() -> aioredis.Redis | None:
    """Create the shared Redis connection pool.  Returns ``None`` if
    Redis is not configured or unreachable (graceful degradation).
    """
    global _redis_client

    url = os.getenv("SELENE_REDIS_URL", "").strip()
    if not url:
        logger.info("SELENE_REDIS_URL not set — Redis caching disabled")
        return None

    try:
        client = await _connect(url)
    except Exception:
        logger.warning(
            "Redis unreachable at %s — caching disabled",
            _host_only(url),
            exc_info=True,
        )
        return None

    _redis_client = client
    logger.info("Redis connected: %s", _host_only(url))
    return client


async def close_redis() -> None:
    """Gracefully close the Redis connection pool."""
    global _redis_client
    client = _redis_client
    if client is None:
        return
    await client.aclose()
    _redis_client = None
    logger.info("Redis connection closed")


def get_redis() -> aioredis.Redis | None:
    """Return the shared Redis client, or ``None`` if unavailable."""
    return _redis_client


async def redis_health_check() -> bool:
    """Quick ping for health monitoring."""
    client = _redis_client
    if client is None:
        return False
    try:
        # redis-py 的 ping 存根是 Awaitable[bool] | bool, 运行时始终是协程。
        return bool(await client.ping())  # type: ignore[misc]
    except Exception:
        return False
