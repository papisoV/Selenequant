"""Unified trading interface for all exchanges.

Provides a Protocol-based abstraction that normalizes order operations
across Binance, OKX, and Polymarket into a common interface. This enables
the agent-runtime and strategy-engine to place orders without knowing
the exchange-specific API details.
"""

from __future__ import annotations

import logging
import time
import uuid
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Any, cast

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from selene_space_types.trading import OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)


def _to_decimal(v: Any) -> Decimal:
    """Coerce to Decimal via str to avoid float precision pollution (H19)."""
    if isinstance(v, Decimal):
        return v
    if v is None:
        return v  # type: ignore[return-value]
    if isinstance(v, bool):
        return Decimal(int(v))
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(str(v))
    if isinstance(v, str):
        return Decimal(v)
    return Decimal(str(v))


# ---------------------------------------------------------------------------
# Unified order request / response
# ---------------------------------------------------------------------------


class OrderRequest(BaseModel):
    """Exchange-agnostic order request.

    This is the unified interface that agents and strategies use.
    The exchange adapter translates it into exchange-specific parameters.
    """

    symbol: str = Field(
        ..., description="Trading pair (e.g. 'BTC/USDT', or token_id for Polymarket)"
    )
    side: OrderSide = Field(..., description="Buy or sell")
    order_type: OrderType = Field(
        ..., description="Market, limit, stop_market, stop_limit"
    )
    quantity: Decimal = Field(..., gt=0, description="Amount to trade")
    price: Decimal | None = Field(
        None, ge=0, description="Limit price (required for limit orders)"
    )
    stop_price: Decimal | None = Field(None, ge=0, description="Stop trigger price")
    time_in_force: str = Field("GTC", description="Time in force: GTC, IOC, FOK")
    client_order_id: str | None = Field(None, description="Custom order ID")
    reduce_only: bool = Field(
        False, description="Futures: never open a position, only reduce (default False)"
    )
    pos_side: str | None = Field(
        None,
        description="Futures position side (hedge mode): LONG or SHORT. None = net mode",
    )

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        json_schema_extra={
            "examples": [
                {
                    "symbol": "BTC/USDT",
                    "side": "buy",
                    "order_type": "limit",
                    "quantity": 0.001,
                    "price": 60000.0,
                    "time_in_force": "GTC",
                }
            ]
        },
    )

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator("quantity", "price", "stop_price", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)


class OrderResponse(BaseModel):
    """Normalized response after placing/cancelling an order."""

    exchange: str = Field(..., description="Exchange name")
    order_id: str = Field(..., description="Exchange-assigned order ID")
    client_order_id: str = Field("", description="Client-assigned order ID")
    symbol: str = Field(..., description="Trading pair")
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    price: Decimal | None = None
    avg_fill_price: Decimal | None = None
    timestamp: int = Field(default_factory=lambda: int(time.time() * 1000))
    raw: dict[str, Any] = Field(
        default_factory=dict, description="Raw exchange response"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator(
        "quantity", "filled_quantity", "price", "avg_fill_price", mode="before"
    )
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)


class BalanceInfo(BaseModel):
    """Normalized account balance for a single asset."""

    asset: str
    free: Decimal = Decimal("0")
    locked: Decimal = Decimal("0")
    total: Decimal = Decimal("0")

    model_config = ConfigDict(arbitrary_types_allowed=True)

    @field_serializer("*", when_used="json")
    def _serialize_decimal_json(self, v: Any) -> Any:
        if isinstance(v, Decimal):
            return float(v)
        return v

    @field_validator("free", "locked", "total", mode="before")
    @classmethod
    def _coerce_decimal(cls, v: Any) -> Any:
        return _to_decimal(v)


# ---------------------------------------------------------------------------
# Trading client Protocol
# ---------------------------------------------------------------------------


