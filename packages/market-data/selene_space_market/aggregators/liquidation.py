"""Rolling-window liquidation statistics.

Keeps a bounded tape of forced-liquidation events and, on each new event,
recomputes long/short totals for the standard windows (15m through 24h).
Fresh stats objects are pushed to listeners — one per window per event.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from selene_space_types.derivatives import LiquidationEvent, LiquidationStats

logger = logging.getLogger(__name__)

# Window label -> span in milliseconds.
WINDOW_SPANS_MS: dict[str, int] = {
    "15m": 15 * 60_000,
    "30m": 30 * 60_000,
    "1h": 60 * 60_000,
    "4h": 4 * 3_600_000,
    "24h": 24 * 3_600_000,
}

_TAPE_CAP = 10_000


def _now_ms() -> int:
    return int(time.time() * 1000)


class LiquidationAggregator:
    """Per-window liquidation tallies refreshed on every event."""

    def __init__(self) -> None:
        self._tape: list[LiquidationEvent] = []
        self._listeners: list[Callable[[LiquidationStats], Any]] = []
        self._latest: dict[str, LiquidationStats] = {}

    def on_stats_update(self, callback: Callable[[LiquidationStats], Any]) -> None:
        """Register a listener; it fires once per window per event."""
        self._listeners.append(callback)

    def process(self, event: LiquidationEvent) -> None:
        """Ingest one liquidation; refresh every window and notify listeners."""
        self._tape.append(event)
        if len(self._tape) > _TAPE_CAP:
            del self._tape[: len(self._tape) - _TAPE_CAP]
        self._drop_expired()
        now_ms = _now_ms()
        for label, span in WINDOW_SPANS_MS.items():
            stats = self._tally(label, now_ms - span, now_ms)
            self._latest[label] = stats
            self._fanout(stats)

    def get_stats(self, window: str = "1h") -> LiquidationStats | None:
        """Latest tally for *window*; None before the first event."""
        return self._latest.get(window)

    def get_all_stats(self) -> dict[str, LiquidationStats]:
        """Latest tallies for every tracked window."""
        return dict(self._latest)

    def get_recent_events(self, limit: int = 50) -> list[LiquidationEvent]:
        """Most recent raw events, oldest first."""
        return self._tape[-limit:] if limit < len(self._tape) else list(self._tape)

    # -- internals -------------------------------------------------------

    def _drop_expired(self) -> None:
        horizon = _now_ms() - WINDOW_SPANS_MS["24h"]
        keep_from = 0
        while keep_from < len(self._tape) and self._tape[keep_from].timestamp < horizon:
            keep_from += 1
        if keep_from:
            del self._tape[:keep_from]

    def _tally(self, window: str, cutoff: int, now_ms: int) -> LiquidationStats:
        long_usd = 0.0
        short_usd = 0.0
        long_n = 0
        short_n = 0
        largest = 0.0
        for e in self._tape:
            if e.timestamp < cutoff:
                continue
            notional = e.notional_usd
            if e.side == "long":
                long_usd += notional
                long_n += 1
            else:
                short_usd += notional
                short_n += 1
            if notional > largest:
                largest = notional
        return LiquidationStats(
            symbol="ALL",
            window=window,
            long_liquidated_usd=long_usd,
            short_liquidated_usd=short_usd,
            total_liquidated_usd=long_usd + short_usd,
            long_count=long_n,
            short_count=short_n,
            largest_single_usd=largest,
            timestamp=now_ms,
        )

    def _fanout(self, stats: LiquidationStats) -> None:
        for cb in self._listeners:
            try:
                cb(stats)
            except Exception:
                logger.exception("liquidation stats listener raised")
