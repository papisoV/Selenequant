"""Deribit WS client: graceful-close disconnect signalling.

Regression: ``websockets`` ends ``async for`` *normally* on a graceful close
(code 1000) — ``StopAsyncIteration``, not ``ConnectionClosed`` — so dispatching
the disconnect only from the ``except`` branches left the client marked
connected and ``wait_disconnected()`` blocked forever, so the reconnect
supervisor never redialled. Binance and OKX had the same read-loop shape.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from selene_space_exchange.exchanges.deribit.ws_client import DeribitWSClient


class FakeDeribitSocket:
    """Scripted Deribit connection: replays queued frames, records sends."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._frames: list[str] = []

    def queue(self, msg: dict) -> None:
        self._frames.append(json.dumps(msg))

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        pass

    def __aiter__(self) -> "FakeDeribitSocket":
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


@pytest.mark.asyncio
async def test_receive_loop_dispatches_disconnect_on_normal_exit() -> None:
    socket = FakeDeribitSocket()
    client = DeribitWSClient()
    seen: list[object] = []
    client.on_disconnect = lambda *a: seen.append(a)

    await client._dispatch_connect()
    client._ws = socket  # type: ignore[assignment]
    # Empty frame queue -> the loop ends the way a graceful close ends it.
    await asyncio.wait_for(client._receive_loop(), timeout=1.0)

    assert seen, "a cleanly-ended read loop never signalled a disconnect"
    assert client.is_connected is False
