"""Binance public market-stream client (no auth needed).

Dials the venue stream endpoint, manages SUBSCRIBE/UNSUBSCRIBE frames,
unwraps combined-stream envelopes, and fans parsed events out to typed
callbacks. A stall watchdog closes the socket when the feed goes quiet so
the reconnect supervisor can redial.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from json import JSONDecodeError, dumps, loads
from logging import getLogger
from typing import Any

import websockets
import websockets.asyncio.client

from selene_space_exchange.base.stall_watchdog import StallTimeoutMeta, StallWatchdog
from selene_space_exchange.base.ws_client import BaseWSClient
from selene_space_exchange.exchanges.binance.normalizer import (
    BinanceDepthDelta,
    BinanceNormalizer,
)
from selene_space_exchange.normalizers.symbol import SymbolNormalizer
from selene_space_exchange.types import WSClientConfig
from selene_space_types.derivatives import FundingRateEvent, LiquidationEvent
from selene_space_types.market import KlineEvent, TickerEvent, TradeEvent

_log = getLogger(__name__)

DEFAULT_BINANCE_WS_URL = "wss://data-stream.binance.vision/ws"

#: Venue rejects bursts above ~5 SUBSCRIBE frames per second; one symbol can
#: fan out to 4-5 channels, so pace each frame by this delay.
_SUBSCRIBE_PACE_S = 0.25


class BinanceWSClient(BaseWSClient):
    """Typed-callback facade over the raw Binance market stream."""

    def __init__(
        self,
        *,
        url: str = DEFAULT_BINANCE_WS_URL,
        proxy_url: str | None = None,
        symbol_normalizer: SymbolNormalizer | None = None,
        stall_timeout_s: float = 30.0,
        on_ticker: Callable[[TickerEvent], Any] | None = None,
        on_trade: Callable[[TradeEvent], Any] | None = None,
        on_kline: Callable[[KlineEvent], Any] | None = None,
        on_depth_update: Callable[[BinanceDepthDelta], Any] | None = None,
        on_liquidation: Callable[[LiquidationEvent], Any] | None = None,
        on_funding_rate: Callable[[FundingRateEvent], Any] | None = None,
        on_stall: Callable[[StallTimeoutMeta], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            WSClientConfig(url=url, exchange="binance", proxy_url=proxy_url),
            **kwargs,
        )
        self._sym = symbol_normalizer if symbol_normalizer is not None else SymbolNormalizer()
        self._parser = BinanceNormalizer(self._sym)
        self._socket: websockets.asyncio.client.ClientConnection | None = None
        self._reader_task: asyncio.Task[None] | None = None
        self._frame_seq = 0

        self.on_ticker = on_ticker
        self.on_trade = on_trade
        self.on_kline = on_kline
        self.on_depth_update = on_depth_update
        self.on_liquidation = on_liquidation
        self.on_funding_rate = on_funding_rate

        self._watchdog = StallWatchdog(
            timeout_s=stall_timeout_s,
            on_timeout=on_stall if on_stall is not None else self._on_feed_stall,
            label="binance-ws-stall",
        )

    # -- lifecycle ---------------------------------------------------------

    async def connect(self) -> None:
        proxy = self.config.proxy_url or None
        _log.info("dialling %s (proxy=%s)", self.config.url, proxy or "direct")
        self._socket = await websockets.asyncio.client.connect(
            self.config.url,
            proxy=proxy,
            ping_interval=30,
            ping_timeout=60,
        )
        await self._dispatch_connect()
        await self._watchdog.start()
        if self.subscriptions:
            self._watchdog.arm()
        self._reader_task = asyncio.create_task(self._reader(), name="binance-ws-recv")

    async def subscribe(self, streams: list[str]) -> None:
        if not streams:
            return
        self._subscriptions.update(streams)
        if self._socket is None:
            _log.info("buffering %d streams until connect", len(streams))
            return
        await self._socket.send(
            dumps({"method": "SUBSCRIBE", "params": streams, "id": self._bump_id()})
        )
        await asyncio.sleep(_SUBSCRIBE_PACE_S)
        self._watchdog.arm()

    async def unsubscribe(self, streams: list[str]) -> None:
        if not streams or self._socket is None:
            return
        self._subscriptions -= set(streams)
        await self._socket.send(
            dumps({"method": "UNSUBSCRIBE", "params": streams, "id": self._bump_id()})
        )

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
        await self._dispatch_disconnect(code=1000, reason="client close")

    # -- channel helpers ---------------------------------------------------

    async def subscribe_ticker(self, symbols: list[str]) -> None:
        await self.subscribe([self.stream_name(s, "ticker") for s in symbols])

    async def subscribe_kline(self, symbols: list[str], interval: str) -> None:
        await self.subscribe(
            [self.stream_name(s, "kline", interval=interval) for s in symbols]
        )

    async def subscribe_trade(self, symbols: list[str]) -> None:
        await self.subscribe([self.stream_name(s, "trade") for s in symbols])

    async def subscribe_agg_trade(self, symbols: list[str]) -> None:
        await self.subscribe([self.stream_name(s, "aggTrade") for s in symbols])

    async def subscribe_depth(self, symbols: list[str]) -> None:
        await self.subscribe([self.stream_name(s, "depth@100ms") for s in symbols])

    async def subscribe_force_order(self, symbols: list[str] | None = None) -> None:
        if symbols is None:
            await self.subscribe(["!forceOrder@arr"])
        else:
            await self.subscribe([self.stream_name(s, "forceOrder") for s in symbols])

    async def subscribe_mark_price(self, symbols: list[str] | None = None) -> None:
        if symbols is None:
            await self.subscribe(["!markPrice@arr@1s"])
        else:
            await self.subscribe([self.stream_name(s, "markPrice@1s") for s in symbols])

    @staticmethod
    def stream_name(symbol: str, channel: str, *, interval: str | None = None) -> str:
        tag = symbol.lower()
        if channel == "kline" and interval:
            return f"{tag}@kline_{interval}"
        return f"{tag}@{channel}"

    # -- internals -----------------------------------------------------------

    async def _reader(self) -> None:
        sock = self._socket
        if sock is None:
            return
        try:
            async for raw in sock:
                try:
                    payload = loads(raw)
                except JSONDecodeError:
                    _log.warning("non-JSON frame dropped: %s", str(raw)[:200])
                    continue
                try:
                    await self._route(payload)
                except Exception as exc:
                    _log.debug("unprocessable frame skipped: %s", exc)
        except websockets.ConnectionClosed as exc:
            code = getattr(exc, "code", 1006)
            _log.info("stream closed (%s)", exc)
            await self._dispatch_disconnect(code=code, reason=str(exc))
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("reader loop failed: %s", exc)
            await self._dispatch_error(exc)

    async def _route(self, payload: dict[str, Any] | list[Any]) -> None:
        self._watchdog.touch()

        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, dict):
                    await self._route_one(item)
            return

        if "stream" in payload and "data" in payload:
            inner = payload["data"]
            if isinstance(inner, list):
                for item in inner:
                    if isinstance(item, dict):
                        await self._route_one(item)
                return
            await self._route_one(inner)
            return

        await self._route_one(payload)

    async def _route_one(self, data: dict[str, Any]) -> None:
        # Venue ACK frames carry result+id and are not market events.
        if "result" in data and "id" in data:
            return

        await self._dispatch_message(data)

        event = self._parser.normalize(data)
        if event is None:
            return

        sink: Callable[..., Any] | None
        match event:
            case TickerEvent():
                sink = self.on_ticker
            case TradeEvent():
                sink = self.on_trade
            case KlineEvent():
                sink = self.on_kline
            case BinanceDepthDelta():
                sink = self.on_depth_update
            case LiquidationEvent():
                sink = self.on_liquidation
            case FundingRateEvent():
                sink = self.on_funding_rate
            case _:
                sink = None
        if sink is not None:
            await self._invoke(sink, event)

    def _bump_id(self) -> int:
        self._frame_seq += 1
        return self._frame_seq

    async def _on_feed_stall(self, meta: StallTimeoutMeta) -> None:
        _log.warning("feed idle %.1fs — closing socket to force redial", meta.idle_s)
        sock = self._socket
        if sock is not None:
            try:
                await sock.close()
            except Exception:
                pass
