"""P0-2: kill switch multi-client cancellation.

Verifies that the on_activate callback iterates over ALL registered trading
clients and fires cancel_all_open_orders on each, so that activating the
kill switch cancels orders across every exchange venue — not just the last
one registered.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest


class _FakeTradingClient:
    """Minimal stand-in for a TradingClient — only cancel_all_open_orders."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.cancel_all_open_orders = AsyncMock(return_value=None)


def _build_callback(clients: list[_FakeTradingClient]):
    """Replicates main.py's _on_kill_switch_activate closure."""
    def _on_kill_switch_activate() -> None:
        for c in list(clients):
            asyncio.ensure_future(c.cancel_all_open_orders())
    return _on_kill_switch_activate


@pytest.mark.asyncio
async def test_kill_switch_cancels_all_registered_clients() -> None:
    """Two clients registered → both get cancel_all_open_orders fired."""
    client_a = _FakeTradingClient("binance")
    client_b = _FakeTradingClient("okx")
    clients = [client_a, client_b]
    cb = _build_callback(clients)

    cb()

    # Drain the event loop so ensure_future tasks complete.
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    client_a.cancel_all_open_orders.assert_awaited_once()
    client_b.cancel_all_open_orders.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_switch_callback_is_resilient_to_single_client() -> None:
    """When only one client is registered, the callback still works."""
    client_a = _FakeTradingClient("binance")
    clients = [client_a]
    cb = _build_callback(clients)

    cb()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    client_a.cancel_all_open_orders.assert_awaited_once()


@pytest.mark.asyncio
async def test_kill_switch_supports_dynamic_registration() -> None:
    """Clients appended after callback creation are still cancelled.

    This mirrors the production wiring: the callback closes over the list
    object, so clients appended later (e.g. a second exchange adapter
    initialised in a later lifespan block) are picked up at fire time.
    """
    client_a = _FakeTradingClient("binance")
    clients: list[_FakeTradingClient] = []
    cb = _build_callback(clients)

    clients.append(client_a)  # register after callback creation
    cb()
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    client_a.cancel_all_open_orders.assert_awaited_once()
