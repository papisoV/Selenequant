"""Tick replay — chronologically playback a trade tick stream.

Used by tick-level backtest engines to simulate trade-by-trade fills.
"""

from __future__ import annotations

from typing import Any, Iterator


class TickReplay:
    """Replay a trade tick stream in chronological order.

    Args:
        trades: List of trade dicts (must be pre-sorted by timestamp_ms).
        speed: Speed multiplier (1.0 = real-time, higher = faster).
    """

    def __init__(
        self,
        trades: list[dict[str, Any]],
        speed: float = 1.0,
    ) -> None:
        self._trades = sorted(trades, key=lambda t: t["timestamp_ms"])
        self._speed = speed
        self._cursor = 0
        self.speed = speed

    @classmethod
    def from_store(
        cls,
        store: Any,  # TickStore (avoid circular import)
        symbol: str = "",
        start_ms: int = 0,
        end_ms: int = 9_999_999_999_999,
        speed: float = 1.0,
    ) -> TickReplay:
        """Create a TickReplay from a TickStore query."""
        trades = store.query(symbol=symbol, start_ms=start_ms, end_ms=end_ms)
        return cls(trades, speed=speed)

    def replay(self) -> Iterator[dict[str, Any]]:
        """Yield trades one by one in chronological order."""
        while self._cursor < len(self._trades):
            yield self._trades[self._cursor]
            self._cursor += 1

    def seek(self, timestamp_ms: int) -> None:
        """Skip to the first trade at or after timestamp_ms."""
        for i, trade in enumerate(self._trades):
            if trade["timestamp_ms"] >= timestamp_ms:
                self._cursor = i
                return
        self._cursor = len(self._trades)

    def reset(self) -> None:
        """Reset cursor to the beginning."""
        self._cursor = 0

    def __len__(self) -> int:
        return len(self._trades)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return self.replay()
