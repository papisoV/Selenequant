"""Typed pub/sub bus: subscribe by event class, publish instances."""

from __future__ import annotations

import threading
from typing import Callable


class EventBus:
    """Dispatch dataclass events to handlers registered per event type.
    Handlers receive the event instance; their return values are ignored."""

    def __init__(self) -> None:
        self._handlers: dict[type, list[Callable]] = {}
        self._lock = threading.Lock()

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
            except Exception:
                pass

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
            except Exception:
                pass

    def handler_count(self, event_type: type) -> int:
        with self._lock:
            return len(self._handlers.get(event_type, []))

    def clear(self) -> None:
        with self._lock:
            self._handlers.clear()


_singleton: EventBus | None = None
_singleton_lock = threading.Lock()


def get_event_bus() -> EventBus:
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = EventBus()
    return _singleton
