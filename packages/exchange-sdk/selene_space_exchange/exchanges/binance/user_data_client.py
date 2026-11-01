"""Binance private user-data stream (WS API session.logon path).

The venue offers two ways to receive your own order/balance events: the
JSON-RPC style ``ws-api`` socket where the connection itself logs on with
an HMAC signature, and the classic listen-key flow over the futures REST
surface. This client speaks the former by default and keeps the listen-key
lifecycle helpers for the latter. Pushed frames arrive keyed by their
``e`` discriminator — spot ``executionReport``, futures
``ORDER_TRADE_UPDATE`` (with the actual payload nested under ``o``),
``outboundAccountPosition`` (batch snapshot under ``B``) and the single
delta ``balanceUpdate``. The venue also emits ``eventStreamTerminated``
when it is about to drop the session, which we treat as a reconnect
signal rather than a fatal error.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import inspect
import json
import logging
import time
from collections.abc import Callable
from decimal import Decimal
from typing import Any

import websockets
import websockets.asyncio.client

from selene_space_exchange.base.stall_watchdog import StallTimeoutMeta, StallWatchdog
from selene_space_exchange.base.ws_client import BaseWSClient
from selene_space_exchange.normalizers.symbol import SymbolNormalizer
from selene_space_exchange.types import WSClientConfig
from selene_space_types.trading import BalanceUpdate, ExchangeOrderUpdate, OrderSide

logger = logging.getLogger(__name__)

EXCHANGE = "binance"

BINANCE_WS_API_URL = "wss://ws-api.binance.com:443/ws-api/v3"
BINANCE_WS_API_TESTNET_URL = "wss://testnet.binance.vision/ws-api/v3"
BINANCE_STREAM_URL = "wss://stream.binance.com:9443/ws"
BINANCE_VISION_STREAM_URL = "wss://data-stream.binance.vision/ws"
BINANCE_FUTURES_STREAM_URL = "wss://fstream.binance.com/ws"
BINANCE_FUTURES_STREAM_TESTNET_URL = "wss://stream.binancefuture.com/ws"

#: Server-side listen-key expiry is 60 minutes; refresh well ahead of it.
LISTEN_KEY_REFRESH_S = 1800.0

_USER_DATA_STREAM = "!userData"


class BinanceUserDataNormalizer:
    """Wire-key decoder for the four user-data push shapes.

    Both order-update shapes share nearly every field position — the
    futures variant simply nests them one level deeper under ``o`` and
    adds an average-price shortcut (``ap``). One key table therefore
    serves both; only the nesting and the avg-price rule differ.
    """

    #: semantic name -> venue wire key
    _FIELD_KEYS = {
        "symbol": "s",
        "client_order_id": "c",
        "exchange_order_id": "i",
        "side": "S",
        "order_type": "o",
        "status": "X",
        "quantity": "q",
        "filled_quantity": "z",
        "last_fill_price": "L",
        "last_fill_quantity": "l",
        "commission": "n",
        "commission_asset": "N",
    }

    def __init__(self, symbol_normalizer: SymbolNormalizer) -> None:
        self._symbols = symbol_normalizer

    # -- payload accessors ---------------------------------------------------

    @staticmethod
    def _payload(data: dict[str, Any]) -> dict[str, Any]:
        """Futures ORDER_TRADE_UPDATE nests everything under ``o``."""
        nested = data.get("o")
        return nested if isinstance(nested, dict) else data

    @staticmethod
    def _avg_fill_price(payload: dict[str, Any]) -> str:
        """Prefer the explicit ``ap``; fall back to quote-volume ÷ filled qty."""
        explicit = payload.get("ap")
        if explicit:
            return str(explicit)
        quote = payload.get("Z")
        filled = payload.get("z")
        try:
            divisor = Decimal(str(filled or "0"))
            if not divisor:
                return "0"
            return str(Decimal(str(quote or "0")) / divisor)
        except Exception:
            return "0"

    def _fields(self, data: dict[str, Any]) -> dict[str, Any]:
        payload = self._payload(data)
        fields: dict[str, Any] = {}
        for name, key in self._FIELD_KEYS.items():
            value = payload.get(key)
            if value is None:
                continue
            if name == "exchange_order_id":
                value = str(value)
            elif name == "side":
                value = OrderSide(str(value).lower())
            elif name == "symbol":
                value = self._symbols.to_unified(EXCHANGE, str(value))
            fields[name] = value
        fields["avg_fill_price"] = self._avg_fill_price(payload)
        return fields

    # -- public decoders -------------------------------------------------------

    def normalize_execution_report(self, data: dict[str, Any]) -> ExchangeOrderUpdate:
        return ExchangeOrderUpdate(
            exchange=EXCHANGE,
            **self._fields(data),
            timestamp=int(data.get("E") or 0),
            raw=data,
        )

    def normalize_order_trade_update(self, data: dict[str, Any]) -> ExchangeOrderUpdate:
        return ExchangeOrderUpdate(
            exchange=EXCHANGE,
            **self._fields(data),
            timestamp=int(self._payload(data).get("T") or data.get("E") or 0),
            raw=data,
        )

    def normalize_account_position(self, data: dict[str, Any]) -> list[BalanceUpdate]:
        stamp = int(data.get("E") or 0)
        return [
            BalanceUpdate(
                exchange=EXCHANGE,
                asset=row.get("a", ""),
                free=str(row.get("f") or "0"),
                locked=str(row.get("l") or "0"),
                timestamp=stamp,
            )
            for row in data.get("B") or []
        ]

    def normalize_balance_update(self, data: dict[str, Any]) -> BalanceUpdate:
        return BalanceUpdate(
            exchange=EXCHANGE,
            asset=str(data.get("a") or ""),
            free=str(data.get("d") or "0"),
            locked="0",
            timestamp=int(data.get("E") or 0),
        )


class BinanceUserDataClient(BaseWSClient):
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        url: str = BINANCE_WS_API_URL,
        testnet: bool = False,
        symbol_normalizer: SymbolNormalizer | None = None,
        stall_timeout_s: float = 60.0,
        on_order_update: Callable[[ExchangeOrderUpdate], Any] | None = None,
        on_balance_update: Callable[[list[BalanceUpdate]], Any] | None = None,
        on_balance_delta: Callable[[BalanceUpdate], Any] | None = None,
        on_stall: Callable[[StallTimeoutMeta], Any] | None = None,
        on_reconnect: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            WSClientConfig(url=url, exchange=EXCHANGE),
            **kwargs,
        )
        self._key = api_key
        self._secret = api_secret
        self._testnet = testnet
        self._decoder = BinanceUserDataNormalizer(symbol_normalizer or SymbolNormalizer())

        self.on_order_update = on_order_update
        self.on_balance_update = on_balance_update
        self.on_balance_delta = on_balance_delta
        self._on_reconnect = on_reconnect

        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._rx_task: asyncio.Task[None] | None = None
        self._keepalive_task: asyncio.Task[None] | None = None
        self._request_seq = 0

        self._watchdog = StallWatchdog(
            timeout_s=stall_timeout_s,
            on_timeout=on_stall if on_stall is not None else self._default_stall_handler,
            label="binance-user-data-stall",
        )

    # -- request plumbing ------------------------------------------------------

    def _next_id(self) -> int:
        self._request_seq += 1
        return self._request_seq

    def _hmac(self, query: str) -> str:
        return hmac.new(
            self._secret.encode(), query.encode(), hashlib.sha256
        ).hexdigest()

    async def _session_logon(self) -> None:
        """Sign-and-logon over the ws-api JSON-RPC channel."""
        stamp = int(time.time() * 1000)
        query = f"apiKey={self._key}&timestamp={stamp}"
        frame = {
            "id": self._next_id(),
            "method": "session.logon",
            "params": {
                "apiKey": self._key,
                "signature": self._hmac(query),
                "timestamp": stamp,
            },
        }
        assert self._ws is not None
        await self._ws.send(json.dumps(frame))

    def _rest_base(self) -> str:
        return (
            "https://testnet.binance.vision"
            if self._testnet
            else "https://api.binance.com"
        )

    async def _signed_rest(
        self, method: str, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        import httpx  # noqa: PLC0415

        merged = dict(params or {})
        merged["timestamp"] = int(time.time() * 1000)
        query = "&".join(f"{k}={merged[k]}" for k in sorted(merged))
        merged["signature"] = self._hmac(query)
        async with httpx.AsyncClient() as http:
            resp = await http.request(
                method,
                f"{self._rest_base()}{path}",
                params=merged,
                headers={"X-MBX-APIKEY": self._key},
            )
            resp.raise_for_status()
            result: dict[str, Any] = resp.json()
            return result

    # -- classic listen-key lifecycle (alternative to session.logon) ----------

    async def _fetch_listen_key(self) -> str:
        data = await self._signed_rest("POST", "/api/v3/userDataStream")
        return str(data["listenKey"])

    async def _refresh_listen_key(self) -> None:
        await self._signed_rest("PUT", "/api/v3/userDataStream")

    async def _close_listen_key(self) -> None:
        await self._signed_rest("DELETE", "/api/v3/userDataStream")

    def _start_listenkey_keepalive(self) -> None:
        if self._keepalive_task is not None and not self._keepalive_task.done():
            return

        async def _beat() -> None:
            while True:
                await asyncio.sleep(LISTEN_KEY_REFRESH_S)
                try:
                    await self._refresh_listen_key()
                except Exception:
                    logger.warning("listen-key refresh failed", exc_info=True)

        self._keepalive_task = asyncio.create_task(_beat())

    # -- lifecycle ---------------------------------------------------------------

    async def connect(self) -> None:
        proxy = self.config.proxy_url or None
        logger.info("dialling binance user-data socket %s", self.config.url)
        self._ws = await websockets.asyncio.client.connect(
            self.config.url, proxy=proxy, ping_interval=25, ping_timeout=50
        )
        self._rx_task = asyncio.create_task(self._receive_loop())
        await self._dispatch_connect()
        self._watchdog.arm()

    async def subscribe(self, streams: list[str]) -> None:
        """WS API path: the streams argument carries no information —
        logging on to the session and subscribing to the user-data feed
        is the whole contract."""
        await self._session_logon()
        assert self._ws is not None
        await self._ws.send(
            json.dumps(
                {
                    "id": self._next_id(),
                    "method": "userDataStream.subscribe",
                    "params": {"streams": [_USER_DATA_STREAM]},
                }
            )
        )
        self._subscriptions.add(_USER_DATA_STREAM)

    async def unsubscribe(self, streams: list[str]) -> None:
        for entry in streams:
            self._subscriptions.discard(entry)
        if self._ws is None:
            return
        await self._ws.send(
            json.dumps(
                {
                    "id": self._next_id(),
                    "method": "userDataStream.unsubscribe",
                    "params": {"streams": [_USER_DATA_STREAM]},
                }
            )
        )

    async def close(self) -> None:
        self._watchdog.stop()
        tasks = [t for t in (self._keepalive_task, self._rx_task) if t is not None]
        for task in tasks:
            task.cancel()
        for task in tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._keepalive_task = None
        self._rx_task = None
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        await self._dispatch_disconnect()

    # -- receive / routing ----------------------------------------------------------

    async def _receive_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                try:
                    payload = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(payload, dict):
                    try:
                        await self._route_message(payload)
                    except Exception as exc:
                        logger.debug("user-data frame dropped: %s", exc)
        except websockets.ConnectionClosed as exc:
            logger.info("user-data socket closed (%s)", exc)
            await self._dispatch_disconnect(code=getattr(exc, "code", 1006))
            await self._notify_reconnect()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("user-data reader failed: %s", exc)
            await self._dispatch_error(exc)

    async def _route_message(self, data: dict[str, Any]) -> None:
        self._watchdog.touch()

        kind = data.get("e", "")
        if kind in ("executionReport", "ORDER_TRADE_UPDATE"):
            update = (
                self._decoder.normalize_execution_report(data)
                if kind == "executionReport"
                else self._decoder.normalize_order_trade_update(data)
            )
            await BaseWSClient._invoke(self.on_order_update, update)
        elif kind == "outboundAccountPosition":
            balances = self._decoder.normalize_account_position(data)
            await BaseWSClient._invoke(self.on_balance_update, balances)
        elif kind == "balanceUpdate":
            delta = self._decoder.normalize_balance_update(data)
            await BaseWSClient._invoke(self.on_balance_delta, delta)
        elif kind == "eventStreamTerminated":
            logger.info("venue terminated the user stream — reconnecting")
            sock = self._ws
            if sock is not None:
                try:
                    await sock.close()
                except Exception:
                    pass
            await self._notify_reconnect()
        elif "id" in data and "result" in data:
            logger.debug("rpc ack id=%s", data.get("id"))

    # -- observability hooks -----------------------------------------------------------

    async def _default_stall_handler(self, meta: StallTimeoutMeta) -> None:
        logger.warning(
            "binance user stream idle %.1fs (timeout %.0fs) — dropping socket",
            meta.idle_s,
            meta.timeout_s,
        )
        sock = self._ws
        if sock is not None:
            try:
                await sock.close()
            except Exception:
                pass
        await self._notify_reconnect()

    async def _notify_reconnect(self) -> None:
        callback = self._on_reconnect
        if callback is None:
            return
        try:
            outcome = callback()
            if inspect.isawaitable(outcome):
                await outcome
        except Exception:
            logger.exception("on_reconnect callback raised (swallowed)")
