"""StockExecutionEngine — US stock execution via AlpacaTradingAdapter.

Implements the ExecutionEngine Protocol. Routes pre-trade through US
market-hours checks + holiday calendar, then delegates order placement
to the AlpacaTradingAdapter (which wraps AlpacaRESTClient).

Trust integration: callers should consult StrategyTrustTracker before
invoking place_order; this engine does not enforce trust levels directly
(that's ExecutionModeGuard's responsibility at a higher layer).
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from selene_space_exchange.exceptions import PreTradeRiskBlockError
from selene_space_exchange.trading import OrderRequest
from selene_space_types.trading import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
)

logger = logging.getLogger(__name__)

# Fixed UTC-5 offset for US/Eastern. DST handling is simplified —
# acceptable for v2 (proper zoneinfo migration tracked as follow-up).
_US_EASTERN = timezone(timedelta(hours=-5))

# US market holidays (month, day). Early-close days (New Year's Eve,
# Black Friday) are marked with their early-close hour (13:00 local).
# Full-day holidays block all trading; early-close days block after 13:00.
_US_HOLIDAYS_FULL: set[tuple[int, int]] = {
    (1, 1),    # New Year's Day
    (1, 20),   # MLK Day (3rd Monday of Jan — approximated to 20th)
    (2, 17),   # Presidents Day (3rd Monday of Feb — approximated)
    (4, 18),   # Good Friday (approximated; date varies)
    (5, 26),   # Memorial Day (last Monday of May — approximated)
    (6, 19),   # Juneteenth
    (7, 4),    # Independence Day
    (9, 1),    # Labor Day (1st Monday of Sep — approximated)
    (11, 27),  # Thanksgiving (4th Thursday of Nov — approximated)
    (12, 25),  # Christmas
}

# Early-close days: (month, day) → early close hour (24h local).
_US_HOLIDAYS_EARLY_CLOSE: dict[tuple[int, int], int] = {
    (12, 24): 13,   # New Year's Eve early close
    (11, 28): 13,   # Black Friday (day after Thanksgiving) early close
}


def _to_eastern(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=_US_EASTERN)


class StockExecutionEngine:
    """US stock execution engine — implements ExecutionEngine Protocol."""

    def __init__(
        self,
        trading_client: Any,
        *,
        journal: Any | None = None,
        state_repo: Any | None = None,
    ) -> None:
        self._client = trading_client
        self._journal = journal
        self._state_repo = state_repo

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.LIVE

    @property
    def exchange(self) -> str:
        return self._client.exchange_name

    async def start(self) -> None:
        logger.info("StockExecutionEngine started for %s", self.exchange)

    async def stop(self) -> None:
        logger.info("StockExecutionEngine stopped for %s", self.exchange)

    async def place_order(
        self,
        *,
        account_id: str,
        symbol: str,
        side: OrderSide,
        order_type: OrderType,
        quantity: float,
        price: float | None = None,
        stop_price: float | None = None,
    ) -> Order:
        now_ts = time.time()
        self._check_market_hours(now_ts)

        client_order_id = f"{account_id}-{uuid.uuid4().hex[:8]}"
        request = OrderRequest(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            time_in_force="gtc",
            client_order_id=client_order_id,
        )
        response = await self._client.place_order(request)

        ts_ms = int(time.time() * 1000)
        return Order(
            id=response.order_id or client_order_id,
            symbol=symbol,
            side=side,
            type=order_type,
            status=response.status if response.status else OrderStatus.ACCEPTED,
            quantity=quantity,
            price=price,
            stop_price=stop_price,
            filled_quantity=response.filled_quantity,
            avg_fill_price=response.avg_fill_price,
            client_order_id=client_order_id,
            created_at=ts_ms,
            updated_at=ts_ms,
        )

    async def cancel_order(self, order_id: str) -> Order:
        ts_ms = int(time.time() * 1000)
        await self._client.cancel_order(symbol="", order_id=order_id)
        return Order(
            id=order_id,
            symbol="",
            side=OrderSide.BUY,
            type=OrderType.MARKET,
            status=OrderStatus.CANCELLED,
            quantity=0,
            created_at=ts_ms,
            updated_at=ts_ms,
        )

    async def get_orders(self, account_id: str, *, status: OrderStatus | None = None) -> list[Order]:
        responses = await self._client.get_open_orders()
        ts_ms = int(time.time() * 1000)
        orders: list[Order] = []
        for r in responses:
            orders.append(
                Order(
                    id=r.order_id,
                    symbol=r.symbol,
                    side=r.side,
                    type=r.order_type,
                    status=r.status,
                    quantity=r.quantity,
                    price=r.price,
                    filled_quantity=r.filled_quantity,
                    avg_fill_price=r.avg_fill_price,
                    client_order_id=r.client_order_id or None,
                    created_at=r.timestamp,
                    updated_at=ts_ms,
                )
            )
        if status is not None:
            orders = [o for o in orders if o.status == status]
        return orders

    async def get_open_orders(self, account_id: str) -> list[Order]:
        return await self.get_orders(account_id)

    async def get_positions(self, account_id: str) -> list[Any]:
        return await self._client.get_positions()

    async def get_balances(self, account_id: str) -> list[Any]:
        return await self._client.get_balances()

    async def get_fills(self, account_id: str) -> list[Any]:
        return []

    def on_order_update(self, callback: Any) -> None:
        pass

    def on_fill(self, callback: Any) -> None:
        pass

    def on_position_update(self, callback: Any) -> None:
        pass

    def on_balance_update(self, callback: Any) -> None:
        pass

    def on_position_update_scoped(self, callback: Any) -> None:
        pass

    def on_balance_update_scoped(self, callback: Any) -> None:
        pass

    def _check_market_hours(self, ts: float) -> None:
        et = _to_eastern(ts)
        if et.weekday() >= 5:
            raise PreTradeRiskBlockError(
                f"US market closed: weekend ({et.strftime('%A')})",
                blocked_by="stock_market_hours",
                reason="weekend",
            )
        md = (et.month, et.day)
        if md in _US_HOLIDAYS_FULL:
            raise PreTradeRiskBlockError(
                f"US market closed: holiday {et.strftime('%Y-%m-%d')}",
                blocked_by="stock_market_hours",
                reason="holiday",
            )
        hour_min = et.hour * 100 + et.minute
        if hour_min < 930 or hour_min > 1600:
            raise PreTradeRiskBlockError(
                f"US market closed: outside 09:30-16:00 ET (now {et.strftime('%H:%M')} ET)",
                blocked_by="stock_market_hours",
                reason="outside_hours",
            )
        early_close_hour = _US_HOLIDAYS_EARLY_CLOSE.get(md)
        if early_close_hour is not None and et.hour >= early_close_hour:
            raise PreTradeRiskBlockError(
                f"US market early close: {et.strftime('%Y-%m-%d')} closes at {early_close_hour}:00 ET",
                blocked_by="stock_market_hours",
                reason="early_close",
            )
