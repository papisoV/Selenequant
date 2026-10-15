"""Alpaca REST client — thin async wrapper around the alpaca-py SDK.

Handles HTTP communication with Alpaca's trading API. Supports both
paper (paper-api.alpaca.markets) and live (api.alpaca.markets) endpoints.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_PAPER_BASE = "https://paper-api.alpaca.markets"
_LIVE_BASE = "https://api.alpaca.markets"
_DATA_BASE = "https://data.alpaca.markets"


class AlpacaRESTClient:
    """Async REST client for Alpaca trading API.

    Uses httpx for HTTP requests. The alpaca-py SDK is optional;
    this client provides a minimal interface sufficient for the
    TradingClient adapter.
    """

    def __init__(
        self,
        *,
        api_key: str,
        secret_key: str,
        paper: bool = True,
        timeout: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._secret_key = secret_key
        self._paper = paper
        self._base_url = _PAPER_BASE if paper else _LIVE_BASE
        self._data_url = _DATA_BASE
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers={
                    "APCA-API-KEY-ID": self._api_key,
                    "APCA-API-SECRET-KEY": self._secret_key,
                    "Content-Type": "application/json",
                },
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = self._get_client()
        resp = await client.request(method, path, params=params, json=body)
        resp.raise_for_status()
        if resp.status_code == 204:
            return {}
        return resp.json()

    # ── Account ───────────────────────────���──────────────────────────────────

    async def get_account(self) -> dict[str, Any]:
        return await self._request("GET", "/v2/account")

    async def test_connectivity(self) -> bool:
        try:
            await self.get_account()
            return True
        except Exception:
            return False

    # ── Orders ───────────────────────────────────────────────────────────────

    async def place_order(self, body: dict[str, Any]) -> dict[str, Any]:
        return await self._request("POST", "/v2/orders", body=body)

    async def cancel_order(self, order_id: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/v2/orders/{order_id}")

    async def get_order(self, order_id: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/orders/{order_id}")

    async def get_open_orders(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"status": "open"}
        if symbol:
            params["symbols"] = symbol
        result = await self._request("GET", "/v2/orders", params=params)
        return result if isinstance(result, list) else []

    # ── Positions ────────────────────────────────────────────────────────────

    async def get_positions(self) -> list[dict[str, Any]]:
        result = await self._request("GET", "/v2/positions")
        return result if isinstance(result, list) else []

    async def get_position(self, symbol: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/positions/{symbol}")

    # ── Assets ───────────────────────────────────────────────────────────────

    async def get_asset(self, symbol: str) -> dict[str, Any]:
        return await self._request("GET", f"/v2/assets/{symbol}")

    # ── Market Data ──────────────────────────────────────────────────────────

    async def get_latest_quote(self, symbol: str) -> dict[str, Any]:
        client = self._get_client()
        resp = await client.request(
            "GET",
            f"{self._data_url}/v2/stocks/{symbol}/quotes/latest",
            headers={
                "APCA-API-KEY-ID": self._api_key,
                "APCA-API-SECRET-KEY": self._secret_key,
            },
        )
        resp.raise_for_status()
        return resp.json()

    @property
    def is_paper(self) -> bool:
        return self._paper
