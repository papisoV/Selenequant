"""Research brief generation engine — LLM-powered and template-based.

Generates concise equity research briefs from radar score data,
news items, and extracted factors. Two modes:
  - LLM: Structured output via call_llm_structured for rich analysis
  - Fallback: Template-based brief from radar score dimensions and signal direction
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.llm_helper import is_llm_configured
from app.core.pro_helpers import call_llm_structured

logger = logging.getLogger(__name__)

# LLM structured output schema for research brief
_BRIEF_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "thesis": {"type": "string"},
        "key_risks": {
            "type": "array",
            "items": {"type": "string"},
        },
        "catalysts": {
            "type": "array",
            "items": {"type": "string"},
        },
        "time_horizon": {"type": "string", "enum": ["short", "medium", "long"]},
        "conviction_level": {"type": "string", "enum": ["low", "medium", "high"]},
        "summary": {"type": "string"},
    },
    "required": ["thesis", "key_risks", "catalysts", "time_horizon", "conviction_level", "summary"],
}

_BRIEF_SYSTEM_PROMPT = "You are an equity research analyst. Be concise, factual, and actionable."

_BRIEF_USER_TEMPLATE = (
    "Write a concise research brief for {symbol} based on the provided data.\n\n"
    "Radar score: {total_score}/100, signal: {signal}\n"
    "Dimensions: {dims_summary}\n"
    "{factors_section}"
    "{news_section}\n"
    "Include:\n"
    "- thesis: 2-3 sentence investment thesis\n"
    "- key_risks: list of 2-3 specific risks\n"
    "- catalysts: list of 2-3 potential catalysts\n"
    "- time_horizon: short (days), medium (weeks), or long (months)\n"
    "- conviction_level: low, medium, or high\n"
    "- summary: 1-2 sentence actionable takeaway"
)


def _dims_summary(score_data: dict[str, Any]) -> str:
    """Format radar dimensions as a readable summary string."""
    dims = score_data.get("dimensions", {})
    return ", ".join(f"{d}={s}" for d, s in dims.items())


def _infer_conviction(score_data: dict[str, Any]) -> str:
    """Infer conviction level from radar score magnitude."""
    total = score_data.get("total_score", 50)
    deviation = abs(total - 50)
    if deviation >= 25:
        return "high"
    if deviation >= 12:
        return "medium"
    return "low"


def _infer_time_horizon_from_score(score_data: dict[str, Any]) -> str:
    """Infer time horizon from dominant dimension signals."""
    dims = score_data.get("dimensions", {})
    # If news or mover dimensions dominate, shorter horizon
    news_score = dims.get("news", 50)
    sentiment_score = dims.get("sentiment", 50)
    if news_score >= 70 or abs(sentiment_score - 50) >= 25:
        return "short"
    # If relationship or macro dimensions dominate, longer horizon
    rel_score = dims.get("relationship", 50)
    if rel_score >= 70:
        return "long"
    return "medium"


def _generate_brief_fallback(
    symbol: str,
    score_data: dict[str, Any],
    news_items: list[dict],
    factors: list[dict] | None,
) -> dict[str, Any]:
    """Fallback: template-based research brief from score data and signal direction."""
    total_score = score_data.get("total_score", 50)
    signal = score_data.get("signal", "neutral")
    if signal not in ("bullish", "bearish", "neutral"):
        # Derive from total_score if signal key missing
        if total_score >= 60:
            signal = "bullish"
        elif total_score <= 40:
            signal = "bearish"
        else:
            signal = "neutral"

    dims = score_data.get("dimensions", {})
    conviction = _infer_conviction(score_data)
    time_horizon = _infer_time_horizon_from_score(score_data)

    # Build thesis from signal + top dimensions
    strong_dims = [d for d, s in dims.items() if s >= 70]
    weak_dims = [d for d, s in dims.items() if s <= 30]

    if signal == "bullish":
        drivers = ", ".join(strong_dims[:3]) if strong_dims else "positive momentum"
        thesis = (
            f"{symbol} presents a bullish setup with a radar score of {total_score}/100, "
            f"driven by {drivers}. "
            f"The current signal suggests upside potential over a {time_horizon}-term horizon."
        )
    elif signal == "bearish":
        headwinds = ", ".join(weak_dims[:3]) if weak_dims else "negative pressure"
        thesis = (
            f"{symbol} shows bearish characteristics with a radar score of {total_score}/100, "
            f"weighed down by {headwinds}. "
            f"Caution is warranted over a {time_horizon}-term horizon."
        )
    else:
        thesis = (
            f"{symbol} is in a neutral zone with a radar score of {total_score}/100. "
            f"No strong directional bias is present; monitor for a breakout signal."
        )

    # Key risks from weak dimensions
    key_risks = []
    for dim in weak_dims[:3]:
        key_risks.append(f"Weak {dim} signal may indicate underlying headwinds")
    if not key_risks:
        key_risks = [
            "Limited data coverage may obscure data — signals may be incomplete",
            "Market regime change could invalidate current readings",
        ]

    # Catalysts from strong dimensions and factors
    catalysts = []
    for dim in strong_dims[:3]:
        catalysts.append(f"Strong {dim} reading could drive further momentum")
    if factors:
        for f in factors[:2]:
            if f.get("direction") == "bullish":
                catalysts.append(f"Factor: {f.get('factor_name', 'unknown')} (bullish)")
    if not catalysts:
        catalysts = [
            "Potential catalyst from upcoming earnings or macro events",
            "Sector rotation could provide tailwind",
        ]

    # Summary
    summary = (
        f"{signal.capitalize()} signal on {symbol} with {conviction} conviction. "
        f"Radar score {total_score}/100."
    )

    return {
        "symbol": symbol,
        "thesis": thesis,
        "key_risks": key_risks[:3],
        "catalysts": catalysts[:3],
        "time_horizon": time_horizon,
        "conviction_level": conviction,
        "summary": summary,
        "llm_enhanced": False,
    }


async def generate_research_brief(
    settings_service: Any,
    symbol: str,
    score_data: dict[str, Any],
    news_items: list[dict],
    factors: list[dict] | None = None,
) -> dict[str, Any]:
    """Generate a research brief for a symbol.

    Uses LLM with structured output when available, falls back to
    template-based generation otherwise.

    Args:
        settings_service: SettingsService for LLM configuration.
        symbol: Ticker symbol.
        score_data: Radar score dict with total_score, dimensions, signals.
        news_items: List of news dicts with title/headline keys.
        factors: Optional list of extracted factor dicts from factorizer.

    Returns:
        Dict with: symbol, thesis, key_risks, catalysts, time_horizon,
        conviction_level, summary, llm_enhanced.
    """
    llm_available = await is_llm_configured(settings_service)

    if llm_available:
        try:
            return await _generate_brief_llm(settings_service, symbol, score_data, news_items, factors)
        except Exception as exc:
            logger.warning("LLM research brief failed, falling back to template: %s", exc)

    return _generate_brief_fallback(symbol, score_data, news_items, factors)


async def _generate_brief_llm(
    settings_service: Any,
    symbol: str,
    score_data: dict[str, Any],
    news_items: list[dict],
    factors: list[dict] | None,
) -> dict[str, Any]:
    """LLM-based research brief with structured output."""
    total_score = score_data.get("total_score", 50)
    signal = score_data.get("signal", "neutral")
    if signal not in ("bullish", "bearish", "neutral"):
        signal = "bullish" if total_score >= 60 else ("bearish" if total_score <= 40 else "neutral")

    dims_str = _dims_summary(score_data)

    factors_section = ""
    if factors:
        factor_lines = [
            f"  - {f.get('factor_name', '?')}: {f.get('direction', '?')} (conf={f.get('confidence', '?')})"
            for f in factors[:8]
        ]
        factors_section = "Extracted factors:\n" + "\n".join(factor_lines) + "\n"

    news_section = ""
    if news_items:
        headlines = [
            f"  - {n.get('title', '') or n.get('headline', '')}"
            for n in news_items[:8]
        ]
        news_section = "Recent news:\n" + "\n".join(headlines) + "\n"

    prompt = _BRIEF_USER_TEMPLATE.format(
        symbol=symbol,
        total_score=total_score,
        signal=signal,
        dims_summary=dims_str,
        factors_section=factors_section,
        news_section=news_section,
    )

    result = await call_llm_structured(
        settings_service,
        prompt=prompt,
        system=_BRIEF_SYSTEM_PROMPT,
        max_tokens=800,
        output_schema=_BRIEF_SCHEMA,
    )

    if not result or "thesis" not in result:
        logger.info("LLM returned no structured brief, falling back to template")
        return _generate_brief_fallback(symbol, score_data, news_items, factors)

    # Validate and clamp enum fields
    conviction = result.get("conviction_level", "medium")
    if conviction not in ("low", "medium", "high"):
        conviction = _infer_conviction(score_data)

    time_horizon = result.get("time_horizon", "medium")
    if time_horizon not in ("short", "medium", "long"):
        time_horizon = _infer_time_horizon_from_score(score_data)

    key_risks = result.get("key_risks", [])
    if not isinstance(key_risks, list) or not key_risks:
        key_risks = ["Insufficient data for comprehensive risk assessment"]

    catalysts = result.get("catalysts", [])
    if not isinstance(catalysts, list) or not catalysts:
        catalysts = ["Monitor for upcoming catalysts"]

    return {
        "symbol": symbol,
        "thesis": result.get("thesis", ""),
        "key_risks": [str(r) for r in key_risks[:5]],
        "catalysts": [str(c) for c in catalysts[:5]],
        "time_horizon": time_horizon,
        "conviction_level": conviction,
        "summary": result.get("summary", ""),
        "llm_enhanced": True,
    }
