"""Alpaca TradingClient adapter — implements the unified TradingClient ABC.

Translates SeleneQuant OrderRequest/OrderResponse to Alpaca's REST API format.
Supports paper and live trading modes.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from selene_space_exchange.trading import BalanceInfo, OrderRequest, OrderResponse, TradingClient
from selene_space_types.trading import OrderSide, OrderStatus, OrderType

logger = logging.getLogger(__name__)


class AlpacaTradingAdapter(TradingClient):
    """Adapts AlpacaRESTClient to the unified TradingClient interface.

    Supports US stock market orders (market, limit). Alpaca paper trading
    is used by default for safety.
    """

    def __init__(self, client: Any) -> None:
        self._client = client

    @property
    def exchange_name(self) -> str:
        return "alpaca"

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        body = self._build_order_body(request)
        raw = await self._client.place_order(body)
        return self._parse_order_response(raw, request)

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raw = await self._client.cancel_order(order_id)
        return OrderResponse(
            exchange="alpaca",
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED,
            quantity=0,
            raw=raw,
        )

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        raw = await self._client.get_order(order_id)
        return self._parse_single_order(raw)

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        raw_list = await self._client.get_open_orders(symbol=symbol)
        return [self._parse_single_order(r) for r in raw_list]

    async def get_balances(self) -> list[BalanceInfo]:
        raw = await self._client.get_account()
        cash = float(raw.get("cash", 0))
        equity = float(raw.get("equity", 0))
        buying_power = float(raw.get("buying_power", 0))
        return [
            BalanceInfo(asset="USD", free=cash, locked=equity - cash, total=equity),
            BalanceInfo(asset="BUYING_POWER", free=buying_power, total=buying_power),
        ]

    async def test_connectivity(self) -> bool:
        return await self._client.test_connectivity()

    async def close(self) -> None:
        await self._client.close()

    async def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        positions = await self._client.get_positions()
        if symbol:
            return [p for p in positions if p.get("symbol") == symbol]
        return positions

    # ── Internal ─────────────────────────────────────────────────────────────

    def _build_order_body(self, request: OrderRequest) -> dict[str, Any]:
        body: dict[str, Any] = {
            "symbol": request.symbol,
            "qty": str(int(request.quantity)),
            "side": request.side.value.lower(),
        }

        if request.order_type == OrderType.MARKET:
            body["type"] = "market"
            body["time_in_force"] = "day"
        elif request.order_type == OrderType.LIMIT:
            body["type"] = "limit"
            body["time_in_force"] = request.time_in_force.lower() or "day"
            if request.price is not None:
                body["limit_price"] = str(request.price)
        elif request.order_type == OrderType.STOP_MARKET:
            body["type"] = "stop"
            body["time_in_force"] = "day"
            if request.stop_price is not None:
                body["stop_price"] = str(request.stop_price)
        elif request.order_type == OrderType.STOP_LIMIT:
            body["type"] = "stop_limit"
            body["time_in_force"] = "day"
            if request.stop_price is not None:
                body["stop_price"] = str(request.stop_price)
            if request.price is not None:
                body["limit_price"] = str(request.price)
        else:
            body["type"] = "market"
            body["time_in_force"] = "day"

        if request.client_order_id:
            body["client_order_id"] = request.client_order_id

        return body

    def _parse_order_response(self, raw: dict[str, Any], request: OrderRequest) -> OrderResponse:
        return OrderResponse(
            exchange="alpaca",
            order_id=raw.get("id", ""),
            client_order_id=raw.get("client_order_id", ""),
            symbol=raw.get("symbol", request.symbol),
            side=request.side,
            order_type=request.order_type,
            status=self._map_status(raw.get("status", "pending_new")),
            quantity=float(raw.get("qty", request.quantity)),
            filled_quantity=float(raw.get("filled_qty", 0)),
            price=float(raw["limit_price"]) if raw.get("limit_price") else None,
            avg_fill_price=float(raw["filled_avg_price"]) if raw.get("filled_avg_price") else None,
            timestamp=int(time.time() * 1000),
            raw=raw,
        )

    def _parse_single_order(self, raw: dict[str, Any]) -> OrderResponse:
        side_map = {"buy": OrderSide.BUY, "sell": OrderSide.SELL}
        type_map = {
            "market": OrderType.MARKET,
            "limit": OrderType.LIMIT,
            "stop": OrderType.STOP_MARKET,
            "stop_limit": OrderType.STOP_LIMIT,
        }

        return OrderResponse(
            exchange="alpaca",
            order_id=raw.get("id", ""),
            client_order_id=raw.get("client_order_id", ""),
            symbol=raw.get("symbol", ""),
            side=side_map.get(raw.get("side", "buy"), OrderSide.BUY),
            order_type=type_map.get(raw.get("type", "market"), OrderType.MARKET),
            status=self._map_status(raw.get("status", "new")),
            quantity=float(raw.get("qty", 0)),
            filled_quantity=float(raw.get("filled_qty", 0)),
            price=float(raw["limit_price"]) if raw.get("limit_price") else None,
            avg_fill_price=float(raw["filled_avg_price"]) if raw.get("filled_avg_price") else None,
            timestamp=int(raw.get("submitted_at", time.time() * 1000)),
            raw=raw,
        )

    @staticmethod
    def _map_status(alpaca_status: str) -> OrderStatus:
        status_map = {
            "new": OrderStatus.ACCEPTED,
            "partially_filled": OrderStatus.PARTIAL,
            "filled": OrderStatus.FILLED,
            "done_for_day": OrderStatus.ACCEPTED,
            "canceled": OrderStatus.CANCELLED,
            "cancelled": OrderStatus.CANCELLED,
            "expired": OrderStatus.EXPIRED,
            "replaced": OrderStatus.ACCEPTED,
            "pending_new": OrderStatus.ACCEPTED,
            "pending_replace": OrderStatus.ACCEPTED,
            "rejected": OrderStatus.REJECTED,
            "stopped": OrderStatus.CANCELLED,
        }
        return status_map.get(alpaca_status, OrderStatus.ACCEPTED)
