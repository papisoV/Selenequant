"""OCO / bracket order manager.

Provides atomic take-profit + stop-loss bracket orders. When the entry fills,
TP and SL orders are activated with OCO (one-cancels-other) semantics:
TP fill → SL auto-cancel, SL fill → TP auto-cancel.

Integrates with ShadowExecutionEngine via on_price_tick for trigger detection
and on_entry_filled for entry→OCO activation.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum


class BracketSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class BracketState(str, Enum):
    PENDING_ENTRY = "pending_entry"
    ACTIVE = "active"
    TP_HIT = "tp_hit"
    SL_HIT = "sl_hit"
    CANCELLED = "cancelled"


@dataclass
class BracketOrder:
    bracket_id: str
    symbol: str
    side: BracketSide
    entry_price: float
    quantity: float
    tp_price: float
    sl_price: float
    state: BracketState = BracketState.PENDING_ENTRY

    def __post_init__(self) -> None:
        if self.entry_price <= 0:
            raise ValueError("entry_price must be positive")
        if self.quantity <= 0:
            raise ValueError("quantity must be positive")
        if self.tp_price <= 0 or self.sl_price <= 0:
            raise ValueError("tp_price and sl_price must be positive")


@dataclass
class _OCOOrder:
    bracket_id: str
    oco_type: str  # "tp" or "sl"
    symbol: str
    side: BracketSide
    trigger_price: float
    quantity: float
    cancelled: bool = False


class BracketOrderManager:
    """Manages bracket/OCO order lifecycle.

    Usage::

        mgr = BracketOrderManager()
        bracket = mgr.place_bracket(
            symbol="BTC/USDT", side=BracketSide.LONG,
            entry_price=50000.0, quantity=0.1,
            tp_price=51000.0, sl_price=49500.0,
        )
        # ... entry order fills ...
        mgr.on_entry_filled(bracket.bracket_id)
        # ... price ticks arrive ...
        triggered = mgr.on_price_tick("BTC/USDT", 51100.0)
        # → [{"type": "tp", "bracket_id": "...", ...}]
    """

    def __init__(self) -> None:
        self._brackets: dict[str, BracketOrder] = {}
        self._oco_orders: dict[str, list[_OCOOrder]] = {}  # bracket_id → [tp, sl]

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def place_bracket(
        self,
        *,
        symbol: str,
        side: BracketSide,
        entry_price: float,
        quantity: float,
        tp_price: float,
        sl_price: float,
    ) -> BracketOrder:
        bracket_id = f"bkt-{uuid.uuid4().hex[:8]}"
        bracket = BracketOrder(
            bracket_id=bracket_id,
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            quantity=quantity,
            tp_price=tp_price,
            sl_price=sl_price,
        )
        self._brackets[bracket_id] = bracket
        return bracket

    def get_bracket(self, bracket_id: str) -> BracketOrder | None:
        return self._brackets.get(bracket_id)

    def list_active_brackets(self) -> list[BracketOrder]:
        return [
            b for b in self._brackets.values()
            if b.state in (BracketState.PENDING_ENTRY, BracketState.ACTIVE)
        ]

    def cancel_bracket(self, bracket_id: str) -> None:
        bracket = self._brackets.get(bracket_id)
        if bracket is None:
            return
        bracket.state = BracketState.CANCELLED
        self._oco_orders.pop(bracket_id, None)

    # ------------------------------------------------------------------
    # Lifecycle transitions
    # ------------------------------------------------------------------

    def on_entry_filled(self, bracket_id: str) -> None:
        bracket = self._brackets.get(bracket_id)
        if bracket is None:
            raise ValueError(f"Bracket {bracket_id} not found")
        if bracket.state != BracketState.PENDING_ENTRY:
            raise ValueError(f"Bracket {bracket_id} is not pending entry (state={bracket.state})")

        bracket.state = BracketState.ACTIVE
        self._oco_orders[bracket_id] = [
            _OCOOrder(
                bracket_id=bracket_id,
                oco_type="tp",
                symbol=bracket.symbol,
                side=bracket.side,
                trigger_price=bracket.tp_price,
                quantity=bracket.quantity,
            ),
            _OCOOrder(
                bracket_id=bracket_id,
                oco_type="sl",
                symbol=bracket.symbol,
                side=bracket.side,
                trigger_price=bracket.sl_price,
                quantity=bracket.quantity,
            ),
        ]

    def on_take_profit_filled(self, bracket_id: str) -> None:
        bracket = self._brackets.get(bracket_id)
        if bracket is None:
            raise ValueError(f"Bracket {bracket_id} not found")
        if bracket.state != BracketState.ACTIVE:
            raise ValueError(
                f"Cannot fill TP: entry not filled for bracket {bracket_id} (state={bracket.state})"
            )
        bracket.state = BracketState.TP_HIT
        self._oco_orders.pop(bracket_id, None)  # Cancel SL

    def on_stop_loss_filled(self, bracket_id: str) -> None:
        bracket = self._brackets.get(bracket_id)
        if bracket is None:
            raise ValueError(f"Bracket {bracket_id} not found")
        if bracket.state != BracketState.ACTIVE:
            raise ValueError(
                f"Cannot fill SL: entry not filled for bracket {bracket_id} (state={bracket.state})"
            )
        bracket.state = BracketState.SL_HIT
        self._oco_orders.pop(bracket_id, None)  # Cancel TP

    # ------------------------------------------------------------------
    # Price tick → OCO trigger detection
    # ------------------------------------------------------------------

    def on_price_tick(self, symbol: str, price: float) -> list[dict]:
        """Check all active OCO orders against a price tick.

        Returns list of triggered orders (tp or sl), each as:
            {"type": "tp"|"sl", "bracket_id": str, "trigger_price": float, "quantity": float}

        Automatically updates bracket state and cancels the other OCO leg.
        """
        triggered: list[dict] = []

        for bracket_id, orders in list(self._oco_orders.items()):
            bracket = self._brackets.get(bracket_id)
            if bracket is None or bracket.symbol != symbol:
                continue
            if bracket.state != BracketState.ACTIVE:
                continue

            for oco in orders:
                if oco.cancelled:
                    continue

                is_triggered = False
                if bracket.side == BracketSide.LONG:
                    if oco.oco_type == "tp" and price >= oco.trigger_price:
                        is_triggered = True
                    elif oco.oco_type == "sl" and price <= oco.trigger_price:
                        is_triggered = True
                else:  # SHORT
                    if oco.oco_type == "tp" and price <= oco.trigger_price:
                        is_triggered = True
                    elif oco.oco_type == "sl" and price >= oco.trigger_price:
                        is_triggered = True

                if is_triggered:
                    triggered.append({
                        "type": oco.oco_type,
                        "bracket_id": bracket_id,
                        "trigger_price": oco.trigger_price,
                        "quantity": oco.quantity,
                    })
                    # Update bracket state and cancel other OCO leg
                    if oco.oco_type == "tp":
                        self.on_take_profit_filled(bracket_id)
                    else:
                        self.on_stop_loss_filled(bracket_id)
                    break  # Only one leg can trigger per tick

        return triggered

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def get_pending_oco_orders(self, bracket_id: str) -> list[dict]:
        """Get active (not cancelled) OCO orders for a bracket."""
        orders = self._oco_orders.get(bracket_id, [])
        return [
            {
                "type": o.oco_type,
                "trigger_price": o.trigger_price,
                "quantity": o.quantity,
            }
            for o in orders
            if not o.cancelled
        ]
