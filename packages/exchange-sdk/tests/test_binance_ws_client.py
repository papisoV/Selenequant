"""BinanceWSClient: control frames, event fan-out, envelope unwrap, stream names."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

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


async def _run_with_socket(socket: _SocketScript, client: BinanceWSClient, settle_s: float = 0.05) -> None:
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
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

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
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


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
        assert BinanceWSClient.stream_name("btcusdt", "kline", interval="1h") == "btcusdt@kline_1h"

    def test_partial_depth_stream_passes_through(self) -> None:
        assert BinanceWSClient.stream_name("btcusdt", "depth@100ms") == "btcusdt@depth@100ms"

    def test_trade_stream_for_any_symbol(self) -> None:
        assert BinanceWSClient.stream_name("ethusdt", "trade") == "ethusdt@trade"

    def test_uppercase_input_gets_folded(self) -> None:
        assert BinanceWSClient.stream_name("BTCUSDT", "ticker") == "btcusdt@ticker"
