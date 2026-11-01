"""BinanceWSClient: control frames, event fan-out, envelope unwrap, stream names."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from websockets.exceptions import ConnectionClosed
from websockets.frames import Close as CloseFrame

from selene_space_exchange.exchanges.binance.normalizer import BinanceDepthDelta
from selene_space_exchange.exchanges.binance.ws_client import BinanceWSClient
from selene_space_types.market import KlineEvent, TickerEvent, TradeEvent

CONNECT_TARGET = "selene_space_exchange.exchanges.binance.ws_client.websockets.asyncio.client.connect"


class _SocketScript:
    """Async-iterable socket double: replays queued frames then stops."""

    def __init__(self, frames: list[dict[str, Any]] | None = None) -> None:
        self._pending = [json.dumps(f) for f in (frames or [])]
        self.outbound: list[str] = []

    async def send(self, data: str) -> None:
        self.outbound.append(data)

    async def close(self) -> None:
        pass

    def __aiter__(self) -> "_SocketScript":
        return self

    async def __anext__(self) -> str:
        if not self._pending:
            raise StopAsyncIteration
        return self._pending.pop(0)

    @property
    def sent(self) -> list[dict[str, Any]]:
        return [json.loads(raw) for raw in self.outbound]


class _HeldSocket(_SocketScript):
    """Socket that stays open until the test ends the stream gracefully.

    ``_SocketScript`` exhausts its frames immediately, which cannot model
    "connected, then the server closes cleanly".
    """

    def __init__(self) -> None:
        super().__init__()
        self._gate: asyncio.Queue[str] = asyncio.Queue()

    def finish_gracefully(self) -> None:
        """End the stream the way a code-1000 close does: normally, no raise."""
        self._gate.put_nowait(_EOF)

    async def __anext__(self) -> str:
        item = await self._gate.get()
        if item is _EOF:
            raise StopAsyncIteration
        return item


_EOF = "\x00__eof__"


async def _run_with_socket(
    socket: _SocketScript, client: BinanceWSClient, settle_s: float = 0.05
) -> None:
    """Connect under a patched transport and give the loop time to drain."""
    with patch(CONNECT_TARGET, new_callable=AsyncMock, return_value=socket):
        await client.connect()
        await asyncio.sleep(settle_s)


class TestControlFrames:
    @pytest.mark.asyncio
    async def test_subscribe_frame_shape(self) -> None:
        socket = _SocketScript()
        client = BinanceWSClient()

        await _run_with_socket(socket, client)
        await client.subscribe(["btcusdt@ticker"])
        await client.close()

        assert len(socket.sent) >= 1
        sub = socket.sent[0]
        checks = {
            "method": sub["method"] == "SUBSCRIBE",
            "params": sub["params"] == ["btcusdt@ticker"],
            "request-id": "id" in sub,
        }
        assert all(
            checks.values()
        ), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_unsubscribe_frame_and_ledger_cleanup(self) -> None:
        socket = _SocketScript()
        client = BinanceWSClient()

        await _run_with_socket(socket, client)
        await client.subscribe(["btcusdt@ticker"])
        await client.unsubscribe(["btcusdt@ticker"])
        await client.close()

        goodbye = socket.sent[1]
        checks = {
            "method": goodbye["method"] == "UNSUBSCRIBE",
            "params": goodbye["params"] == ["btcusdt@ticker"],
            "ledger-empty": client.subscriptions == frozenset(),
        }
        assert all(
            checks.values()
        ), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestEventFanOut:
    @pytest.mark.asyncio
    async def test_ticker_reaches_handler_as_unified_event(
        self, sample_binance_ticker: dict[str, Any]
    ) -> None:
        seen: list[TickerEvent] = []
        client = BinanceWSClient(on_ticker=seen.append)

        await _run_with_socket(_SocketScript([sample_binance_ticker]), client)
        await client.close()

        assert len(seen) == 1
        assert (seen[0].symbol, seen[0].last_price) == ("BTC/USDT", 67000.0)

    @pytest.mark.asyncio
    async def test_trade_reaches_handler(
        self, sample_binance_trade: dict[str, Any]
    ) -> None:
        seen: list[TradeEvent] = []
        client = BinanceWSClient(on_trade=seen.append)

        await _run_with_socket(_SocketScript([sample_binance_trade]), client)
        await client.close()

        assert len(seen) == 1 and seen[0].side == "buy"

    @pytest.mark.asyncio
    async def test_kline_reaches_handler(
        self, sample_binance_kline: dict[str, Any]
    ) -> None:
        seen: list[KlineEvent] = []
        client = BinanceWSClient(on_kline=seen.append)

        await _run_with_socket(_SocketScript([sample_binance_kline]), client)
        await client.close()

        assert len(seen) == 1
        assert (seen[0].interval, seen[0].closed) == ("1h", True)

    @pytest.mark.asyncio
    async def test_depth_delta_reaches_handler(
        self, sample_binance_depth_update: dict[str, Any]
    ) -> None:
        seen: list[BinanceDepthDelta] = []
        client = BinanceWSClient(on_depth_update=seen.append)

        await _run_with_socket(_SocketScript([sample_binance_depth_update]), client)
        await client.close()

        assert len(seen) == 1 and seen[0].first_update_id == 100001


def _combined_envelope() -> dict[str, Any]:
    """Combined streams wrap the payload under stream/data keys."""
    inner = {
        "e": "24hrTicker",
        "E": 1711000000000,
        "s": "BTCUSDT",
        "c": "67000.00",
        "b": "66999.50",
        "a": "67000.50",
        "v": "12345.67",
        "q": "827160490.00",
        "h": "68000.00",
        "l": "66000.00",
        "P": "2.35",
    }
    return {"stream": "btcusdt@ticker", "data": inner}


class TestEnvelopeHandling:
    @pytest.mark.asyncio
    async def test_combined_stream_payload_is_unwrapped(self) -> None:
        seen: list[TickerEvent] = []
        client = BinanceWSClient(on_ticker=seen.append)

        await _run_with_socket(_SocketScript([_combined_envelope()]), client)
        await client.close()

        assert len(seen) == 1 and seen[0].symbol == "BTC/USDT"

    @pytest.mark.asyncio
    async def test_subscription_ack_is_swallowed(self) -> None:
        raw_frames: list[dict[str, Any]] = []
        client = BinanceWSClient(on_message=raw_frames.append)

        await _run_with_socket(_SocketScript([{"result": None, "id": 1}]), client)
        await client.close()

        assert raw_frames == []


class TestStreamNaming:
    def test_ticker_stream(self) -> None:
        assert BinanceWSClient.stream_name("btcusdt", "ticker") == "btcusdt@ticker"

    def test_kline_stream_includes_interval(self) -> None:
        assert (
            BinanceWSClient.stream_name("btcusdt", "kline", interval="1h")
            == "btcusdt@kline_1h"
        )

    def test_partial_depth_stream_passes_through(self) -> None:
        assert (
            BinanceWSClient.stream_name("btcusdt", "depth@100ms")
            == "btcusdt@depth@100ms"
        )

    def test_trade_stream_for_any_symbol(self) -> None:
        assert BinanceWSClient.stream_name("ethusdt", "trade") == "ethusdt@trade"

    def test_uppercase_input_gets_folded(self) -> None:
        assert BinanceWSClient.stream_name("BTCUSDT", "ticker") == "btcusdt@ticker"


class _DroppingSocket(_SocketScript):
    """Socket double that dies mid-read with the venue's close frame."""

    def __init__(
        self, close_code: int = 1008, reason: str = "policy violation"
    ) -> None:
        super().__init__([])
        self._close = CloseFrame(close_code, reason)
        self.sent_after_death: list[str] = []
        self._dead = False

    async def send(self, data: str) -> None:
        if self._dead:
            self.sent_after_death.append(data)
            raise ConnectionClosed(self._close, None)
        self.outbound.append(data)

    async def __anext__(self) -> str:
        if not self._dead:
            self._dead = True
            raise ConnectionClosed(self._close, None)
        raise StopAsyncIteration


