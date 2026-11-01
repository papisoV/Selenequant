"""Trend analysis — pure functions over OHLCV candle sequences.

Each candle is either an object with a ``.close`` attribute or a dict with
a ``"close"`` key.  Used by the grid sentinel tools to describe market
state to the LLM; no external state, no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Sequence

_SIDEWAYS_PCT = 0.01
_VOL_LOW = 0.005
_VOL_HIGH = 0.02


@dataclass
class TrendAnalysis:
    trend: str
    strength: float
    volatility: str
    summary: str


def _close_of(candle: Any) -> float | None:
    if isinstance(candle, dict):
        value = candle.get("close")
    else:
        value = getattr(candle, "close", None)
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def analyze_trend(candles: Sequence[Any]) -> TrendAnalysis:
    closes = [c for c in (_close_of(k) for k in candles) if c is not None]
    if len(closes) < 2:
        return TrendAnalysis(
            trend="sideways",
            strength=0.0,
            volatility="low",
            summary="数据不足，无法判断趋势",
        )

    first, last = closes[0], closes[-1]
    total_change = (last - first) / first

    # Volatility from per-step returns.
    returns = [
        (closes[i] - closes[i - 1]) / closes[i - 1]
        for i in range(1, len(closes))
        if closes[i - 1] != 0
    ]
    mean_r = sum(returns) / len(returns) if returns else 0.0
    var = sum((r - mean_r) ** 2 for r in returns) / len(returns) if returns else 0.0
    std = var**0.5

    if std < _VOL_LOW:
        volatility = "low"
    elif std > _VOL_HIGH:
        volatility = "high"
    else:
        volatility = "medium"

    if abs(total_change) <= _SIDEWAYS_PCT:
        strength = min(abs(total_change) / _SIDEWAYS_PCT * 0.3, 0.3)
        vol_txt = {"low": "低波动", "medium": "中波动", "high": "高波动"}[volatility]
        return TrendAnalysis(
            trend="sideways",
            strength=strength,
            volatility=volatility,
            summary=f"横盘震荡，累计变化 {total_change:+.2%}，{vol_txt}",
        )

    trend = "up" if total_change > 0 else "down"
    direction_word = "上行" if trend == "up" else "下行"
    magnitude = abs(total_change)

    if magnitude >= 0.05:
        strength = min(0.6 + magnitude, 1.0)
        strength_word = "强度强"
    elif magnitude >= 0.02:
        strength = min(0.3 + magnitude * 4, 0.8)
        strength_word = "强度中等"
    else:
        strength = magnitude / 0.02 * 0.3
        strength_word = "强度较弱"

    vol_txt = {"low": "低波动", "medium": "中波动", "high": "高波动"}[volatility]
    summary = f"{direction_word}趋势，{strength_word}，累计变化 {total_change:+.2%}，{vol_txt}"
    return TrendAnalysis(
        trend=trend,
        strength=strength,
        volatility=volatility,
        summary=summary,
    )
