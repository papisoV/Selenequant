"""OKX v5 REST trading client.

Every request is signed with the OK-ACCESS-* header set (Base64 HMAC of
``ts + METHOD + path + body``). OKX answers errors in two shapes: a top-level
non-zero ``code`` for transport-level failures, and per-item ``sCode`` entries
inside ``data`` for batch order operations — both are translated here.
"""

from __future__ import annotations

import json
import logging
from enum import Enum
from typing import Any

from selene_space_exchange.base.auth import ExchangeCredentials, OKXAuthenticator
from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter
from selene_space_exchange.base.rest_client import BaseRESTClient
from selene_space_exchange.exceptions import (
    AuthenticationError,
    ExchangeAPIError,
    InsufficientBalanceError,
    InvalidOrderError,
    OrderNotFoundError,
    OrderRejectedError,
)

_log = logging.getLogger(__name__)

OKX_API_URL = "https://www.okx.com"

#: Order types that must carry a ``px`` field on the wire.
_PRICE_REQUIRED = ("limit", "post_only")


class OKXOrderType(str, Enum):
    MARKET = "market"
    LIMIT = "limit"
    POST_ONLY = "post_only"
    FOK = "fok"
    IOC = "ioc"


class OKXTradeMode(str, Enum):
    CASH = "cash"
    CROSS = "cross"
    ISOLATED = "isolated"


