"""Wire-format locks for selene_space_types.agent — role/regime enums,
TradeIntent, MarketState, ChatMessage, and AgentStreamEvent variants."""

import pytest
from selene_space_types.agent import (
    AgentRole,
    AgentStreamEvent,
    AgentStreamEventType,
    ChatMessage,
    MarketRegime,
    MarketState,
    TradeIntent,
)
from selene_space_types.trading import OrderSide, OrderType


def _recycle(model_cls, instance):
    return model_cls.model_validate_json(instance.model_dump_json())


def test_agent_role_enum_is_exactly_four_members():
    assert {role.name for role in AgentRole} == {
        "MARKET_ANALYST",
        "STRATEGY_ARCHITECT",
        "RISK_GUARDIAN",
        "STRATEGY_CODER",
    }


class TestTradeIntentModel:
    def test_survives_json_cycle(self):
        intent = TradeIntent(
            symbol="ETH/USDT",
            side=OrderSide.BUY,
            quantity=0.4,
            order_type=OrderType.MARKET,
            reasoning="SMA cross detected",
            confidence=0.82,
            risk_params={"stop_loss": 2980.0},
            timestamp=1720002222333,
        )
        assert _recycle(TradeIntent, intent) == intent

    def test_spec_requires_reasoning_confidence_risk_params(self):
        """Spec: TradeIntent must have reasoning + confidence + risk_params."""
        required = {"reasoning", "confidence", "risk_params"}
        assert required <= set(TradeIntent.model_fields.keys())

    def test_optional_execution_fields_do_not_break_construction(self):
        """P1-4 C2:新增 leverage/reduce_only 可选字段不破坏现有构造点。

        现有构造点(trading.py:447/:1061、daily-cycle risk_review.py:93、
        risk-engine 各 _make_intent、paper 风险门)都不传这两个字段;
        默认值必须保守(leverage=1, reduce_only=False)且可 JSON roundtrip。
        """
        intent = TradeIntent(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=0.5,
            order_type=OrderType.MARKET,
            reasoning="construction-site compatibility",
            confidence=0.9,
            risk_params={},
            timestamp=1720002222333,
        )
        # 默认值存在且保守。
        assert intent.leverage == 1
        assert intent.reduce_only is False
        # 显式传入也保留。
        explicit = TradeIntent(
            symbol="BTC/USDT",
            side=OrderSide.BUY,
            quantity=0.5,
            order_type=OrderType.MARKET,
            reasoning="explicit",
            confidence=0.9,
            risk_params={},
            timestamp=1720002222333,
            leverage=5,
            reduce_only=True,
        )
        assert explicit.leverage == 5
        assert explicit.reduce_only is True
        # JSON roundtrip 保留新字段。
        assert _recycle(TradeIntent, explicit) == explicit


class TestMarketStateModel:
    def test_survives_json_cycle(self):
        state = MarketState(
            symbol="BTC/USDT",
            regime=MarketRegime.TRENDING,
            trend_strength=0.68,
            volatility=0.31,
            timestamp=1720002222444,
        )
        assert _recycle(MarketState, state) == state

    def test_market_regime_enum_has_three_members(self):
        names = {regime.name for regime in MarketRegime}
        assert names == {"TRENDING", "RANGING", "VOLATILE"}


class TestChatMessageModel:
    def test_survives_json_cycle_with_metadata(self):
        msg = ChatMessage(
            role="user",
            content="Draft a momentum strategy",
            timestamp=1720002222555,
            metadata={"source": "web"},
        )
        assert _recycle(ChatMessage, msg) == msg

    def test_metadata_defaults_to_none(self):
        bare = ChatMessage(
            role="assistant",
            content="Done.",
            timestamp=1720002222666,
        )
        assert bare.metadata is None


class TestAgentStreamEventVariants:
    def test_text_delta_variant_roundtrips(self):
        ev = AgentStreamEvent(
            type=AgentStreamEventType.TEXT_DELTA,
            data={"text": "Analyzing..."},
            timestamp=1720002222777,
        )
        assert _recycle(AgentStreamEvent, ev) == ev

    def test_tool_call_variant_carries_invocation(self):
        ev = AgentStreamEvent(
            type=AgentStreamEventType.TOOL_CALL,
            data={"tool": "market_ticker", "args": {"symbol": "BTC/USDT"}},
            timestamp=1720002222888,
        )
        restored = _recycle(AgentStreamEvent, ev)
        assert restored.type is AgentStreamEventType.TOOL_CALL

    def test_done_variant_marks_completion(self):
        ev = AgentStreamEvent(
            type=AgentStreamEventType.DONE,
            data={},
            timestamp=1720002222999,
        )
        restored = _recycle(AgentStreamEvent, ev)
        assert restored.type is AgentStreamEventType.DONE

    @pytest.mark.parametrize("variant", ["text_delta", "tool_call", "done"])
    def test_stream_event_type_values_present(self, variant):
        """Spec: AgentStreamEvent must have text_delta/tool_call/done types."""
        assert variant in {t.value for t in AgentStreamEventType}
