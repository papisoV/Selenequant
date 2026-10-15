"""Signal scoring for stock assets.

Accepts a flat metrics dict (as produced by ``calculate_indicators``) and
returns a ``SignalScore`` from ``selene_space_types.stock``.
"""

from __future__ import annotations

from selene_space_types.stock import SignalScore


def score_asset(metrics: dict[str, float | int | str | None]) -> SignalScore:
    """Score an asset based on its indicator metrics and return a SignalScore."""
    score = 50
    reasons: list[str] = []

    last = _num(metrics.get("last_close"))
    sma20 = _num(metrics.get("sma20"))
    sma50 = _num(metrics.get("sma50"))
    sma200 = _num(metrics.get("sma200"))
    rsi = _num(metrics.get("rsi14"))
    macd = _num(metrics.get("macd"))
    macd_signal = _num(metrics.get("macd_signal"))
    ret20 = _num(metrics.get("return_20d"))
    ret60 = _num(metrics.get("return_60d"))
    drawdown = _num(metrics.get("drawdown_60d"))
    atr_pct = _num(metrics.get("atr_pct"))
    volume_ratio = _num(metrics.get("volume_ratio_20d"))

    if last is not None and sma20 is not None:
        if last > sma20:
            score += 8
            reasons.append("Price is above the 20-day average.")
        else:
            score -= 6
            reasons.append("Price is below the 20-day average.")
    if last is not None and sma50 is not None:
        if last > sma50:
            score += 8
            reasons.append("Price is above the 50-day average.")
        else:
            score -= 7
            reasons.append("Price is below the 50-day average.")
    if last is not None and sma200 is not None:
        if last > sma200:
            score += 5
            reasons.append("Long-term trend remains above the 200-day average.")
        else:
            score -= 8
            reasons.append("Long-term trend is below the 200-day average.")
    if sma20 is not None and sma50 is not None:
        if sma20 > sma50:
            score += 5
            reasons.append("Short trend is stronger than medium trend.")
        else:
            score -= 4
            reasons.append("Short trend is weaker than medium trend.")
    if rsi is not None:
        if 45 <= rsi <= 65:
            score += 6
            reasons.append("RSI is in a balanced momentum zone.")
        elif 30 <= rsi < 45:
            score += 1
            reasons.append("RSI is soft but not deeply oversold.")
        elif rsi < 30:
            score -= 3
            reasons.append("RSI is oversold; rebound risk and trend weakness coexist.")
        elif 65 < rsi <= 75:
            score += 2
            reasons.append("RSI shows strong momentum but needs overheating checks.")
        else:
            score -= 7
            reasons.append("RSI is overheated.")
    if macd is not None and macd_signal is not None:
        if macd > macd_signal:
            score += 5
            reasons.append("MACD is above signal line.")
        else:
            score -= 4
            reasons.append("MACD is below signal line.")
    if ret20 is not None:
        if ret20 > 0.05:
            score += 5
            reasons.append("20-day return is strongly positive.")
        elif ret20 < -0.05:
            score -= 6
            reasons.append("20-day return is meaningfully negative.")
    if ret60 is not None:
        if ret60 > 0.08:
            score += 4
            reasons.append("60-day return confirms medium-term strength.")
        elif ret60 < -0.08:
            score -= 5
            reasons.append("60-day return confirms medium-term weakness.")
    if drawdown is not None:
        if drawdown < -0.18:
            score -= 8
            reasons.append("Recent drawdown is large.")
        elif drawdown < -0.10:
            score -= 4
            reasons.append("Recent drawdown deserves risk control.")
    if atr_pct is not None and atr_pct > 0.045:
        score -= 4
        reasons.append("ATR implies elevated daily price range.")
    if volume_ratio is not None and volume_ratio > 1.5 and ret20 is not None and ret20 > 0:
        score += 3
        reasons.append("Volume expansion supports recent strength.")

    clipped = max(0, min(100, round(score)))
    return SignalScore(
        score=clipped,
        stance=_stance(clipped),
        risk_level=_risk_level(drawdown, atr_pct),
        reasons=reasons[:6],
    )


def _stance(score: int) -> str:
    if score >= 75:
        return "constructive"
    if score >= 60:
        return "watch bullish"
    if score >= 45:
        return "neutral"
    if score >= 30:
        return "defensive"
    return "high risk"


def _risk_level(drawdown: float | None, atr_pct: float | None) -> str:
    if (drawdown is not None and drawdown < -0.18) or (atr_pct is not None and atr_pct > 0.06):
        return "high"
    if (drawdown is not None and drawdown < -0.10) or (atr_pct is not None and atr_pct > 0.035):
        return "medium"
    return "low"


def _num(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
