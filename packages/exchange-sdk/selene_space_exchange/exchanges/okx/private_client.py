"""OKX private WebSocket: order / balance pushes over the v5 private socket.

Login is a one-shot ``op=login`` frame carrying an HMAC-SHA256 signature of
``GET/ws/v5/private<ts>`` base64-encoded. After the ACK the client subscribes
to the ``orders`` and ``account`` channels and fans typed events out to
callbacks. A stall watchdog closes idle sockets so the supervisor redials.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Callable
from typing import Any

import websockets
import websockets.asyncio.client

from selene_space_exchange.base.stall_watchdog import StallTimeoutMeta, StallWatchdog
from selene_space_exchange.base.ws_client import BaseWSClient
from selene_space_exchange.types import WSClientConfig
from selene_space_types.trading import BalanceUpdate, ExchangeOrderUpdate, OrderSide

_log = logging.getLogger(__name__)

DEFAULT_OKX_PRIVATE_URL = "wss://ws.okx.com:8443/ws/v5/private"

EXCHANGE = "okx"

#: OKX order-state strings -> unified status vocabulary.
_STATE_MAP = {
    "live": "NEW",
    "partially_filled": "PARTIALLY_FILLED",
    "filled": "FILLED",
    "canceled": "CANCELED",
}

_SIDE_MAP = {member.value: member for member in OrderSide}


def _dec(value: Any) -> Any:
    """Pass through to pydantic Decimal coercion; None becomes zero."""
    return 0 if value in (None, "") else value


class OKXPrivateNormalizer:
    @staticmethod
    def normalize_order(item: dict[str, Any]) -> ExchangeOrderUpdate:
        fee = item.get("fee")
        # Venue reports fees as a debit (negative); we store magnitude.
        charge = abs(float(fee)) if fee else 0.0
        fill_px = item.get("avgPx") or item.get("fillPx") or "0"
        return ExchangeOrderUpdate(
            exchange=EXCHANGE,
            exchange_order_id=item.get("ordId", ""),
            client_order_id=item.get("clOrdId"),
            symbol=str(item.get("instId", "")).replace("-", "/"),
            side=_SIDE_MAP.get(item.get("side", ""), OrderSide.BUY),
            order_type=item.get("ordType", ""),
            status=_STATE_MAP.get(item.get("state", ""), item.get("state", "")),
            quantity=_dec(item.get("sz")),
            filled_quantity=_dec(item.get("accFillSz")),
            avg_fill_price=_dec(fill_px),
            last_fill_price=_dec(item.get("fillPx")),
            last_fill_quantity=_dec(item.get("fillSz")),
            commission=charge,
            commission_asset=item.get("feeCcy"),
            timestamp=int(item.get("uTime", 0)),
            raw=item,
        )

    @staticmethod
    def normalize_account(item: dict[str, Any], ts: int) -> list[BalanceUpdate]:
        rows = item.get("details") or []
        return [
            BalanceUpdate(
                exchange=EXCHANGE,
                asset=row.get("ccy", ""),
                free=_dec(row.get("availBal")),
                locked=_dec(row.get("frozenBal")),
                timestamp=ts,
            )
            for row in rows
        ]


class OKXPrivateWSClient(BaseWSClient):
    def __init__(
        self,
        *,
        api_key: str,
        api_secret: str,
        passphrase: str,
        url: str = DEFAULT_OKX_PRIVATE_URL,
        stall_timeout_s: float = 60.0,
        on_order_update: Callable[[ExchangeOrderUpdate], Any] | None = None,
        on_balance_update: Callable[[list[BalanceUpdate]], Any] | None = None,
        on_stall: Callable[[StallTimeoutMeta], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            WSClientConfig(url=url, exchange=EXCHANGE),
            **kwargs,
        )
        self._api_key = api_key
        self._api_secret = api_secret
        self._passphrase = passphrase
        self._socket: websockets.asyncio.client.ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None

        self.on_order_update = on_order_update
        self.on_balance_update = on_balance_update

        self._watchdog = StallWatchdog(
            timeout_s=stall_timeout_s,
            on_timeout=on_stall if on_stall is not None else self._default_stall_handler,
            label="okx-private-ws-stall",
        )

    async def connect(self) -> None:
        _log.info("dialling private stream %s", self.config.url)
        self._socket = await websockets.asyncio.client.connect(self.config.url)
        await self._login()
        await self._dispatch_connect()
        self._watchdog.start()
        self._reader_task = asyncio.create_task(
            self._receive_loop(), name="okx-private-recv"
        )

    async def subscribe(self, streams: list[str]) -> None:
        if not streams or self._socket is None:
            return
        args = [dict(pair.split(":", 1) for pair in entry.split(",")) for entry in streams]
        for chunk_start in range(0, len(args), 10):
            chunk = args[chunk_start : chunk_start + 10]
            await self._send({"op": "subscribe", "args": chunk})
            self._watchdog.arm()

    async def unsubscribe(self, streams: list[str]) -> None:
        if not streams or self._socket is None:
            return
        args = [dict(pair.split(":", 1) for pair in entry.split(",")) for entry in streams]
        await self._send({"op": "unsubscribe", "args": args})
        self._subscriptions -= set(streams)

    async def close(self) -> None:
        self._watchdog.stop()
        reader = self._reader_task
        if reader is not None and not reader.done():
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass
        self._reader_task = None
        sock = self._socket
        if sock is not None:
            try:
                await sock.close()
            except Exception:
                pass
            self._socket = None
        await self._dispatch_disconnect()

    # -- internals -------------------------------------------------------------

    async def _send(self, frame: dict[str, Any]) -> None:
        if self._socket is None:
            return
        await self._socket.send(json.dumps(frame))

    def _login_frame(self) -> dict[str, Any]:
        ts = str(int(time.time()))
        prehash = f"{ts}GET/users/self/verify"
        sig = base64.b64encode(
            hmac.new(
                self._api_secret.encode("utf-8"),
                prehash.encode("utf-8"),
                hashlib.sha256,
            ).digest()
        ).decode("utf-8")
        return {
            "op": "login",
            "args": [
                {
                    "apiKey": self._api_key,
                    "passphrase": self._passphrase,
                    "timestamp": ts,
                    "sign": sig,
                }
            ],
        }

    async def _login(self) -> None:
        await self._send(self._login_frame())
        if self._socket is None:
            return
        raw = await asyncio.wait_for(self._socket.recv(), timeout=10.0)
        ack = json.loads(raw)
        if ack.get("event") == "login" and ack.get("code") not in (None, "0"):
            raise ConnectionError(f"OKX private login failed: {ack}")

    async def _receive_loop(self) -> None:
        sock = self._socket
        if sock is None:
            return
        try:
            async for raw in sock:
                try:
                    payload = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                try:
                    await self._route_message(payload)
                except Exception as exc:
                    _log.debug("private frame dropped: %s", exc)
        except websockets.ConnectionClosed as exc:
            _log.info("private stream closed (%s)", exc)
            await self._dispatch_disconnect(code=getattr(exc, "code", 1006))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("private reader failed: %s", exc)
            await self._dispatch_error(exc)

    async def _route_message(self, data: dict[str, Any]) -> None:
        self._watchdog.touch()

        # Event frames are login/subscribe/error ACKs, not market data.
        if "event" in data and "arg" not in data:
            if data.get("event") == "error":
                _log.warning("OKX private error frame: %s", data)
            return

        arg = data.get("arg") or {}
        channel = arg.get("channel", "")
        rows = data.get("data")
        if not rows:
            return

        if channel.startswith("orders"):
            for item in rows:
                await BaseWSClient._invoke(
                    self.on_order_update, OKXPrivateNormalizer.normalize_order(item)
                )
        elif channel.startswith("account"):
            stamp = int(data.get("ts", 0)) or int(time.time() * 1000)
            updates = OKXPrivateNormalizer.normalize_account(rows[0], ts=stamp)
            if updates:
                await BaseWSClient._invoke(self.on_balance_update, updates)

    async def _default_stall_handler(self, meta: StallTimeoutMeta) -> None:
        _log.warning("private feed idle %.1fs — closing socket", meta.idle_s)
        sock = self._socket
        if sock is not None:
            try:
                await sock.close()
            except Exception:
                pass
