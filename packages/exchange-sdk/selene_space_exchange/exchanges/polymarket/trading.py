"""Polymarket CLOB order management (L2 API-key auth).

Auth headers sign ``timestamp + METHOD + path + body`` with HMAC-SHA256.
Order prices are probabilities bounded to the venue tick band; the client
rejects out-of-band input locally before anything reaches the wire.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from enum import Enum
from typing import Any

import httpx

from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter
from selene_space_exchange.exceptions import ExchangeAPIError, InvalidOrderError

_log = logging.getLogger(__name__)

POLYMARKET_CLOB_URL = "https://clob.polymarket.com"

_TICK_FLOOR, _TICK_CEIL = 0.01, 0.99


class PolymarketOrderType(str, Enum):
    GTC = "GTC"
    FOK = "FOK"
    GTD = "GTD"


class PolymarketSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class PolymarketCredentials:
    """L2 API credentials plus the owning wallet (optional for reads)."""

    def __init__(self, **entries: str) -> None:
        for name in ("api_key", "api_secret", "api_passphrase"):
            setattr(self, name, entries.pop(name))
        self.wallet_address: str | None = entries.get("wallet_address")


def _enum_value(value: Any, enum_type: type[Enum]) -> str:
    return value.value if isinstance(value, enum_type) else str(value).upper()


class PolymarketTradingClient:
    def __init__(
        self,
        credentials: PolymarketCredentials,
        *,
        base_url: str = POLYMARKET_CLOB_URL,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._creds = credentials
        self._base_url = base_url.rstrip("/")
        self._rate_limiter = rate_limiter or SlidingWindowRateLimiter(
            calls_per_window=100, window_ms=10_000
        )
        self._http = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._http.aclose()

    # -- signing ---------------------------------------------------------------

    def _build_auth_headers(self, method: str, path: str, body: str = "") -> dict[str, str]:
        stamp = str(int(time.time()))
        digest = hmac.new(
            self._creds.api_secret.encode("utf-8"),
            f"{stamp}{method.upper()}{path}{body}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        signed = {
            "POLY-SIGNATURE": digest,
            "POLY-TIMESTAMP": stamp,
            "POLY-API-KEY": self._creds.api_key,
            "POLY-PASSPHRASE": self._creds.api_passphrase,
        }
        if self._creds.wallet_address:
            signed["POLY-ADDRESS"] = self._creds.wallet_address
        return signed

    # -- transport -----------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self._rate_limiter.acquire()
        payload = json.dumps(body) if body else ""
        headers = self._build_auth_headers(method, path, payload)
        if payload:
            headers["Content-Type"] = "application/json"

        resp = await self._http.request(
            method,
            f"{self._base_url}{path}",
            params=params,
            content=payload or None,
            headers=headers,
        )
        if resp.status_code >= 400:
            self._handle_error(resp)
        decoded: dict[str, Any] = resp.json()
        return decoded

    def _handle_error(self, resp: httpx.Response) -> None:
        try:
            detail = resp.json().get("error") or resp.text
        except Exception:
            detail = resp.text
        raise ExchangeAPIError(
            f"Polymarket rejected {resp.status_code}: {detail}",
            exchange="polymarket",
            status_code=resp.status_code,
        )

    @classmethod
    def _check_order_inputs(cls, price: float, size: float) -> None:
        if not (_TICK_FLOOR <= price <= _TICK_CEIL):
            raise InvalidOrderError(
                f"price {price} must sit between {_TICK_FLOOR} and {_TICK_CEIL}",
                exchange="polymarket",
            )
        if size <= 0:
            raise InvalidOrderError("size must be positive", exchange="polymarket")

    # -- order lifecycle ----------------------------------------------------------------

    async def place_order(
        self,
        *,
        token_id: str,
        side: str | PolymarketSide,
        price: float,
        size: float,
        order_type: str | PolymarketOrderType = PolymarketOrderType.GTC,
        expiration: int | None = None,
    ) -> dict[str, Any]:
        self._check_order_inputs(price, size)
        body: dict[str, Any] = {
            "tokenID": token_id,
            "price": price,
            "size": size,
            "side": _enum_value(side, PolymarketSide),
            "type": _enum_value(order_type, PolymarketOrderType),
        }
        if expiration is not None:
            body["expiration"] = expiration
        return await self._request("POST", "/order", body=body)

    async def cancel_order(self, *, order_id: str) -> dict[str, Any]:
        return await self._request("DELETE", "/order", body={"orderID": order_id})

    async def cancel_orders(self, *, order_ids: list[str]) -> dict[str, Any]:
        return await self._request("DELETE", "/orders", body={"orderIDs": order_ids})

    async def cancel_all_orders(self) -> dict[str, Any]:
        return await self._request("DELETE", "/cancel-all")

    # -- queries ---------------------------------------------------------------------------

    async def get_active_orders(
        self, *, market: str | None = None, asset_id: str | None = None
    ) -> dict[str, Any]:
        result: dict[str, Any] = await self._request(
            "GET", "/data/orders", params=self._market_filter(market, asset_id)
        )
        return result

    async def get_trades(
        self, *, market: str | None = None, asset_id: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        filters = self._market_filter(market, asset_id)
        filters["limit"] = limit
        result: dict[str, Any] = await self._request("GET", "/data/trades", params=filters)
        return result

    @staticmethod
    def _market_filter(market: str | None, asset_id: str | None) -> dict[str, Any]:
        picked: dict[str, Any] = {}
        if market:
            picked["market"] = market
        if asset_id:
            picked["asset_id"] = asset_id
        return picked

    # -- balances -----------------------------------------------------------------------------

    async def get_balance(self) -> dict[str, Any]:
        result: dict[str, Any] = await self._request("GET", "/balance")
        return result

    async def get_token_balance(self, token_id: str) -> dict[str, Any]:
        result: dict[str, Any] = await self._request(
            "GET", "/balance", params={"token_id": token_id}
        )
        return result

    async def test_connectivity(self) -> bool:
        try:
            resp = await self._http.get(f"{self._base_url}/time")
            return resp.status_code == 200
        except Exception:
            return False