class OKXRESTClient(BaseRESTClient):
    def __init__(
        self,
        credentials: ExchangeCredentials,
        *,
        demo: bool = False,
        base_url: str | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        timeout: float = 15.0,
    ) -> None:
        super().__init__(
            base_url=base_url or OKX_API_URL,
            authenticator=OKXAuthenticator(credentials),
            rate_limiter=rate_limiter
            or SlidingWindowRateLimiter(calls_per_window=60, window_ms=2_000),
            timeout=timeout,
        )
        self._demo = demo

    @property
    def _exchange_name(self) -> str:
        return "okx"

    # -- transport -----------------------------------------------------------

    async def _okx_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        await self._rate_limiter.acquire()

        query = dict(params) if params else None
        payload = json.dumps(body) if body else ""
        headers = self._auth.sign_request(
            method=method, path=path, params=query, body=payload or None
        )
        headers["Content-Type"] = "application/json"

        resp = await self._http.request(
            method=method,
            url=f"{self._base_url}{path}",
            params=query,
            content=payload if body else None,
            headers=headers,
        )

        data: dict[str, Any]
        try:
            data = resp.json()
        except Exception:
            data = {"code": str(resp.status_code), "msg": resp.text}

        code = str(data.get("code", "0"))
        if resp.status_code >= 400 or code not in ("0", ""):
            self._handle_okx_error(data, status_code=resp.status_code)
        return data

    # -- error translation -----------------------------------------------------

    def _handle_error_response(self, resp: Any) -> None:
        try:
            self._handle_okx_error(resp.json(), status_code=resp.status_code)
        except Exception:
            raise ExchangeAPIError(
                f"OKX HTTP {resp.status_code}", exchange="okx", status_code=resp.status_code
            )

    #: Top-level codes that mean "the API key is not accepted".
    _AUTH_CODES = frozenset({"50101", "50102", "50111"})
    #: Per-item sCode buckets.
    _INSUFFICIENT_SCODES = frozenset({"51008", "51009", "51010"})
    _NOT_FOUND_SCODES = frozenset({"51603"})
    _REJECTED_SCODES = frozenset({"51000", "51001", "51002", "51119"})

    def _handle_okx_error(self, data: dict[str, Any], status_code: int | None = None) -> None:
        msg = data.get("msg") or data.get("sMsg") or str(data)
        top_code = str(data.get("code", ""))

        if top_code in self._AUTH_CODES or status_code == 401:
            raise AuthenticationError(f"OKX auth error: {msg}", exchange="okx")

        items = data.get("data") or []
        for entry in items:
            scode = str(entry.get("sCode", ""))
            detail = entry.get("sMsg") or msg
            if scode in self._INSUFFICIENT_SCODES:
                raise InsufficientBalanceError(
                    f"Insufficient balance: {detail}", exchange="okx"
                )
            if scode in self._NOT_FOUND_SCODES:
                raise OrderNotFoundError(f"Order not found: {detail}", exchange="okx")
            if scode in self._REJECTED_SCODES:
                raise OrderRejectedError(
                    f"Order rejected: {detail}", exchange="okx", details=entry
                )
        raise ExchangeAPIError(
            f"OKX error ({top_code}): {msg}",
            exchange="okx",
            status_code=status_code,
            details=data,
        )

    # -- trading -----------------------------------------------------------------

    @staticmethod
    def _validate_order_params(body: dict[str, Any]) -> None:
        kind = str(body.get("ordType", "")).lower()
        if kind in _PRICE_REQUIRED and "px" not in body:
            raise InvalidOrderError(f"{kind} order requires 'px' (price)", exchange="okx")
        if "sz" not in body:
            raise InvalidOrderError("order requires 'sz'", exchange="okx")

    async def place_order(
        self,
        *,
        inst_id: str,
        side: str,
        order_type: str | OKXOrderType,
        size: str,
        trade_mode: str | OKXTradeMode = OKXTradeMode.CASH,
        price: str | None = None,
        client_order_id: str | None = None,
        tag: str | None = None,
        reduce_only: bool = False,
        target_currency: str | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "instId": inst_id,
            "tdMode": (
                trade_mode.value if isinstance(trade_mode, OKXTradeMode) else str(trade_mode)
            ),
            "side": side.lower(),
            "ordType": (
                order_type.value if isinstance(order_type, OKXOrderType) else str(order_type).lower()
            ),
            "sz": size,
        }
        optional = {
            "px": price,
            "clOrdId": client_order_id,
            "tag": tag,
            "tgtCcy": target_currency,
        }
        for key, value in optional.items():
            if value is not None:
                body[key] = value
        if reduce_only:
            body["reduceOnly"] = True

        self._validate_order_params(body)
        return await self._okx_request("POST", "/api/v5/trade/order", body=body)

    async def cancel_order(
        self,
        *,
        inst_id: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        body = self._order_identity(inst_id, order_id, client_order_id)
        return await self._okx_request("POST", "/api/v5/trade/cancel-order", body=body)

    async def get_order(
        self,
        *,
        inst_id: str,
        order_id: str | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        query = self._order_identity(inst_id, order_id, client_order_id)
        result = await self._okx_request("GET", "/api/v5/trade/order", params=query)
        rows = result.get("data") or [{}]
        return rows[0]

    @staticmethod
    def _order_identity(
        inst_id: str, order_id: str | None, client_order_id: str | None
    ) -> dict[str, str]:
        identity: dict[str, str] = {"instId": inst_id}
        if order_id is not None:
            identity["ordId"] = order_id
        elif client_order_id is not None:
            identity["clOrdId"] = client_order_id
        else:
            raise InvalidOrderError(
                "Either order_id or client_order_id is required", exchange="okx"
            )
        return identity

    async def get_open_orders(
        self,
        *,
        inst_id: str | None = None,
        order_type: str | None = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {"limit": str(limit)}
        if inst_id:
            query["instId"] = inst_id
        if order_type:
            query["ordType"] = order_type
        return await self._okx_request("GET", "/api/v5/trade/orders-pending", params=query)

    async def get_order_history(
        self,
        *,
        inst_id: str | None = None,
        inst_type: str = "SPOT",
        limit: int = 100,
    ) -> dict[str, Any]:
        query: dict[str, Any] = {"instType": inst_type, "limit": str(limit)}
        if inst_id:
            query["instId"] = inst_id
        return await self._okx_request(
            "GET", "/api/v5/trade/orders-history-archive", params=query
        )

    # -- account -----------------------------------------------------------------

    async def get_balance(self, *, currencies: list[str] | None = None) -> dict[str, Any]:
        query = {"ccy": ",".join(currencies)} if currencies else None
        return await self._okx_request("GET", "/api/v5/account/balance", params=query)

    async def get_positions(
        self, *, inst_id: str | None = None, inst_type: str | None = None
    ) -> dict[str, Any]:
        query: dict[str, Any] = {}
        if inst_id:
            query["instId"] = inst_id
        if inst_type:
            query["instType"] = inst_type
        return await self._okx_request("GET", "/api/v5/account/positions", params=query)

    async def get_instruments(
        self, inst_type: str = "SPOT", inst_id: str | None = None
    ) -> dict[str, Any]:
        query: dict[str, Any] = {"instType": inst_type}
        if inst_id:
            query["instId"] = inst_id
        return await self._okx_request("GET", "/api/v5/public/instruments", params=query)

    async def test_connectivity(self) -> bool:
        try:
            resp = await self._http.get(f"{self._base_url}/api/v5/public/time")
            return resp.status_code == 200
        except Exception:
            return False
