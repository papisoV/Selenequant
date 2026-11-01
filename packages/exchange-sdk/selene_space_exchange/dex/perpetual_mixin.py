"""Perpetual DEX mixin — extended interface for on-chain perpetual exchanges.

The base TradingClient ABC covers generic order operations. Perpetual DEXes
need additional methods for market data, leverage, fill detection via polling,
and crash-resume order adoption. This mixin defines that extended contract.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

from selene_space_exchange.dex.types import (
    DEXFill,
    DEXMarket,
    DEXPosition,
    DEXPriceUpdate,
)
from selene_space_types.trading import OrderSide


class PerpetualDEXMixin(ABC):
    """Extended interface for on-chain perpetual DEX adapters.

    Inherit alongside TradingClient to provide the additional methods
    the GridExecutionEngine needs for live grid management.
    """

    # --- Market data --------------------------------------------------------

    @abstractmethod
    async def get_markets(self) -> list[DEXMarket]:
        """Return all tradeable perpetual markets."""

    @abstractmethod
    async def get_price(self, market_id: int) -> float | None:
        """Return current mid-price for a market."""

    @abstractmethod
    async def get_candles(
        self, market_id: int, interval_sec: int = 3600, n: int = 200
    ) -> list[dict[str, Any]]:
        """Return recent candles. Each dict: {time, open, high, low, close, volume}."""

    # --- Leverage ------------------------------------------------------------

    @abstractmethod
    async def set_leverage(self, market_id: int, leverage: int) -> bool:
        """Set leverage for a market. Return True on success."""

    # --- Order management (grid-specific) ------------------------------------

    @abstractmethod
    async def place_limit_order(
        self,
        market_id: int,
        side: OrderSide,
        price: float,
        size_base: float,
        *,
        reduce_only: bool = False,
        level_index: int = -1,
        client_order_id: int | None = None,
        post_only: bool = False,
    ) -> dict[str, Any]:
        """Place a limit order. Return {order_id, ...}."""

    @abstractmethod
    async def cancel_order_by_id(self, market_id: int, order_id: str) -> bool:
        """Cancel a single order. Return True on success."""

    @abstractmethod
    async def cancel_all(self, market_id: int) -> bool:
        """Cancel all open orders for a market. Return True on success."""

    @abstractmethod
    async def close_position(self, market_id: int) -> bool:
        """Close the current position with a reduce-only market order."""

    # --- Reconciliation & resume ---------------------------------------------

    @abstractmethod
    async def fetch_open_orders(self, market_id: int) -> list[dict[str, Any]] | None:
        """Fetch real resting orders from the exchange for reconciliation.
        Return None if the snapshot is unreliable (skip this reconcile cycle)."""

    @abstractmethod
    def adopt_order(
        self,
        order_id: str,
        market_id: int,
        level_index: int,
        side: OrderSide,
        price: float,
        size_base: float,
    ) -> None:
        """Re-attach a previously-placed order to internal tracking (resume)."""

    @abstractmethod
    def get_position(self, market_id: int) -> DEXPosition | None:
        """Return current position for a market, or None."""

    @abstractmethod
    async def reconnect(self) -> bool:
        """Re-establish connection without touching resting orders."""

    # --- Event callbacks -----------------------------------------------------

    @abstractmethod
    def on_fill(self, callback: Callable[[DEXFill], None]) -> None:
        """Register fill event callback."""

    @abstractmethod
    def on_price(self, callback: Callable[[DEXPriceUpdate], None]) -> None:
        """Register price update callback."""

    @abstractmethod
    def on_error(self, callback: Callable[[Exception], None]) -> None:
        """Register error callback."""

    # --- Polled state --------------------------------------------------------

    @property
    @abstractmethod
    def equity(self) -> float | None:
        """Latest account equity."""

    @property
    @abstractmethod
    def balance(self) -> float | None:
        """Latest account balance."""

    @property
    @abstractmethod
    def realized_pnl(self) -> float | None:
        """Cumulative realized PnL."""

    @property
    @abstractmethod
    def data_source(self) -> str:
        """'real' if connected to live API, 'synthetic' if using fallback data."""

    @property
    @abstractmethod
    def last_ok_at(self) -> float:
        """Unix timestamp of last successful API call."""

    # --- Polling lifecycle ---------------------------------------------------

    @abstractmethod
    def start_polling(self) -> None:
        """Start the background polling loop (fills, prices, positions)."""

    @abstractmethod
    def stop_polling(self) -> None:
        """Stop the background polling loop."""
