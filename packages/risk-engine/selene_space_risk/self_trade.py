"""Self-trade prevention — detect and block orders that would match against
existing orders from the same account but different strategies.

In multi-strategy setups, two strategies may simultaneously submit
opposing orders on the same symbol (e.g. strategy A buys BTC while
strategy B sells BTC). On a real exchange this results in a self-trade
which is either rejected or results in wash trading penalties.

This module provides:
  1. SelfTradePreventionRule — risk rule that warns/blocks potential self-trades
  2. OrderBook — lightweight order tracker for cross-strategy conflict detection
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.trading import OrderSide


def _now_ms() -> int:
    import time
    return int(time.time() * 1000)


def _alert(rule_id: str, level: RiskLevel, message: str, **context: Any) -> RiskAlert:
    return RiskAlert(
        rule_id=rule_id,
        level=level,
        message=message,
        context=context,
        timestamp=_now_ms(),
    )


@dataclass(frozen=True)
class ActiveOrder:
    """Tracked active order for self-trade detection."""

    order_id: str
    symbol: str
    side: OrderSide
    strategy_id: str
    quantity: float
    price: float | None = None


class OrderTracker:
    """Track active orders across strategies for self-trade detection.

    Thread-safety: not thread-safe. Caller must synchronize if used
    from multiple threads.
    """

    def __init__(self) -> None:
        self._orders: dict[str, ActiveOrder] = {}

    def add(self, order: ActiveOrder) -> None:
        """Register an active order."""
        self._orders[order.order_id] = order

    def remove(self, order_id: str) -> None:
        """Remove an order (filled, cancelled, or expired)."""
        self._orders.pop(order_id, None)

    def get_opposing_orders(
        self,
        symbol: str,
        side: OrderSide,
        strategy_id: str,
        price: float | None = None,
    ) -> list[ActiveOrder]:
        """Find orders that could match against a new order.

        A self-trade conflict exists when:
        - Same symbol
        - Opposite side (buy vs sell)
        - Different strategy_id
        - Price levels overlap (if limit orders with price info)

        Args:
            symbol: Trading pair of the new order.
            side: Side of the new order.
            strategy_id: Strategy submitting the new order.
            price: Limit price of the new order (None for market orders).

        Returns:
            List of opposing ActiveOrders that could self-trade.
        """
        opposing_side = OrderSide.SELL if side == OrderSide.BUY else OrderSide.BUY
        conflicts: list[ActiveOrder] = []

        for order in self._orders.values():
            if order.symbol != symbol:
                continue
            if order.side != opposing_side:
                continue
            if order.strategy_id == strategy_id:
                continue

            # For limit orders, check price overlap
            if price is not None and order.price is not None:
                # Buy at price >= Sell at order.price → match
                if side == OrderSide.BUY and price >= order.price:
                    conflicts.append(order)
                # Sell at price <= Buy at order.price → match
                elif side == OrderSide.SELL and price <= order.price:
                    conflicts.append(order)
            else:
                # Market order or no price info — assume conflict
                conflicts.append(order)

        return conflicts

    @property
    def active_count(self) -> int:
        return len(self._orders)

    def clear(self) -> None:
        """Remove all tracked orders."""
        self._orders.clear()


class SelfTradePreventionRule:
    """Risk rule that detects and prevents self-trading across strategies.

    Checks if a new order would match against an existing order from a
    different strategy on the same account. Returns CRITICAL if a
    self-trade would occur (hard block), or WARNING if a potential
    conflict exists.

    Context keys:
        order_tracker (OrderTracker): The shared order tracker instance.
        strategy_id (str): ID of the strategy submitting the order.
    """

    def __init__(
        self,
        mode: str = "block",
        *,
        enabled: bool = True,
    ) -> None:
        """Initialize self-trade prevention rule.

        Args:
            mode: Prevention mode.
                "block" — hard block (CRITICAL), trade is denied.
                "warn" — soft warning (WARNING), trade proceeds.
                "cancel_oldest" — cancel the older conflicting order (WARNING).
            enabled: Whether the rule is active.
        """
        if mode not in ("block", "warn", "cancel_oldest"):
            raise ValueError(f"mode must be 'block', 'warn', or 'cancel_oldest', got '{mode}'")
        self._mode = mode
        self._enabled = enabled

    @property
    def rule_id(self) -> str:
        return "self_trade_prevention"

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        """Check if the intent would cause a self-trade.

        Args:
            intent: The trade intent to check.
            context: Must contain 'order_tracker' and 'strategy_id'.

        Returns:
            RiskAlert if a self-trade conflict is detected, None otherwise.
        """
        if not self._enabled:
            return None

        tracker: OrderTracker | None = context.get("order_tracker")
        if not isinstance(tracker, OrderTracker):
            return None

        strategy_id: str = context.get("strategy_id", "")
        if not strategy_id:
            return None

        side = OrderSide.BUY if intent.side == "buy" else OrderSide.SELL
        price = intent.price

        conflicts = tracker.get_opposing_orders(
            symbol=intent.symbol,
            side=side,
            strategy_id=strategy_id,
            price=price,
        )

        if not conflicts:
            return None

        conflict_ids = [o.order_id for o in conflicts]
        conflict_strategies = [o.strategy_id for o in conflicts]

        if self._mode == "block":
            return _alert(
                self.rule_id, RiskLevel.CRITICAL,
                f"自交易拦截：策略 {strategy_id} 的 {side.value} 单会与策略 {', '.join(conflict_strategies)} 的挂单成交",
                strategy_id=strategy_id,
                conflicting_orders=conflict_ids,
                conflicting_strategies=conflict_strategies,
                mode=self._mode,
            )

        if self._mode == "cancel_oldest":
            return _alert(
                self.rule_id, RiskLevel.WARNING,
                f"自交易检测：将取消较早的冲突订单 {conflict_ids[0]}（策略 {conflict_strategies[0]}）",
                strategy_id=strategy_id,
                conflicting_orders=conflict_ids,
                conflicting_strategies=conflict_strategies,
                mode=self._mode,
            )

        # mode == "warn"
        return _alert(
            self.rule_id, RiskLevel.WARNING,
            f"自交易警告：策略 {strategy_id} 的 {side.value} 单可能与策略 {', '.join(conflict_strategies)} 的挂单成交",
            strategy_id=strategy_id,
            conflicting_orders=conflict_ids,
            conflicting_strategies=conflict_strategies,
            mode=self._mode,
        )
