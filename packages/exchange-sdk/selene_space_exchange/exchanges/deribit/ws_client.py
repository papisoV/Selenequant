"""Deribit WebSocket client for public options market data.

Connects to Deribit's JSON-RPC 2.0 over WebSocket API and provides typed
callbacks for ticker and quote events. No API key required for public
endpoints (ticker, instruments, book summary).

Docs: https://docs.deribit.com/#websocket-api
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
from selene_space_exchange.types import WSClientConfig
from selene_space_types.derivatives import OptionsTicker

logger = logging.getLogger(__name__)

DEFAULT_DERIBIT_WS_URL = "wss://www.deribit.com/ws/api/v2"
DEFAULT_DERIBIT_TEST_WS_URL = "wss://test.deribit.com/ws/api/v2"

_HEARTBEAT_INTERVAL_S = 30
_HEARTBEAT_METHOD = "public/test"
_SUBSCRIBE_METHOD = "public/subscribe"
_UNSUBSCRIBE_METHOD = "public/unsubscribe"


class DeribitWSClient(BaseWSClient):
    """Deribit WebSocket client for public options market data.

    Contract:
        - Connects to ``wss://www.deribit.com/ws/api/v2``.
        - Subscribes via JSON-RPC 2.0: ``{"jsonrpc":"2.0", "method":"public/subscribe", ...}``.
        - Supports ticker and quote channels for options instruments.
        - Sends heartbeat (``public/test``) every 30 seconds.
        - Integrates :class:`StallWatchdog` for data stall detection.
        - No API key required for public channels.
    """

    def __init__(
        self,
        *,
        url: str = DEFAULT_DERIBIT_WS_URL,
        proxy_url: str | None = None,
        stall_timeout_s: float = 45.0,
        on_ticker: Callable[[OptionsTicker], Any] | None = None,
        on_stall: Callable[[StallTimeoutMeta], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        config = WSClientConfig(url=url, exchange="deribit", proxy_url=proxy_url)
        super().__init__(config, **kwargs)

        self._ws: websockets.asyncio.client.ClientConnection | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._request_id: int = 0

        self._stall_watchdog = StallWatchdog(
            timeout_s=stall_timeout_s,
            on_timeout=on_stall or self._default_stall_handler,
            label="deribit-ws-stall",
        )

        self.on_ticker = on_ticker

    # ------------------------------------------------------------------
    # BaseWSClient implementation
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Open WebSocket connection to Deribit."""
        proxy = self._config.proxy_url or None
        logger.info(
            "Connecting to Deribit WS: %s (proxy=%s)", self._config.url, proxy or "none"
        )
        self._ws = await websockets.asyncio.client.connect(
            self._config.url,
            proxy=proxy,
            ping_interval=None,  # Deribit uses application-level heartbeat
            ping_timeout=60,
        )
        await self._dispatch_connect()
        await self._stall_watchdog.start()
        if self._subscriptions:
            self._stall_watchdog.arm()
        self._receive_task = asyncio.create_task(
            self._receive_loop(), name="deribit-ws-recv"
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name="deribit-ws-heartbeat"
        )

    async def subscribe(self, streams: list[str]) -> None:
        """Subscribe to Deribit channels via JSON-RPC 2.0.

        Args:
            streams: List of channel names in Deribit format, e.g.
                ``["ticker.BTC-28MAR25-80000-C.100ms", "quote.BTC-28MAR25-80000-C"]``.
        """
        if not streams:
            return
        self._subscriptions.update(streams)
        if self._ws is None:
            logger.info(
                "Queued %d Deribit channels (WS not connected yet)", len(streams)
            )
            return
        msg = {
            "jsonrpc": "2.0",
            "method": _SUBSCRIBE_METHOD,
            "params": {"channels": streams},
            "id": self._next_id(),
        }
        await self._ws.send(json.dumps(msg))
        self._stall_watchdog.arm()
        logger.info("Subscribed to %d Deribit channels", len(streams))

    async def unsubscribe(self, streams: list[str]) -> None:
        """Unsubscribe from Deribit channels."""
        if not streams or self._ws is None:
            return
        self._subscriptions -= set(streams)
        msg = {
            "jsonrpc": "2.0",
            "method": _UNSUBSCRIBE_METHOD,
            "params": {"channels": streams},
            "id": self._next_id(),
        }
        await self._ws.send(json.dumps(msg))
        logger.info("Unsubscribed from %d Deribit channels", len(streams))

    async def close(self) -> None:
        """Close the WebSocket connection and cancel background tasks."""
        self._stall_watchdog.stop()

        if self._heartbeat_task is not None and not self._heartbeat_task.done():
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        if self._receive_task is not None and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        await self._dispatch_disconnect(code=1000, reason="client close")

    # ------------------------------------------------------------------
    # Convenience subscription methods
    # ------------------------------------------------------------------

    async def subscribe_ticker(
        self, instrument_names: list[str], *, frequency: str = "100ms"
    ) -> None:
        """Subscribe to ticker channels for the given options instruments.

        Args:
            instrument_names: Deribit instrument names, e.g.
                ``["BTC-28MAR25-80000-C", "ETH-28MAR25-3000-P"]``.
            frequency: Update frequency, e.g. ``"100ms"``, ``"raw"``.
        """
        channels = [f"ticker.{name}.{frequency}" for name in instrument_names]
        await self.subscribe(channels)

    async def subscribe_quote(self, instrument_names: list[str]) -> None:
        """Subscribe to quote channels for the given options instruments.

        Args:
            instrument_names: Deribit instrument names.
        """
        channels = [f"quote.{name}" for name in instrument_names]
        await self.subscribe(channels)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _receive_loop(self) -> None:
        """Read messages from WebSocket and route them."""
        if self._ws is None:
            return

        # A graceful close (code 1000) ends ``async for`` normally rather than
        # raising, so the except branches alone would leave the client marked
        # connected and ``wait_disconnected()`` blocked forever — see the
        # matching note in the binance client.
        code = 1006
        cancelled = False
        try:
            async for raw in self._ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    logger.warning("Invalid JSON from Deribit: %s", raw[:200])
                    continue

                try:
                    await self._route_message(data)
                except Exception as route_exc:
                    logger.debug(
                        "Skipping unprocessable Deribit message: %s",
                        route_exc,
                    )
                    continue
        except websockets.ConnectionClosed as exc:
            code = exc.code if hasattr(exc, "code") else 1006
            logger.info("Deribit WS connection closed: %s", exc)
        except asyncio.CancelledError:
            cancelled = True
            raise
        except Exception as exc:
            logger.error("Error in Deribit receive loop: %s", exc)
            await self._dispatch_error(exc)
        finally:
            if not cancelled:
                await self._dispatch_disconnect(code=code, reason="receive loop ended")

    async def _route_message(self, data: dict[str, Any]) -> None:
        """Route a parsed JSON-RPC message to the appropriate handler."""
        self._stall_watchdog.touch()

        # JSON-RPC response (subscription ack, heartbeat response, etc.)
        if "id" in data and ("result" in data or "error" in data):
            if "error" in data:
                error = data["error"]
                logger.warning(
                    "Deribit JSON-RPC error (code=%s): %s",
                    error.get("code"),
                    error.get("message"),
                )
            return

        # Subscription notification: {"method": "subscription", "params": {...}}
        method = data.get("method")
        if method == "subscription":
            params = data.get("params", {})
            channel = params.get("channel", "")
            payload = params.get("data", {})

            await self._dispatch_message(data)

            if channel.startswith("ticker."):
                ticker = self._parse_ticker(payload, channel)
                if ticker is not None:
                    await self._invoke(self.on_ticker, ticker)
            elif channel.startswith("quote."):
                # Quote updates are dispatched as raw messages for now.
                pass
            return

        # Ignore other methods (e.g. heartbeat responses already handled above).
        logger.debug("Unhandled Deribit method: %s", method)

    @staticmethod
    def _parse_ticker(data: dict[str, Any], channel: str) -> OptionsTicker | None:
        """Parse a Deribit ticker subscription payload into an OptionsTicker.

        Deribit ticker fields (selected):
            instrument_name, mark_price, underlying_price, open_interest,
            stats.volume (24h), bid_price, ask_price, last_price,
            greeks (delta, gamma, theta, vega, rho), timestamp

        Mark price in Deribit is in base currency (BTC/ETH). We compute
        mark_price_usd = mark_price * underlying_price.
        """
        try:
            instrument_name = data.get("instrument_name", "")
            mark_price = float(data.get("mark_price", 0.0))
            underlying_price = float(data.get("underlying_price", 0.0))
            mark_price_usd = (
                mark_price * underlying_price if underlying_price > 0 else 0.0
            )

            greeks = data.get("greeks", {}) or {}
            stats = data.get("stats", {}) or {}

            return OptionsTicker(
                instrument_name=instrument_name,
                mark_price=mark_price,
                mark_price_usd=mark_price_usd,
                underlying_price=underlying_price,
                implied_volatility=float(data.get("mark_iv", 0.0)) / 100.0
                if data.get("mark_iv") is not None
                else 0.0,
                delta=float(greeks.get("delta", 0.0)),
                gamma=float(greeks.get("gamma", 0.0)),
                theta=float(greeks.get("theta", 0.0)),
                vega=float(greeks.get("vega", 0.0)),
                rho=float(greeks.get("rho", 0.0)),
                open_interest=float(data.get("open_interest", 0.0)),
                volume_24h=float(stats.get("volume", 0.0)),
                bid_price=float(data.get("best_bid_price", 0.0)),
                ask_price=float(data.get("best_ask_price", 0.0)),
                last_price=float(data.get("last_price", 0.0)),
                timestamp=int(data.get("timestamp", 0)),
            )
        except Exception as exc:
            logger.warning(
                "Failed to parse Deribit ticker from channel %s: %s", channel, exc
            )
            return None

    async def _heartbeat_loop(self) -> None:
        """Send periodic heartbeat (public/test) to Deribit to keep the connection alive.

        Deribit requires a heartbeat or other request at least every 30 seconds
        to avoid connection timeout.
        """
        try:
            while self._ws is not None:
                await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
                if self._ws is None:
                    break
                try:
                    msg = {
                        "jsonrpc": "2.0",
                        "method": _HEARTBEAT_METHOD,
                        "id": self._next_id(),
                    }
                    await self._ws.send(json.dumps(msg))
                except Exception as exc:
                    logger.debug("Deribit heartbeat send failed: %s", exc)
                    break
        except asyncio.CancelledError:
            raise

    def _next_id(self) -> int:
        """Return an auto-incrementing request ID."""
        self._request_id += 1
        return self._request_id

    async def _default_stall_handler(self, meta: StallTimeoutMeta) -> None:
        """Default stall handler: log and force reconnect by closing WS."""
        logger.warning(
            "Deribit WS stall detected (idle %.1fs). Closing for reconnect.",
            meta.idle_s,
        )
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
