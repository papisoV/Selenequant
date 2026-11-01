"""Tests for BinanceUserDataClient on_reconnect callback hook (Phase 5 observability).

Verifies:
  - on_reconnect callback is invoked when _default_stall_handler triggers (stall-induced reconnect)
  - on_reconnect callback is invoked when eventStreamTerminated triggers reconnect
  - on_reconnect=None (default) does not break reconnect path
  - on_reconnect callback raising is swallowed (non-fatal)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from selene_space_exchange.exchanges.binance.user_data_client import (
    BinanceUserDataClient,
)
from selene_space_exchange.base.stall_watchdog import StallTimeoutMeta


def _make_client(**kwargs: Any) -> BinanceUserDataClient:
    return BinanceUserDataClient(
        api_key="test-key",
        api_secret="test-secret",
        url="wss://test.example/ws",
        testnet=False,
        **kwargs,
    )


@pytest.mark.asyncio
async def test_on_reconnect_callback_fired_on_stall_handler() -> None:
    """_default_stall_handler triggers on_reconnect callback."""
    fired = []
    client = _make_client(on_reconnect=lambda: fired.append("reconnect"))
    # Simulate stall handler invocation
    meta = StallTimeoutMeta(idle_s=65.0, timeout_s=60.0)
    await client._default_stall_handler(meta)
    assert fired == ["reconnect"]


@pytest.mark.asyncio
async def test_on_reconnect_callback_fired_on_event_stream_terminated() -> None:
    """eventStreamTerminated event triggers on_reconnect callback."""
    fired = []
    client = _make_client(on_reconnect=lambda: fired.append("reconnect"))
    # Mock the _ws so close() doesn't blow up
    client._ws = AsyncMock()
    client._ws.close = AsyncMock()
    # Route an eventStreamTerminated event
    await client._route_message({"e": "eventStreamTerminated"})
    assert fired == ["reconnect"]


@pytest.mark.asyncio
async def test_on_reconnect_none_default_does_not_break() -> None:
    """on_reconnect=None (default) — stall handler still runs without error."""
    client = _make_client()  # no on_reconnect
    assert client._on_reconnect is None
    meta = StallTimeoutMeta(idle_s=65.0, timeout_s=60.0)
    # Should not raise
    await client._default_stall_handler(meta)


@pytest.mark.asyncio
async def test_on_reconnect_callback_raising_is_swallowed() -> None:
    """If on_reconnect raises, the exception is swallowed (non-fatal)."""

    def bad_callback() -> None:
        raise RuntimeError("boom")

    client = _make_client(on_reconnect=bad_callback)
    meta = StallTimeoutMeta(idle_s=65.0, timeout_s=60.0)
    # Should not raise
    await client._default_stall_handler(meta)


@pytest.mark.asyncio
async def test_on_reconnect_async_callback_awaited() -> None:
    """Async on_reconnect callback is awaited."""

    fired = []

    async def async_cb() -> None:
        fired.append("async-reconnect")

    client = _make_client(on_reconnect=async_cb)
    meta = StallTimeoutMeta(idle_s=65.0, timeout_s=60.0)
    await client._default_stall_handler(meta)
    assert fired == ["async-reconnect"]
