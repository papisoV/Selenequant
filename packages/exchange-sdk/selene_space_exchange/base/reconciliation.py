"""Snapshot orders/balances/positions from a venue after a disconnect."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from selene_space_exchange.trading import OrderResponse, TradingClient
import time

from selene_space_types.trading import AccountSnapshot, BalanceUpdate

logger = logging.getLogger(__name__)


class ReconciliationManager:
    """Pull authoritative account state and push it to listeners.

    Position fetch is best-effort: an adapter without derivatives support
    must not void the balance snapshot that was already retrieved.
    """

    def __init__(
        self,
        trading_client: TradingClient,
        *,
        on_snapshot: Callable[[AccountSnapshot], Any] | None = None,
        on_orders: Callable[[list[OrderResponse]], Any] | None = None,
        on_positions: Callable[[list[dict[str, Any]]], Any] | None = None,
    ) -> None:
        self._client = trading_client
        self._on_snapshot = on_snapshot
        self._on_orders = on_orders
        self._on_positions = on_positions
        self._last_diff_count = 0
        self._periodic_task: asyncio.Task[None] | None = None
        self._periodic_stop = asyncio.Event()

    def get_last_diff_count(self) -> int:
        return self._last_diff_count

    def record_diff_count(self, count: int) -> None:
        self._last_diff_count = count

    @property
    def exchange(self) -> str:
        return self._client.exchange_name

    async def reconcile_on_reconnect(self) -> AccountSnapshot:
        balances = await self._client.get_balances()
        now_ms = int(time.time() * 1000)
        rows = [
            BalanceUpdate(
                exchange=self._client.exchange_name,
                asset=b.asset,
                free=b.free,
                locked=b.locked,
                timestamp=now_ms,
            )
            for b in balances
        ]
        snapshot = AccountSnapshot(
            exchange=self._client.exchange_name,
            balances=rows,
            timestamp=now_ms,
        )
        try:
            positions = await self.reconcile_positions()
            logger.info("reconnect: fetched %d positions", len(positions))
        except Exception as exc:
            logger.warning("reconnect: position fetch failed (%s)", exc)
        if self._on_snapshot is not None:
            await self._emit(self._on_snapshot, snapshot)
        return snapshot

    async def reconcile_orders(
        self, symbol: str | None = None
    ) -> list[OrderResponse]:
        orders = await self._client.get_open_orders(symbol)
        if self._on_orders is not None:
            await self._emit(self._on_orders, orders)
        return orders

    async def reconcile_positions(
        self, symbol: str | None = None
    ) -> list[dict[str, Any]]:
        positions = await self._client.get_positions(symbol)
        if self._on_positions is not None:
            await self._emit(self._on_positions, positions)
        return positions

    async def start_periodic(self, interval_s: float = 300.0) -> None:
        if self._periodic_task is not None and not self._periodic_task.done():
            return
        self._periodic_stop.clear()
        self._periodic_task = asyncio.create_task(self._periodic_loop(interval_s))

    async def stop(self) -> None:
        self._periodic_stop.set()
        task = self._periodic_task
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._periodic_task = None

    async def _periodic_loop(self, interval_s: float) -> None:
        try:
            while not self._periodic_stop.is_set():
                try:
                    await self.reconcile_orders()
                    await self.reconcile_positions()
                except Exception as exc:
                    logger.error("periodic reconcile failed: %s", exc)
                try:
                    await asyncio.wait_for(
                        self._periodic_stop.wait(), timeout=interval_s
                    )
                except asyncio.TimeoutError:
                    pass
        except asyncio.CancelledError:
            pass

    @staticmethod
    async def _emit(handler: Callable[..., Any], *args: Any) -> None:
        outcome = handler(*args)
        if asyncio.iscoroutine(outcome):
            await outcome
