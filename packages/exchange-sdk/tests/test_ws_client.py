"""BaseWSClient ABC contract: state flags, subscription set, callback dispatch."""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_exchange.base.ws_client import BaseWSClient
from selene_space_exchange.types import WSClientConfig


class ProbeClient(BaseWSClient):
    """Bare concrete subclass exposing the protected dispatch surface."""

    def __init__(self, **callbacks: Any) -> None:
        super().__init__(WSClientConfig(url="wss://probe.invalid/stream", exchange="test"), **callbacks)
        self.was_connected = False
        self.was_closed = False

    async def connect(self) -> None:
        self.was_connected = True
        await self._dispatch_connect()

    async def subscribe(self, streams: list[str]) -> None:
        self._subscriptions.update(streams)

    async def unsubscribe(self, streams: list[str]) -> None:
        self._subscriptions -= set(streams)

    async def close(self) -> None:
        self.was_closed = True
        await self._dispatch_disconnect(code=1000, reason="normal")


class TestConnectionLifecycle:
    @pytest.mark.asyncio
    async def test_fresh_client_is_disconnected(self) -> None:
        client = ProbeClient()
        checks = {
            "flag": client.is_connected is False,
            "subs-empty": client.subscriptions == frozenset(),
            "exchange-tag": client.config.exchange == "test",
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_connect_flips_the_flag(self) -> None:
        client = ProbeClient()
        await client.connect()
        assert (client.is_connected, client.was_connected) == (True, True)

    @pytest.mark.asyncio
    async def test_close_clears_the_flag(self) -> None:
        client = ProbeClient()
        await client.connect()
        await client.close()
        assert (client.is_connected, client.was_closed) == (False, True)


class TestSubscriptionTracking:
    @pytest.mark.asyncio
    async def test_subscribe_accumulates(self) -> None:
        client = ProbeClient()
        await client.subscribe(["solusdt@ticker", "xrpusdt@trade"])
        assert client.subscriptions == frozenset({"solusdt@ticker", "xrpusdt@trade"})

    @pytest.mark.asyncio
    async def test_unsubscribe_prunes(self) -> None:
        client = ProbeClient()
        await client.subscribe(["solusdt@ticker", "xrpusdt@trade"])
        await client.unsubscribe(["solusdt@ticker"])
        assert client.subscriptions == frozenset({"xrpusdt@trade"})

    @pytest.mark.asyncio
    async def test_view_is_insulated_from_later_changes(self) -> None:
        client = ProbeClient()
        await client.subscribe(["stream_a"])
        frozen_view = client.subscriptions
        await client.subscribe(["stream_b"])
        assert "stream_b" not in frozen_view


class TestCallbackDispatch:
    @pytest.mark.asyncio
    async def test_sync_message_handler(self) -> None:
        seen: list[dict[str, Any]] = []
        client = ProbeClient(on_message=seen.append)
        await client._dispatch_message({"ping": 1})
        assert seen == [{"ping": 1}]

    @pytest.mark.asyncio
    async def test_async_message_handler(self) -> None:
        seen: list[dict[str, Any]] = []

        async def handler(payload: dict[str, Any]) -> None:
            seen.append(payload)

        client = ProbeClient(on_message=handler)
        await client._dispatch_message({"pong": 2})
        assert seen == [{"pong": 2}]

    @pytest.mark.asyncio
    async def test_error_handler_receives_exception_instance(self) -> None:
        caught: list[Exception] = []
        failure = RuntimeError("boom")
        client = ProbeClient(on_error=caught.append)
        await client._dispatch_error(failure)
        assert caught == [failure]

    @pytest.mark.asyncio
    async def test_connect_handler_fires_on_connect(self) -> None:
        ticks: list[bool] = []
        client = ProbeClient(on_connect=lambda: ticks.append(True))
        await client.connect()
        assert ticks == [True]

    @pytest.mark.asyncio
    async def test_disconnect_handler_carries_code_and_reason(self) -> None:
        hangups: list[tuple[int, str]] = []
        client = ProbeClient(on_disconnect=lambda code, why: hangups.append((code, why)))
        await client.connect()
        await client.close()
        assert hangups == [(1000, "normal")]

    @pytest.mark.asyncio
    async def test_dispatching_with_no_handlers_is_safe(self) -> None:
        """Unconfigured callbacks must never raise on dispatch."""
        client = ProbeClient()
        await client._dispatch_message({"any": "thing"})
        await client._dispatch_error(ValueError("swallowed"))
        await client._dispatch_connect()
        await client._dispatch_disconnect()
