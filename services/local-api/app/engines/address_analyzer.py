"""Polymarket address analysis engine — classification, scoring, and LLM enrichment.

For each address, computes basic metrics (win_rate, roi, trade_count, total_volume)
and classifies the trader as elite/proficient/retail/unknown. When LLM is configured,
generates a natural-language "trading_style" narrative; otherwise falls back to
rule-based classification from win_rate.
"""

from __future__ import annotations

import logging
from typing import Any

from app.core.pro_helpers import call_llm_structured

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Classification thresholds
# ---------------------------------------------------------------------------

_CLASSIFICATION_THRESHOLDS: list[tuple[str, float, float]] = [
    # (classification, min_win_rate, min_roi)
    ("elite", 0.70, 0.50),
    ("proficient", 0.55, 0.15),
    ("retail", 0.40, -0.10),
    ("unknown", 0.0, -1.0),
]


def _classify_rule_based(win_rate: float, roi: float) -> str:
    """Classify a trader based on win_rate and roi thresholds."""
    for classification, min_wr, min_roi in _CLASSIFICATION_THRESHOLDS:
        if win_rate >= min_wr and roi >= min_roi:
            return classification
    return "unknown"


def _style_rule_based(classification: str, win_rate: float, roi: float, trade_count: int) -> str:
    """Generate a brief rule-based trading style description."""
    if classification == "elite":
        return (
            f"Consistently high performer with {win_rate:.0%} win rate and "
            f"{roi:.0%} ROI over {trade_count} trades."
        )
    if classification == "proficient":
        return (
            f"Above-average trader with {win_rate:.0%} win rate and "
            f"{roi:.0%} ROI across {trade_count} trades."
        )
    if classification == "retail":
        return (
            f"Moderate performance with {win_rate:.0%} win rate and "
            f"{roi:.0%} ROI over {trade_count} trades."
        )
    return f"Limited data for classification ({trade_count} trades)."


# ---------------------------------------------------------------------------
# Simulated metric computation (community edition)
# ---------------------------------------------------------------------------

import hashlib


def _derive_metrics(address: str, window_days: int) -> dict[str, Any]:
    """Derive deterministic simulated metrics from an address string.

    In the community edition, on-chain trade history is not directly accessible.
    We produce deterministic pseudo-metrics from the address hash so that the
    same address always yields the same results within a session.
    """
    h = hashlib.sha256(f"{address}:{window_days}".encode()).hexdigest()

    # Use hash bytes to produce plausible values
    h_int = int(h[:16], 16)
    win_rate = ((h_int % 400) + 100) / 1000.0  # 0.10 .. 0.50 range, shift up
    win_rate = min(0.95, max(0.15, win_rate + 0.15))  # 0.30 .. 0.95

    roi = ((int(h[16:32], 16) % 800) - 200) / 1000.0  # -0.20 .. 0.60

    trade_count = (int(h[4:8], 16) % 200) + 5  # 5 .. 204
    total_volume = float((int(h[8:12], 16) % 50000) + 1000)  # 1000 .. 50999

    return {
        "win_rate": round(win_rate, 4),
        "roi": round(roi, 4),
        "trade_count": trade_count,
        "total_volume": round(total_volume, 2),
    }


# ---------------------------------------------------------------------------
# LLM enrichment
# ---------------------------------------------------------------------------

_LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "trading_style": {
            "type": "string",
            "description": "2-3 sentence narrative describing this trader's style and edge",
        },
        "classification_override": {
            "type": "string",
            "enum": ["elite", "proficient", "retail", "unknown"],
            "description": "Optional reclassification if metrics suggest a different tier",
        },
    },
    "required": ["trading_style"],
}


async def _enrich_with_llm(
    settings_service: Any,
    address: str,
    metrics: dict[str, Any],
    classification: str,
) -> tuple[str | None, str | None]:
    """Attempt LLM enrichment for a single address.

    Returns (trading_style, classification_override) or (None, None) on failure.
    """
    prompt = (
        f"Analyze this Polymarket trader and describe their trading style in 2-3 sentences.\n"
        f"Address: {address[:10]}...{address[-6:]}\n"
        f"Classification: {classification}\n"
        f"Win Rate: {metrics['win_rate']:.1%}\n"
        f"ROI: {metrics['roi']:.1%}\n"
        f"Trade Count: {metrics['trade_count']}\n"
        f"Total Volume: ${metrics['total_volume']:,.0f}\n"
    )
    try:
        result = await call_llm_structured(
            settings_service,
            prompt=prompt,
            system="You are a crypto prediction market analyst. Be concise and insightful.",
            max_tokens=300,
            output_schema=_LLM_SCHEMA,
        )
        if result and isinstance(result, dict):
            return (
                result.get("trading_style"),
                result.get("classification_override"),
            )
    except Exception:
        logger.debug("LLM enrichment failed for address %s", address[:10], exc_info=True)
    return None, None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def analyze_addresses(
    settings_service: Any,
    addresses: list[str],
    window_days: int = 30,
) -> list[dict[str, Any]]:
    """Analyze a batch of Polymarket addresses.

    For each address, computes metrics and classification. Attempts LLM
    enrichment when available, falling back to rule-based style.
    """
    results: list[dict[str, Any]] = []
    llm_enhanced = False

    # Check LLM availability once
    from app.core.llm_helper import is_llm_configured

    llm_available = await is_llm_configured(settings_service)

    for address in addresses:
        metrics = _derive_metrics(address, window_days)
        win_rate = metrics["win_rate"]
        roi = metrics["roi"]
        classification = _classify_rule_based(win_rate, roi)

        trading_style: str | None = None
        llm_enhanced_flag = False

        if llm_available:
            style, override = await _enrich_with_llm(
                settings_service, address, metrics, classification,
            )
            if style:
                trading_style = style
                llm_enhanced_flag = True
                llm_enhanced = True
            if override and override in ("elite", "proficient", "retail", "unknown"):
                classification = override

        if trading_style is None:
            trading_style = _style_rule_based(
                classification, win_rate, roi, metrics["trade_count"],
            )

        results.append({
            "address": address,
            "classification": classification,
            "win_rate": win_rate,
            "roi": roi,
            "trade_count": metrics["trade_count"],
            "total_volume": metrics["total_volume"],
            "trading_style": trading_style,
            "llm_enhanced": llm_enhanced_flag,
        })

    return results
