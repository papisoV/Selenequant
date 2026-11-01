"""Per-key execution lanes: one worker per key, keys run concurrently."""

from __future__ import annotations

import asyncio
from typing import Any, Callable, Hashable


class KeyedQueue:
    """Serialize async work that shares a key; independent keys proceed in parallel."""

    def __init__(self) -> None:
        self._lanes: dict[Hashable, asyncio.Lock] = {}
        self._lane_refs: dict[Hashable, int] = {}

    def _lock_for(self, key: Hashable) -> asyncio.Lock:
        lock = self._lanes.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._lanes[key] = lock
            self._lane_refs[key] = 0
        self._lane_refs[key] += 1
        return lock

    def _release_lock(self, key: Hashable) -> None:
        self._lane_refs[key] -= 1
        if self._lane_refs[key] <= 0:
            self._lane_refs.pop(key, None)
            self._lanes.pop(key, None)

    async def execute(self, key: Hashable, fn: Callable[..., Any]) -> Any:
        lock = self._lock_for(key)
        try:
            async with lock:
                return await fn()
        finally:
            self._release_lock(key)
