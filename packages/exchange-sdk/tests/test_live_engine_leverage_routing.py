"""F-A-test live_engine unit 层: 验证 LiveExecutionEngine.place_order(leverage=N) 把
leverage 透传到 risk_engine.pre_check 的 intent.risk_params['leverage'] 和 context['leverage']。

只覆盖 live_engine 内部 routing (place_order → TradeIntent.risk_params + context dict)。
不覆盖 HTTP 层契约 (req.leverage → engine.place_order kwargs),见
services/local-api/tests/test_api_live_place_order.py。

继承 exchange-sdk/tests/conftest.py 的 autouse fixture:
  - SELENE_ENABLE_REAL_TRADING=1 (绕过 Phase 5.5-P0.5 安全网)
  - _auto_approve_live_engine (绕过 Phase 6 first-trade approval gate)
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from selene_space_exchange.exceptions import PreTradeRiskBlockError
from selene_space_exchange.execution.live_engine import LiveExecutionEngine
from selene_space_types.trading import OrderSide, OrderType


class _MockRiskEngine:
    """Mock risk engine that records every pre_check call and short-circuits.

    Returns allowed=False to ensure place_order raises PreTradeRiskBlockError
    BEFORE the exchange REST call — so we can assert intent/context without
    needing a real exchange client.
    """

    def __init__(self) -> None:
        self.pre_check_calls: list[tuple] = []

    def pre_check(self, intent, context=None):
        self.pre_check_calls.append((intent, context))
        return SimpleNamespace(
            allowed=False,
            level=SimpleNamespace(value="critical"),
            reason="test short-circuit",
            alerts=[],
            timestamp=0,
        )


def _make_engine(risk_engine: _MockRiskEngine) -> LiveExecutionEngine:
    client = SimpleNamespace(
        exchange_name="test",
        place_order=AsyncMock(),  # should never be called
    )
    return LiveExecutionEngine(trading_client=client, risk_engine=risk_engine)


@pytest.mark.asyncio
async def test_place_order_routes_leverage_to_intent_risk_params() -> None:
    """leverage=10 → intent.risk_params['leverage'] == 10."""
    risk = _MockRiskEngine()
    engine = _make_engine(risk)

    with pytest.raises(PreTradeRiskBlockError):
        await engine.place_order(
            account_id="acct-1",
            symbol="SOLUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.066,
            leverage=10,
        )

    assert len(risk.pre_check_calls) == 1
    intent, context = risk.pre_check_calls[0]
    assert intent.risk_params == {"leverage": 10}


@pytest.mark.asyncio
async def test_place_order_routes_leverage_to_context() -> None:
    """leverage=10 → context['leverage'] == 10 and context['portfolio_leverage'] == 10.0."""
    risk = _MockRiskEngine()
    engine = _make_engine(risk)

    with pytest.raises(PreTradeRiskBlockError):
        await engine.place_order(
            account_id="acct-1",
            symbol="SOLUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.066,
            leverage=10,
        )

    intent, context = risk.pre_check_calls[0]
    assert context["leverage"] == 10
    assert context["portfolio_leverage"] == 10.0


@pytest.mark.asyncio
async def test_place_order_default_leverage_is_one() -> None:
    """不传 leverage → intent.risk_params['leverage'] == 1 (default)."""
    risk = _MockRiskEngine()
    engine = _make_engine(risk)

    with pytest.raises(PreTradeRiskBlockError):
        await engine.place_order(
            account_id="acct-1",
            symbol="SOLUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.066,
        )

    intent, context = risk.pre_check_calls[0]
    assert intent.risk_params == {"leverage": 1}
    assert context["leverage"] == 1


@pytest.mark.asyncio
async def test_place_order_no_rest_call_when_risk_blocks() -> None:
    """risk_engine blocks → no exchange REST call (sanity check for test setup)."""
    risk = _MockRiskEngine()
    engine = _make_engine(risk)

    with pytest.raises(PreTradeRiskBlockError):
        await engine.place_order(
            account_id="acct-1",
            symbol="SOLUSDT",
            side=OrderSide.BUY,
            order_type=OrderType.MARKET,
            quantity=0.066,
            leverage=10,
        )

    # Exchange client should never be called — risk gate short-circuits.
    engine._client.place_order.assert_not_awaited()
