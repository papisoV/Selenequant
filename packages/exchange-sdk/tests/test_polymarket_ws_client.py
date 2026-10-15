"""Polymarket WS client: enums, dual-socket routing, auth subscription."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from selene_space_exchange.exchanges.polymarket.ws_client import (
    POLYMARKET_WS_MARKET,
    POLYMARKET_WS_USER,
    PolymarketEventType,
    PolymarketOrderEventType,
    PolymarketTradeStatus,
    PolymarketWSClient,
)


class TestEnums:
    def test_market_feed_event_names(self) -> None:
        wire_names = {
            PolymarketEventType.BOOK: "book",
            PolymarketEventType.PRICE_CHANGE: "price_change",
            PolymarketEventType.ORDER: "order",
            PolymarketEventType.TRADE: "trade",
            PolymarketEventType.LAST_TRADE_PRICE: "last_trade_price",
            PolymarketEventType.MARKET_CREATED: "market_created",
        }
        assert all(enum.value == name for enum, name in wire_names.items())

    def test_trade_lifecycle_statuses(self) -> None:
        states = {s.name: s.value for s in PolymarketTradeStatus}
        assert states == {
            "MATCHED": "MATCHED",
            "MINED": "MINED",
            "CONFIRMED": "CONFIRMED",
            "RETRYING": "RETRYING",
            "FAILED": "FAILED",
        }

    def test_order_event_kinds(self) -> None:
        kinds = {k.name: k.value for k in PolymarketOrderEventType}
        assert kinds == {"PLACEMENT": "PLACEMENT", "UPDATE": "UPDATE", "CANCELLATION": "CANCELLATION"}


class TestConstruction:
    def test_default_endpoints(self) -> None:
        ws = PolymarketWSClient()
        assert (ws._market_url, ws._user_url) == (POLYMARKET_WS_MARKET, POLYMARKET_WS_USER)

    def test_endpoint_overrides(self) -> None:
        ws = PolymarketWSClient(market_url="wss://custom/market", user_url="wss://custom/user")
        assert (ws._market_url, ws._user_url) == ("wss://custom/market", "wss://custom/user")

    def test_exchange_tag_and_auth_state(self) -> None:
        fresh = PolymarketWSClient()
        checks = {
            "exchange": fresh.config.exchange == "polymarket",
            "unauthenticated": fresh.is_user_authenticated is False,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_callbacks_stored_by_reference(self) -> None:
        probe = MagicMock()
        ws = PolymarketWSClient(on_book=probe, on_order=probe, on_trade=probe)
        identity = {ws.on_book is probe, ws.on_order is probe, ws.on_trade is probe}
        assert identity == {True}


def _routed_client() -> PolymarketWSClient:
    """Client with every handler mocked and watchdog stubbed out."""
    ws = PolymarketWSClient()
    ws.on_book = AsyncMock()
    ws.on_price_change = AsyncMock()
    ws.on_last_trade = AsyncMock()
    ws.on_order = AsyncMock()
    ws.on_trade = AsyncMock()
    ws._stall_watchdog = MagicMock()
    ws._stall_watchdog.touch = MagicMock()
    return ws


class TestMessageRouting:
    @pytest.mark.asyncio
    async def test_book_frame_hits_book_handler(self) -> None:
        ws = _routed_client()
        frame = {"event_type": "book", "asset_id": "abc123", "bids": [["0.55", "100"]], "asks": [["0.56", "200"]]}
        await ws._route_message(frame, "market")
        ws.on_book.assert_awaited_once_with(frame)

    @pytest.mark.asyncio
    async def test_price_change_hits_its_handler(self) -> None:
        ws = _routed_client()
        frame = {"event_type": "price_change", "asset_id": "abc123", "price": "0.57"}
        await ws._route_message(frame, "market")
        ws.on_price_change.assert_awaited_once_with(frame)

    @pytest.mark.asyncio
    async def test_last_trade_price_hits_its_handler(self) -> None:
        ws = _routed_client()
        frame = {"event_type": "last_trade_price", "asset_id": "abc123", "price": "0.58"}
        await ws._route_message(frame, "market")
        ws.on_last_trade.assert_awaited_once_with(frame)

    @pytest.mark.asyncio
    async def test_order_frame_on_user_socket_hits_order_handler(self) -> None:
        ws = _routed_client()
        frame = {
            "event_type": "order",
            "id": "order-001",
            "asset_id": "abc123",
            "side": "BUY",
            "price": "0.55",
            "original_size": "10",
            "size_matched": "0",
            "type": "PLACEMENT",
            "market": "0xcondition",
            "owner": "0xowner",
        }
        await ws._route_message(frame, "user")
        ws.on_order.assert_awaited_once_with(frame)

    @pytest.mark.asyncio
    async def test_trade_frame_on_user_socket_hits_trade_handler(self) -> None:
        ws = _routed_client()
        frame = {
            "event_type": "trade",
            "id": "trade-001",
            "asset_id": "abc123",
            "side": "BUY",
            "price": "0.55",
            "size": "10",
            "status": "MATCHED",
            "market": "0xcondition",
            "owner": "0xowner",
        }
        await ws._route_message(frame, "user")
        ws.on_trade.assert_awaited_once_with(frame)

    @pytest.mark.asyncio
    async def test_sockets_do_not_cross_deliver(self) -> None:
        market_side = _routed_client()
        await market_side._route_message({"event_type": "book", "asset_id": "abc"}, "market")
        assert (market_side.on_order.await_count, market_side.on_trade.await_count) == (0, 0)

        user_side = _routed_client()
        await user_side._route_message({"event_type": "order", "id": "o1"}, "user")
        assert (user_side.on_book.await_count, user_side.on_price_change.await_count) == (0, 0)

    @pytest.mark.asyncio
    async def test_unrecognized_event_is_dropped_quietly(self) -> None:
        ws = _routed_client()
        await ws._route_message({"event_type": "unknown_type", "data": "something"}, "market")
        ws.on_book.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_every_frame_touches_the_watchdog(self) -> None:
        ws = _routed_client()
        await ws._route_message({"event_type": "book", "asset_id": "abc"}, "market")
        ws._stall_watchdog.touch.assert_called_once()


class TestSubscriptionTracking:
    @staticmethod
    def _socket_mock() -> AsyncMock:
        socket = AsyncMock()
        socket.send = AsyncMock()
        return socket

    def test_market_subscribe_records_prefixed_keys(self) -> None:
        ws = PolymarketWSClient()
        ws._ws_market = self._socket_mock()

        asyncio.run(ws.subscribe_market(["token1", "token2"]))

        tracked = {"market:token1" in ws._subscriptions, "market:token2" in ws._subscriptions}
        assert tracked == {True}
        assert ws._ws_market.send.call_count >= 1

    def test_unsubscribe_removes_only_the_named_stream(self) -> None:
        ws = PolymarketWSClient()
        ws._subscriptions.update({"market:token1", "market:token2"})
        ws._ws_market = self._socket_mock()

        asyncio.run(ws.unsubscribe(["market:token1"]))

        assert ("market:token1" in ws._subscriptions, "market:token2" in ws._subscriptions) == (False, True)

    def test_generic_subscribe_defaults_to_market_channel(self) -> None:
        ws = PolymarketWSClient()
        ws._ws_market = self._socket_mock()

        asyncio.run(ws.subscribe(["token_a"]))
        assert "market:token_a" in ws._subscriptions


class TestUserAuthSubscription:
    @staticmethod
    def _client() -> PolymarketWSClient:
        ws = PolymarketWSClient()
        ws._ws_user = AsyncMock()
        ws._ws_user.send = AsyncMock()
        return ws

    def test_subscribe_user_embeds_credentials_and_markets(self) -> None:
        ws = self._client()
        asyncio.run(
            ws.subscribe_user(
                api_key="key",
                api_secret="secret",
                api_passphrase="pass",
                markets=["0xcondition1"],
            )
        )

        sent = json.loads(ws._ws_user.send.call_args[0][0])
        checks = {
            "type": sent["type"] == "user",
            "key": sent["auth"]["apiKey"] == "key",
            "secret": sent["auth"]["secret"] == "secret",
            "passphrase": sent["auth"]["passphrase"] == "pass",
            "markets": sent["markets"] == ["0xcondition1"],
            "auth-flag": ws._user_authenticated is True,
            "tracked-auth": "user:authenticated" in ws._subscriptions,
            "tracked-market": "user:0xcondition1" in ws._subscriptions,
        }
        assert all(checks.values()), f"failing: {[k for k, ok in checks.items() if not ok]}"

    def test_subscribe_user_without_markets_omits_the_key(self) -> None:
        ws = self._client()
        asyncio.run(ws.subscribe_user(api_key="k", api_secret="s", api_passphrase="p"))

        sent = json.loads(ws._ws_user.send.call_args[0][0])
        assert ("markets" not in sent, ws._user_authenticated is True) == (True, True)


@pytest.mark.asyncio
async def test_close_tears_down_both_sockets_and_flags() -> None:
    ws = PolymarketWSClient()
    ws._ws_market = AsyncMock()
    ws._ws_user = AsyncMock()
    ws._user_authenticated = True
    ws._stall_watchdog = MagicMock()
    ws._stall_watchdog.stop = MagicMock()

    await ws.close()

    teardown = {
        "market-null": ws._ws_market is None,
        "user-null": ws._ws_user is None,
        "auth-cleared": ws._user_authenticated is False,
        "disconnected": ws.is_connected is False,
    }
    assert all(teardown.values()), f"failing: {[k for k, ok in teardown.items() if not ok]}"
