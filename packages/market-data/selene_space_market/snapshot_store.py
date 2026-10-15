"""In-memory store of latest L2 order book snapshots.

Keyed by trading symbol so consumers can always ask "what does the current
book look like for this pair right now" without touching the WS layer.
"""

from __future__ import annotations

import threading

from selene_space_types.market import OrderBookL2Snapshot


class SnapshotStore:
    """Latest-book-per-symbol container, safe under concurrent use."""

    def __init__(self) -> None:
        self._guard = threading.Lock()
        self._snaps: dict[str, OrderBookL2Snapshot] = {}

    def update(self, symbol: str, snap: OrderBookL2Snapshot) -> None:
        """Replace the stored snapshot for *symbol*."""
        with self._guard:
            self._snaps[symbol] = snap

    def get_snapshot(self, symbol: str) -> OrderBookL2Snapshot | None:
        with self._guard:
            return self._snaps.get(symbol)

    def remove(self, symbol: str) -> bool:
        """Drop one symbol; True only when it was present."""
        with self._guard:
            if symbol in self._snaps:
                del self._snaps[symbol]
                return True
            return False

    def symbols(self) -> list[str]:
        """Symbols currently holding a snapshot."""
        with self._guard:
            return list(self._snaps.keys())

    def clear(self) -> None:
        with self._guard:
            self._snaps.clear()

    @property
    def size(self) -> int:
        """Number of tracked symbols."""
        with self._guard:
            return len(self._snaps)
