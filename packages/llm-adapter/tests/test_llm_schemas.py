"""Behavior locks for selene_space_llm.schemas: JSON-schema exposure and
LLM-output parsing/validation into typed models."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from selene_space_llm.base import LLMError
from selene_space_llm.schemas import (
    MarketAnalysis,
    extract_structured,
    get_json_schema,
    market_analysis_schema,
    strategy_config_schema,
    trade_intent_schema,
)


class SimpleModel(BaseModel):
    name: str
    value: int = 0


# --- schema generation ------------------------------------------------------


def test_generic_model_schema_lists_its_fields() -> None:
    schema = get_json_schema(SimpleModel)
    assert isinstance(schema, dict) and "properties" in schema
    assert "name" in schema["properties"]


SCHEMA_FACTORIES = {
    "trade_intent": (trade_intent_schema, {"symbol", "confidence"}),
    "strategy_config": (strategy_config_schema, {"name", "symbols"}),
    "market_analysis": (market_analysis_schema, {"symbol", "regime"}),
}


@pytest.mark.parametrize("label", sorted(SCHEMA_FACTORIES))
def test_builtin_schema_exposes_expected_properties(label: str) -> None:
    factory, expected_props = SCHEMA_FACTORIES[label]
    produced = factory()
    assert isinstance(produced, dict)
    assert expected_props <= set(produced["properties"])


# --- extract_structured: happy paths ----------------------------------------


def test_full_payload_populates_every_field() -> None:
    parsed = extract_structured(json.dumps({"name": "test", "value": 42}), SimpleModel)
    assert isinstance(parsed, SimpleModel)
    assert (parsed.name, parsed.value) == ("test", 42)


def test_pydantic_default_fills_absent_optional_field() -> None:
    parsed = extract_structured(json.dumps({"name": "test"}), SimpleModel)
    assert parsed.value == 0


def test_market_analysis_document_roundtrips() -> None:
    document = {
        "symbol": "BTC/USDT",
        "summary": "Strong uptrend with high volume",
        "regime": "trending",
        "trend_direction": "bullish",
        "confidence": 0.85,
        "key_levels": {"support": 65000, "resistance": 72000},
        "recommendation": "Consider long positions",
    }
    parsed = extract_structured(json.dumps(document), MarketAnalysis)
    assert parsed.symbol == "BTC/USDT"
    assert parsed.confidence == pytest.approx(0.85)
    assert parsed.key_levels.get("support") == 65000


def test_trade_intent_from_agent_types_module() -> None:
    from selene_space_types.agent import TradeIntent

    document = {
        "symbol": "BTC/USDT",
        "side": "buy",
        "quantity": 0.1,
        "order_type": "market",
        "reasoning": "SMA cross",
        "confidence": 0.8,
        "timestamp": 1711000000000,
    }
    parsed = extract_structured(json.dumps(document), TradeIntent)
    assert (parsed.symbol, parsed.confidence) == ("BTC/USDT", 0.8)


# --- extract_structured: rejection paths -------------------------------------


def test_garbage_json_is_rejected() -> None:
    with pytest.raises(LLMError, match="Failed to parse"):
        extract_structured("not json {{{", SimpleModel)


def test_payload_missing_required_field_is_rejected() -> None:
    # 'name' is mandatory; only 'value' is present.
    with pytest.raises(LLMError, match="does not match"):
        extract_structured('{"value": 42}', SimpleModel)


def test_field_with_wrong_value_type_is_rejected() -> None:
    bad = '{"name": "ok", "value": "not_an_int"}'
    with pytest.raises(LLMError, match="does not match"):
        extract_structured(bad, SimpleModel)


# --- mutable default isolation ----------------------------------------------


def _make_market_analysis(symbol: str, summary: str, regime: str,
                          direction: str, confidence: float) -> MarketAnalysis:
    return MarketAnalysis(
        symbol=symbol,
        summary=summary,
        regime=regime,
        trend_direction=direction,
        confidence=confidence,
    )


def test_key_levels_default_not_shared_between_instances() -> None:
    first = _make_market_analysis("BTC/USDT", "A", "trending", "bullish", 0.9)
    second = _make_market_analysis("ETH/USDT", "B", "ranging", "neutral", 0.6)

    first.key_levels["support"] = 60000.0
    assert "support" not in second.key_levels
