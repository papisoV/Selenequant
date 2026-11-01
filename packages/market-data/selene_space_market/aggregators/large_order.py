"""Whale-order tracker over consecutive L2 book snapshots.

For every snapshot we price each resting level; levels clearing the threshold
become "walls". Comparing against the previous snapshot per symbol yields
appeared / increased / decreased / disappeared transitions, each surfaced as
a :class:`LargeOrderEvent` and fanned out to listeners.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from selene_space_types.derivatives import LargeOrderEvent
from selene_space_types.market import OrderBookL2Snapshot, PriceLevel

logger = logging.getLogger(__name__)

_HISTORY_CAP = 200
# Relative notional change needed before a persisting wall counts as moved.
_GROWTH_STEP = 0.10


def _now_ms() -> int:
    return int(time.time() * 1000)


def _level_key(side: str, price: float) -> str:
    return f"{side}@{price}"


class LargeOrderDetector:
    """Tracks single-level walls above *threshold_usd* across snapshots."""

    def __init__(self, threshold_usd: float = 100_000.0) -> None:
        self._floor = max(0.0, threshold_usd)
        self._listeners: list[Callable[[LargeOrderEvent], Any]] = []
        self._history: list[LargeOrderEvent] = []
        # symbol -> {"bid@68000.0": notional} for walls live in the last snapshot
        self._live_walls: dict[str, dict[str, float]] = {}

    @property
    def threshold_usd(self) -> float:
        return self._floor

    @threshold_usd.setter
    def threshold_usd(self, value: float) -> None:
        self._floor = max(0.0, value)

    def on_large_order(self, callback: Callable[[LargeOrderEvent], Any]) -> None:
        """Register a listener; exceptions inside it are contained."""
        self._listeners.append(callback)

    def process(self, snapshot: OrderBookL2Snapshot) -> list[LargeOrderEvent]:
        """Diff one snapshot against the last for the same symbol."""
        now_ms = _now_ms()
        events: list[LargeOrderEvent] = []
        seen: dict[str, float] = {}

        for side, levels in (("bid", snapshot.bids), ("ask", snapshot.asks)):
            for rank, level in enumerate(levels):
                event = self._classify(
                    level, side, rank, snapshot, now_ms, seen
                )
                if event is not None:
                    events.append(event)

        gone = self._find_gone(snapshot.symbol, now_ms, seen)
        events.extend(gone)
        self._live_walls[snapshot.symbol] = seen

        for event in events:
            self._remember(event)
            self._fanout(event)
        return events

    def get_recent(self, limit: int = 50) -> list[LargeOrderEvent]:
        """Most recent wall transitions, oldest first."""
        return self._history[-limit:] if limit < len(self._history) else list(self._history)

    def get_current_walls(self, snapshot: OrderBookL2Snapshot) -> dict[str, list[dict[str, Any]]]:
        """Walls visible in *snapshot* right now, split by side."""
        report: dict[str, list[dict[str, Any]]] = {"bid_walls": [], "ask_walls": []}
        for side, levels in (("bid_walls", snapshot.bids), ("ask_walls", snapshot.asks)):
            for rank, level in enumerate(levels):
                notional = level.price * level.quantity
                if notional >= self._floor:
                    report[side].append(
                        {
                            "price": level.price,
                            "quantity": level.quantity,
                            "notional_usd": notional,
                            "rank": rank,
                        }
                    )
        return report

    # -- internals -------------------------------------------------------

    def _classify(
        self,
        level: PriceLevel,
        side: str,
        rank: int,
        snapshot: OrderBookL2Snapshot,
        now_ms: int,
        seen: dict[str, float],
    ) -> LargeOrderEvent | None:
        notional = level.price * level.quantity
        if notional < self._floor:
            return None
        key = _level_key(side, level.price)
        seen[key] = notional
        prior = self._live_walls.get(snapshot.symbol, {}).get(key)

        if prior is None:
            transition = "appeared"
        elif notional > prior * (1 + _GROWTH_STEP):
            transition = "increased"
        elif notional < prior * (1 - _GROWTH_STEP):
            transition = "decreased"
        else:
            return None

        return LargeOrderEvent(
            exchange=snapshot.exchange,
            symbol=snapshot.symbol,
            market_type=snapshot.market_type,
            side=side,
            price=level.price,
            quantity=level.quantity,
            notional_usd=notional,
            depth_rank=rank,
            event_type=transition,
            timestamp=now_ms,
        )

    def _find_gone(
        self, symbol: str, now_ms: int, seen: dict[str, float]
    ) -> list[LargeOrderEvent]:
        events: list[LargeOrderEvent] = []
        for key, prior_notional in self._live_walls.get(symbol, {}).items():
            if key in seen:
                continue
            side, _, price_text = key.partition("@")
            price = float(price_text)
            events.append(
                LargeOrderEvent(
                    exchange="",
                    symbol=symbol,
                    market_type="futures",
                    side=side,
                    price=price,
                    quantity=prior_notional / price if price > 0 else 0.0,
                    notional_usd=prior_notional,
                    depth_rank=0,
                    event_type="disappeared",
                    timestamp=now_ms,
                )
            )
        return events

    def _remember(self, event: LargeOrderEvent) -> None:
        self._history.append(event)
        if len(self._history) > _HISTORY_CAP:
            del self._history[: len(self._history) - _HISTORY_CAP]

    def _fanout(self, event: LargeOrderEvent) -> None:
        for cb in self._listeners:
            try:
                cb(event)
            except Exception:
                logger.exception("large-order listener raised")
