"""T2: RiskEngine pre_check — CRITICAL fires flatten_hook.fire_and_forget."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from selene_space_risk.engine import RiskEngine
from selene_space_risk.flatten_hook import FlattenHook
from selene_space_types.agent import TradeIntent
from selene_space_types.risk import RiskAlert, RiskLevel
from selene_space_types.trading import OrderSide, OrderType


def _make_intent(**overrides: Any) -> TradeIntent:
    defaults = {
        "symbol": "BTC/USDT",
        "side": OrderSide.BUY,
        "quantity": 0.1,
        "price": 67000.0,
        "order_type": OrderType.MARKET,
        "reasoning": "test",
        "confidence": 0.8,
        "risk_params": {"stop_loss": 65000.0, "take_profit": 70000.0},
        "timestamp": int(time.time() * 1000),
    }
    defaults.update(overrides)
    return TradeIntent(**defaults)


class _CriticalRule:
    rule_id = "critical_rule"
    enabled = True

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        return RiskAlert(
            rule_id=self.rule_id,
            level=RiskLevel.CRITICAL,
            message="Critical block from test rule",
            context={},
            timestamp=int(time.time() * 1000),
        )


class _PassRule:
    rule_id = "pass_all"
    enabled = True

    def check(self, intent: TradeIntent, context: dict[str, Any]) -> RiskAlert | None:
        return None


async def _drain(hook: FlattenHook) -> None:
    """Let any scheduled fire_and_forget task run to completion."""
    while hook._pending_tasks:
        await asyncio.sleep(0)


def test_critical_fires_flatten_scoped_to_strategy() -> None:
    """CRITICAL pre_check triggers fire_and_forget carrying ctx strategy_id scope."""

    fired: list[str] = []

    async def _run() -> None:
        hook = FlattenHook()

        async def _flatten() -> None:
            fired.append("funding_arb")

        hook.register(_flatten, strategy_id="funding_arb")
        engine = RiskEngine(rules=[_CriticalRule()], flatten_hook=hook)
        decision = engine.pre_check(_make_intent(), context={"strategy_id": "funding_arb"})
        assert decision.allowed is False
        await _drain(hook)

    asyncio.run(_run())
    assert fired == ["funding_arb"]


def test_critical_does_not_fire_other_strategy_scope() -> None:
    """A funding_arb-scoped callback must NOT fire for a different strategy_id."""

    fired: list[str] = []

    async def _run() -> None:
        hook = FlattenHook()

        async def _flatten() -> None:
            fired.append("funding_arb")

        hook.register(_flatten, strategy_id="funding_arb")
        engine = RiskEngine(rules=[_CriticalRule()], flatten_hook=hook)
        engine.pre_check(_make_intent(), context={"strategy_id": "grid"})
        await _drain(hook)

    asyncio.run(_run())
    assert fired == []


def test_no_critical_no_flatten() -> None:
    """Passing rules never fire the flatten hook."""

    fired: list[str] = []

    async def _run() -> None:
        hook = FlattenHook()

        async def _flatten() -> None:
            fired.append("x")

        hook.register(_flatten)
        engine = RiskEngine(rules=[_PassRule()], flatten_hook=hook)
        engine.pre_check(_make_intent(), context={"strategy_id": "funding_arb"})
        await _drain(hook)

    asyncio.run(_run())
    assert fired == []
