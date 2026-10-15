"""Redis Pub/Sub for cross-worker WebSocket broadcasting.

When running with ``uvicorn --workers N``, market events from the exchange
WebSocket connection (which lives in one worker) need to reach WS clients
connected to other workers.  This module provides:

- ``publish(channel, data)``: publish an event to Redis
- ``start_subscriber(on_message)``: background task that listens for events
  and forwards them to the local ConnectionManager
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Coroutine

from app.core.redis import get_redis

logger = logging.getLogger(__name__)

_PUBSUB_CHANNEL = "selenespace:market_events"
#: 订阅断开后重连前的等待秒数。
_RECONNECT_DELAY = 2
#: 事件载荷里区分"频道"与"数据"的字段名。
_CHANNEL_FIELD = "channel"
_DATA_FIELD = "data"

MessageHandler = Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]]

_subscriber_task: asyncio.Task | None = None


def _encode_event(channel: str, data: dict[str, Any]) -> str:
    """把 (频道, 数据) 编成紧凑 JSON, 中文不转义。"""
    return json.dumps(
        {_CHANNEL_FIELD: channel, _DATA_FIELD: data},
        ensure_ascii=False,
        separators=(",", ":"),
    )


async def _publish_payload(redis: Any, payload: str) -> None:
    """发布并吞掉失败: Redis 短暂不可用不该影响行情主流程。"""
    try:
        await redis.publish(_PUBSUB_CHANNEL, payload)
    except Exception:
        logger.debug("cross-worker publish skipped (redis error)", exc_info=True)


async def publish(channel: str, data: dict[str, Any]) -> None:
    """Publish a market event to Redis Pub/Sub for cross-worker delivery."""
    redis = get_redis()
    if redis is not None:
        await _publish_payload(redis, _encode_event(channel, data))


async def _forward_message(raw_msg: dict[str, Any], on_message: MessageHandler) -> None:
    """解析单条消息并转发; 解析失败只记日志, 不打断订阅。"""
    try:
        payload = json.loads(raw_msg["data"])
    except Exception:
        logger.debug("Pub/Sub message parse error", exc_info=True)
        return
    await on_message(payload.get(_CHANNEL_FIELD, ""), payload.get(_DATA_FIELD, {}))


async def _consume(pubsub: Any, on_message: MessageHandler) -> None:
    """把订阅流里逐条 message 转给 on_message(其余控制帧跳过)。"""
    async for raw_msg in pubsub.listen():
        if raw_msg["type"] != "message":
            continue
        try:
            await _forward_message(raw_msg, on_message)
        except Exception:
            logger.debug("Pub/Sub handler error", exc_info=True)


async def _subscribe_once(redis: Any, on_message: MessageHandler) -> None:
    """建一次订阅并吃到流结束(或被取消)。"""
    pubsub = redis.pubsub()
    await pubsub.subscribe(_PUBSUB_CHANNEL)
    logger.info("Redis Pub/Sub subscriber started on %s", _PUBSUB_CHANNEL)
    await _consume(pubsub, on_message)


async def _listen(on_message: MessageHandler, redis: Any) -> None:
    """持续订阅; 连接异常则隔几秒重连, 收到取消则退出。"""
    while True:
        try:
            await _subscribe_once(redis, on_message)
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning(
                "Redis Pub/Sub subscriber error, reconnecting in %ds",
                _RECONNECT_DELAY,
                exc_info=True,
            )
            await asyncio.sleep(_RECONNECT_DELAY)


def _subscriber_running() -> bool:
    """是否已有在跑的订阅任务(避免重复起)。"""
    return _subscriber_task is not None and not _subscriber_task.done()


def _start_task(on_message: MessageHandler, redis: Any) -> None:
    """建订阅后台任务并记账。"""
    global _subscriber_task
    _subscriber_task = asyncio.create_task(
        _listen(on_message, redis), name="redis-pubsub-subscriber"
    )


def _subscriber_redis() -> Any:
    """订阅用的 Redis 句柄; 不可用时记一条并返回 None。"""
    redis = get_redis()
    if redis is None:
        logger.info("Redis not available, cross-worker pub/sub disabled")
    return redis


async def start_subscriber(on_message: MessageHandler) -> None:
    """Start a background task that subscribes to Redis Pub/Sub and forwards
    events to the local WS broadcast function.

    ``on_message(channel, data)`` is called for each received event.
    """
    if _subscriber_running():
        return
    redis = _subscriber_redis()
    if redis is None:
        return
    _start_task(on_message, redis)


async def stop_subscriber() -> None:
    """Stop the background subscriber task."""
    global _subscriber_task
    task = _subscriber_task
    if task is None:
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    _subscriber_task = None
