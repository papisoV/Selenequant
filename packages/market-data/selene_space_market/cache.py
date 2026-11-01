"""TTL + LRU caches for hot market data.

Two layers here: a generic expiry-aware cache with least-recently-used
eviction, and a thin market-specific wrapper holding the newest ticker and
candle per symbol.
"""

from __future__ import annotations

import threading
import time
from collections import OrderedDict
from typing import Generic, TypeVar

from selene_space_types.market import KlineEvent, TickerEvent

V = TypeVar("V")

_DEFAULT_TTL_SECONDS = 60.0
_DEFAULT_MAX_SIZE = 500


class TTLLRUCache(Generic[V]):
    """Values vanish after *ttl_seconds* or when more than *max_size* live.

    Reads refresh recency; eviction targets the coldest key first.
    """

    def __init__(
        self,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        max_size: int = _DEFAULT_MAX_SIZE,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError(f"ttl_seconds must be positive, got {ttl_seconds}")
        if max_size <= 0:
            raise ValueError(f"max_size must be positive, got {max_size}")
        self._ttl = ttl_seconds
        self._capacity = max_size
        self._lock = threading.Lock()
        self._items: OrderedDict[str, tuple[V, float]] = OrderedDict()

    def get(self, key: str) -> V | None:
        """Return the live value for *key*, refreshing its recency."""
        now = time.monotonic()
        with self._lock:
            found = self._items.get(key)
            if found is None:
                return None
            value, expires_at = found
            if now >= expires_at:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return value

    def put(self, key: str, value: V, ttl_seconds: float | None = None) -> None:
        """Insert or refresh *key*, evicting the oldest entry if over capacity.

        Optional per-entry *ttl_seconds* overrides the constructor TTL.
        """
        expires_at = time.monotonic() + (
            self._ttl if ttl_seconds is None else ttl_seconds
        )
        with self._lock:
            self._items[key] = (value, expires_at)
            self._items.move_to_end(key)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)

    def remove(self, key: str) -> bool:
        """Drop *key*; True only when it was cached."""
        with self._lock:
            if key in self._items:
                del self._items[key]
                return True
            return False

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._items)


class MarketDataCache:
    """Newest ticker + candle per symbol, both under one TTL policy."""

    def __init__(self, ttl_seconds: float = 5.0) -> None:
        self._tickers: TTLLRUCache[TickerEvent] = TTLLRUCache(
            ttl_seconds=ttl_seconds, max_size=_DEFAULT_MAX_SIZE
        )
        self._klines: TTLLRUCache[KlineEvent] = TTLLRUCache(
            ttl_seconds=ttl_seconds, max_size=_DEFAULT_MAX_SIZE
        )

    def put_ticker(self, symbol: str, ticker: TickerEvent) -> None:
        self._tickers.put(symbol, ticker)

    def get_ticker(self, symbol: str) -> TickerEvent | None:
        return self._tickers.get(symbol)

    def put_kline(self, symbol: str, kline: KlineEvent) -> None:
        self._klines.put(symbol, kline)

    def get_kline(self, symbol: str) -> KlineEvent | None:
        return self._klines.get(symbol)

    def clear(self) -> None:
        self._tickers.clear()
        self._klines.clear()
