"""Binance Spot REST trading endpoints (HMAC-signed).

Wire notes: signed parameters travel as the request query string (never a
JSON body), and the HMAC must cover exactly the bytes sent, so the client
serializes its parameter map itself rather than letting httpx re-encode it.

Endpoints covered: order placement/cancellation/query, open + historical
order listings, account balances, exchange filters, key-permission
introspection and the connectivity ping.
"""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from enum import Enum
from logging import getLogger
from typing import Any

from selene_space_exchange.base.auth import BinanceAuthenticator, ExchangeCredentials
from selene_space_exchange.base.rate_limiter import SlidingWindowRateLimiter
from selene_space_exchange.base.rest_client import BaseRESTClient
from selene_space_exchange.exceptions import (
    AuthenticationError,
    ExchangeAPIError,
    InsufficientBalanceError,
    InvalidOrderError,
    OrderNotFoundError,
    OrderRejectedError,
    RateLimitExceededError,
)
from selene_space_exchange.trading import OrderRequest, OrderResponse
from selene_space_types.trading import OrderStatus

_log = getLogger(__name__)

BINANCE_API_URL = "https://api.binance.com"
BINANCE_TESTNET_URL = "https://testnet.binance.vision"

#: Venue allows 1200 request-weight per minute on spot.
_DEFAULT_WEIGHT_PER_MIN = 1200

_VENUE = "binance"

# -- error routing -----------------------------------------------------------
# Status codes bucket requests into coarse groups (rate-limit/auth); the
# venue's numeric codes pick the fine-grained order-error buckets.

_HTTP_TOO_MANY_REQUESTS = 429
_HTTP_UNAUTHORIZED = frozenset({401, 403})
_AUTH_REJECT_CODES = frozenset({-2015, -2014, -1022})
_SHORT_BALANCE_CODE = -2010
_UNKNOWN_ORDER_CODES = frozenset({-2011, -2013})
_MALFORMED_ORDER_CODES = frozenset({-1013, -1111, -1116, -1117, -1121})


def _too_many_requests(msg: str, status: int, data: dict[str, Any]) -> Exception:
    return RateLimitExceededError(
        f"Binance rate limit: {msg}", exchange=_VENUE, status_code=status
    )


def _auth_rejected(msg: str, status: int, data: dict[str, Any]) -> Exception:
    return AuthenticationError(
        f"Binance auth error: {msg}", exchange=_VENUE, status_code=status
    )


def _balance_short(msg: str, status: int, data: dict[str, Any]) -> Exception:
    return InsufficientBalanceError(f"Insufficient balance: {msg}", exchange=_VENUE)


def _order_unknown(msg: str, status: int, data: dict[str, Any]) -> Exception:
    return OrderNotFoundError(f"Order not found: {msg}", exchange=_VENUE)


def _order_malformed(msg: str, status: int, data: dict[str, Any]) -> Exception:
    return OrderRejectedError(
        f"Order rejected: {msg}", exchange=_VENUE, details=data
    )


_ErrorBuilder = Callable[[str, int, dict[str, Any]], Exception]
_CodeMatcher = Callable[[int, int], bool]

#: First matching route wins; anything falling through becomes ExchangeAPIError.
_ERROR_ROUTES: tuple[tuple[_CodeMatcher, _ErrorBuilder], ...] = (
    (lambda status, code: status == _HTTP_TOO_MANY_REQUESTS, _too_many_requests),
    (
        lambda status, code: status in _HTTP_UNAUTHORIZED or code in _AUTH_REJECT_CODES,
        _auth_rejected,
    ),
    (lambda status, code: code == _SHORT_BALANCE_CODE, _balance_short),
    (lambda status, code: code in _UNKNOWN_ORDER_CODES, _order_unknown),
    (lambda status, code: code in _MALFORMED_ORDER_CODES, _order_malformed),
)

# -- order parameter validation ----------------------------------------------

#: Order types whose listed fields must ALL be present.
_FIELDS_ALL_REQUIRED: dict[str, tuple[str, ...]] = {
    "LIMIT": ("quantity", "price", "timeInForce"),
    "STOP_LOSS": ("quantity", "stopPrice"),
    "TAKE_PROFIT": ("quantity", "stopPrice"),
    "STOP_LOSS_LIMIT": ("quantity", "price", "timeInForce", "stopPrice"),
    "TAKE_PROFIT_LIMIT": ("quantity", "price", "timeInForce", "stopPrice"),
}