class TradingClient(ABC):
    """Unified trading client interface.

    All exchange-specific clients implement this ABC, providing a consistent
    API for the agent-runtime and strategy execution pipeline.
    """

    @property
    @abstractmethod
    def exchange_name(self) -> str:
        """Return the exchange identifier."""

    @abstractmethod
    async def place_order(self, request: OrderRequest) -> OrderResponse:
        """Place a new order on the exchange.

        Args:
            request: Unified order request.

        Returns:
            Normalized order response.
        """

    @abstractmethod
    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        """Cancel an existing order.

        Args:
            symbol: Trading pair.
            order_id: Exchange order ID.
        """

    @abstractmethod
    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        """Get current status of an order.

        Args:
            symbol: Trading pair.
            order_id: Exchange order ID.
        """

    @abstractmethod
    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        """Get all currently open orders."""

    async def cancel_all_open_orders(self, symbol: str | None = None) -> int:
        """Cancel all currently open orders, optionally filtered by symbol.

        Default implementation: fetch open orders, cancel each one.
        Adapters may override with a single batch-cancel REST call when the
        exchange supports it (more efficient, avoids N round-trips).

        Args:
            symbol: If provided, only cancel orders for this trading pair.

        Returns:
            Number of orders successfully cancelled.
        """
        open_orders = await self.get_open_orders(symbol)
        cancelled = 0
        for order in open_orders:
            try:
                await self.cancel_order(symbol=order.symbol, order_id=order.order_id)
                cancelled += 1
            except Exception:
                logger.warning(
                    "cancel_all_open_orders: failed to cancel %s on %s",
                    order.order_id,
                    self.exchange_name,
                    exc_info=True,
                )
        logger.info(
            "cancel_all_open_orders(%s): cancelled %d/%d orders on %s",
            symbol or "all",
            cancelled,
            len(open_orders),
            self.exchange_name,
        )
        return cancelled

    @abstractmethod
    async def get_balances(self) -> list[BalanceInfo]:
        """Get account balances."""

    @abstractmethod
    async def test_connectivity(self) -> bool:
        """Check if the exchange is reachable."""

    async def close(self) -> None:
        """Close underlying connections. Override if needed."""

    # -- Derivatives settings (optional overrides) --

    async def set_leverage(self, *, symbol: str, leverage: int) -> dict[str, Any]:
        """Set leverage for a symbol. Override in derivatives adapters."""
        raise NotImplementedError(f"{self.exchange_name} does not support set_leverage")

    async def set_margin_mode(self, *, symbol: str, margin_mode: str) -> dict[str, Any]:
        """Set margin mode (cross/isolated). Override in derivatives adapters."""
        raise NotImplementedError(
            f"{self.exchange_name} does not support set_margin_mode"
        )

    async def set_position_mode(self, *, dual_side: bool) -> dict[str, Any]:
        """Set position mode: True=hedge, False=one-way. Override in derivatives adapters."""
        raise NotImplementedError(
            f"{self.exchange_name} does not support set_position_mode"
        )

    async def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        """Get open positions from the exchange. Override if supported."""
        return []


# ---------------------------------------------------------------------------
# Binance adapter
# ---------------------------------------------------------------------------

# Outbound translation tables: unified OrderType -> this exchange's wire string.
# Kept as one table per market so a new order type is a one-line addition.
_BINANCE_SPOT_OUTBOUND_TYPE: dict[OrderType, str] = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_MARKET: "STOP_LOSS",
    OrderType.STOP_LIMIT: "STOP_LOSS_LIMIT",
}
_BINANCE_FUTURES_OUTBOUND_TYPE: dict[OrderType, str] = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_MARKET: "STOP_MARKET",
    OrderType.STOP_LIMIT: "STOP",
    OrderType.IOC: "LIMIT",
}
_OKX_OUTBOUND_TYPE: dict[OrderType, str] = {
    OrderType.MARKET: "market",
    OrderType.LIMIT: "limit",
    OrderType.STOP_MARKET: "market",
    OrderType.STOP_LIMIT: "limit",
}

