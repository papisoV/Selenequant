"""P1-2: self-trade prevention wired into LiveExecutionEngine.

Verifies that LiveExecutionEngine maintains an OrderTracker and feeds it
into RiskEngine.pre_check via context, so SelfTradePreventionRule can
detect cross-strategy conflicts before the order reaches the exchange.

Tests cover:
1. After a successful place_order, the order is tracked.
2. After cancel_order, the order is removed from the tracker.
3. When a new order would self-trade against a tracked order, pre_check
   receives the tracker and SelfTradePreventionRule fires CRITICAL.
4. Same-strategy orders do NOT trigger self-trade (rule skips them).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_risk.engine import RiskEngine
from selene_space_risk.self_trade import (
    ActiveOrder,
    SelfTradePreventionRule,
)
from selene_space_types.trading import OrderSide, OrderStatus, OrderType


class _MockJournal:
    def __init__(self) -> None:
        self.intents: list[dict] = []
        self.failed: list[tuple[str, str]] = []

    def append_intent(self, **kwargs) -> None:
        self.intents.append(kwargs)

    def mark_failed(self, client_order_id: str, reason: str = "") -> None:
        self.failed.append((client_order_id, reason))

    def mark_submitted(self, client_order_id: str, exchange_order_id: str) -> None:
        pass

    def mark_confirmed(self, client_order_id: str) -> None:
        pass


def _make_engine(*, risk_engine=None) -> tuple[LiveExecutionEngine, AsyncMock, _MockJournal]:
    journal = _MockJournal()
    client = SimpleNamespace(
        exchange_name="test",
        place_order=AsyncMock(return_value=SimpleNamespace(
            order_id="ex-1",
            status=OrderStatus.ACCEPTED,
            filled_quantity=0.0,
            avg_fill_price=0.0,
        )),
        cancel_order=AsyncMock(return_value=None),
    )
    engine = LiveExecutionEngine(
        trading_client=client,
        journal=journal,
        state_repo=None,
        risk_engine=risk_engine,
    )
    return engine, client, journal


@pytest.mark.asyncio
async def test_order_tracked_after_successful_place() -> None:
    """A non-terminal order placed successfully must be added to the tracker."""
    engine, _client, _journal = _make_engine()

    await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
        strategy_id="acct-1",
    )

    # Tracker should now have one active order.
    assert len(engine._order_tracker._orders) == 1
    tracked = next(iter(engine._order_tracker._orders.values()))
    assert tracked.symbol == "BTCUSDT"
    assert tracked.side == OrderSide.BUY
    assert tracked.strategy_id == "acct-1"


@pytest.mark.asyncio
async def test_order_removed_from_tracker_after_cancel() -> None:
    """cancel_order must remove the order from the self-trade tracker."""
    engine, _client, _journal = _make_engine()

    order = await engine.place_order(
        account_id="acct-1",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
    )
    assert len(engine._order_tracker._orders) == 1

    await engine.cancel_order(order.id)

    assert len(engine._order_tracker._orders) == 0


@pytest.mark.asyncio
async def test_self_trade_rule_fires_on_conflicting_order() -> None:
    """A buy that would match an existing tracked sell → SelfTradePreventionRule CRITICAL.

    Setup: pre-populate the tracker with a SELL order from strategy "A".
    Then place a BUY from strategy "B" at a price that overlaps.
    The risk gate must call pre_check, which runs SelfTradePreventionRule,
    which returns CRITICAL → decision.allowed=False → order blocked.
    """
    rule = SelfTradePreventionRule(mode="block")
    risk_engine = RiskEngine(rules=[rule])

    engine, client, _journal = _make_engine(risk_engine=risk_engine)

    # Pre-populate tracker with an opposing sell from a different "strategy".
    engine._order_tracker.add(ActiveOrder(
        order_id="existing-sell-1",
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        strategy_id="strategy-A",
        quantity=0.5,
        price=50_000.0,
    ))

    # Now place a BUY at 50_000 from strategy "strategy-B".
    # This would match the existing sell → self-trade → must be blocked.
    from selene_space_exchange.exceptions import PreTradeRiskBlockError

    with pytest.raises(PreTradeRiskBlockError) as exc_info:
        await engine.place_order(
            account_id="acct-1",
            symbol="BTCUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            quantity=0.1,
            price=50_000.0,
            strategy_id="strategy-B",
        )

    assert "self" in str(exc_info.value).lower() or "自交易" in str(exc_info.value)
    client.place_order.assert_not_awaited()


@pytest.mark.asyncio
async def test_same_strategy_does_not_trigger_self_trade() -> None:
    """Orders from the same strategy do NOT trigger self-trade (rule skips them)."""
    rule = SelfTradePreventionRule(mode="block")
    risk_engine = RiskEngine(rules=[rule])

    engine, client, _journal = _make_engine(risk_engine=risk_engine)

    # Pre-populate with same-strategy sell.
    engine._order_tracker.add(ActiveOrder(
        order_id="own-sell-1",
        symbol="BTCUSDT",
        side=OrderSide.SELL,
        strategy_id="strategy-A",
        quantity=0.5,
        price=50_000.0,
    ))

    # Buy from the SAME strategy → not a self-trade conflict.
    order = await engine.place_order(
        account_id="strategy-A",
        symbol="BTCUSDT",
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        quantity=0.1,
        price=50_000.0,
    )

    # Order proceeds to REST.
    client.place_order.assert_awaited_once()
    assert order.symbol == "BTCUSDT"