class TestDroppedSocketIsForgotten:
    """A dead socket must not stay in ``_socket``.

    Regression for the 2026-10 Binance incident: the venue closed the stream
    with ``1008 Too many requests``, but ``_socket`` kept pointing at the corpse.
    The supervisor's resubscribe then sent a SUBSCRIBE frame on that closed
    socket, which raised and killed the supervisor's ``run()`` loop — the feed
    never came back. Forgetting the socket routes the resubscribe into the
    buffering path instead, where it is harmless.
    """

    @pytest.mark.asyncio
    async def test_reader_clears_the_socket_it_lost(self) -> None:
        socket = _DroppingSocket()
        client = BinanceWSClient()

        await _run_with_socket(socket, client, settle_s=0.05)

        assert client._socket is None, "dead socket still held after reader unwind"
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_resubscribe_after_loss_buffers_instead_of_sending(self) -> None:
        socket = _DroppingSocket()
        client = BinanceWSClient()

        await _run_with_socket(socket, client, settle_s=0.05)
        await client.subscribe(["btcusdt@ticker"])

        checks = {
            "no send on dead socket": socket.sent_after_death == [],
            "stream recorded for replay": "btcusdt@ticker" in client.subscriptions,
        }
        assert all(
            checks.values()
        ), f"failing: {[k for k, ok in checks.items() if not ok]}"