# Inbound translation tables: exchange wire string -> unified enum.
# Spot collapses EXPIRED into CANCELLED; futures keeps it distinct, and the
# futures type vocabulary differs from spot (STOP / TAKE_PROFIT_MARKET ...).
_BINANCE_SPOT_INBOUND_STATUS: dict[str, OrderStatus] = {
    "NEW": OrderStatus.ACCEPTED,
    "PARTIALLY_FILLED": OrderStatus.PARTIAL,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.CANCELLED,
}
_BINANCE_SPOT_INBOUND_TYPE: dict[str, OrderType] = {
    "LIMIT": OrderType.LIMIT,
    "MARKET": OrderType.MARKET,
    "STOP_LOSS": OrderType.STOP_MARKET,
    "STOP_LOSS_LIMIT": OrderType.STOP_LIMIT,
    "TAKE_PROFIT": OrderType.STOP_MARKET,
    "TAKE_PROFIT_LIMIT": OrderType.STOP_LIMIT,
    "LIMIT_MAKER": OrderType.LIMIT,
}
_BINANCE_FUTURES_STATUS_MAP: dict[str, OrderStatus] = {
    "NEW": OrderStatus.ACCEPTED,
    "PARTIALLY_FILLED": OrderStatus.PARTIAL,
    "FILLED": OrderStatus.FILLED,
    "CANCELED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}
_BINANCE_FUTURES_TYPE_MAP: dict[str, OrderType] = {
    "LIMIT": OrderType.LIMIT,
    "MARKET": OrderType.MARKET,
    "STOP": OrderType.STOP_LIMIT,
    "STOP_MARKET": OrderType.STOP_MARKET,
    "TAKE_PROFIT": OrderType.STOP_LIMIT,
    "TAKE_PROFIT_MARKET": OrderType.STOP_MARKET,
}
_OKX_INBOUND_STATUS: dict[str, OrderStatus] = {
    "live": OrderStatus.ACCEPTED,
    "partially_filled": OrderStatus.PARTIAL,
    "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELLED,
    "cancelled": OrderStatus.CANCELLED,
}
_OKX_INBOUND_TYPE: dict[str, OrderType] = {
    "market": OrderType.MARKET,
    "limit": OrderType.LIMIT,
    "post_only": OrderType.LIMIT,
    "fok": OrderType.LIMIT,
    "ioc": OrderType.LIMIT,
}
_SIDE_UPPER: dict[str, OrderSide] = {"BUY": OrderSide.BUY, "SELL": OrderSide.SELL}
_SIDE_LOWER: dict[str, OrderSide] = {"buy": OrderSide.BUY, "sell": OrderSide.SELL}


def _first_present(raw: dict[str, Any], *names: str) -> Any:
    """Return the first non-empty ``raw[name]``, else ``None``.

    Used where exchanges send the same logical field under different names
    (e.g. spot ``cummulativeQuoteQty`` vs futures ``cumQuote``).
    """
    for name in names:
        value = raw.get(name)
        if value:
            return value
    return None


def _parse_binance_order_response(
    raw: dict[str, Any],
    *,
    exchange: str,
    status_map: dict[str, OrderStatus],
    type_map: dict[str, OrderType],
    quote_field: str,
    timestamp_fields: tuple[str, ...],
) -> OrderResponse:
    """Shared Binance spot + futures order-response parser.

    Args:
        exchange: "binance" (spot) or "binance_futures".
        status_map: Per-market OrderStatus mapping (spot uses CANCELLED for
            EXPIRED, futures uses EXPIRED).
        type_map: Per-market OrderType mapping (spot has STOP_LOSS /
            STOP_LOSS_LIMIT / LIMIT_MAKER; futures has STOP / STOP_MARKET /
            TAKE_PROFIT_MARKET).
        quote_field: Spot uses ``cummulativeQuoteQty`` (legacy typo in
            Binance API), futures uses ``cumQuote``.
        timestamp_fields: Spot prefers ``transactTime`` then ``time``;
            futures prefers ``updateTime`` then ``time``.
    """
    executed = Decimal(str(raw.get("executedQty", 0)))
    quoted = Decimal(str(raw.get(quote_field, 0)))
    limit_price = _first_present(raw, "price")

    return OrderResponse(
        exchange=exchange,
        order_id=str(raw.get("orderId", "")),
        client_order_id=raw.get("clientOrderId", ""),
        symbol=raw.get("symbol", ""),
        side=_SIDE_UPPER.get(raw.get("side", "BUY"), OrderSide.BUY),
        order_type=type_map.get(raw.get("type", "MARKET"), OrderType.MARKET),
        status=status_map.get(raw.get("status", "NEW"), OrderStatus.ACCEPTED),
        quantity=Decimal(str(raw.get("origQty", 0))),
        filled_quantity=executed,
        price=Decimal(str(limit_price))
        if limit_price and Decimal(str(limit_price)) > 0
        else None,
        avg_fill_price=(quoted / executed) if executed > 0 else None,
        timestamp=int(_first_present(raw, *timestamp_fields) or time.time() * 1000),
        raw=raw,
    )


class BinanceTradingAdapter(TradingClient):
    """Adapts BinanceRESTClient to the unified TradingClient interface."""

    def __init__(self, client: Any) -> None:
        """Args:
        client: A ``BinanceRESTClient`` instance.
        """
        self._client = client

    @property
    def exchange_name(self) -> str:
        return "binance"

    def _wire_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "")

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        params: dict[str, Any] = {
            "symbol": self._wire_symbol(request.symbol),
            "side": request.side.value.upper(),
            "order_type": _BINANCE_SPOT_OUTBOUND_TYPE.get(request.order_type, "MARKET"),
            "quantity": str(request.quantity),
        }
        if request.price is not None:
            params["price"] = str(request.price)
        if request.stop_price is not None:
            params["stop_price"] = str(request.stop_price)
        if request.time_in_force and request.order_type != OrderType.MARKET:
            params["time_in_force"] = request.time_in_force
        if request.client_order_id:
            params["new_client_order_id"] = request.client_order_id

        return self._parse_order_response(await self._client.place_order(**params))

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raw = await self._client.cancel_order(
            symbol=self._wire_symbol(symbol), order_id=int(order_id)
        )
        return self._parse_order_response(raw)

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raw = await self._client.get_order(
            symbol=self._wire_symbol(symbol), order_id=int(order_id)
        )
        return self._parse_order_response(raw)

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        wire = self._wire_symbol(symbol) if symbol else None
        rows = await self._client.get_open_orders(symbol=wire)
        return [self._parse_order_response(r) for r in rows]

    async def get_all_orders(
        self, symbol: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Passthrough to BinanceRESTClient.get_all_orders (US-002 reconciliation).

        Returns raw exchange order dicts (not OrderResponse) — LiveEngine's
        _rest_reconciliation_loop maps exchange orderId → local internal ID
        and checks status field directly.
        """
        return cast(
            list[dict[str, Any]],
            await self._client.get_all_orders(
                symbol=self._wire_symbol(symbol), limit=limit
            ),
        )

    async def get_balances(self) -> list[BalanceInfo]:
        rows = await self._client.get_balances()
        out: list[BalanceInfo] = []
        for row in rows:
            free = Decimal(str(row.get("free", 0)))
            locked = Decimal(str(row.get("locked", 0)))
            out.append(
                BalanceInfo(
                    asset=row["asset"], free=free, locked=locked, total=free + locked
                )
            )
        return out

    async def test_connectivity(self) -> bool:
        result: bool = await self._client.test_connectivity()
        return result

    async def close(self) -> None:
        await self._client.close()

    def _parse_order_response(self, raw: dict[str, Any]) -> OrderResponse:
        return _parse_binance_order_response(
            raw,
            exchange="binance",
            status_map=_BINANCE_SPOT_INBOUND_STATUS,
            type_map=_BINANCE_SPOT_INBOUND_TYPE,
            quote_field="cummulativeQuoteQty",
            timestamp_fields=("transactTime", "time"),
        )


# ---------------------------------------------------------------------------
# OKX adapter
# ---------------------------------------------------------------------------


def _okx_data_first(raw: dict[str, Any]) -> dict[str, Any]:
    """Return the first element of OKX's ``data`` array, or ``{}``."""
    data = raw.get("data") or []
    return data[0] if data else {}


class OKXTradingAdapter(TradingClient):
    """Adapts OKXRESTClient to the unified TradingClient interface.

    Args:
        client: OKXRESTClient instance.
        market_type: "spot" (default) or "futures". For futures, the
            instrument type is SWAP (perpetual) and trade_mode defaults to
            cross-margin; for spot, tdMode=cash.
        trade_mode: Override the tdMode sent to OKX. Defaults derived from
            market_type (CASH for spot, CROSS for futures).
    """

    def __init__(
        self,
        client: Any,
        *,
        market_type: str = "spot",
        trade_mode: Any | None = None,
    ) -> None:
        self._client = client
        self._market_type = market_type.lower()
        if trade_mode is not None:
            self._trade_mode = trade_mode
            return
        # Lazy import to avoid module-level circular dependency.
        from selene_space_exchange.exchanges.okx.rest_client import OKXTradeMode

        self._trade_mode = (
            OKXTradeMode.CROSS if self._market_type == "futures" else OKXTradeMode.CASH
        )

    @property
    def exchange_name(self) -> str:
        return "okx"

    @property
    def market_type(self) -> str:
        return self._market_type

    def _wire_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "-")

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        params: dict[str, Any] = {
            "inst_id": self._wire_symbol(request.symbol),
            "side": request.side.value,
            "order_type": _OKX_OUTBOUND_TYPE.get(request.order_type, "market"),
            "size": str(request.quantity),
            "trade_mode": self._trade_mode,
        }
        if request.price is not None:
            params["price"] = str(request.price)
        if request.client_order_id:
            params["client_order_id"] = request.client_order_id

        return self._parse_order_response(
            await self._client.place_order(**params), request
        )

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raw = await self._client.cancel_order(
            inst_id=self._wire_symbol(symbol), order_id=order_id
        )
        return self._parse_cancel_response(raw, symbol)

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raw = await self._client.get_order(
            inst_id=self._wire_symbol(symbol), order_id=order_id
        )
        return self._parse_query_response(raw)

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        wire = self._wire_symbol(symbol) if symbol else None
        raw = await self._client.get_open_orders(inst_id=wire)
        return [self._parse_single_order(item) for item in (raw.get("data") or [])]

    async def get_balances(self) -> list[BalanceInfo]:
        raw = await self._client.get_balance()
        details = _okx_data_first(raw).get("details", [])
        return [
            BalanceInfo(
                asset=detail.get("ccy", ""),
                free=Decimal(str(detail.get("availBal", 0))),
                locked=Decimal(str(detail.get("frozenBal", 0))),
                total=Decimal(str(detail.get("cashBal", 0))),
            )
            for detail in details
        ]

    async def test_connectivity(self) -> bool:
        result: bool = await self._client.test_connectivity()
        return result

    async def close(self) -> None:
        await self._client.close()

    async def set_leverage(self, *, symbol: str, leverage: int) -> dict[str, Any]:
        if not hasattr(self._client, "set_leverage"):
            raise NotImplementedError("OKX client does not support set_leverage")
        return cast(
            dict[str, Any],
            await self._client.set_leverage(
                inst_id=self._wire_symbol(symbol), lever=str(leverage), mgn_mode="cross"
            ),
        )

    async def set_margin_mode(self, *, symbol: str, margin_mode: str) -> dict[str, Any]:
        if not hasattr(self._client, "set_margin_mode"):
            raise NotImplementedError("OKX client does not support set_margin_mode")
        return cast(
            dict[str, Any],
            await self._client.set_margin_mode(
                inst_id=self._wire_symbol(symbol), mgn_mode=margin_mode
            ),
        )

    async def set_position_mode(self, *, dual_side: bool) -> dict[str, Any]:
        if not hasattr(self._client, "set_position_mode"):
            raise NotImplementedError("OKX client does not support set_position_mode")
        return cast(
            dict[str, Any],
            await self._client.set_position_mode(dual_side=dual_side),
        )

    async def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        if not hasattr(self._client, "get_positions"):
            return []
        wire = self._wire_symbol(symbol) if symbol else None
        return cast(
            list[dict[str, Any]], await self._client.get_positions(inst_id=wire)
        )

    def _okx_ack(self, payload: dict[str, Any], **overrides: Any) -> OrderResponse:
        """Build a response from an OKX ack (IDs only, no order body)."""
        item = _okx_data_first(payload)
        base: dict[str, Any] = {
            "exchange": "okx",
            "order_id": item.get("ordId", ""),
            "client_order_id": item.get("clOrdId", ""),
            "side": OrderSide.BUY,
            "order_type": OrderType.LIMIT,
            "status": OrderStatus.ACCEPTED,
            "quantity": Decimal(0),
        }
        base.update(overrides)
        return OrderResponse(**base)

    def _parse_order_response(
        self, raw: dict[str, Any], request: OrderRequest
    ) -> OrderResponse:
        # OKX returns the assigned IDs but the request already holds the
        # authoritative side/type/quantity, so echo those back.
        return self._okx_ack(
            raw,
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            price=request.price,
            raw=raw,
        )

    def _parse_cancel_response(self, raw: dict[str, Any], symbol: str) -> OrderResponse:
        # A cancel ack carries no meaningful order data beyond the IDs.
        return self._okx_ack(raw, symbol=symbol, status=OrderStatus.CANCELLED, raw=raw)

    def _parse_query_response(self, raw: dict[str, Any]) -> OrderResponse:
        data = raw.get("data") or []
        if data:
            return self._parse_single_order(data[0])
        # Empty data = OKX has no such order.
        return self._okx_ack(
            raw,
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            status=OrderStatus.REJECTED,
            raw=raw,
        )

    def _parse_single_order(self, item: dict[str, Any]) -> OrderResponse:
        avg_raw = item.get("avgPx", "")
        return OrderResponse(
            exchange="okx",
            order_id=item.get("ordId", ""),
            client_order_id=item.get("clOrdId", ""),
            symbol=item.get("instId", "").replace("-", "/"),
            side=_SIDE_LOWER.get(item.get("side", "buy"), OrderSide.BUY),
            order_type=_OKX_INBOUND_TYPE.get(
                item.get("ordType", "market"), OrderType.MARKET
            ),
            status=_OKX_INBOUND_STATUS.get(
                item.get("state", "live"), OrderStatus.ACCEPTED
            ),
            quantity=Decimal(str(item.get("sz", 0) or 0)),
            filled_quantity=Decimal(str(item.get("fillSz", 0) or 0)),
            price=Decimal(str(item["px"])) if item.get("px") else None,
            avg_fill_price=Decimal(str(avg_raw)) if avg_raw else None,
            raw=item,
        )


