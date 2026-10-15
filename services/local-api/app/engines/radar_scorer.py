"""US Radar scoring engine — 8-dimension radar score computation.

Dimensions and weights match the online SeleneQuant version:
  news(+19), sentiment(+5), technical(0), volume(+7),
  l2(0), relationship(+10), risk(0), flow(+4)

Simple community-edition implementations using Yahoo Finance data.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Dimension weights (from online version)
_WEIGHTS: dict[str, int] = {
    "news": 19,
    "sentiment": 5,
    "technical": 0,
    "volume": 7,
    "l2": 0,
    "relationship": 10,
    "risk": 0,
    "flow": 4,
}

_TOTAL_WEIGHT = sum(_WEIGHTS.values())  # 45


def _score_news(symbol_data: dict[str, Any]) -> int:
    """Score from news headline count and recency."""
    news_items = symbol_data.get("news", [])
    count = len(news_items)
    if count == 0:
        return 0
    if count <= 2:
        return 30
    if count <= 5:
        return 55
    if count <= 10:
        return 75
    return 90


def _score_sentiment(symbol_data: dict[str, Any]) -> int:
    """Score from price action (up = bullish, down = bearish)."""
    quote = symbol_data.get("quote", {})
    change_pct = float(quote.get("regularMarketChangePercent", 0) or 0)
    # Map -10..+10% to 0..100
    raw = 50 + change_pct * 5
    return max(0, min(100, int(raw)))


def _score_technical(symbol_data: dict[str, Any]) -> int:
    """Score from simple moving average position."""
    chart = symbol_data.get("chart", {})
    closes = chart.get("closes", [])
    if len(closes) < 20:
        return 50  # neutral when insufficient data
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50 if len(closes) >= 50 else ma20
    current = closes[-1]
    if current > ma20 > ma50:
        return 80
    if current > ma20:
        return 65
    if current < ma20 < ma50:
        return 20
    if current < ma20:
        return 35
    return 50


def _score_volume(symbol_data: dict[str, Any]) -> int:
    """Score from relative volume vs average."""
    quote = symbol_data.get("quote", {})
    vol = float(quote.get("regularMarketVolume", 0) or 0)
    avg_vol = float(quote.get("averageDailyVolume3Month", 0) or 0)
    if avg_vol == 0:
        return 50
    ratio = vol / avg_vol
    if ratio > 3.0:
        return 95
    if ratio > 2.0:
        return 80
    if ratio > 1.5:
        return 65
    if ratio > 1.0:
        return 50
    if ratio > 0.5:
        return 30
    return 15


def _score_l2(symbol_data: dict[str, Any]) -> int:
    """L2 orderbook score — not available in community edition."""
    return 50


def _score_relationship(symbol_data: dict[str, Any]) -> int:
    """Score from sector/correlation relationships."""
    chains = symbol_data.get("relationship_chains", [])
    if not chains:
        return 50
    # More chains = stronger relationship signal
    if len(chains) >= 5:
        return 75
    if len(chains) >= 3:
        return 60
    return 50


def _score_risk(symbol_data: dict[str, Any]) -> int:
    """Risk score — placeholder for community edition."""
    return 50


def _score_flow(symbol_data: dict[str, Any]) -> int:
    """Money flow score from volume and price direction."""
    quote = symbol_data.get("quote", {})
    change = float(quote.get("regularMarketChange", 0) or 0)
    vol = float(quote.get("regularMarketVolume", 0) or 0)
    if vol == 0:
        return 50
    # Positive change + high volume = strong flow
    if change > 0 and vol > 0:
        return min(100, 50 + int(abs(change) * 10))
    if change < 0 and vol > 0:
        return max(0, 50 - int(abs(change) * 10))
    return 50


_SCORERS = {
    "news": _score_news,
    "sentiment": _score_sentiment,
    "technical": _score_technical,
    "volume": _score_volume,
    "l2": _score_l2,
    "relationship": _score_relationship,
    "risk": _score_risk,
    "flow": _score_flow,
}


def compute_radar_score(symbol_data: dict[str, Any]) -> dict[str, Any]:
    """Compute 8-dimension radar score for a symbol.

    Returns:
        dict with total_score, dimensions, signals, and weights.
    """
    dimensions: dict[str, int] = {}
    weighted_sum = 0
    signals: list[str] = []

    for dim, scorer in _SCORERS.items():
        score = scorer(symbol_data)
        dimensions[dim] = score
        weight = _WEIGHTS[dim]
        weighted_sum += score * weight

        # Generate signals
        if score >= 70:
            signals.append(f"{dim}_strong_positive")
        elif score >= 55:
            signals.append(f"{dim}_positive")
        elif score <= 30:
            signals.append(f"{dim}_strong_negative")
        elif score <= 45:
            signals.append(f"{dim}_negative")

    total_score = (
        int(weighted_sum / _TOTAL_WEIGHT) if _TOTAL_WEIGHT > 0 else 0
    )

    return {
        "total_score": total_score,
        "dimensions": dimensions,
        "weights": _WEIGHTS,
        "signals": signals,
    }


def classify_signal(score_data: dict[str, Any]) -> str:
    """Classify overall signal as bullish/bearish/neutral."""
    total = score_data.get("total_score", 50)
    if total >= 60:
        return "bullish"
    if total <= 40:
        return "bearish"
    return "neutral"
