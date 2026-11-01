"""Order lifecycle: an explicit transition table and its bookkeeping manager.

The state machine is deliberately narrow — every legal edge is enumerated and
anything outside the table is refused. ``update_fill`` is the only path that
moves an order toward FILLED, and it maintains the volume-weighted average
fill price as quantities accumulate.
"""

from __future__ import annotations

import time
import uuid
from decimal import Decimal
from typing import Any

from selene_space_types.trading import (
    MarginMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)

Symbol = str


class InvalidOrderTransition(Exception):
    """Raised when a status change is not in the legal-edge table."""

    def __init__(self, current: OrderStatus, target: OrderStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"cannot move order from {current.value} to {target.value}")


# Adjacency list of the lifecycle. Terminal states simply have no row.
_ALLOWED_EDGES: dict[OrderStatus, frozenset[OrderStatus]] = {
    OrderStatus.CREATED: frozenset({OrderStatus.ACCEPTED, OrderStatus.REJECTED}),
    OrderStatus.ACCEPTED: frozenset(
        {OrderStatus.PARTIAL, OrderStatus.FILLED, OrderStatus.CANCELLED}
    ),
    OrderStatus.PARTIAL: frozenset({OrderStatus.FILLED, OrderStatus.CANCELLED}),
}


def can_transition(current: OrderStatus, target: OrderStatus) -> bool:
    return target in _ALLOWED_EDGES.get(current, frozenset())


def _ms() -> int:
    return int(time.time() * 1000)


class PaperOrderManager:
    """Creates orders and applies status / fill updates under the frozen rules."""

    def __init__(self) -> None:
        self._orders: dict[str, Order] = {}
        # Order has no account column, so ownership lives here.
        self._owner: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def place_order(
        self,
        account_id: str,
        *,
        symbol: Symbol,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
        leverage: int = 1,
        margin_mode: MarginMode = MarginMode.CROSS,
        pos_side: PositionSide = PositionSide.NET,
        reduce_only: bool = False,
        client_order_id: str | None = None,
        strategy_id: str | None = None,
        signal_timestamp_ms: int | None = None,
        time_in_force: str | None = None,
        tp_price: float | None = None,
        sl_price: float | None = None,
    ) -> Order:
        now = _ms()
        order = Order(
            id=f"po-{uuid.uuid4().hex[:12]}",
            symbol=symbol,
            side=side,
            type=order_type,
            quantity=Decimal(str(quantity)),
            price=None if price is None else Decimal(str(price)),
            stop_price=None if stop_price is None else Decimal(str(stop_price)),
            filled_quantity=Decimal("0"),
            leverage=leverage,
            margin_mode=margin_mode,
            pos_side=pos_side,
            reduce_only=reduce_only,
            created_at=now,
            updated_at=now,
        )
        self._orders[order.id] = order
        self._owner[order.id] = account_id
        # Fresh orders are born CREATED; admission moves them straight to
        # ACCEPTED so fills never need to special-case CREATED.
        self._move(order, OrderStatus.ACCEPTED)
        return order

    # ------------------------------------------------------------------
    # Status changes
    # ------------------------------------------------------------------

    def cancel_order(self, order_id: str) -> Order:
        order = self._require(order_id)
        self._move(order, OrderStatus.CANCELLED)
        return order

    def reject_order(self, order_id: str, reason: str = "") -> Order:
        order = self._require(order_id)
        if not can_transition(order.status, OrderStatus.REJECTED):
            raise InvalidOrderTransition(order.status, OrderStatus.REJECTED)
        order.status = OrderStatus.REJECTED
        order.updated_at = _ms()
        return order

    def _move(self, order: Order, target: OrderStatus) -> None:
        if not can_transition(order.status, target):
            raise InvalidOrderTransition(order.status, target)
        order.status = target
        order.updated_at = _ms()

    # ------------------------------------------------------------------
    # Fill bookkeeping
    # ------------------------------------------------------------------

    def update_fill(
        self, order_id: str, fill_quantity: float, fill_price: float
    ) -> Order:
        """Accumulate one execution onto the order.

        The running average is kept in exact Decimal arithmetic; a fill that
        exhausts the remaining size flips the order to FILLED, anything less
        to PARTIAL.
        """
        order = self._require(order_id)
        if not can_transition(order.status, OrderStatus.PARTIAL) and not (
            order.status == OrderStatus.PARTIAL
        ):
            raise InvalidOrderTransition(order.status, OrderStatus.PARTIAL)

        added = Decimal(str(fill_quantity))
        px = Decimal(str(fill_price))
        prior_qty = order.filled_quantity
        prior_notional = prior_qty * (order.avg_fill_price or Decimal("0"))
        new_qty = prior_qty + added
        order.avg_fill_price = (prior_notional + added * px) / new_qty
        order.filled_quantity = new_qty

        target = OrderStatus.FILLED if new_qty >= order.quantity else OrderStatus.PARTIAL
        if order.status != target:
            self._move(order, target)
        return order

    def sync_fill(self, order_id: str) -> Order:
        """Recompute derived status after an external caller mutated the order.

        ``try_fill`` writes filled_quantity/avg_fill_price directly onto the
        Order object; this reconciles PARTIAL/FILLED without double-counting.
        """
        order = self._require(order_id)
        fully = order.filled_quantity >= order.quantity
        started = order.filled_quantity > 0
        target = (
            OrderStatus.FILLED if fully else OrderStatus.PARTIAL if started else None
        )
        if target is not None and order.status != target:
            self._move(order, target)
        else:
            order.updated_at = _ms()
        return order

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def get_orders(
        self, account_id: str, status: OrderStatus | None = None
    ) -> list[Order]:
        found = [o for oid, o in self._orders.items() if self._owner.get(oid) == account_id]
        if status is not None:
            found = [o for o in found if o.status == status]
        return found

    def get_open_orders(self, account_id: str | None = None) -> list[Order]:
        live = {OrderStatus.ACCEPTED, OrderStatus.PARTIAL}
        return [
            o
            for oid, o in self._orders.items()
            if o.status in live
            and (account_id is None or self._owner.get(oid) == account_id)
        ]

    def clear_orders(self, account_id: str) -> None:
        doomed = [oid for oid in self._orders if self._owner.get(oid) == account_id]
        for oid in doomed:
            del self._orders[oid]
            self._owner.pop(oid, None)

    # ------------------------------------------------------------------
    # Persistence bridge
    # ------------------------------------------------------------------

    def get_all_data(self) -> dict[str, Any]:
        return {"orders": dict(self._orders), "owner": dict(self._owner)}

    def load_data(self, data: dict[str, Any]) -> None:
        raw = data.get("orders", {})
        rebuilt: dict[str, Order] = {}
        for key, value in raw.items():
            rebuilt[key] = value if isinstance(value, Order) else Order.model_validate(value)
        self._orders = rebuilt
        owners = data.get("owner", {})
        self._owner = {
            oid: str(owners.get(oid, "")) for oid in rebuilt if oid in owners
        }

    def _require(self, order_id: str) -> Order:
        try:
            return self._orders[order_id]
        except KeyError:
            raise KeyError(f"unknown order: {order_id}") from None
