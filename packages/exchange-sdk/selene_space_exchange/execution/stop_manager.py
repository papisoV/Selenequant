"""Server-side stop order manager.

Manages stop-loss orders that automatically trigger when price reaches
a threshold. Supports trailing stops that ratchet favorably.

Integrates with ShadowExecutionEngine via on_price_tick for trigger
detection — stops are evaluated against real-time price ticks and
fire stop_market orders when breached.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum


class StopSide(str, Enum):
    LONG = "long"   # Stop triggers when price <= stop_price
    SHORT = "short" # Stop triggers when price >= stop_price


class StopStatus(str, Enum):
    ACTIVE = "active"
    TRIGGERED = "triggered"
    CANCELLED = "cancelled"


@dataclass
class StopHandle:
    stop_id: str
    symbol: str
    side: StopSide
    stop_price: float
    quantity: float
    status: StopStatus = StopStatus.ACTIVE
    trailing_enabled: bool = False
    trailing_distance: float = 0.0
    reduce_only: bool = True
    position_link: str | None = None


class StopManager:
    """Manages server-side stop-loss orders with optional trailing.

    Usage::

        mgr = StopManager()
        handle = mgr.register_stop(
            symbol="BTC/USDT", side=StopSide.LONG,
            stop_price=49000.0, quantity=0.1,
            trailing_enabled=True, trailing_distance=500.0,
        )
        # On each price tick:
        triggered = mgr.on_price_tick("BTC/USDT", 48900.0)
        # → [{"stop_id": "...", "symbol": "BTC/USDT", ...}]
    """

    def __init__(self) -> None:
        self._stops: dict[str, StopHandle] = {}
        # Track highest/lowest price seen for trailing stop calculation
        self._best_prices: dict[str, float] = {}  # stop_id → best price

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def register_stop(
        self,
        symbol: str,
        side: StopSide,
        stop_price: float,
        quantity: float,
        *,
        trailing_enabled: bool = False,
        trailing_distance: float = 0.0,
        reduce_only: bool = True,
        position_link: str | None = None,
    ) -> StopHandle:
        stop_id = f"stop-{uuid.uuid4().hex[:8]}"
        handle = StopHandle(
            stop_id=stop_id,
            symbol=symbol,
            side=side,
            stop_price=stop_price,
            quantity=quantity,
            trailing_enabled=trailing_enabled,
            trailing_distance=trailing_distance,
            reduce_only=reduce_only,
            position_link=position_link,
        )
        self._stops[stop_id] = handle
        if trailing_enabled:
            self._best_prices[stop_id] = stop_price
        return handle

    def get_stop(self, stop_id: str) -> StopHandle | None:
        return self._stops.get(stop_id)

    def cancel_stop(self, stop_id: str) -> None:
        handle = self._stops.get(stop_id)
        if handle is not None:
            handle.status = StopStatus.CANCELLED
            self._best_prices.pop(stop_id, None)

    def list_active_stops(self) -> list[StopHandle]:
        return [h for h in self._stops.values() if h.status == StopStatus.ACTIVE]

    # ------------------------------------------------------------------
    # Price tick → trigger detection + trailing ratchet
    # ------------------------------------------------------------------

    def on_price_tick(self, symbol: str, price: float) -> list[dict]:
        """Evaluate all active stops against a price tick.

        For trailing stops, first ratchet the stop price if the price
        moved favorably, then check if any stop is triggered.

        Returns list of triggered stops as dicts.
        """
        triggered: list[dict] = []

        for stop_id, handle in list(self._stops.items()):
            if handle.status != StopStatus.ACTIVE:
                continue
            if handle.symbol != symbol:
                continue

            # Update trailing stop if enabled
            if handle.trailing_enabled:
                self._ratchet_trailing(handle, price)

            # Check trigger
            is_triggered = False
            if handle.side == StopSide.LONG and price <= handle.stop_price:
                is_triggered = True
            elif handle.side == StopSide.SHORT and price >= handle.stop_price:
                is_triggered = True

            if is_triggered:
                handle.status = StopStatus.TRIGGERED
                self._best_prices.pop(stop_id, None)
                triggered.append({
                    "stop_id": stop_id,
                    "symbol": handle.symbol,
                    "side": handle.side.value,
                    "stop_price": handle.stop_price,
                    "quantity": handle.quantity,
                    "trigger_price": price,
                })

        return triggered

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _ratchet_trailing(self, handle: StopHandle, price: float) -> None:
        """Ratchet trailing stop price in the favorable direction."""
        if handle.trailing_distance <= 0:
            return

        best_key = handle.stop_id
        current_best = self._best_prices.get(best_key, handle.stop_price)

        if handle.side == StopSide.LONG:
            # Price moved up: ratchet stop up
            if price > current_best:
                self._best_prices[best_key] = price
                handle.stop_price = price - handle.trailing_distance
        else:  # SHORT
            # Price moved down: ratchet stop down
            if price < current_best or current_best == handle.stop_price:
                if price < current_best:
                    self._best_prices[best_key] = price
                    handle.stop_price = price + handle.trailing_distance
                elif current_best == handle.stop_price and price < current_best:
                    self._best_prices[best_key] = price
                    handle.stop_price = price + handle.trailing_distance
