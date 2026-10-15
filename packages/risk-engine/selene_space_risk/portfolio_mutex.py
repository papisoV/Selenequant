"""PortfolioMutex — cross-strategy symbol-level mutual exclusion.

Absorbed design intent (L1) from LEAN's manual single isolation: orders from
distinct strategies are issued by distinct actors, and concurrent position
openings on the same symbol should be mutually exclusive unless explicitly
allowed. This closes the gap below the aggregate portfolio rules
(total/net exposure, margin utilization): a portfolio can stay within its
aggregate budget while two strategies still pile into one symbol.

Lock lifecycle is external — acquire once the order actually fills, release
when the position reaches zero. A TTL auto-releases stale locks so a crashed
strategy cannot deadlock a symbol forever.

Single-process asyncio scope: an in-process dict suffices (no distributed
lock). Not thread-safe — the live engine's single event loop serializes access.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

DEFAULT_LOCK_TTL_MS: int = 300_000


def _default_clock_ms() -> int:
    return int(time.time() * 1000)


@dataclass(frozen=True)
class SymbolLock:
    """Immutable snapshot of one held symbol lock."""

    symbol: str
    owner_strategy: str | None  # None = free; the mutex never stores free entries
    held_since_ms: int
    expires_at_ms: int | None  # None = no TTL (never produced by the default mutex)


class PortfolioMutex:
    """Symbol-level mutex: at most one strategy holds a symbol at a time.

    - ``acquire`` returns True when the symbol is free or already held by the
      same strategy (re-entrant), False when another strategy holds it.
    - ``release`` only clears the lock for its holder; any other caller is
      ignored and never raises.
    - Expired locks auto-release on access (crash-safe TTL).

    The clock is injectable so expiry behavior is testable without sleeping.
    """

    def __init__(
        self,
        ttl_ms: int = DEFAULT_LOCK_TTL_MS,
        *,
        clock: Callable[[], int] | None = None,
    ) -> None:
        if ttl_ms <= 0:
            raise ValueError("ttl_ms must be positive")
        self._ttl_ms = ttl_ms
        self._clock = clock if clock is not None else _default_clock_ms
        self._locks: dict[str, SymbolLock] = {}

    def acquire(self, symbol: str, strategy_id: str) -> bool:
        """Take the lock for ``strategy_id``; True when granted."""
        lock = self._lock_for(symbol)
        now = self._clock()
        if lock is None:
            self._locks[symbol] = SymbolLock(
                symbol=symbol,
                owner_strategy=strategy_id,
                held_since_ms=now,
                expires_at_ms=now + self._ttl_ms,
            )
            return True
        if lock.owner_strategy == strategy_id:
            # Re-entry from the live owner refreshes the deadline: an active
            # strategy keeps its lock as long as it keeps trading the symbol.
            self._locks[symbol] = SymbolLock(
                symbol=symbol,
                owner_strategy=strategy_id,
                held_since_ms=lock.held_since_ms,
                expires_at_ms=now + self._ttl_ms,
            )
            return True
        return False

    def release(self, symbol: str, strategy_id: str) -> None:
        """Clear the lock when ``strategy_id`` is its holder; else no-op."""
        lock = self._lock_for(symbol)
        if lock is None or lock.owner_strategy != strategy_id:
            return
        del self._locks[symbol]

    def is_locked(self, symbol: str) -> bool:
        """True when an unexpired lock exists on ``symbol``."""
        return self._lock_for(symbol) is not None

    def owner_of(self, symbol: str) -> str | None:
        """Current holder's strategy id, or None when free/expired."""
        lock = self._lock_for(symbol)
        return lock.owner_strategy if lock is not None else None

    def active_locks(self) -> dict[str, str]:
        """Snapshot of symbol → owner for all live (unexpired) locks."""
        result: dict[str, str] = {}
        for symbol in list(self._locks):
            lock = self._lock_for(symbol)
            if lock is not None and lock.owner_strategy is not None:
                result[symbol] = lock.owner_strategy
        return result

    def _lock_for(self, symbol: str) -> SymbolLock | None:
        """The live lock for ``symbol``, pruning expired entries on access."""
        lock = self._locks.get(symbol)
        if lock is None:
            return None
        if lock.expires_at_ms is not None and self._clock() >= lock.expires_at_ms:
            del self._locks[symbol]
            return None
        return lock
