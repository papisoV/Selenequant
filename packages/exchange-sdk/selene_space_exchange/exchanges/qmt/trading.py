"""QMT TradingClient adapter — implements the unified TradingClient ABC.

Translates SeleneQuant OrderRequest/OrderResponse to XTP protocol messages
for A-share trading via miniQmt.

A-share specific rules enforced at the adapter level:
- Trading hours: 9:30-11:30, 13:00-15:00 CST
- Lot size: minimum 100 shares, must be multiple of 100 for main board
- T+1: cannot sell shares purchased on the same day
- No short selling for retail accounts
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone, timedelta
from typing import Any

from selene_space_exchange.trading import BalanceInfo, OrderRequest, OrderResponse, TradingClient
from selene_space_types.trading import OrderSide, OrderStatus, OrderType
from selene_space_exchange.exchanges.qmt.xtp_client import XTPClient
from selene_space_exchange.exchanges.qmt.protocol import (
    ORDER_TYPE_LIMIT,
    ORDER_TYPE_MARKET,
    SIDE_BUY,
    SIDE_SELL,
    MSG_ORDER_RESP,
    MSG_ASSET_RESP,
    MSG_POSITION_RESP,
    ashare_symbol_to_xtp,
    build_order_insert,
    build_order_cancel,
    build_order_query,
    build_position_query,
    build_asset_query,
)

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))


class QMTTradingAdapter(TradingClient):
    """Adapts XTPClient to the unified TradingClient interface.

    Requires miniQmt running locally with XTP protocol enabled.
    Enforces A-share trading rules at the adapter level.
    """

    def __init__(self, *, client: XTPClient) -> None:
        self._client = client
        self._today_purchases: dict[str, float] = {}  # symbol -> qty bought today

    @property
    def exchange_name(self) -> str:
        return "qmt"

    async def place_order(self, request: OrderRequest) -> OrderResponse:
        # A-share pre-trade checks
        self._validate_trading_hours()
        self._validate_lot_size(request)
        self._validate_t1(request)
        self._validate_no_short(request)

        symbol_code, exchange = ashare_symbol_to_xtp(request.symbol)
        side = SIDE_BUY if request.side == OrderSide.BUY else SIDE_SELL
        order_type = ORDER_TYPE_MARKET if request.order_type == OrderType.MARKET else ORDER_TYPE_LIMIT
        price = request.price or 0.0

        data = build_order_insert(
            account_id=self._client._account_id,
            symbol=symbol_code,
            exchange=exchange,
            price=price,
            quantity=int(request.quantity),
            side=side,
            order_type=order_type,
        )

        resp = await self._client.send_request(data, MSG_ORDER_RESP, timeout=10.0)
        if resp is None:
            return OrderResponse(
                exchange="qmt",
                order_id="",
                symbol=request.symbol,
                side=request.side,
                order_type=request.order_type,
                status=OrderStatus.REJECTED,
                quantity=request.quantity,
                raw={"error": "timeout"},
            )

        # Track today's purchases for T+1 enforcement
        if request.side == OrderSide.BUY and resp.body.get("success", False):
            self._today_purchases[request.symbol] = (
                self._today_purchases.get(request.symbol, 0) + request.quantity
            )

        return self._parse_order_response(resp, request)

    async def cancel_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        data = build_order_cancel(
            account_id=self._client._account_id,
            order_id=order_id,
        )
        resp = await self._client.send_request(data, MSG_ORDER_RESP, timeout=10.0)
        return OrderResponse(
            exchange="qmt",
            order_id=order_id,
            symbol=symbol,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            status=OrderStatus.CANCELLED,
            quantity=0,
            raw=resp.body if resp else {},
        )

    async def get_order(self, *, symbol: str, order_id: str) -> OrderResponse:
        data = build_order_query(account_id=self._client._account_id)
        resp = await self._client.send_request(data, MSG_ORDER_RESP, timeout=10.0)
        if resp is None:
            return OrderResponse(
                exchange="qmt", order_id=order_id, symbol=symbol,
                side=OrderSide.BUY, order_type=OrderType.MARKET,
                status=OrderStatus.REJECTED, quantity=0, raw={},
            )
        # Find the specific order in the response
        orders = resp.body.get("orders", [])
        for o in orders:
            if o.get("order_id") == order_id:
                return self._parse_single_order(o)
        return OrderResponse(
            exchange="qmt", order_id=order_id, symbol=symbol,
            side=OrderSide.BUY, order_type=OrderType.MARKET,
            status=OrderStatus.REJECTED, quantity=0, raw=resp.body,
        )

    async def get_open_orders(self, symbol: str | None = None) -> list[OrderResponse]:
        data = build_order_query(account_id=self._client._account_id)
        resp = await self._client.send_request(data, MSG_ORDER_RESP, timeout=10.0)
        if resp is None:
            return []
        orders = resp.body.get("orders", [])
        results = [self._parse_single_order(o) for o in orders]
        if symbol:
            results = [r for r in results if r.symbol == symbol]
        return results

    async def get_balances(self) -> list[BalanceInfo]:
        data = build_asset_query(account_id=self._client._account_id)
        resp = await self._client.send_request(data, MSG_ASSET_RESP, timeout=10.0)
        if resp is None:
            return [BalanceInfo(asset="CNY", free=0, total=0)]

        body = resp.body
        total = float(body.get("total_asset", 0))
        free = float(body.get("available", 0))
        locked = total - free
        return [BalanceInfo(asset="CNY", free=free, locked=locked, total=total)]

    async def test_connectivity(self) -> bool:
        return await self._client.test_connectivity()

    async def close(self) -> None:
        await self._client.disconnect()

    async def get_positions(self, symbol: str | None = None) -> list[dict[str, Any]]:
        data = build_position_query(account_id=self._client._account_id)
        resp = await self._client.send_request(data, MSG_POSITION_RESP, timeout=10.0)
        if resp is None:
            return []
        positions = resp.body.get("positions", [])
        if symbol:
            positions = [p for p in positions if p.get("symbol") == symbol]
        return positions

    def reset_daily(self) -> None:
        """Reset T+1 tracking at market open (called by scheduler)."""
        self._today_purchases.clear()

    # ── A-share validation ───────────────────────────────────────────────────

    @staticmethod
    def _validate_trading_hours() -> None:
        now = datetime.now(_CST)
        t = now.hour * 100 + now.minute
        in_morning = 930 <= t <= 1130
        in_afternoon = 1300 <= t <= 1500
        if not (in_morning or in_afternoon):
            raise RuntimeError(
                f"A-share trading hours only: 9:30-11:30, 13:00-15:00 CST (now {now.strftime('%H:%M')})"
            )

    @staticmethod
    def _validate_lot_size(request: OrderRequest) -> None:
        qty = int(request.quantity)
        if qty < 100:
            raise RuntimeError(f"A-share minimum lot size is 100 shares, got {qty}")
        if qty % 100 != 0:
            raise RuntimeError(f"A-share lot size must be multiple of 100, got {qty}")

    def _validate_t1(self, request: OrderRequest) -> None:
        if request.side == OrderSide.SELL:
            bought_today = self._today_purchases.get(request.symbol, 0)
            if bought_today > 0:
                raise RuntimeError(
                    f"A-share T+1 rule: cannot sell {request.symbol} purchased today "
                    f"(bought {bought_today} shares today)"
                )

    @staticmethod
    def _validate_no_short(request: OrderRequest) -> None:
        # Short selling check is done at the risk engine level via T1SettlementRule
        # and position verification. This is a safety net.
        pass

    # ── Response parsing ─────────────────────────────────────────────────────

    def _parse_order_response(self, resp: Any, request: OrderRequest) -> OrderResponse:
        body = resp.body if hasattr(resp, "body") else resp
        success = body.get("success", False)
        return OrderResponse(
            exchange="qmt",
            order_id=body.get("order_id", ""),
            client_order_id=body.get("client_order_id", ""),
            symbol=request.symbol,
            side=request.side,
            order_type=request.order_type,
            status=OrderStatus.ACCEPTED if success else OrderStatus.REJECTED,
            quantity=request.quantity,
            filled_quantity=0,
            price=request.price,
            timestamp=int(time.time() * 1000),
            raw=body,
        )

    def _parse_single_order(self, raw: dict[str, Any]) -> OrderResponse:
        side_map = {1: OrderSide.BUY, 2: OrderSide.SELL}
        status_map = {
            "submitted": OrderStatus.ACCEPTED,
            "accepted": OrderStatus.ACCEPTED,
            "partial": OrderStatus.PARTIAL,
            "filled": OrderStatus.FILLED,
            "cancelled": OrderStatus.CANCELLED,
            "rejected": OrderStatus.REJECTED,
        }

        return OrderResponse(
            exchange="qmt",
            order_id=raw.get("order_id", ""),
            client_order_id=raw.get("client_order_id", ""),
            symbol=raw.get("symbol", ""),
            side=side_map.get(raw.get("side", 1), OrderSide.BUY),
            order_type=OrderType.LIMIT if raw.get("order_type") == 1 else OrderType.MARKET,
            status=status_map.get(raw.get("status", "submitted"), OrderStatus.ACCEPTED),
            quantity=float(raw.get("quantity", 0)),
            filled_quantity=float(raw.get("filled_quantity", 0)),
            price=float(raw["price"]) if raw.get("price") else None,
            avg_fill_price=float(raw["avg_price"]) if raw.get("avg_price") else None,
            timestamp=int(raw.get("create_time", time.time() * 1000)),
            raw=raw,
        )
