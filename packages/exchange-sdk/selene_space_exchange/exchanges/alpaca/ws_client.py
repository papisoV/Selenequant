"""Alpaca WebSocket trade stream for real-time order updates.

Connects to Alpaca's trade stream endpoint to receive order fill
and status change notifications.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable

import httpx

logger = logging.getLogger(__name__)

_PAPER_WS = "wss://paper-api.alpaca.markets/stream"
_LIVE_WS = "wss://api.alpaca.markets/stream"


class AlpacaTradeStream:
    """WebSocket client for Alpaca trade updates.

    Receives real-time order status changes and fill notifications.
    Calls registered callbacks for each event type.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        paper: bool = True,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._ws_url = _PAPER_WS if paper else _LIVE_WS
        self._running = False
        self._ws: httpx.AsyncClient | None = None
        self._callbacks: dict[str, list[Callable]] = {
            "order_update": [],
            "trade_update": [],
        }

    def on_order_update(self, callback: Callable[[dict[str, Any]], Any]) -> None:
        self._callbacks["order_update"].append(callback)

    def on_trade_update(self, callback: Callable[[dict[str, Any]], Any]) -> None:
        self._callbacks["trade_update"].append(callback)

    async def start(self) -> None:
        self._running = True
        asyncio.create_task(self._listen_loop())
        logger.info("alpaca trade stream started (paper=%s)", self._ws_url == _PAPER_WS)

    async def stop(self) -> None:
        self._running = False
        logger.info("alpaca trade stream stopped")

    async def _listen_loop(self) -> None:
        while self._running:
            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream("GET", self._ws_url) as response:
                        async for line in response.aiter_lines():
                            if not self._running:
                                break
                            await self._handle_message(line)
            except Exception as e:
                if self._running:
                    logger.warning("alpaca ws error: %s, reconnecting in 5s", e)
                    await asyncio.sleep(5)

    async def _handle_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            return

        stream = msg.get("stream", "")
        data = msg.get("data", msg)

        if "trade_update" in stream or msg.get("event") == "trade_update":
            for cb in self._callbacks["trade_update"]:
                try:
                    cb(data)
                except Exception as e:
                    logger.warning("trade_update callback error: %s", e)

        if "order_update" in stream or "order" in str(data).lower():
            for cb in self._callbacks["order_update"]:
                try:
                    cb(data)
                except Exception as e:
                    logger.warning("order_update callback error: %s", e)
