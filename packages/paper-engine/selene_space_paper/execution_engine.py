"""ExecutionEngine — the structural contract every trading backend satisfies.

Paper simulator, shadow trader, live gateway, and stock broker all satisfy
this Protocol structurally; the desktop API and strategy runner accept any
implementer. Member NAMES are load-bearing platform-wide and cannot change
without breaking every backend simultaneously.

The read half of the surface (balances / positions / fills / orders) is
polling-style; the write half is place_order + cancel_order; the async
half is the on_* subscription family. Scoped variants exist because
multi-account backends must not cross-deliver private events between
accounts — unscoped callbacks receive whatever the backend emits.
"""

from __future__ import annotations

from typing import Any, Callable, Protocol, runtime_checkable

from selene_space_types.trading import BalanceUpdate, Fill, Order, Position
from selene_space_types.trading import ExecutionMode
from selene_space_types.trading import OrderSide, OrderType
from selene_space_types.trading import OrderStatus


_OrderHandler = Callable[[Order], Any]
_FillHandler = Callable[[Fill], Any]
_PositionHandler = Callable[[Position], Any]
_BalanceHandler = Callable[[list[BalanceUpdate]], Any]
_ScopedPositionHandler = Callable[[str, Position], Any]
_ScopedBalanceHandler = Callable[[str, list[BalanceUpdate]], Any]


@runtime_checkable
class ExecutionEngine(Protocol):
    """Order placement, account-state reads, and private event streams."""

    def mode(self) -> ExecutionMode:
        "Which execution flavor this backend reports."
        raise NotImplementedError

    def exchange(self) -> str:
        "Venue identifier string."
        raise NotImplementedError

    async def start(self) -> None:
        # connect upstream resources
        raise NotImplementedError

    async def stop(self) -> None:
        # disconnect and release in-flight work
        raise NotImplementedError

    # -- state reads -----------------------------------------------------

    async def get_balances(self, acct: str) -> list[BalanceUpdate]:
        "Per-asset snapshot for one account."
        raise NotImplementedError

    async def get_positions(self, acct: str) -> list[Position]:
        """Positions held by one account."""
        raise NotImplementedError

    async def get_fills(self, acct: str) -> list[Fill]:
        "Executions booked against the account."
        raise NotImplementedError

    async def get_open_orders(self, acct: str) -> list[Order]:
        """Orders still working (accepted or partially filled)."""
        raise NotImplementedError

    async def get_orders(
        self, acct: str, *, status: OrderStatus | None = None
    ) -> list[Order]:
        "Full order history; ``status`` narrows to one state when given."
        raise NotImplementedError

    # -- order mutation ----------------------------------------------------

    async def place_order(
        self,
        *,
        size: float,
        price: float | None = None,
        symbol: str,
        acct: str,
        stop_price: float | None = None,
        side: OrderSide,
        order_type: OrderType,
    ) -> Order:
        """Submit one new order and get back its initial-state record.

        Every parameter is keyword-only, so implementations may declare them
        in whatever order suits their internals.
        """
        raise NotImplementedError

    async def cancel_order(self, ticket: str) -> Order:
        """Cancel while still cancellable; raises afterwards."""
        raise NotImplementedError

    # -- event subscription --------------------------------------------------

    def on_order_update(self, handler: _OrderHandler) -> None:
        # fires on status transitions
        raise NotImplementedError

    def on_fill(self, handler: _FillHandler) -> None:
        "Fires once per booked execution."
        raise NotImplementedError

    def on_position_update(self, handler: _PositionHandler) -> None:
        # fires on position changes
        raise NotImplementedError

    def on_balance_update(
        self, handler: _BalanceHandler
    ) -> None:
        # batched balance events
        raise NotImplementedError

    def on_position_update_scoped(
        self, handler: _ScopedPositionHandler
    ) -> None:
        """Same as on_position_update but carries the owning account id."""
        raise NotImplementedError

    def on_balance_update_scoped(
        self, handler: _ScopedBalanceHandler
    ) -> None:
        """Same as on_balance_update but carries the owning account id."""
        raise NotImplementedError
