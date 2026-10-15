"""Notional-size gate for the trade tape.

Every incoming trade is priced in USD; anything at or above the configured
threshold is promoted to a :class:`LargeTradeEvent`, fanned out to listeners,
and kept in a bounded ring so recent whale prints can be listed by the API.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Any

from selene_space_types.derivatives import LargeTradeEvent
from selene_space_types.market import TradeEvent

logger = logging.getLogger(__name__)

_HISTORY_CAP = 500


def _now_ms() -> int:
    return int(time.time() * 1000)


class LargeTradeDetector:
    """Flags trades whose notional clears *threshold_usd*."""

    def __init__(self, threshold_usd: float = 50_000.0) -> None:
        self._floor = max(0.0, threshold_usd)
        self._listeners: list[Callable[[LargeTradeEvent], Any]] = []
        self._tape: list[LargeTradeEvent] = []

    @property
    def threshold_usd(self) -> float:
        return self._floor

    @threshold_usd.setter
    def threshold_usd(self, value: float) -> None:
        self._floor = max(0.0, value)

    def on_large_trade(self, callback: Callable[[LargeTradeEvent], Any]) -> None:
        """Register a listener; exceptions inside it are contained."""
        self._listeners.append(callback)

    def process(self, trade: TradeEvent) -> LargeTradeEvent | None:
        """Gate one trade; returns the event when it qualifies, else None."""
        notional = trade.price * trade.quantity
        if notional < self._floor:
            return None
        event = self._promote(trade, notional)
        self._remember(event)
        self._fanout(event)
        return event

    def get_recent(self, limit: int = 50) -> list[LargeTradeEvent]:
        """Most recent qualifying trades, newest last."""
        return self._tape[-limit:] if limit < len(self._tape) else list(self._tape)

    def get_stats(self, window_ms: int = 3_600_000) -> dict[str, Any]:
        """Buy/sell split over the trailing window (default 1h)."""
        cutoff = _now_ms() - window_ms
        buys = [e.notional_usd for e in self._tape if e.timestamp >= cutoff and e.side == "buy"]
        sells = [e.notional_usd for e in self._tape if e.timestamp >= cutoff and e.side == "sell"]
        return {
            "window_ms": window_ms,
            "buy_count": len(buys),
            "sell_count": len(sells),
            "buy_volume_usd": sum(buys),
            "sell_volume_usd": sum(sells),
            "total_count": len(buys) + len(sells),
        }

    # -- internals -------------------------------------------------------

    @staticmethod
    def _promote(trade: TradeEvent, notional: float) -> LargeTradeEvent:
        """Re-shape a raw tape print into the whale-event model."""
        return LargeTradeEvent(
            **{
                "exchange": trade.exchange,
                "symbol": trade.symbol,
                "market_type": trade.market_type,
                "side": trade.side,
                "price": trade.price,
                "quantity": trade.quantity,
                "notional_usd": notional,
                "trade_id": trade.trade_id,
                "timestamp": trade.timestamp,
            }
        )

    def _remember(self, event: LargeTradeEvent) -> None:
        self._tape.append(event)
        if len(self._tape) > _HISTORY_CAP:
            del self._tape[: len(self._tape) - _HISTORY_CAP]

    def _fanout(self, event: LargeTradeEvent) -> None:
        for cb in self._listeners:
            try:
                cb(event)
            except Exception:
                logger.exception("large-trade listener raised")
