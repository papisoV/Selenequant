"""AShareExecutionEngine — A-share (China stock) execution via QMTTradingAdapter.

Implements the ExecutionEngine Protocol. Pre-trade checks:
1. Lot-size rounding (multiples of 100, min 100)
2. A-share market hours (9:30-11:30 + 13:00-15:00 CST) + holiday calendar
3. PriceLimitRule (涨跌停) — uses TradeIntent + prev_close context
4. T1SettlementRule — T+1 same-day sell blocked

Position schema note: Position has `opened_at` (not `entry_ts`) and no
`account_id` field — engine filters by `opened_at` only; account
association is via client_order_id prefix on orders, not on positions.
"""

from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any

from selene_space_exchange.exceptions import PreTradeRiskBlockError
from selene_space_exchange.trading import OrderRequest
from selene_space_risk.market_rules import PriceLimitRule, T1SettlementRule
from selene_space_types.agent import TradeIntent
from selene_space_types.trading import (
    ExecutionMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
)

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8))

# A-share holiday calendar (approximated for v2; ranges for multi-day
# holidays like Spring Festival/National Day use representative dates).
_ASHARE_HOLIDAYS: set[tuple[int, int]] = {
    (1, 1),    # New Year's Day
    (2, 10),   # Spring Festival (approximated; actual date varies)
    (2, 11),
    (2, 12),
    (2, 13),
    (2, 14),
    (4, 4),    # Qingming Festival
    (5, 1),    # Labour Day
    (5, 2),
    (5, 3),
    (6, 10),   # Dragon Boat Festival
    (9, 17),   # Mid-Autumn Festival
    (10, 1),   # National Day
    (10, 2),
    (10, 3),
    (10, 4),
    (10, 5),
    (10, 6),
    (10, 7),
}


def _to_cst(ts: float) -> datetime:
    return datetime.fromtimestamp(ts, tz=_CST)


class AShareExecutionEngine:
    """A-share execution engine — implements ExecutionEngine Protocol."""

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
        self._price_limit_rule = PriceLimitRule()
        self._t1_rule = T1SettlementRule()

    @property
    def mode(self) -> ExecutionMode:
        return ExecutionMode.LIVE

    @property
    def exchange(self) -> str:
        return self._client.exchange_name

    async def start(self) -> None:
        logger.info("AShareExecutionEngine started for %s", self.exchange)

    async def stop(self) -> None:
        logger.info("AShareExecutionEngine stopped for %s", self.exchange)

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
        rounded_qty = self._round_to_lot(quantity)
        self._check_market_hours(now_ts)

        # Build TradeIntent (verified schema: agent.py:55-72 — no stop_price,
        # no account_id; reasoning/confidence/timestamp are required).
        intent = TradeIntent(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=float(rounded_qty),
            price=price,
            reasoning="ashare_engine_pre_trade_check",
            confidence=1.0,
            timestamp=int(now_ts * 1000),
        )
        context = self._build_rule_context(symbol)

        price_alert = self._price_limit_rule.check(intent, context)
        if price_alert is not None:
            raise PreTradeRiskBlockError(
                price_alert.message,
                blocked_by="ashare_price_limit",
                reason=price_alert.rule_id,
            )
        t1_alert = self._t1_rule.check(intent, context)
        if t1_alert is not None:
            raise PreTradeRiskBlockError(
                t1_alert.message,
                blocked_by="ashare_t1_settlement",
                reason=t1_alert.rule_id,
            )

        client_order_id = f"{account_id}-{uuid.uuid4().hex[:8]}"
        request = OrderRequest(
            symbol=symbol,
            side=side,
            order_type=order_type,
            quantity=float(rounded_qty),
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
            quantity=float(rounded_qty),
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

    async def get_positions(self, account_id: str) -> list[Position]:
        if self._state_repo is None:
            return []
        return list(self._state_repo.load_positions())

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

    def _round_to_lot(self, qty: float) -> int:
        rounded = (int(qty) // 100) * 100
        if rounded < 100:
            raise PreTradeRiskBlockError(
                f"A-share lot size: quantity {qty} rounds to {rounded}, minimum is 100",
                blocked_by="ashare_lot_size",
                reason="below_min_lot",
            )
        return rounded

    def _check_market_hours(self, ts: float) -> None:
        cst = _to_cst(ts)
        if cst.weekday() >= 5:
            raise PreTradeRiskBlockError(
                f"A-share market closed: weekend ({cst.strftime('%A')})",
                blocked_by="ashare_market_hours",
                reason="weekend",
            )
        md = (cst.month, cst.day)
        if md in _ASHARE_HOLIDAYS:
            raise PreTradeRiskBlockError(
                f"A-share market closed: holiday {cst.strftime('%Y-%m-%d')}",
                blocked_by="ashare_market_hours",
                reason="holiday",
            )
        hour_min = cst.hour * 100 + cst.minute
        in_morning = 930 <= hour_min <= 1130
        in_afternoon = 1300 <= hour_min <= 1500
        if not (in_morning or in_afternoon):
            raise PreTradeRiskBlockError(
                f"A-share market closed: outside 09:30-11:30/13:00-15:00 CST "
                f"(now {cst.strftime('%H:%M')} CST)",
                blocked_by="ashare_market_hours",
                reason="outside_hours",
            )

    def _build_rule_context(self, symbol: str) -> dict[str, Any]:
        prev_close = self._fetch_prev_close(symbol)
        today_purchases = self._fetch_today_purchases(symbol)
        return {
            "prev_close": {symbol: prev_close} if prev_close is not None else {},
            "stock_names": {},
            "today_purchases": today_purchases,
        }

    def _fetch_prev_close(self, symbol: str) -> float | None:
        if self._state_repo is None or not hasattr(self._state_repo, "load_prev_closes"):
            return None
        try:
            closes = self._state_repo.load_prev_closes()
            return closes.get(symbol) if isinstance(closes, dict) else None
        except Exception:
            logger.warning("ashare: failed to load prev_close for %s", symbol, exc_info=True)
            return None

    def _fetch_today_purchases(self, symbol: str) -> dict[str, float]:
        """Return {symbol: qty_bought_today}.

        Position schema (verified trading.py:209-262) has NO account_id and
        NO entry_ts; it uses `opened_at` for open time and `side` for
        direction. We filter by `opened_at >= today_midnight_cst` and
        side == BUY.
        """
        if self._state_repo is None:
            return {}
        try:
            positions = self._state_repo.load_positions()
        except Exception:
            logger.warning("ashare: failed to load positions", exc_info=True)
            return {}
        if not positions:
            return {}
        today_midnight = datetime.now(_CST).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).timestamp()
        purchases: dict[str, float] = {}
        for pos in positions:
            opened_at = getattr(pos, "opened_at", None)
            if opened_at is None or opened_at < today_midnight:
                continue
            if getattr(pos, "side", None) != OrderSide.BUY:
                continue
            sym = getattr(pos, "symbol", None)
            if sym is None:
                continue
            purchases[sym] = purchases.get(sym, 0) + float(getattr(pos, "quantity", 0))
        return purchases
