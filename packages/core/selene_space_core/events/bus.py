"""Typed pub/sub bus: subscribe by event class, publish instances."""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

logger = logging.getLogger(__name__)

# A handler that fails on every event would otherwise log once per event — and
# the bus is published per trade. Repeats of the same failure are counted and
# folded into the next line instead of filling the rotating log.
_FAILURE_LOG_INTERVAL_SECONDS = 60.0
_FAILURE_LOG_MAX_KEYS = 512


def _handler_name(handler: Callable) -> str:
    """Readable handler identity for logs; ``repr`` of a bound method can be huge."""
    return getattr(handler, "__qualname__", None) or repr(handler)


class EventBus:
    """Dispatch dataclass events to handlers registered per event type.
    Handlers receive the event instance; their return values are ignored."""

    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable]] = {}
        self._lock = threading.Lock()
        # (handler name, event type, exception type) -> [last_logged_monotonic, suppressed]
        self._failure_log_state: dict[tuple[str, str, str], list] = {}

    def subscribe(self, event_type: type, handler: Callable) -> None:
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

    def unsubscribe(self, event_type: type, handler: Callable) -> None:
        with self._lock:
            bucket = self._handlers.get(event_type)
            if bucket and handler in bucket:
                bucket.remove(handler)

    def publish(self, event) -> None:
        with self._lock:
            targets = [
                h
                for etype, bucket in self._handlers.items()
                if isinstance(event, etype)
                for h in bucket
            ]
        for handler in targets:
            try:
                result = handler(event)
                # Await coroutines so async handlers still run to completion.
                import asyncio

                if asyncio.iscoroutine(result):
                    try:
                        loop = asyncio.get_running_loop()
                    except RuntimeError:
                        asyncio.run(result)
                    else:
                        loop.create_task(result)
            except Exception as exc:
                self._log_handler_failure(handler, event, exc)

    async def publish_async(self, event) -> None:
        """Await dispatch: async handlers run inline, sync handlers call directly."""
        import inspect

        with self._lock:
            targets = [
                h
                for etype, bucket in self._handlers.items()
                if isinstance(event, etype)
                for h in bucket
            ]
        for handler in targets:
            try:
                if inspect.iscoroutinefunction(handler):
                    await handler(event)
                else:
                    handler(event)
            except Exception as exc:
                self._log_handler_failure(handler, event, exc)

    def _log_handler_failure(self, handler: Callable, event, exc: Exception) -> None:
        """Record a handler failure, folding repeats into a suppressed count."""
        key = (_handler_name(handler), type(event).__name__, type(exc).__name__)
        now = time.monotonic()
        with self._lock:
            state = self._failure_log_state.get(key)
            if state is not None and now - state[0] < _FAILURE_LOG_INTERVAL_SECONDS:
                state[1] += 1
                return
            suppressed = state[1] if state is not None else 0
            if state is None and len(self._failure_log_state) >= _FAILURE_LOG_MAX_KEYS:
                self._failure_log_state.clear()
            self._failure_log_state[key] = [now, 0]

        if suppressed:
            logger.warning(
                "event handler %s raised on %s: %s: %s (%d more suppressed)",
                key[0],
                key[1],
                key[2],
                exc,
                suppressed,
            )
        else:
            logger.warning(
                "event handler %s raised on %s: %s: %s", key[0], key[1], key[2], exc
            )
        logger.debug("event handler traceback", exc_info=True)

    def handler_count(self, event_type: type) -> int:
        with self._lock:
            return len(self._handlers.get(event_type, []))

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()
            self._failure_log_state.clear()


_singleton: EventBus | None = None
_singleton_lock = threading.Lock()


def get_event_bus() -> EventBus:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = EventBus()
    return _singleton
