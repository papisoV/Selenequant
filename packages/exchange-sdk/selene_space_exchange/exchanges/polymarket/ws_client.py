"""Polymarket WebSocket client (market + user channels).

Two sockets: the public market socket carries book/price pushes keyed by
``event_type``; the authenticated user socket needs an auth block in its
first subscribe frame and then streams order/trade lifecycle events. The
venue expects a literal ``"PING"`` text frame as keepalive. Routing is
channel-scoped: a market event never reaches user callbacks and vice versa.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from enum import Enum
from typing import Any

import websockets
import websockets.asyncio.client

from selene_space_exchange.base.stall_watchdog import StallTimeoutMeta, StallWatchdog
from selene_space_exchange.base.ws_client import BaseWSClient
from selene_space_exchange.types import WSClientConfig

_log = logging.getLogger(__name__)

POLYMARKET_WS_MARKET = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
POLYMARKET_WS_USER = "wss://ws-subscriptions-clob.polymarket.com/ws/user"

#: Venue drops silent sockets; text "PING" keeps them alive.
PING_TEXT = "PING"
PING_INTERVAL_S = 50


class PolymarketEventType(str, Enum):
    BOOK = "book"
    PRICE_CHANGE = "price_change"
    LAST_TRADE_PRICE = "last_trade_price"
    TICK_SIZE_CHANGE = "tick_size_change"
    ORDER = "order"
    TRADE = "trade"
    MARKET_CREATED = "market_created"
    MARKET_RESOLVED = "market_resolved"
    TRADES = "trades"
    ORDERS_MATCHED = "orders_matched"


class PolymarketTradeStatus(str, Enum):
    MATCHED = "MATCHED"
    MINED = "MINED"
    CONFIRMED = "CONFIRMED"
    RETRYING = "RETRYING"
    FAILED = "FAILED"


class PolymarketOrderEventType(str, Enum):
    PLACEMENT = "PLACEMENT"
    UPDATE = "UPDATE"
    CANCELLATION = "CANCELLATION"


_MARKET_ROUTES = {
    PolymarketEventType.BOOK.value: "on_book",
    PolymarketEventType.PRICE_CHANGE.value: "on_price_change",
    PolymarketEventType.LAST_TRADE_PRICE.value: "on_last_trade",
}

_USER_ROUTES = {
    PolymarketEventType.ORDER.value: "on_order",
    PolymarketEventType.TRADE.value: "on_trade",
}


class PolymarketWSClient(BaseWSClient):
    def __init__(
        self,
        *,
        market_url: str = POLYMARKET_WS_MARKET,
        user_url: str = POLYMARKET_WS_USER,
        stall_timeout_s: float = 90.0,
        auto_reconnect: bool = True,
        max_reconnect_attempts: int = 0,
        proxy: str | None = None,
        on_book: Callable[[dict[str, Any]], Any] | None = None,
        on_price_change: Callable[[dict[str, Any]], Any] | None = None,
        on_last_trade: Callable[[dict[str, Any]], Any] | None = None,
        on_order: Callable[[dict[str, Any]], Any] | None = None,
        on_trade: Callable[[dict[str, Any]], Any] | None = None,
        on_stall: Callable[[StallTimeoutMeta], Any] | None = None,
        on_disconnect: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            WSClientConfig(url=market_url, exchange="polymarket", proxy_url=proxy),
            **kwargs,
        )
        self._market_url = market_url
        self._user_url = user_url
        self._proxy = proxy
        self._auto_reconnect = auto_reconnect
        self._max_reconnect_attempts = max_reconnect_attempts

        self.on_book = on_book
        self.on_price_change = on_price_change
        self.on_last_trade = on_last_trade
        self.on_order = on_order
        self.on_trade = on_trade
        self.on_disconnect = on_disconnect

        self._ws_market: websockets.asyncio.client.ClientConnection | None = None
        self._ws_user: websockets.asyncio.client.ClientConnection | None = None
        self._user_authenticated = False
        self._reader_tasks: list[asyncio.Task[None]] = []
        self._ping_task: asyncio.Task[None] | None = None

        self._stall_watchdog = StallWatchdog(
            timeout_s=stall_timeout_s,
            on_timeout=on_stall if on_stall is not None else self._default_stall_handler,
            label="polymarket-ws-stall",
        )

    # -- connection properties -----------------------------------------------

    @property
    def is_user_authenticated(self) -> bool:
        return self._user_authenticated

    @property
    def is_connected(self) -> bool:
        return self._ws_market is not None or self._ws_user is not None

    def _ws_connect_kwargs(self) -> dict[str, Any]:
        return {
            "ping_interval": 25,
            "ping_timeout": 50,
            **({"proxy": self._proxy} if self._proxy else {}),
        }

    async def connect(self) -> None:
        await self._connect_market()
        await self._dispatch_connect()
        self._start_ping_loop()

    async def _connect_market(self) -> None:
        _log.info("dialling polymarket market socket %s", self._market_url)
        self._ws_market = await websockets.asyncio.client.connect(
            self._market_url, **self._ws_connect_kwargs()
        )
        self._reader_tasks.append(
            asyncio.create_task(self._receive_loop(self._ws_market, "market"))
        )

    async def _connect_user(self) -> None:
        _log.info("dialling polymarket user socket %s", self._user_url)
        self._ws_user = await websockets.asyncio.client.connect(
            self._user_url, **self._ws_connect_kwargs()
        )
        self._reader_tasks.append(
            asyncio.create_task(self._receive_loop(self._ws_user, "user"))
        )

    def _start_ping_loop(self) -> None:
        if self._ping_task is None or self._ping_task.done():
            self._ping_task = asyncio.create_task(self._ping_loop())

    async def _ping_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(PING_INTERVAL_S)
                for sock in (self._ws_market, self._ws_user):
                    if sock is not None:
                        try:
                            await sock.send(PING_TEXT)
                        except Exception:
                            pass
        except asyncio.CancelledError:
            raise

    async def _reconnect(self) -> None:
        if not self._auto_reconnect:
            return
        attempts = 0
        while self._max_reconnect_attempts == 0 or attempts < self._max_reconnect_attempts:
            attempts += 1
            delay = min(2**attempts, 30)
            _log.info("polymarket reconnect attempt %d in %.0fs", attempts, delay)
            await asyncio.sleep(delay)
            try:
                if self._ws_market is None:
                    await self._connect_market()
                    break
                if self._user_authenticated and self._ws_user is None:
                    await self._connect_user()
                break
            except Exception as exc:
                _log.warning("reconnect failed: %s", exc)

    # -- subscriptions -----------------------------------------------------------

    async def subscribe(self, streams: list[str]) -> None:
        """Convenience alias: tokens go to the market socket."""
        await self.subscribe_market(streams)

    async def subscribe_market(self, token_ids: list[str]) -> None:
        if self._ws_market is None:
            await self._connect_market()
            self._start_ping_loop()
        assets = [f"market:{t}" for t in token_ids]
        for token in token_ids:
            self._subscriptions.add(f"market:{token}")
        assert self._ws_market is not None
        await self._ws_market.send(json.dumps({"type": "market", "assets": token_ids, "_raw": assets}))

    async def subscribe_user(
        self,
        *,
        api_key: str,
        api_secret: str,
        api_passphrase: str,
        markets: list[str] | None = None,
    ) -> None:
        if self._ws_user is None:
            await self._connect_user()
            self._start_ping_loop()
        assert self._ws_user is not None
        frame: dict[str, Any] = {
            "type": "user",
            "auth": {
                "apiKey": api_key,
                "secret": api_secret,
                "passphrase": api_passphrase,
            },
        }
        self._subscriptions.add("user:authenticated")
        if markets is not None:
            frame["markets"] = markets
            for market in markets:
                self._subscriptions.add(f"user:{market}")
        await self._ws_user.send(json.dumps(frame))
        self._user_authenticated = True

    async def unsubscribe(self, streams: list[str]) -> None:
        for entry in streams:
            self._subscriptions.discard(entry)
        scope = "market" if any(s.startswith("market:") for s in streams) else "user"
        assets = [s.split(":", 1)[1] for s in streams]
        sock = self._ws_market if scope == "market" else self._ws_user
        if sock is not None and assets:
            try:
                await sock.send(json.dumps({"type": scope, "assets": assets}))
            except Exception:
                pass

    async def close(self) -> None:
        self._stall_watchdog.stop()
        for task in (self._ping_task, *self._reader_tasks):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._ping_task, *self._reader_tasks):
            if task is not None:
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._ping_task = None
        self._reader_tasks.clear()
        for attr in ("_ws_market", "_ws_user"):
            setattr(self, attr, None)
        self._user_authenticated = False
        await self._dispatch_disconnect()

    # -- receive / routing -------------------------------------------------------------

    async def _receive_loop(
        self, ws: websockets.asyncio.client.ClientConnection, label: str
    ) -> None:
        try:
            async for raw in ws:
                text = raw.strip() if isinstance(raw, str) else str(raw)
                if text == PING_TEXT or text.startswith("PONG"):
                    continue
                try:
                    frames = json.loads(text)
                except ValueError:
                    continue
                batch = frames if isinstance(frames, list) else [frames]
                for item in batch:
                    if isinstance(item, dict):
                        try:
                            await self._route_message(item, label)
                        except Exception as exc:
                            _log.debug("[%s] event dropped: %s", label, exc)
        except websockets.ConnectionClosed as exc:
            _log.info("[%s] closed (%s)", label, exc)
            await self._dispatch_disconnect(code=getattr(exc, "code", 1006))
            if self.on_disconnect is not None:
                await BaseWSClient._invoke(self.on_disconnect)
            await self._reconnect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("[%s] reader failed: %s", label, exc)
            await self._dispatch_error(exc)

    async def _route_message(self, data: dict[str, Any], channel: str) -> None:
        self._stall_watchdog.touch()

        kind = data.get("event_type", "")
        if channel == "user":
            await self._route_user_event(data, kind)
        else:
            await self._route_market_event(data, kind)

    async def _route_market_event(self, data: dict[str, Any], event_type: str) -> None:
        handler_name = _MARKET_ROUTES.get(event_type)
        if handler_name is not None:
            await BaseWSClient._invoke(getattr(self, handler_name), data)

    async def _route_user_event(self, data: dict[str, Any], event_type: str) -> None:
        handler_name = _USER_ROUTES.get(event_type)
        if handler_name is not None:
            await BaseWSClient._invoke(getattr(self, handler_name), data)

    async def _default_stall_handler(self, meta: StallTimeoutMeta) -> None:
        _log.warning("polymarket feed idle %.1fs — closing sockets", meta.idle_s)
        for sock in (self._ws_market, self._ws_user):
            if sock is not None:
                try:
                    await sock.close()
                except Exception:
                    pass
