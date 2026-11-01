"""Structured-output contracts and JSON extraction helpers."""

from __future__ import annotations

import json
from typing import TypeVar

from pydantic import BaseModel, Field

from selene_space_llm.base import LLMError

T = TypeVar("T", bound=BaseModel)


class TokenUsage(BaseModel):
    """Token accounting for a single completion."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    #: Provider stop reason. ``"length"`` means the reply was cut off by
    #: max_tokens — callers that need a parseable body must treat it as a
    #: truncated response, not as "the model said this". Empty when the
    #: endpoint omits it (many OpenAI-compatible gateways do).
    finish_reason: str = ""


class ToolCall(BaseModel):
    """One function the model asked the host to run."""

    id: str = ""
    name: str
    arguments: dict = Field(default_factory=dict)


class ToolCallResult(BaseModel):
    """Outcome of a tool-enabled completion.

    Either the model produced plain ``text``, or it requested one or more
    :class:`ToolCall` entries; usage and model name ride along when known.
    """

    text: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: TokenUsage = Field(default_factory=TokenUsage)
    model: str | None = None


class MarketAnalysis(BaseModel):
    """Narrative read of a symbol's current market condition."""

    symbol: str
    summary: str
    regime: str
    trend_direction: str
    confidence: float
    key_levels: dict = Field(default_factory=dict)
    recommendation: str | None = None


def get_json_schema(model_cls: type[BaseModel]) -> dict:
    """Expose the JSON schema of a pydantic model as a plain dict."""
    return model_cls.model_json_schema()


def trade_intent_schema() -> dict:
    from selene_space_types.agent import TradeIntent

    return get_json_schema(TradeIntent)


def strategy_config_schema() -> dict:
    from selene_space_types.strategy import StrategyConfig

    return get_json_schema(StrategyConfig)


def market_analysis_schema() -> dict:
    return get_json_schema(MarketAnalysis)


def extract_structured(raw: str, schema: type[T]) -> T:
    """Parse LLM text output into a validated pydantic instance.

    Malformed JSON and schema violations both surface as :class:`LLMError`
    with a human-readable explanation; the raw head is quoted to make
    debugging of bad completions practical.
    """
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        head = raw[:200]
        raise LLMError(
            f"Failed to parse LLM output as JSON: {exc}. Raw output (first 200 chars): {head}"
        ) from exc

    try:
        return schema.model_validate(data)
    except Exception as exc:
        raise LLMError(
            f"LLM output does not match {schema.__name__} schema: {exc}"
        ) from exc


__all__ = [
    "MarketAnalysis",
    "TokenUsage",
    "ToolCall",
    "ToolCallResult",
    "extract_structured",
    "get_json_schema",
    "market_analysis_schema",
    "strategy_config_schema",
    "trade_intent_schema",
]
