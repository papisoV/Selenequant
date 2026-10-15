"""Factor extraction engine — LLM-powered and keyword-based factor analysis.

Extracts tradable factors from news items using two modes:
  - LLM: Structured output via call_llm_structured for high-quality factor extraction
  - Fallback: Keyword-based extraction using _EVENT_KEYWORDS from us_radar API
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.llm_helper import is_llm_configured
from app.core.pro_helpers import call_llm_structured

logger = logging.getLogger(__name__)

# Reuse the same keyword taxonomy as the us_radar event-signal classifier
_EVENT_KEYWORDS: dict[str, list[str]] = {
    "earnings": ["earnings", "revenue", "eps", "profit", "quarterly", "fiscal", "guidance", "outlook"],
    "regulatory": ["sec", "fda", "regulation", "antitrust", "ban", "lawsuit", "compliance", "investigation"],
    "filing": ["filing", "10-k", "10-q", "8-k", "proxy", "sec filing", "annual report"],
    "mover": ["surge", "plunge", "rally", "selloff", "soars", "tumbles", "jumps", "drops", "spikes"],
    "macro": ["fed", "inflation", "cpi", "interest rate", "gdp", "unemployment", "treasury", "fomc"],
}

# Direction inference from event type + keyword polarity
_BULLISH_KEYWORDS = {"surge", "rally", "soars", "jumps", "profit", "beats", "upgrade", "buy", "growth", "gain"}
_BEARISH_KEYWORDS = {"plunge", "selloff", "tumbles", "drops", "spikes", "ban", "lawsuit", "investigation", "cut", "downgrade", "loss"}

# LLM structured output schema for factor extraction
_FACTOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "factors": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "factor_name": {"type": "string"},
                    "direction": {"type": "string", "enum": ["bullish", "bearish", "neutral"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    "evidence": {"type": "string"},
                    "time_horizon": {"type": "string", "enum": ["short", "medium", "long"]},
                },
                "required": ["factor_name", "direction", "confidence", "evidence", "time_horizon"],
            },
        },
    },
    "required": ["factors"],
}

_FACTOR_SYSTEM_PROMPT = "You are a quantitative factor analyst. Be precise, concise, and data-driven."

_FACTOR_USER_TEMPLATE = (
    "Extract tradable factors from these news items"
    "{symbol_clause}.\n\n"
    "For each factor, provide:\n"
    "- factor_name: concise name (e.g. 'earnings_beat', 'regulatory_risk', 'fed_rate_sensitivity')\n"
    "- direction: bullish, bearish, or neutral\n"
    "- confidence: 0-1 based on how clearly the news supports this factor\n"
    "- evidence: brief quote or paraphrase from the news\n"
    "- time_horizon: short (days), medium (weeks), or long (months)\n\n"
    "News items:\n{news_text}"
)


def _infer_direction(headline: str) -> str:
    """Infer bullish/bearish/neutral direction from headline keywords."""
    hl = headline.lower()
    bullish_hits = sum(1 for kw in _BULLISH_KEYWORDS if kw in hl)
    bearish_hits = sum(1 for kw in _BEARISH_KEYWORDS if kw in hl)
    if bullish_hits > bearish_hits:
        return "bullish"
    if bearish_hits > bullish_hits:
        return "bearish"
    return "neutral"


def _infer_time_horizon(event_type: str) -> str:
    """Map event type to typical time horizon."""
    if event_type in ("mover",):
        return "short"
    if event_type in ("earnings", "filing"):
        return "medium"
    if event_type in ("macro", "regulatory"):
        return "long"
    return "medium"


def _keyword_confidence(event_type: str, direction: str) -> float:
    """Rule-based confidence from event type and direction clarity."""
    base = 0.4 if event_type == "news_event" else 0.6
    if direction != "neutral":
        base += 0.1
    return min(1.0, base)


def _extract_factors_keyword(news_items: list[dict]) -> list[dict]:
    """Fallback: keyword-based factor extraction from news headlines."""
    factors: list[dict] = []
    seen: set[str] = set()

    for item in news_items:
        headline = item.get("title", "") or item.get("headline", "")
        if not headline:
            continue

        hl_lower = headline.lower()
        for event_type, keywords in _EVENT_KEYWORDS.items():
            matched_kws = [kw for kw in keywords if kw in hl_lower]
            if not matched_kws:
                continue

            factor_name = f"{event_type}_factor"
            if factor_name in seen:
                continue
            seen.add(factor_name)

            direction = _infer_direction(headline)
            confidence = _keyword_confidence(event_type, direction)
            time_horizon = _infer_time_horizon(event_type)

            factors.append({
                "factor_name": factor_name,
                "direction": direction,
                "confidence": round(confidence, 2),
                "evidence": headline[:120],
                "time_horizon": time_horizon,
                "source": "keyword",
            })

    # If no keyword matches, create a generic news_event factor
    if not factors and news_items:
        first_headline = news_items[0].get("title", "") or news_items[0].get("headline", "")
        direction = _infer_direction(first_headline)
        factors.append({
            "factor_name": "news_event_factor",
            "direction": direction,
            "confidence": 0.3,
            "evidence": first_headline[:120],
            "time_horizon": "medium",
            "source": "keyword",
        })

    return factors


async def extract_factors(
    settings_service: Any,
    news_items: list[dict],
    symbol: str | None = None,
) -> list[dict]:
    """Extract tradable factors from news items.

    Uses LLM with structured output when available, falls back to
    keyword-based extraction otherwise.

    Args:
        settings_service: SettingsService for LLM configuration.
        news_items: List of news dicts with at least 'title' or 'headline' keys.
        symbol: Optional symbol to scope the analysis.

    Returns:
        List of factor dicts, each containing:
        factor_name, direction, confidence, evidence, time_horizon, source.
    """
    if not news_items:
        return []

    llm_available = await is_llm_configured(settings_service)

    if llm_available:
        try:
            return await _extract_factors_llm(settings_service, news_items, symbol)
        except Exception as exc:
            logger.warning("LLM factor extraction failed, falling back to keywords: %s", exc)

    return _extract_factors_keyword(news_items)


async def _extract_factors_llm(
    settings_service: Any,
    news_items: list[dict],
    symbol: str | None,
) -> list[dict]:
    """LLM-based factor extraction with structured output."""
    symbol_clause = f" for {symbol}" if symbol else ""
    news_text = "\n".join(
        f"- [{item.get('symbol', '')}] {item.get('title', '') or item.get('headline', '')}"
        for item in news_items[:20]
    )

    prompt = _FACTOR_USER_TEMPLATE.format(
        symbol_clause=symbol_clause,
        news_text=news_text,
    )

    result = await call_llm_structured(
        settings_service,
        prompt=prompt,
        system=_FACTOR_SYSTEM_PROMPT,
        max_tokens=800,
        output_schema=_FACTOR_SCHEMA,
    )

    if not result or "factors" not in result:
        logger.info("LLM returned no structured factors, falling back to keywords")
        return _extract_factors_keyword(news_items)

    factors = []
    for raw in result["factors"]:
        direction = raw.get("direction", "neutral")
        if direction not in ("bullish", "bearish", "neutral"):
            direction = "neutral"
        confidence = max(0.0, min(1.0, float(raw.get("confidence", 0.5))))
        time_horizon = raw.get("time_horizon", "medium")
        if time_horizon not in ("short", "medium", "long"):
            time_horizon = "medium"

        factors.append({
            "factor_name": raw.get("factor_name", "unnamed_factor"),
            "direction": direction,
            "confidence": round(confidence, 2),
            "evidence": raw.get("evidence", "")[:200],
            "time_horizon": time_horizon,
            "source": "llm",
        })

    return factors if factors else _extract_factors_keyword(news_items)


async def auto_factorize(
    settings_service: Any,
    client: Any,
    symbol: str,
) -> list[dict]:
    """Fetch news for a symbol and extract factors in one call.

    Args:
        settings_service: SettingsService for LLM configuration.
        client: YahooFinanceClient instance with get_news() method.
        symbol: Ticker symbol to fetch news for.

    Returns:
        List of extracted factor dicts.
    """
    try:
        news_raw = await client.get_news(symbol)
    except Exception as exc:
        logger.error("Failed to fetch news for auto_factorize %s: %s", symbol, exc)
        return []

    news_items: list[dict] = []
    if isinstance(news_raw, dict):
        for n in news_raw.get("news", [])[:15]:
            news_items.append({
                "symbol": symbol,
                "title": n.get("title", ""),
                "publisher": n.get("publisher", ""),
                "published_at": n.get("providerPublishTime", 0),
                "url": n.get("link", ""),
            })

    if not news_items:
        logger.info("No news items found for %s in auto_factorize", symbol)
        return []

    return await extract_factors(settings_service, news_items, symbol=symbol)
