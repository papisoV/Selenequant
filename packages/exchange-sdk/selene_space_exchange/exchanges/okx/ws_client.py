"""OKX public market-data WebSocket client.

OKX splits its public surface across two sockets: ``/ws/v5/public`` for
tickers/trades/books/liquidation pushes and ``/ws/v5/business`` for candle
history. This client holds both, tracks subscriptions as
``"<channel>:<instId>"`` strings, and routes each push frame's ``data``
entries through the OKX normalizer to typed callbacks.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import Any

import websockets
import websockets.asyncio.client

from selene_space_exchange.base.stall_watchdog import StallTimeoutMeta, StallWatchdog
from selene_space_exchange.base.ws_client import BaseWSClient
from selene_space_exchange.exchanges.okx.normalizer import OKXNormalizer
from selene_space_exchange.types import WSClientConfig
from selene_space_types.derivatives import LiquidationEvent
from selene_space_types.market import KlineEvent, OrderBookL2Snapshot, TickerEvent, TradeEvent

_log = logging.getLogger(__name__)

DEFAULT_OKX_PUBLIC_URL = "wss://ws.okx.com:8443/ws/v5/public"
DEFAULT_OKX_BUSINESS_URL = "wss://ws.okx.com:8443/ws/v5/business"

#: Channels that live on the business socket (candles) vs the public one.
_CANDLE_CHANNELS = frozenset({"candles", "candle"})
_INST_TYPE_CHANNELS = frozenset({"liquidation-orders"})

_PUBLIC_SINKS = {
    "tickers": "on_ticker",
    "trades": "on_trade",
    "books": "on_depth",
    "bbo-tbt": "on_depth",
    "liquidation-orders": "on_liquidation",
}


class OKXWSClient(BaseWSClient):
    def __init__(
        self,
        *,
        public_url: str = DEFAULT_OKX_PUBLIC_URL,
        business_url: str = DEFAULT_OKX_BUSINESS_URL,
        proxy_url: str | None = None,
        kline_interval: str = "1H",
        stall_timeout_s: float = 30.0,
        on_ticker: Callable[[TickerEvent], Any] | None = None,
        on_trade: Callable[[TradeEvent], Any] | None = None,
        on_kline: Callable[[KlineEvent], Any] | None = None,
        on_depth: Callable[[OrderBookL2Snapshot], Any] | None = None,
        on_liquidation: Callable[[LiquidationEvent], Any] | None = None,
        on_stall: Callable[[StallTimeoutMeta], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            WSClientConfig(url=public_url, exchange="okx", proxy_url=proxy_url),
            **kwargs,
        )
        self._public_url = public_url
        self._business_url = business_url
        self._default_interval = kline_interval
        self._parser = OKXNormalizer()
        self._ws_public: websockets.asyncio.client.ClientConnection | None = None
        self._ws_business: websockets.asyncio.client.ClientConnection | None = None
        self._reader_tasks: list[asyncio.Task[None]] = []

        self.on_ticker = on_ticker
        self.on_trade = on_trade
        self.on_kline = on_kline
        self.on_depth = on_depth
        self.on_liquidation = on_liquidation

        self._watchdog = StallWatchdog(
            timeout_s=stall_timeout_s,
            on_timeout=on_stall if on_stall is not None else self._default_stall_handler,
            label="okx-ws-stall",
        )

    # -- lifecycle -------------------------------------------------------------

    async def connect(self) -> None:
        proxy = self.config.proxy_url or None
        _log.info("dialling okx public=%s business=%s", self._public_url, self._business_url)
        self._ws_public = await websockets.asyncio.client.connect(
            self._public_url, proxy=proxy, ping_interval=25, ping_timeout=50
        )
        self._reader_tasks.append(
            asyncio.create_task(self._receive_loop(self._ws_public, "public"))
        )
        await self._dispatch_connect()

    async def subscribe(self, streams: list[str]) -> None:
        """Streams are ``channel:value`` pairs, e.g. ``tickers:BTC-USDT``."""
        if not streams:
            return
        public_args: list[dict[str, str]] = []
        business_args: list[dict[str, str]] = []
        for entry in streams:
            channel, _, value = entry.partition(":")
            arg = self._build_sub_arg(channel, value)
            if self._socket_for(channel) is self._ws_business:
                business_args.append(arg)
            else:
                public_args.append(arg)
            self._subscriptions.add(entry)
        if public_args:
            await self._send_op("subscribe", public_args)
        if business_args:
            await self._ensure_business_socket()
            await self._send_op("subscribe", business_args)
        self._watchdog.arm()

    async def unsubscribe(self, streams: list[str]) -> None:
        if not streams:
            return
        by_socket: dict[bool, list[dict[str, str]]] = {True: [], False: []}
        for entry in streams:
            channel, _, value = entry.partition(":")
            by_socket[self._socket_for(channel) is self._ws_business].append(
                self._build_sub_arg(channel, value)
            )
            self._subscriptions.discard(entry)
        if self._ws_public is not None and by_socket[False]:
            await self._send_op("unsubscribe", by_socket[False])
        if self._ws_business is not None and by_socket[True]:
            await self._send_op("unsubscribe", by_socket[True])

    async def close(self) -> None:
        self._watchdog.stop()
        for task in self._reader_tasks:
            if not task.done():
                task.cancel()
        for task in self._reader_tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._reader_tasks.clear()
        for attr in ("_ws_public", "_ws_business"):
            sock: websockets.asyncio.client.ClientConnection | None = getattr(self, attr)
            if sock is not None:
                try:
                    await sock.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        await self._dispatch_disconnect()

    # -- typed subscription helpers ---------------------------------------------

    async def subscribe_ticker(self, inst_ids: list[str]) -> None:
        await self.subscribe([f"tickers:{i}" for i in inst_ids])

    async def subscribe_trades(self, inst_ids: list[str]) -> None:
        await self.subscribe([f"trades:{i}" for i in inst_ids])

    async def subscribe_depth(self, inst_ids: list[str]) -> None:
        await self.subscribe([f"books:{i}" for i in inst_ids])

    async def subscribe_kline(self, inst_ids: list[str], interval: str | None = None) -> None:
        bar = interval or self._default_interval
        await self.subscribe([f"candle{bar}:{i}" for i in inst_ids])

    async def subscribe_liquidation_orders(self, inst_type: str = "SWAP") -> None:
        await self.subscribe([f"liquidation-orders:{inst_type}"])

    @classmethod
    def _build_sub_arg(cls, channel: str, value: str) -> dict[str, str]:
        if channel in _INST_TYPE_CHANNELS:
            return {"channel": channel, "instType": value}
        return {"channel": channel, "instId": value}

    # -- internals ----------------------------------------------------------------

    def _socket_for(self, channel: str) -> Any:
        head = channel.rstrip("0123456789HYMD")  # candle1H -> candle
        return (
            self._ws_business
            if head in _CANDLE_CHANNELS or channel.startswith("candle")
            else self._ws_public
        )

    async def _ensure_business_socket(self) -> None:
        if self._ws_business is not None:
            return
        proxy = self.config.proxy_url or None
        self._ws_business = await websockets.asyncio.client.connect(
            self._business_url, proxy=proxy, ping_interval=25, ping_timeout=50
        )
        self._reader_tasks.append(
            asyncio.create_task(self._receive_loop(self._ws_business, "business"))
        )

    async def _send_op(self, op: str, args: list[dict[str, str]]) -> None:
        target = (
            self._ws_business if args and args[0]["channel"].startswith("candle") else self._ws_public
        )
        if target is None:
            return
        await target.send(json.dumps({"op": op, "args": args}))

    async def _receive_loop(
        self, ws: websockets.asyncio.client.ClientConnection, label: str
    ) -> None:
        try:
            async for raw in ws:
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue
                try:
                    await self._route_message(payload)
                except Exception as exc:
                    _log.debug("[%s] frame dropped: %s", label, exc)
        except websockets.ConnectionClosed as exc:
            _log.info("[%s] closed (%s)", label, exc)
            await self._dispatch_disconnect(code=getattr(exc, "code", 1006))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("[%s] reader failed: %s", label, exc)
            await self._dispatch_error(exc)

    async def _route_message(self, data: dict[str, Any]) -> None:
        self._watchdog.touch()

        # Login/subscribe/error ACK frames carry event= and no arg.
        if "event" in data and "arg" not in data:
            if data.get("event") == "error":
                _log.warning("okx stream error frame: %s", data)
            return

        arg = data.get("arg") or {}
        channel = arg.get("channel", "")
        inst_id = arg.get("instId", "")
        rows = data.get("data")
        if not rows:
            return

        sink_name = _PUBLIC_SINKS.get(channel)
        if channel.startswith("candle"):
            for row in rows:
                await BaseWSClient._invoke(
                    self.on_kline, self._parser.normalize_candle(row, inst_id, channel)
                )
        elif sink_name == "on_depth":
            await BaseWSClient._invoke(
                self.on_depth, self._parser.normalize_orderbook(rows[-1], inst_id)
            )
        elif sink_name == "on_liquidation":
            for row in rows:
                for event in self._parser.normalize_liquidation(row, inst_id):
                    await BaseWSClient._invoke(self.on_liquidation, event)
        elif sink_name is not None:
            handler: Callable[..., Any] | None = getattr(self, sink_name)
            for row in rows:
                method = (
                    self._parser.normalize_ticker
                    if sink_name == "on_ticker"
                    else self._parser.normalize_trade
                )
                await BaseWSClient._invoke(handler, method(row, inst_id))

    async def _default_stall_handler(self, meta: StallTimeoutMeta) -> None:
        _log.warning("okx feed idle %.1fs — closing sockets", meta.idle_s)
        for sock in (self._ws_public, self._ws_business):
            if sock is not None:
                try:
                    await sock.close()
                except Exception:
                    pass