# ---------------------------------------------------------------------------
# Polymarket adapter
# ---------------------------------------------------------------------------


def _poly_order_id(item: dict[str, Any]) -> str:
    """Polymarket returns the id under ``id`` or ``orderID`` depending on route."""
    found = item.get("id") or item.get("orderID") or ""
    return str(found)


class PolymarketTradingAdapter(TradingClient):
    """Adapts PolymarketTradingClient to the unified TradingClient interface."""

    def __init__(self, client: Any) -> None:
        self._client = client

    @property
    def exchange_name(self) -> str:
        return "polymarket"

    def _synthetic(self, **overrides: Any) -> OrderResponse:
        """Build a response when the exchange gave us no usable order body.

        Polymarket acks carry only IDs, so side/type/quantity are filled with
        neutral placeholders; callers that need truth must re-query.
        """
        base: dict[str, Any] = {
            "exchange": "polymarket",
            "order_id": "",
            "symbol": "",
            "side": OrderSide.BUY,
            "order_type": OrderType.LIMIT,
            "status": OrderStatus.ACCEPTED,
            "quantity": Decimal(0),
        }
        base.update(overrides)
        return OrderResponse(**base)

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        raw = await self._client.place_order(
            token_id=request.symbol,
            side=request.side.value.upper(),
            price=request.price or 0.5,
            size=request.quantity,
        )
        return self._synthetic(
            order_id=raw.get("orderID") or raw.get("id") or str(uuid.uuid4()),
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            quantity=request.quantity,
            price=request.price,
            raw=raw,
        )

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raw = await self._client.cancel_order(order_id=order_id)
        return self._synthetic(
            order_id=order_id,
            symbol=symbol,
            status=OrderStatus.CANCELLED,
            raw=raw,
        )

    async def cancel_all_open_orders(self, symbol: str | None = None) -> int:
        """Cancel all open Polymarket orders.

        Polymarket's REST API exposes a single ``DELETE /cancel-all`` endpoint
        that cancels everything (no per-symbol filter). When ``symbol`` is
        None we use it directly (1 round-trip); otherwise we fall back to the
        default fetch-and-loop implementation to honour the filter.
        """
        if symbol is not None:
            return await super().cancel_all_open_orders(symbol=symbol)
        open_before = await self.get_open_orders()
        await self._client.cancel_all_orders()
        logger.info(
            "cancel_all_open_orders: bulk-cancelled %d polymarket orders",
            len(open_before),
        )
        return len(open_before)

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raw = await self._client.get_active_orders(asset_id=symbol)
        orders = raw.get("data", raw.get("orders", []))
        for o in orders if isinstance(orders, list) else []:
            if _poly_order_id(o) == order_id:
                return self._parse_poly_order(o)
        # Not in the active set — treat as filled (upstream behavior).
        return self._synthetic(
            order_id=order_id,
            symbol=symbol,
            status=OrderStatus.FILLED,
            raw=raw,
        )

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        raw = await self._client.get_active_orders(asset_id=symbol if symbol else None)
        orders = raw.get("data", raw.get("orders", []))
        return [
            self._parse_poly_order(o)
            for o in (orders if isinstance(orders, list) else [])
        ]

    async def get_balances(self) -> list[BalanceInfo]:
        raw = await self._client.get_balance()
        balance = Decimal(str(raw.get("balance", raw.get("amount", 0))))
        return [BalanceInfo(asset="USDC", free=balance, total=balance)]

    async def test_connectivity(self) -> bool:
        result: bool = await self._client.test_connectivity()
        return result

    async def close(self) -> None:
        await self._client.close()

    @staticmethod
    def _parse_poly_order(item: dict[str, Any]) -> OrderResponse:
        return OrderResponse(
            exchange="polymarket",
            order_id=_poly_order_id(item),
            symbol=item.get("asset_id", item.get("tokenID", "")),
            side=_SIDE_UPPER.get(item.get("side", "BUY"), OrderSide.BUY),
            order_type=OrderType.LIMIT,
            status=OrderStatus.ACCEPTED,
            quantity=Decimal(str(item.get("original_size", item.get("size", 0)))),
            price=Decimal(str(item.get("price", 0))),
            raw=item,
        )


