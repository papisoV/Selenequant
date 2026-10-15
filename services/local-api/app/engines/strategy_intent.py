"""Strategy intent engine — signal-to-strategy conversion and paper trading.

Converts radar signals into structured strategy intents with entry/exit
conditions, risk parameters, and position sizing. Two modes:
  - LLM: Structured output via call_llm_structured for nuanced strategy design
  - Fallback: Rule-based from signal direction + confidence
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.llm_helper import is_llm_configured
from app.core.pro_helpers import call_llm_structured

logger = logging.getLogger(__name__)

# Default risk parameters by direction
_DEFAULT_RISK_PARAMS: dict[str, dict[str, float]] = {
    "bullish": {
        "stop_loss_pct": 5.0,
        "take_profit_pct": 10.0,
        "max_position_pct": 10.0,
    },
    "bearish": {
        "stop_loss_pct": 5.0,
        "take_profit_pct": 10.0,
        "max_position_pct": 8.0,
    },
    "neutral": {
        "stop_loss_pct": 3.0,
        "take_profit_pct": 5.0,
        "max_position_pct": 3.0,
    },
}

# LLM structured output schema for strategy intent
_INTENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "entry_conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "threshold": {"type": "string"},
                },
                "required": ["type", "description"],
            },
        },
        "exit_conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "type": {"type": "string"},
                    "description": {"type": "string"},
                    "threshold": {"type": "string"},
                },
                "required": ["type", "description"],
            },
        },
        "risk_params": {
            "type": "object",
            "properties": {
                "stop_loss_pct": {"type": "number"},
                "take_profit_pct": {"type": "number"},
                "max_position_pct": {"type": "number"},
                "stop_loss_type": {"type": "string", "enum": ["fixed_pct", "atr_dynamic"]},
            },
        },
        "position_sizing": {
            "type": "object",
            "properties": {
                "method": {"type": "string", "enum": ["fixed_fraction", "kelly_half"]},
                "default_pct": {"type": "number"},
            },
        },
        "reasoning": {"type": "string"},
    },
    "required": ["entry_conditions", "exit_conditions", "risk_params", "position_sizing", "reasoning"],
}

_INTENT_SYSTEM_PROMPT = "You are a systematic strategy designer. Be precise and risk-aware."

_INTENT_USER_TEMPLATE = (
    "Convert this signal into a strategy intent.\n\n"
    "Signal: {symbol} {direction} with confidence {confidence:.2f}\n"
    "Radar dimensions: {dims_summary}\n\n"
    "Provide:\n"
    "- entry_conditions: list of conditions to enter (type, description, threshold)\n"
    "- exit_conditions: list of conditions to exit (type, description, threshold)\n"
    "- risk_params: stop_loss_pct, take_profit_pct, max_position_pct\n"
    "- position_sizing: method (e.g. 'fixed_fraction', 'risk_parity') and default_pct\n"
    "- reasoning: 2-3 sentence rationale for the strategy\n\n"
    "Risk guidelines:\n"
    "- Stop loss: typically 3-8% depending on confidence and volatility\n"
    "- Take profit: typically 1.5-3x the stop loss\n"
    "- Max position: typically 3-10% of portfolio\n"
    "- Scale position size with confidence: higher confidence = larger position"
)

# LLM structured output schema for paper trade
_PAPER_TRADE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "side": {"type": "string", "enum": ["buy", "sell"]},
        "amount_pct": {"type": "number"},
        "order_type": {"type": "string", "enum": ["market", "limit"]},
        "stop_loss_pct": {"type": "number"},
        "take_profit_pct": {"type": "number"},
        "reasoning": {"type": "string"},
    },
    "required": ["side", "amount_pct", "order_type", "stop_loss_pct", "take_profit_pct", "reasoning"],
}

_PAPER_TRADE_SYSTEM_PROMPT = "You are a paper trading simulator. Be realistic and conservative."

_PAPER_TRADE_USER_TEMPLATE = (
    "Given this strategy intent, suggest concrete paper trade parameters.\n\n"
    "Symbol: {symbol}\n"
    "Direction: {direction}\n"
    "Entry conditions: {entry_desc}\n"
    "Exit conditions: {exit_desc}\n"
    "Risk params: SL={sl_pct}%, TP={tp_pct}%, MaxPos={max_pos_pct}%\n"
    "Position sizing: {sizing_method} at {sizing_pct}%\n\n"
    "Provide:\n"
    "- side: buy or sell\n"
    "- amount_pct: percentage of paper account to allocate (1-10)\n"
    "- order_type: market or limit\n"
    "- stop_loss_pct: stop loss percentage\n"
    "- take_profit_pct: take profit percentage\n"
    "- reasoning: 1-2 sentence justification"
)


def _build_entry_conditions(direction: str, confidence: float, dims: dict[str, int]) -> list[dict]:
    """Rule-based entry conditions from signal direction and dimensions."""
    conditions: list[dict] = []

    if direction == "bullish":
        conditions.append({
            "type": "momentum",
            "description": "Price shows upward momentum confirming bullish signal",
            "threshold": "positive_daily_change",
        })
        if dims.get("volume", 50) >= 55:
            conditions.append({
                "type": "volume_confirmation",
                "description": "Volume supports the move",
                "threshold": "volume_ratio > 1.2",
            })
        if dims.get("sentiment", 50) >= 55:
            conditions.append({
                "type": "sentiment_alignment",
                "description": "Sentiment aligns with bullish direction",
                "threshold": "sentiment_score > 55",
            })
    elif direction == "bearish":
        conditions.append({
            "type": "momentum",
            "description": "Price shows downward momentum confirming bearish signal",
            "threshold": "negative_daily_change",
        })
        if dims.get("volume", 50) >= 55:
            conditions.append({
                "type": "volume_confirmation",
                "description": "Selling volume supports the move",
                "threshold": "volume_ratio > 1.2",
            })
    else:
        conditions.append({
            "type": "breakout",
            "description": "Wait for directional breakout before entry",
            "threshold": "price_moves_2_pct_from_range",
        })

    # Confidence gate: require minimum confidence for entry
    if confidence < 0.4:
        conditions.append({
            "type": "confidence_gate",
            "description": "Low confidence — wait for additional confirmation",
            "threshold": "confidence >= 0.4 or additional_signal",
        })

    return conditions


def _build_exit_conditions(direction: str, risk_params: dict[str, float]) -> list[dict]:
    """Rule-based exit conditions from direction and risk params."""
    conditions: list[dict] = []

    sl_pct = risk_params.get("stop_loss_pct", 5.0)
    tp_pct = risk_params.get("take_profit_pct", 10.0)

    conditions.append({
        "type": "stop_loss",
        "description": f"Exit if price moves against position by {sl_pct}%",
        "threshold": f"loss >= {sl_pct}%",
    })
    conditions.append({
        "type": "take_profit",
        "description": f"Exit if price reaches {tp_pct}% profit target",
        "threshold": f"profit >= {tp_pct}%",
    })
    conditions.append({
        "type": "time_stop",
        "description": "Exit if position is held beyond expected holding period without reaching target",
        "threshold": "holding_period_exceeded",
    })

    if direction == "bullish":
        conditions.append({
            "type": "signal_reversal",
            "description": "Exit if radar signal reverses to bearish",
            "threshold": "signal_bearish",
        })
    elif direction == "bearish":
        conditions.append({
            "type": "signal_reversal",
            "description": "Exit if radar signal reverses to bullish",
            "threshold": "signal_bullish",
        })

    return conditions


def _compute_risk_params(direction: str, confidence: float) -> dict[str, float]:
    """Rule-based risk parameters scaled by confidence."""
    base = _DEFAULT_RISK_PARAMS.get(direction, _DEFAULT_RISK_PARAMS["neutral"])

    # Scale stop loss tighter with higher confidence (more conviction = tighter risk)
    sl = base["stop_loss_pct"]
    if confidence >= 0.7:
        sl = max(3.0, sl - 1.0)
    elif confidence < 0.4:
        sl = min(8.0, sl + 2.0)

    # Take profit scales with confidence
    tp = base["take_profit_pct"]
    if confidence >= 0.7:
        tp = max(tp, sl * 2.5)
    elif confidence < 0.4:
        tp = max(tp, sl * 1.5)

    # Max position scales with confidence
    max_pos = base["max_position_pct"]
    if confidence >= 0.7:
        max_pos = min(10.0, max_pos + 2.0)
    elif confidence < 0.4:
        max_pos = max(2.0, max_pos - 3.0)

    return {
        "stop_loss_pct": round(sl, 1),
        "take_profit_pct": round(tp, 1),
        "max_position_pct": round(max_pos, 1),
        "stop_loss_type": "atr_dynamic" if confidence >= 0.6 else "fixed_pct",
    }


def _compute_position_sizing(direction: str, confidence: float) -> dict[str, Any]:
    """Rule-based position sizing from direction and confidence.

    Uses kelly_half for high-confidence signals (>= 0.6),
    fixed_fraction otherwise.
    """
    # Default position as fraction of portfolio
    if confidence >= 0.7:
        default_pct = 7.0 if direction != "neutral" else 3.0
        method = "kelly_half" if direction != "neutral" else "fixed_fraction"
    elif confidence >= 0.4:
        default_pct = 5.0 if direction != "neutral" else 2.0
        method = "fixed_fraction"
    else:
        default_pct = 3.0
        method = "fixed_fraction"

    return {
        "method": method,
        "default_pct": round(default_pct, 1),
    }


def _signal_to_intent_fallback(
    signal_id: str,
    symbol: str,
    direction: str,
    confidence: float,
    score_data: dict[str, Any],
) -> dict[str, Any]:
    """Fallback: rule-based strategy intent from signal direction + confidence."""
    dims = score_data.get("dimensions", {})
    risk_params = _compute_risk_params(direction, confidence)
    position_sizing = _compute_position_sizing(direction, confidence)
    entry_conditions = _build_entry_conditions(direction, confidence, dims)
    exit_conditions = _build_exit_conditions(direction, risk_params)

    # Build reasoning
    strong_dims = [d for d, s in dims.items() if s >= 70]
    weak_dims = [d for d, s in dims.items() if s <= 30]

    reasoning = f"Strategy intent for {symbol} based on {direction} signal (confidence: {confidence:.2f}). "
    if strong_dims:
        reasoning += f"Supported by strong {', '.join(strong_dims[:3])} dimensions. "
    if weak_dims:
        reasoning += f"Caution: weak {', '.join(weak_dims[:3])} dimensions. "
    reasoning += f"Risk parameters: SL={risk_params['stop_loss_pct']}%, TP={risk_params['take_profit_pct']}%."

    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "direction": direction,
        "entry_conditions": entry_conditions,
        "exit_conditions": exit_conditions,
        "risk_params": risk_params,
        "position_sizing": position_sizing,
        "reasoning": reasoning,
        "llm_enhanced": False,
    }


async def signal_to_strategy_intent(
    settings_service: Any,
    signal_id: str,
    symbol: str,
    direction: str,
    confidence: float,
    score_data: dict[str, Any],
) -> dict[str, Any]:
    """Convert a radar signal into a strategy intent.

    Uses LLM with structured output when available, falls back to
    rule-based generation otherwise.

    Args:
        settings_service: SettingsService for LLM configuration.
        signal_id: Unique identifier for the source signal.
        symbol: Ticker symbol.
        direction: Signal direction (bullish/bearish/neutral).
        confidence: Signal confidence (0-1).
        score_data: Radar score dict with dimensions.

    Returns:
        Dict with: signal_id, symbol, direction, entry_conditions,
        exit_conditions, risk_params, position_sizing, reasoning, llm_enhanced.
    """
    # Clamp inputs
    direction = direction if direction in ("bullish", "bearish", "neutral") else "neutral"
    confidence = max(0.0, min(1.0, float(confidence)))

    llm_available = await is_llm_configured(settings_service)

    if llm_available:
        try:
            return await _signal_to_intent_llm(
                settings_service, signal_id, symbol, direction, confidence, score_data,
            )
        except Exception as exc:
            logger.warning("LLM strategy intent failed, falling back to rules: %s", exc)

    return _signal_to_intent_fallback(signal_id, symbol, direction, confidence, score_data)


async def _signal_to_intent_llm(
    settings_service: Any,
    signal_id: str,
    symbol: str,
    direction: str,
    confidence: float,
    score_data: dict[str, Any],
) -> dict[str, Any]:
    """LLM-based strategy intent with structured output."""
    dims = score_data.get("dimensions", {})
    dims_summary = ", ".join(f"{d}={s}" for d, s in dims.items())

    prompt = _INTENT_USER_TEMPLATE.format(
        symbol=symbol,
        direction=direction,
        confidence=confidence,
        dims_summary=dims_summary,
    )

    result = await call_llm_structured(
        settings_service,
        prompt=prompt,
        system=_INTENT_SYSTEM_PROMPT,
        max_tokens=800,
        output_schema=_INTENT_SCHEMA,
    )

    if not result or "entry_conditions" not in result:
        logger.info("LLM returned no structured intent, falling back to rules")
        return _signal_to_intent_fallback(signal_id, symbol, direction, confidence, score_data)

    # Validate and sanitize risk_params
    risk_params = result.get("risk_params", {})
    if not isinstance(risk_params, dict):
        risk_params = _compute_risk_params(direction, confidence)
    else:
        # Clamp values to sane ranges
        risk_params = {
            "stop_loss_pct": round(max(1.0, min(20.0, float(risk_params.get("stop_loss_pct", 5.0)))), 1),
            "take_profit_pct": round(max(1.0, min(50.0, float(risk_params.get("take_profit_pct", 10.0)))), 1),
            "max_position_pct": round(max(1.0, min(20.0, float(risk_params.get("max_position_pct", 5.0)))), 1),
        }

    # Validate position_sizing
    position_sizing = result.get("position_sizing", {})
    if not isinstance(position_sizing, dict):
        position_sizing = _compute_position_sizing(direction, confidence)
    else:
        position_sizing = {
            "method": str(position_sizing.get("method", "fixed_fraction")),
            "default_pct": round(max(1.0, min(15.0, float(position_sizing.get("default_pct", 5.0)))), 1),
        }

    # Validate entry/exit conditions
    entry_conditions = result.get("entry_conditions", [])
    if not isinstance(entry_conditions, list) or not entry_conditions:
        entry_conditions = _build_entry_conditions(direction, confidence, dims)

    exit_conditions = result.get("exit_conditions", [])
    if not isinstance(exit_conditions, list) or not exit_conditions:
        exit_conditions = _build_exit_conditions(direction, risk_params)

    # Ensure each condition has required keys
    for cond_list in (entry_conditions, exit_conditions):
        for i, cond in enumerate(cond_list):
            if not isinstance(cond, dict):
                cond_list[i] = {"type": "unknown", "description": str(cond)}
            cond_list[i].setdefault("type", "unknown")
            cond_list[i].setdefault("description", "")

    return {
        "signal_id": signal_id,
        "symbol": symbol,
        "direction": direction,
        "entry_conditions": entry_conditions,
        "exit_conditions": exit_conditions,
        "risk_params": risk_params,
        "position_sizing": position_sizing,
        "reasoning": result.get("reasoning", ""),
        "llm_enhanced": True,
    }


async def intent_to_paper_trade(
    settings_service: Any,
    intent: dict[str, Any],
    account_id: str = "paper-default",
) -> dict[str, Any]:
    """Convert a strategy intent into concrete paper trade parameters.

    Uses LLM when available for nuanced trade sizing, falls back to
    fixed parameters from the intent direction.

    Args:
        settings_service: SettingsService for LLM configuration.
        intent: Strategy intent dict from signal_to_strategy_intent.
        account_id: Paper trading account identifier.

    Returns:
        Dict with: symbol, side, amount_pct, order_type, stop_loss_pct,
        take_profit_pct, reasoning, llm_enhanced.
    """
    symbol = intent.get("symbol", "")
    direction = intent.get("direction", "neutral")
    risk_params = intent.get("risk_params", _DEFAULT_RISK_PARAMS.get(direction, _DEFAULT_RISK_PARAMS["neutral"]))
    position_sizing = intent.get("position_sizing", {"method": "fixed_fraction", "default_pct": 5.0})

    llm_available = await is_llm_configured(settings_service)

    if llm_available:
        try:
            return await _intent_to_paper_trade_llm(settings_service, intent, account_id)
        except Exception as exc:
            logger.warning("LLM paper trade failed, falling back to rules: %s", exc)

    return _intent_to_paper_trade_fallback(intent, account_id)


def _intent_to_paper_trade_fallback(
    intent: dict[str, Any],
    account_id: str,
) -> dict[str, Any]:
    """Fallback: fixed paper trade parameters from intent direction."""
    direction = intent.get("direction", "neutral")
    risk_params = intent.get("risk_params", _DEFAULT_RISK_PARAMS.get(direction, _DEFAULT_RISK_PARAMS["neutral"]))
    position_sizing = intent.get("position_sizing", {"method": "fixed_fraction", "default_pct": 5.0})

    side = "buy" if direction == "bullish" else ("sell" if direction == "bearish" else "buy")
    amount_pct = float(position_sizing.get("default_pct", 5.0))
    sl_pct = float(risk_params.get("stop_loss_pct", 5.0))
    tp_pct = float(risk_params.get("take_profit_pct", 10.0))

    reasoning = (
        f"Paper trade for {intent.get('symbol', '?')}: {side} with {amount_pct}% allocation. "
        f"SL={sl_pct}%, TP={tp_pct}%. Based on {direction} signal."
    )

    return {
        "symbol": intent.get("symbol", ""),
        "side": side,
        "amount_pct": round(amount_pct, 1),
        "order_type": "market",
        "stop_loss_pct": round(sl_pct, 1),
        "take_profit_pct": round(tp_pct, 1),
        "reasoning": reasoning,
        "llm_enhanced": False,
    }


async def _intent_to_paper_trade_llm(
    settings_service: Any,
    intent: dict[str, Any],
    account_id: str,
) -> dict[str, Any]:
    """LLM-based paper trade parameter generation."""
    symbol = intent.get("symbol", "")
    direction = intent.get("direction", "neutral")
    risk_params = intent.get("risk_params", _DEFAULT_RISK_PARAMS.get(direction, _DEFAULT_RISK_PARAMS["neutral"]))
    position_sizing = intent.get("position_sizing", {"method": "fixed_fraction", "default_pct": 5.0})

    entry_desc = "; ".join(
        c.get("description", str(c)) for c in intent.get("entry_conditions", [])[:3]
    )
    exit_desc = "; ".join(
        c.get("description", str(c)) for c in intent.get("exit_conditions", [])[:3]
    )

    prompt = _PAPER_TRADE_USER_TEMPLATE.format(
        symbol=symbol,
        direction=direction,
        entry_desc=entry_desc or "none specified",
        exit_desc=exit_desc or "none specified",
        sl_pct=risk_params.get("stop_loss_pct", 5.0),
        tp_pct=risk_params.get("take_profit_pct", 10.0),
        max_pos_pct=risk_params.get("max_position_pct", 10.0),
        sizing_method=position_sizing.get("method", "fixed_fraction"),
        sizing_pct=position_sizing.get("default_pct", 5.0),
    )

    result = await call_llm_structured(
        settings_service,
        prompt=prompt,
        system=_PAPER_TRADE_SYSTEM_PROMPT,
        max_tokens=400,
        output_schema=_PAPER_TRADE_SCHEMA,
    )

    if not result or "side" not in result:
        logger.info("LLM returned no structured paper trade, falling back to rules")
        return _intent_to_paper_trade_fallback(intent, account_id)

    # Validate and clamp
    side = result.get("side", "buy")
    if side not in ("buy", "sell"):
        side = "buy" if direction == "bullish" else "sell"

    amount_pct = max(1.0, min(15.0, float(result.get("amount_pct", 5.0))))
    order_type = result.get("order_type", "market")
    if order_type not in ("market", "limit"):
        order_type = "market"
    sl_pct = max(1.0, min(20.0, float(result.get("stop_loss_pct", 5.0))))
    tp_pct = max(1.0, min(50.0, float(result.get("take_profit_pct", 10.0))))

    return {
        "symbol": symbol,
        "side": side,
        "amount_pct": round(amount_pct, 1),
        "order_type": order_type,
        "stop_loss_pct": round(sl_pct, 1),
        "take_profit_pct": round(tp_pct, 1),
        "reasoning": result.get("reasoning", ""),
        "llm_enhanced": True,
    }