class TestPreConnectSubscriptionBuffering:
    """Streams registered before the socket exists must reach the venue on connect.

    Regression: ``subscribe()`` deliberately records intent in ``_subscriptions``
    and no-ops while ``_socket is None`` ("buffering N streams until connect"),
    but nothing replayed that buffer when the socket came up. The normal
    ``start()`` -> ``subscribe()`` call order races the async dial, so the
    buffered streams were silently never sent — and the idle watchdog could not
    recover it, because the always-on ``!markPrice@arr@1s`` feed kept the socket
    looking alive, so no redial (and thus no ``_resubscribe``) ever ran.
    """

    @pytest.mark.asyncio
    async def test_subscription_made_before_connect_is_sent_on_connect(self) -> None:
        socket = _SocketScript()
        client = BinanceWSClient()

        await client.subscribe(["btcusdt@ticker"])
        assert (
            socket.outbound == []
        ), "nothing should hit the wire before the socket exists"

        await _run_with_socket(socket, client)
        await client.close()

        assert client.subscriptions == frozenset({"btcusdt@ticker"})
        frames = [f for f in socket.sent if f.get("method") == "SUBSCRIBE"]
        assert frames, "buffered subscription never reached the venue"
        assert frames[0]["params"] == ["btcusdt@ticker"]

    @pytest.mark.asyncio
    async def test_multiple_buffered_streams_flush_in_one_frame(self) -> None:
        socket = _SocketScript()
        client = BinanceWSClient()

        await client.subscribe(["btcusdt@kline_1m", "btcusdt@aggTrade"])
        await _run_with_socket(socket, client)
        await client.close()

        frames = [f for f in socket.sent if f.get("method") == "SUBSCRIBE"]
        assert len(frames) == 1, "buffered streams should coalesce into one frame"
        assert sorted(frames[0]["params"]) == ["btcusdt@aggTrade", "btcusdt@kline_1m"]

    @pytest.mark.asyncio
    async def test_subscribing_after_connect_is_not_replayed_twice(self) -> None:
        socket = _SocketScript()
        client = BinanceWSClient()

        await _run_with_socket(socket, client)
        await client.subscribe(["btcusdt@ticker"])
        await client.close()

        frames = [f for f in socket.sent if f.get("method") == "SUBSCRIBE"]
        assert len(frames) == 1, "a post-connect subscribe must go out exactly once"


class TestReaderExitAlwaysSignalsDisconnect:
    """Every reader exit must release the supervisor, clean close included.

    Regression: ``websockets`` ends ``async for`` *normally* on a graceful
    close (code 1000) — ``StopAsyncIteration``, not ``ConnectionClosed`` — so
    only the ``except`` branches dispatched the disconnect. ``is_connected``
    stayed ``True``, ``wait_disconnected()`` blocked forever, and the
    supervisor never redialled. A clean server-side close is the common case.
    """

    @pytest.mark.asyncio
    async def test_graceful_close_dispatches_disconnect(self) -> None:
        socket = _HeldSocket()
        client = BinanceWSClient()
        seen: list[tuple[int, str]] = []
        client.on_disconnect = lambda code, reason: seen.append((code, reason))

        await _run_with_socket(socket, client)
        assert client.is_connected, "connect() should have marked the client live"
        assert seen == [], "still connected — nothing to report yet"

        # A graceful server close ends the iteration normally, no exception.
        socket.finish_gracefully()
        await asyncio.sleep(0.1)

        assert seen, "a cleanly-ended reader never signalled the supervisor"
        assert client.is_connected is False

    @pytest.mark.asyncio
    async def test_wait_disconnected_returns_after_graceful_close(self) -> None:
        socket = _HeldSocket()
        client = BinanceWSClient()

        await _run_with_socket(socket, client)
        waiter = asyncio.ensure_future(client.wait_disconnected())
        await asyncio.sleep(0.05)
        assert not waiter.done(), "must block while the socket is live"

        socket.finish_gracefully()
        # Mirrors what ReconnectManager does: await the drop, then redial.
        await asyncio.wait_for(waiter, timeout=1.0)