# ---------------------------------------------------------------------------
# Binance Futures adapter
# ---------------------------------------------------------------------------


class BinanceFuturesTradingAdapter(TradingClient):
    """Adapts BinanceFuturesRESTClient to the unified TradingClient interface.

    Supports USDT-M perpetual futures with leverage, margin mode,
    and position mode settings.
    """

    def __init__(self, client: Any) -> None:
        self._client = client
        self._position_mode_ensured = False
        self._is_hedge_mode: bool | None = None

    @property
    def exchange_name(self) -> str:
        return "binance_futures"

    async def _ensure_position_mode_once(self) -> None:
        """On first call, switch account to net (one-way) position mode if needed.

        Binance mainnet defaults to hedge (dual-side) mode, which rejects orders
        that don't include positionSide=LONG/SHORT with -4061. SeleneQuant assumes
        net mode (no positionSide param), so we auto-switch on first order.
        Idempotent and non-blocking: failures log a warning and let the order
        surface the -4061 so the user can switch manually.
        """
        if self._position_mode_ensured:
            return
        self._position_mode_ensured = True
        get_mode = getattr(self._client, "get_position_mode", None)
        set_mode = getattr(self._client, "set_position_mode", None)
        if get_mode is None or set_mode is None:
            return
        try:
            is_hedge = await get_mode()
        except Exception as e:
            logger.warning(
                "Could not query Binance position mode: %s. "
                "If account is in hedge mode, orders will fail with -4061.",
                e,
            )
            return
        self._is_hedge_mode = bool(is_hedge)
        if not is_hedge:
            logger.info("Binance account already in net (one-way) position mode")
            return
        try:
            await set_mode(dual_side_position=False)
            # Account is NOW net — stale True would send positionSide into
            # net mode (-4061). On switch failure _is_hedge_mode stays True,
            # which correctly matches the account's actual hedge mode.
            self._is_hedge_mode = False
            logger.warning(
                "Switched Binance account from hedge to net (one-way) position mode. "
                "Note: this cancels all open orders on the account."
            )
        except Exception as e:
            logger.error(
                "Failed to switch Binance account to net position mode: %s. "
                "Orders will fail with -4061 until you manually switch via "
                "POST /fapi/v1/positionSide/dual?dualSidePosition=false",
                e,
            )

    def _wire_symbol(self, symbol: str) -> str:
        return symbol.replace("/", "")

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        await self._ensure_position_mode_once()

        params: dict[str, Any] = {
            "symbol": self._wire_symbol(request.symbol),
            "side": request.side.value.upper(),
            "order_type": _BINANCE_FUTURES_OUTBOUND_TYPE.get(
                request.order_type, "MARKET"
            ),
            "quantity": str(request.quantity),
        }
        if request.price is not None:
            params["price"] = str(request.price)
        if request.stop_price is not None:
            params["stop_price"] = str(request.stop_price)
        if request.time_in_force and request.time_in_force != "GTC":
            params["time_in_force"] = request.time_in_force
        if request.order_type == OrderType.IOC:
            params["time_in_force"] = "IOC"
        if request.client_order_id:
            params["new_client_order_id"] = request.client_order_id
        if request.reduce_only:
            params["reduce_only"] = True
        if self._is_hedge_mode:
            # Hedge (dual-side) mode: Binance requires positionSide and does
            # NOT accept reduceOnly there. The runner's mirrored pos_side is
            # wrong for close orders (SELL-to-close-long must carry
            # positionSide=LONG), so derive from side+reduce_only:
            #   open  (reduce_only=False): BUY→LONG, SELL→SHORT
            #   close (reduce_only=True):  BUY→SHORT, SELL→LONG
            is_long = (request.side == OrderSide.BUY) != bool(request.reduce_only)
            params["position_side"] = "LONG" if is_long else "SHORT"
            params.pop("reduce_only", None)
        # Net (one-way) mode: no positionSide allowed (-4061); reduce_only
        # above carries the close semantics. request.pos_side is ignored.

        return self._parse_order_response(await self._client.place_order(**params))

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raw = await self._client.cancel_order(
            symbol=self._wire_symbol(symbol), order_id=int(order_id)
        )
        return self._parse_order_response(raw)

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raw = await self._client.get_order(
            symbol=self._wire_symbol(symbol), order_id=int(order_id)
        )
        return self._parse_order_response(raw)

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        wire = self._wire_symbol(symbol) if symbol else None
        rows = await self._client.get_open_orders(symbol=wire)
        return [self._parse_order_response(r) for r in rows]

    async def get_all_orders(
        self, symbol: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Passthrough to BinanceRESTClient.get_all_orders (US-002 reconciliation).

        Returns raw exchange order dicts (not OrderResponse) — LiveEngine's
        _rest_reconciliation_loop maps exchange orderId → local internal ID
        and checks status field directly.
        """
        return cast(
            list[dict[str, Any]],
            await self._client.get_all_orders(
                symbol=self._wire_symbol(symbol), limit=limit
            ),
        )

    async def get_balances(self) -> list[BalanceInfo]:
        rows = await self._client.get_balances()
        return [
            BalanceInfo(
                asset=row.get("asset", ""),
                free=Decimal(
                    str(row.get("availableBalance", row.get("withdrawAvailable", 0)))
                ),
                locked=Decimal(str(row.get("initialMargin", 0))),
                total=Decimal(str(row.get("balance", 0))),
            )
            for row in rows
        ]

    async def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        wire = self._wire_symbol(symbol) if symbol else None
        return cast(list[dict[str, Any]], await self._client.get_positions(symbol=wire))

    async def test_connectivity(self) -> bool:
        return cast(bool, await self._client.test_connectivity())

    async def close(self) -> None:
        await self._client.close()

    async def set_leverage(self, *, symbol: str, leverage: int) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._client.set_leverage(
                symbol=self._wire_symbol(symbol), leverage=leverage
            ),
        )

    async def set_margin_mode(self, *, symbol: str, margin_mode: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._client.set_margin_type(
                symbol=self._wire_symbol(symbol), margin_type=margin_mode
            ),
        )

    async def set_position_mode(self, *, dual_side: bool) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._client.set_position_mode(dual_side_position=dual_side),
        )

    def _parse_order_response(self, raw: dict[str, Any]) -> OrderResponse:
        return _parse_binance_order_response(
            raw,
            exchange="binance_futures",
            status_map=_BINANCE_FUTURES_STATUS_MAP,
            type_map=_BINANCE_FUTURES_TYPE_MAP,
            quote_field="cumQuote",
            timestamp_fields=("updateTime", "time"),
        )
