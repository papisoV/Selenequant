"""Binance USDT-M Futures REST trading client.

Endpoints implemented (all require HMAC-SHA256 signed requests):
- POST   /fapi/v1/order          — place new order
- DELETE /fapi/v1/order          — cancel order
- GET    /fapi/v1/order          — query order status
- GET    /fapi/v1/openOrders     — list open orders
- GET    /fapi/v1/positionRisk   — get positions
- GET    /fapi/v2/balance        — account balances
- POST   /fapi/v1/leverage       — set leverage
- POST   /fapi/v1/marginType     — set margin mode (cross/isolated)
- POST   /fapi/v1/positionSide/dual — set position mode (hedge/net)

Docs: https://binance-docs.github.io/apidocs/futures/en/
"""

from __future__ import annotations

import logging
import time
from enum import Enum
from typing import Any

import httpx

from selene_space_exchange.base.auth import BinanceAuthenticator, ExchangeCredentials
from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter
from selene_space_exchange.base.rest_client import BaseRESTClient
from selene_space_exchange.exceptions import (
    AuthenticationError,
    ExchangeAPIError,
    InsufficientBalanceError,
    OrderNotFoundError,
    OrderRejectedError,
    RateLimitExceededError,
)

logger = logging.getLogger(__name__)

BINANCE_FUTURES_URL = "https://fapi.binance.com"
BINANCE_FUTURES_TESTNET_URL = "https://testnet.binancefuture.com"


class FuturesOrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP = "STOP"  # stop-market
    STOP_MARKET = "STOP_MARKET"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_MARKET = "TAKE_PROFIT_MARKET"


class FuturesTimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"
    GTX = "GTX"  # Good Till Crossing (post-only)


