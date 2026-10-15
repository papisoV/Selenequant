"""OKX WS client: public/business socket split, routing, subscription keys."""

from __future__ import annotations

import json

import pytest

from selene_space_exchange.exchanges.okx.ws_client import OKXWSClient
from selene_space_types.market import KlineEvent, TickerEvent


class FakeOKXSocket:
    """Scripted OKX connection: records sends, replays queued frames."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self._frames: list[str] = []

    def queue(self, msg: dict) -> None:
        self._frames.append(json.dumps(msg))

    async def send(self, data: str) -> None:
        self.sent.append(data)

    async def close(self) -> None:
        pass

    def __aiter__(self) -> "FakeOKXSocket":
        return self

    async def __anext__(self) -> str:
        if not self._frames:
            raise StopAsyncIteration
        return self._frames.pop(0)


@pytest.fixture
def public_socket() -> FakeOKXSocket:
    return FakeOKXSocket()


@pytest.fixture
def business_socket() -> FakeOKXSocket:
    return FakeOKXSocket()


def _dual_socket_client(public: FakeOKXSocket, business: FakeOKXSocket) -> OKXWSClient:
    client = OKXWSClient()
    client._ws_public = public  # type: ignore[assignment]
    client._ws_business = business  # type: ignore[assignment]
    return client


class TestChannelPlacement:
    @pytest.mark.asyncio
    async def test_tickers_go_over_the_public_socket(self, public_socket: FakeOKXSocket, business_socket: FakeOKXSocket) -> None:
        client = _dual_socket_client(public_socket, business_socket)
        await client.subscribe_ticker(["BTC-USDT", "ETH-USDT"])

        assert len(public_socket.sent) == 1
        msg = json.loads(public_socket.sent[0])
        checks = {
            "op": msg["op"] == "subscribe",
            "two-args": len(msg["args"]) == 2,
            "first-arg": msg["args"][0] == {"channel": "tickers", "instId": "BTC-USDT"},
            "business-quiet": len(business_socket.sent) == 0,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_klines_go_over_the_business_socket(self, public_socket: FakeOKXSocket, business_socket: FakeOKXSocket) -> None:
        client = _dual_socket_client(public_socket, business_socket)
        await client.subscribe_kline(["BTC-USDT"], interval="1H")

        assert len(business_socket.sent) == 1
        msg = json.loads(business_socket.sent[0])
        checks = {
            "op": msg["op"] == "subscribe",
            "arg": msg["args"][0] == {"channel": "candle1H", "instId": "BTC-USDT"},
            "public-quiet": len(public_socket.sent) == 0,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"


def _ticker_frame() -> dict:
    return {
        "arg": {"channel": "tickers", "instId": "BTC-USDT"},
        "data": [
            {
                "last": "70000",
                "bidPx": "69999",
                "askPx": "70001",
                "open24h": "69000",
                "vol24h": "5000",
                "ts": "1700000000000",
            }
        ],
    }


_KLINE_ROW = ["1700000000000", "2200", "2250", "2180", "2240", "500", "1100000", "1100000", "1"]


def _kline_frame(channel: str, inst_id: str) -> dict:
    return {
        "arg": {"channel": channel, "instId": inst_id},
        "data": [_KLINE_ROW],
    }


class TestRouting:
    @pytest.mark.asyncio
    async def test_ticker_frame_yields_unified_event(self) -> None:
        received: list[TickerEvent] = []
        client = OKXWSClient(on_ticker=received.append)

        await client._route_message(_ticker_frame())

        checks = {
            "one-event": len(received) == 1,
            "symbol": received[0].symbol == "BTC/USDT",
            "last": received[0].last_price == 70000.0,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_kline_frame_yields_closed_hourly_bar(self) -> None:
        received: list[KlineEvent] = []
        client = OKXWSClient(on_kline=received.append)

        await client._route_message(_kline_frame("candle1H", "ETH-USDT"))

        checks = {
            "one-event": len(received) == 1,
            "symbol": received[0].symbol == "ETH/USDT",
            "close": received[0].close == 2240.0,
            "interval": received[0].interval == "1h",
            "closed": received[0].closed is True,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    @pytest.mark.asyncio
    async def test_ack_envelope_never_reaches_handlers(self) -> None:
        received: list[TickerEvent] = []
        client = OKXWSClient(on_ticker=received.append)

        await client._route_message(
            {"event": "subscribe", "arg": {"channel": "tickers", "instId": "BTC-USDT"}}
        )

        assert received == []


class TestSubscriptionLedger:
    @pytest.mark.asyncio
    async def test_both_channels_recorded_under_channel_inst_keys(self, public_socket: FakeOKXSocket, business_socket: FakeOKXSocket) -> None:
        client = _dual_socket_client(public_socket, business_socket)

        await client.subscribe_ticker(["BTC-USDT"])
        await client.subscribe_kline(["BTC-USDT"])

        tracked = {"tickers:BTC-USDT" in client.subscriptions, "candle1H:BTC-USDT" in client.subscriptions}
        assert tracked == {True}

    @pytest.mark.asyncio
    async def test_unsubscribe_drops_only_its_key(self, public_socket: FakeOKXSocket, business_socket: FakeOKXSocket) -> None:
        client = _dual_socket_client(public_socket, business_socket)

        await client.subscribe_ticker(["BTC-USDT"])
        await client.unsubscribe(["tickers:BTC-USDT"])

        assert "tickers:BTC-USDT" not in client.subscriptions