#: Order types needing at least ONE of the listed fields.
_FIELD_ANY_REQUIRED: dict[str, tuple[str, ...]] = {
    "MARKET": ("quantity", "quoteOrderQty"),
}

_NOTIONAL_FILTER_TYPES = ("MIN_NOTIONAL", "NOTIONAL")


class BinanceOrderType(str, Enum):
    LIMIT = "LIMIT"
    MARKET = "MARKET"
    STOP_LOSS = "STOP_LOSS"
    STOP_LOSS_LIMIT = "STOP_LOSS_LIMIT"
    TAKE_PROFIT = "TAKE_PROFIT"
    TAKE_PROFIT_LIMIT = "TAKE_PROFIT_LIMIT"
    LIMIT_MAKER = "LIMIT_MAKER"


class BinanceTimeInForce(str, Enum):
    GTC = "GTC"
    IOC = "IOC"
    FOK = "FOK"


def _parse_positive_float(value: Any) -> float | None:
    """Float-parse a filter value; blank/unparsable input yields None."""
    text = "" if value is None else str(value).strip()
    try:
        return float(text)
    except ValueError:
        return None


class BinanceRESTClient(BaseRESTClient):
    def __init__(
        self,
        credentials: ExchangeCredentials,
        *,
        testnet: bool = False,
        base_url: str | None = None,
        rate_limiter: SlidingWindowRateLimiter | None = None,
        timeout: float = 15.0,
        recv_window: int = 5000,
    ) -> None:
        default_url = BINANCE_TESTNET_URL if testnet else BINANCE_API_URL
        super().__init__(
            base_url=base_url or default_url,
            authenticator=BinanceAuthenticator(credentials),
            rate_limiter=rate_limiter
            or SlidingWindowRateLimiter(
                calls_per_window=_DEFAULT_WEIGHT_PER_MIN, window_ms=60_000
            ),
            timeout=timeout,
            recv_window=recv_window,
        )

    @property
    def _exchange_name(self) -> str:
        return _VENUE

    # -- transport -----------------------------------------------------------

    async def _signed_request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Sign into the params mapping, then send it verbatim as the query.

        The authenticator leaves the HMAC inside the mapping; we splice it on
        last so the transmitted query is byte-identical to what was signed.
        """
        await self._rate_limiter.acquire()

        work: dict[str, Any] = {} if params is None else params
        headers = self._auth.sign_request(method=method, path=path, params=work)

        signature = work.pop("signature", None)
        pairs = sorted(work.items())
        query = "&".join(f"{k}={v}" for k, v in pairs)
        if signature is not None:
            query = f"{query}&signature={signature}" if query else f"signature={signature}"

        target = f"{self._base_url}{path}"
        if query:
            target = f"{target}?{query}"
        _log.debug("binance %s %s", method, path)

        resp = await self._http.request(method=method, url=target, headers=headers)

        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            self._rate_limiter.set_retry_after(retry_after)
        if resp.status_code >= 400:
            self._handle_error_response(resp)

        payload: dict[str, Any] = resp.json()
        return payload

    # -- error translation -----------------------------------------------------

    def _handle_error_response(self, resp: Any) -> None:
        """Raise the typed exception selected by :data:`_ERROR_ROUTES`."""
        try:
            data = resp.json()
        except Exception:
            data = {"msg": resp.text}

        status = resp.status_code
        code = data.get("code", 0)
        msg = data.get("msg", str(data))

        for matches, build in _ERROR_ROUTES:
            if matches(status, code):
                raise build(msg, status, data)

        raise ExchangeAPIError(
            f"Binance API error ({code}): {msg}",
            exchange=_VENUE,
            status_code=status,
            details=data,
        )

    # -- shared param plumbing ---------------------------------------------------

    @staticmethod
    def _identify_order(
        *,
        symbol: str,
        order_id: int | None,
        client_order_id: str | None,
    ) -> dict[str, Any]:
        """Symbol + whichever order identifier was supplied."""
        selector: dict[str, Any] = {"symbol": symbol.upper()}
        if order_id is not None:
            selector["orderId"] = str(order_id)
        if client_order_id is not None:
            selector["origClientOrderId"] = client_order_id
        return selector

    @staticmethod
    def _ensure_list(payload: Any) -> list[dict[str, Any]]:
        return payload if isinstance(payload, list) else [payload]

    async def _lot_and_notional_filters(self, symbol: str) -> list[dict[str, Any]]:
        """Filter rules the venue published for *symbol* (empty if unknown)."""
        info = await self.get_exchange_info(symbol=symbol)
        wanted = symbol.upper()
        for entry in info.get("symbols", []):
            if entry.get("symbol") == wanted:
                found: list[dict[str, Any]] = entry.get("filters", [])
                return found
        return []

    # -- trading -----------------------------------------------------------------

    async def place_order(
        self,
        *,
        symbol: str,
        side: str,
        order_type: str | BinanceOrderType,
        quantity: str | None = None,
        quote_order_qty: str | None = None,
        price: str | None = None,
        time_in_force: str | BinanceTimeInForce | None = None,
        stop_price: str | None = None,
        new_client_order_id: str | None = None,
        new_order_resp_type: str = "FULL",
    ) -> dict[str, Any]:
        if quantity is not None:
            step = await self._get_step_size(symbol)
            if step:
                quantity = self._round_to_step(quantity, step)

        kind = (
            order_type.value
            if isinstance(order_type, BinanceOrderType)
            else str(order_type).upper()
        )
        tif = str(time_in_force).upper() if time_in_force is not None else None

        fields: dict[str, Any] = {
            "symbol": symbol.upper(),
            "side": side.upper(),
            "type": kind,
            "newOrderRespType": new_order_resp_type,
        }
        optional_values: dict[str, Any] = {
            "quantity": quantity,
            "quoteOrderQty": quote_order_qty,
            "price": price,
            "timeInForce": tif,
            "stopPrice": stop_price,
            "newClientOrderId": new_client_order_id,
        }
        fields |= {key: val for key, val in optional_values.items() if val is not None}

        self._validate_order_params(fields)
        return await self._signed_request("POST", "/api/v3/order", params=fields)

    async def place_order_normalized(self, request: OrderRequest) -> OrderResponse:
        """TradingClient contract: OrderRequest in, OrderResponse out."""

        def trim(value: Any) -> str | None:
            if value is None:
                return None
            text = f"{value:.8f}".rstrip("0").rstrip(".")
            return text or "0"

        compact_symbol = request.symbol.replace("/", "").replace("-", "").upper()
        is_limit = request.order_type.value.upper() == "LIMIT"

        raw = await self.place_order(
            symbol=compact_symbol,
            side=request.side.value,
            order_type=request.order_type.value,
            quantity=trim(request.quantity),
            price=trim(request.price),
            stop_price=trim(request.stop_price),
            time_in_force=request.time_in_force if is_limit else None,
            new_client_order_id=request.client_order_id,
            new_order_resp_type="FULL",
        )

        filled_qty = float(raw.get("executedQty", "0") or "0")
        placed_qty = float(raw.get("origQty", "0") or "0")
        reported_status = str(raw.get("status", "NEW")).upper()

        return OrderResponse(
            exchange=_VENUE,
            order_id=str(raw.get("orderId", "")),
            client_order_id=raw.get("clientOrderId", request.client_order_id or ""),
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=_STATUS_MAP.get(reported_status, OrderStatus.ACCEPTED),
            quantity=placed_qty,
            filled_quantity=filled_qty,
            price=request.price,
            avg_fill_price=self._avg_fill_price(raw, filled_qty),
            timestamp=int(raw.get("transactTime", time.time() * 1000)),
            raw=raw,
        )

    @staticmethod
    def _avg_fill_price(raw: dict[str, Any], executed_qty: float) -> float | None:
        fills = raw.get("fills")
        quote_total = raw.get("cummulativeQuoteQty")

        try:
            if quote_total and executed_qty > 0:
                return float(quote_total) / executed_qty
        except (ValueError, ZeroDivisionError):
            pass

        if fills:
            try:
                lead_price = float(fills[0].get("price", "0"))
                if lead_price > 0:
                    return lead_price
            except (ValueError, IndexError, KeyError):
                pass
        return None

    async def cancel_order(
        self,
        *,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        selector = self._identify_order(
            symbol=symbol, order_id=order_id, client_order_id=client_order_id
        )
        if len(selector) == 1:
            raise InvalidOrderError(
                "Either order_id or client_order_id is required", exchange=_VENUE
            )
        return await self._signed_request("DELETE", "/api/v3/order", params=selector)

    async def get_order(
        self,
        *,
        symbol: str,
        order_id: int | None = None,
        client_order_id: str | None = None,
    ) -> dict[str, Any]:
        selector = self._identify_order(
            symbol=symbol, order_id=order_id, client_order_id=client_order_id
        )
        return await self._signed_request("GET", "/api/v3/order", params=selector)

    async def get_open_orders(self, *, symbol: str | None = None) -> list[dict[str, Any]]:
        scope: dict[str, Any] = {}
        if symbol is not None:
            scope["symbol"] = symbol.upper()
        payload = await self._signed_request("GET", "/api/v3/openOrders", params=scope)
        return self._ensure_list(payload)

    async def get_all_orders(
        self,
        *,
        symbol: str,
        limit: int = 500,
        order_id: int | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
    ) -> list[dict[str, Any]]:
        window: dict[str, Any] = {
            "symbol": symbol.upper(),
            "limit": str(min(limit, 1000)),
        }
        if order_id is not None:
            window["orderId"] = str(order_id)
        if start_time is not None:
            window["startTime"] = str(start_time)
        if end_time is not None:
            window["endTime"] = str(end_time)
        payload = await self._signed_request("GET", "/api/v3/allOrders", params=window)
        return self._ensure_list(payload)

    # -- account -------------------------------------------------------------

    async def get_account(self, *, omit_zero_balances: bool = True) -> dict[str, Any]:
        flags: dict[str, Any] = {}
        if omit_zero_balances:
            flags["omitZeroBalances"] = "true"
        return await self._signed_request("GET", "/api/v3/account", params=flags)

    async def get_balances(self) -> list[dict[str, str]]:
        account = await self.get_account(omit_zero_balances=True)
        balances: list[dict[str, str]] = account.get("balances", [])
        return balances

    # -- public / metadata -----------------------------------------------------

    async def get_exchange_info(self, symbol: str | None = None) -> dict[str, Any]:
        params = {"symbol": symbol.upper()} if symbol else {}
        resp = await self._http.get(f"{self._base_url}/api/v3/exchangeInfo", params=params or None)
        resp.raise_for_status()
        result: dict[str, Any] = resp.json()
        return result

    async def get_api_restrictions(self) -> dict[str, Any]:
        """SAPI permission map; live startup refuses keys with dangerous grants."""
        return await self._signed_request("GET", "/sapi/v1/account/apiRestrictions")

    async def get_min_notional(self, symbol: str) -> float | None:
        """Largest min-notional across MIN_NOTIONAL/NOTIONAL filters (None if absent)."""
        thresholds = [
            parsed
            for parsed in (
                _parse_positive_float(rule.get("minNotional"))
                for rule in await self._lot_and_notional_filters(symbol)
                if rule.get("filterType") in _NOTIONAL_FILTER_TYPES
            )
            if parsed is not None
        ]
        return max(thresholds) if thresholds else None

    async def _get_step_size(self, symbol: str) -> float | None:
        for rule in await self._lot_and_notional_filters(symbol):
            if rule.get("filterType") != "LOT_SIZE":
                continue
            step = _parse_positive_float(rule.get("stepSize", "0"))
            return step if step and step > 0 else None
        return None

    @staticmethod
    def _round_to_step(qty: float | str, step: float) -> str:
        """Floor to a LOT_SIZE multiple so SELLs can never exceed free balance."""
        amount = float(qty)
        if step <= 0:
            return str(amount)
        floored = math.floor(amount / step) * step
        digits = 0 if step >= 1 else max(0, -int(math.floor(math.log10(step))))
        return f"{floored:.{digits}f}"

    async def test_connectivity(self) -> bool:
        try:
            pong = await self._http.get(f"{self._base_url}/api/v3/ping")
        except Exception:
            return False
        return pong.status_code == 200

    # -- validation ----------------------------------------------------------

    @staticmethod
    def _validate_order_params(params: dict[str, Any]) -> None:
        kind = params.get("type", "")

        absent = [name for name in _FIELDS_ALL_REQUIRED.get(kind, ()) if name not in params]
        if absent:
            raise InvalidOrderError(
                f"{kind} order requires '{absent[0]}'", exchange=_VENUE
            )

        alternatives = _FIELD_ANY_REQUIRED.get(kind)
        if alternatives and not any(name in params for name in alternatives):
            choices = " or ".join(f"'{name}'" for name in alternatives)
            raise InvalidOrderError(
                f"{kind} order requires {choices}", exchange=_VENUE
            )


_STATUS_MAP = {
    "NEW": OrderStatus.ACCEPTED,
    "PARTIALLY_FILLED": OrderStatus.PARTIAL,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.REJECTED,
}