class BinanceFuturesRESTClient(BaseRESTClient):
    """Authenticated Binance USDT-M Futures REST client."""

    def __init__(
        self,
        credentials: ExchangeCredentials,
        testnet: bool = False,
        rate_limiter: SlidingWindowRateLimiter | None = None,
    ) -> None:
        base_url = BINANCE_FUTURES_TESTNET_URL if testnet else BINANCE_FUTURES_URL
        authenticator = BinanceAuthenticator(credentials)
        limiter = rate_limiter or SlidingWindowRateLimiter(calls_per_window=1200, window_ms=60_000)

        super().__init__(
            base_url=base_url,
            authenticator=authenticator,
            rate_limiter=limiter,
            # Tight connect timeout so a dead SOCKS proxy fails fast (2s)
            # instead of blocking the event loop for the default 15s.
            timeout=httpx.Timeout(5.0, connect=2.0),
        )
        self._testnet = testnet

    @property
    def _exchange_name(self) -> str:
        return "binance"

    # ------------------------------------------------------------------
    # Binance-specific request: params are sent as query string / form
    # ------------------------------------------------------------------

    async def _signed_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send a signed request using Binance's query-param signature scheme.

        Binance signs params (not JSON body), and sends API key via header.
        Same scheme for spot and USDT-M futures.
        """
        await self._rate_limiter.acquire()

        if params is None:
            params = {}
        params["timestamp"] = str(int(time.time() * 1000))
        if self._recv_window is not None:
            params["recvWindow"] = str(self._recv_window)

        auth_headers = self._auth.sign_request(method=method, path=path, params=params)

        # Build the query string in the SAME order used for signing
        # (BinanceAuthenticator.sign_request sorts params.keys() before
        # HMAC, excluding the signature it just added). httpx's
        # `params=dict` re-encodes in insertion order, which can differ
        # from the signed order and trigger -1022 "Signature not valid".
        sig_value = params.pop("signature", None)
        signed_query = "&".join(f"{k}={v}" for k, v in sorted(params.items()))
        if sig_value is not None:
            signed_query = f"{signed_query}&signature={sig_value}" if signed_query else f"signature={sig_value}"

        url = f"{self._base_url}{path}"
        logger.debug("Binance Futures REST %s %s", method, path)

        full_url = f"{url}?{signed_query}" if signed_query else url
        resp = await self._http.request(method=method, url=full_url, headers=auth_headers)

        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            self._rate_limiter.set_retry_after(retry_after)

        if resp.status_code >= 400:
            self._handle_error_response(resp)

        result: dict[str, Any] = resp.json()
        return result

    async def _public_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an unsigned public request (ping, server time, etc.)."""
        await self._rate_limiter.acquire()
        url = f"{self._base_url}{path}"
        resp = await self._http.request(method=method, url=url, params=params or {})
        if resp.status_code >= 400:
            self._handle_error_response(resp)
        result: dict[str, Any] = resp.json()
        return result

    # ------------------------------------------------------------------
    # Error handling
    # ------------------------------------------------------------------

    def _handle_error_response(self, resp: httpx.Response) -> None:
        """Map Binance error codes to typed exceptions (spot + futures share codes)."""
        try:
            data = resp.json()
        except Exception:
            data = {"msg": resp.text}

        code = data.get("code", 0)
        msg = data.get("msg", str(data))

        if resp.status_code == 429:
            raise RateLimitExceededError(f"Binance rate limit: {msg}", exchange="binance", status_code=429)

        if resp.status_code in (401, 403) or code in (-2015, -2014, -1022):
            raise AuthenticationError(
                f"Binance auth error: {msg}",
                exchange="binance",
                status_code=resp.status_code,
            )

        if code == -2010:
            raise InsufficientBalanceError(f"Insufficient balance: {msg}", exchange="binance")

        if code in (-2011, -2013):
            raise OrderNotFoundError(f"Order not found: {msg}", exchange="binance")

        if code in (-1013, -1111, -1116, -1117, -1121):
            raise OrderRejectedError(f"Order rejected: {msg}", exchange="binance", details=data)

        raise ExchangeAPIError(
            f"Binance API error ({code}): {msg}",
            exchange="binance",
            status_code=resp.status_code,
            details=data,
        )

    # -- Order operations --

    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str,
        quantity: str,
        price: str | None = None,
        stop_price: str | None = None,
        time_in_force: str | None = None,
        position_side: str | None = None,
        reduce_only: bool = False,
        new_client_order_id: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "symbol": symbol,
            "side": side.upper(),
            "type": order_type.upper(),
            "quantity": quantity,
        }
        if price is not None:
            params["price"] = price
        if stop_price is not None:
            params["stopPrice"] = stop_price
        if time_in_force is not None:
            params["timeInForce"] = time_in_force
        if position_side is not None:
            params["positionSide"] = position_side
        if reduce_only:
            params["reduceOnly"] = "true"
        if new_client_order_id is not None:
            params["newClientOrderId"] = new_client_order_id

        return await self._signed_request("POST", "/fapi/v1/order", params=params)

    async def cancel_order(self, *, symbol: str, order_id: int) -> dict[str, Any]:
        return await self._signed_request(
            "DELETE", "/fapi/v1/order", params={"symbol": symbol, "orderId": order_id}
        )

    async def get_order(self, *, symbol: str, order_id: int) -> dict[str, Any]:
        return await self._signed_request(
            "GET", "/fapi/v1/order", params={"symbol": symbol, "orderId": order_id}
        )

    async def get_open_orders(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        result = await self._signed_request("GET", "/fapi/v1/openOrders", params=params)
        return result if isinstance(result, list) else []

    async def get_all_orders(self, *, symbol: str, limit: int = 500) -> list[dict[str, Any]]:
        result = await self._signed_request(
            "GET", "/fapi/v1/allOrders", params={"symbol": symbol, "limit": limit}
        )
        return result if isinstance(result, list) else []

    # -- Account operations --

    async def get_balances(self) -> list[dict[str, Any]]:
        result = await self._signed_request("GET", "/fapi/v2/balance")
        return result if isinstance(result, list) else []

    async def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {}
        if symbol:
            params["symbol"] = symbol
        # Binance deprecated /fapi/v1/positionRisk (returns 404 HTML error page);
        # v2 is the current endpoint and returns the same payload shape.
        result = await self._signed_request("GET", "/fapi/v2/positionRisk", params=params)
        return result if isinstance(result, list) else []

    async def get_account(self) -> dict[str, Any]:
        result = await self._signed_request("GET", "/fapi/v2/account")
        return result if isinstance(result, dict) else {}

    # -- Settings --

    async def set_leverage(self, *, symbol: str, leverage: int) -> dict[str, Any]:
        return await self._signed_request(
            "POST", "/fapi/v1/leverage", params={"symbol": symbol, "leverage": leverage}
        )

    async def set_margin_type(self, *, symbol: str, margin_type: str) -> dict[str, Any]:
        """Set margin mode: 'CROSS' or 'ISOLATED'."""
        try:
            return await self._signed_request(
                "POST", "/fapi/v1/marginType",
                params={"symbol": symbol, "marginType": margin_type.upper()},
            )
        except ExchangeAPIError as e:
            if "No need to change margin type" in str(e):
                return {"symbol": symbol, "marginType": margin_type, "msg": "already set"}
            raise

    async def set_position_mode(self, *, dual_side_position: bool) -> dict[str, Any]:
        """Set position mode: True=hedge mode, False=one-way mode."""
        try:
            return await self._signed_request(
                "POST", "/fapi/v1/positionSide/dual",
                params={"dualSidePosition": "true" if dual_side_position else "false"},
            )
        except ExchangeAPIError as e:
            if "No need to change position side" in str(e):
                return {"dualSidePosition": dual_side_position, "msg": "already set"}
            raise

    async def get_position_mode(self) -> bool:
        """Return True if account is in hedge (dual-side) mode, False if net (one-way)."""
        result = await self._signed_request("GET", "/fapi/v1/positionSide/dual")
        return bool(result.get("dualSidePosition", False))

    # -- Connectivity --

    async def test_connectivity(self) -> bool:
        try:
            result = await self._public_request("GET", "/fapi/v1/ping")
            return isinstance(result, dict)
        except Exception:
            return False

    async def get_exchange_time(self) -> dict[str, Any]:
        return await self._public_request("GET", "/fapi/v1/time")
