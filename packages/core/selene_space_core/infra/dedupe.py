"""TTL deduplication with bounded memory: have I seen this key recently?"""

from __future__ import annotations

import time
from collections import OrderedDict


class Deduplicator:
    """Remembers keys for ttl_seconds. First sight -> False, repeats -> True.
    Oldest entries are evicted past max_size."""

    def __init__(self, ttl_seconds: float = 300.0, max_size: int = 10_000) -> None:
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._seen: OrderedDict[str, float] = OrderedDict()

    def is_duplicate(self, key: str) -> bool:
        now = time.monotonic()
        self._evict_expired(now)
        if key in self._seen:
            return True
        self._seen[key] = now
        while len(self._seen) > self._max_size:
            self._seen.popitem(last=False)
        return False

    @property
    def size(self) -> int:
        self._evict_expired(time.monotonic())
        return len(self._seen)

    def clear(self) -> None:
        self._seen.clear()

    def _evict_expired(self, now: float) -> None:
        cutoff = now - self._ttl
        while self._seen:
            _, stamp = next(iter(self._seen.items()))
            if stamp > cutoff:
                break
            self._seen.popitem(last=False)
