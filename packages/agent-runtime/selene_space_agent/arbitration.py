"""Objective-signal arbitration for LLM price-action decisions.

Absorbed idea (QD 专题② Phase 4): LLM decisions must be checked against
objective evidence — never trusted on confidence alone. This is a pure
function domain: no IO, pandas indicator math only.

Pipeline: K-lines → per-timeframe ``ObjectiveSignal`` → weighted
``Consensus`` → ``arbitrate()`` four-layer gate (data quality / panic /
regime-scaled threshold / objective override). All thresholds are injectable
for calibration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

Direction = Literal["long", "short", "neutral"]
FinalDirection = Literal["long", "short", "neutral", "hold"]
Regime = Literal["trending", "ranging", "panic_down", "panic_up"]

# Long-period anchoring: 1d carries the most weight in consensus.
TF_WEIGHTS = {"1d": 1.30, "4h": 1.15, "1h": 1.0}

MIN_CANDLES = 30  # fewer bars → EMA/RSI/ATR estimates are unreliable
_RSI_EXTREME_PENALTY = 20.0
_ATR_HIGH = 0.02  # range/price above which signal strength gets discounted
_STRENGTH_FLOOR = 0.3
_PANIC_DEVIATION = 0.15  # price vs EMA50 distance that counts as panic
_RANGING_SPREAD = 0.005  # EMA20/50 spread below which the regime is ranging


@dataclass(frozen=True)
class ObjectiveSignal:
    direction: Direction
    strength: float  # 0..1 signal reliability
    score: float  # -100..+100 composite
    source: str  # indicator basis for the score


@dataclass(frozen=True)
class Consensus:
    score: float  # weighted composite
    agreement: float  # 0..1 cross-TF direction agreement
    dominant_tf: str  # timeframe with the largest weighted |score|


@dataclass(frozen=True)
class ArbitrationDecision:
    final_direction: FinalDirection
    confidence: float  # 0..1 post-arbitration
    override_reason: str | None
    forced_hold: bool
    consensus_score: float


def _direction_from_score(score: float) -> Direction:
    if score > 0:
        return "long"
    if score < 0:
        return "short"
    return "neutral"


def compute_objective_signal(klines: pd.DataFrame) -> ObjectiveSignal:
    """Multi-factor objective signal: EMA trend + RSI deviation + ATR volatility.

    EMA20 vs EMA50 sets the base direction; RSI in the extreme bands
    (>70/<30) discounts the trend (reversal risk, capped so it cannot flip
    the sign); high ATR% lowers signal strength. ``score`` ∈ [-100, +100].
    """
    if klines is None or len(klines) < MIN_CANDLES or "close" not in klines:
        return ObjectiveSignal("neutral", 0.0, 0.0, "insufficient_data")

    close = klines["close"].astype(float)
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    last_ema20 = float(ema20.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])
    last_price = float(close.iloc[-1])

    base_score = 0.0
    if last_price > 0:
        diff_pct = (last_ema20 - last_ema50) / last_price
        base_score = float(np.clip(diff_pct * 10_000.0, -100.0, 100.0))

    score = base_score
    last_rsi = float(_compute_rsi(close).iloc[-1])
    if last_rsi > 70.0 and base_score > 0:
        score = base_score - min(base_score, _RSI_EXTREME_PENALTY)
    elif last_rsi < 30.0 and base_score < 0:
        score = base_score + min(-base_score, _RSI_EXTREME_PENALTY)
    score = float(np.clip(score, -100.0, 100.0))

    atr_pct = _compute_atr_pct(klines)
    strength = 1.0
    if atr_pct > _ATR_HIGH:
        strength = max(_STRENGTH_FLOOR, 1.0 - (atr_pct - _ATR_HIGH) * 15.0)

    source = (
        f"ema20={last_ema20:.4g} ema50={last_ema50:.4g} "
        f"rsi={last_rsi:.1f} atr_pct={atr_pct:.4f}"
    )
    return ObjectiveSignal(_direction_from_score(score), strength, score, source)


def compute_consensus(tf_signals: dict[str, ObjectiveSignal]) -> Consensus:
    """Weighted multi-timeframe consensus anchored on the long period.

    ``score`` = Σ(weight × tf score) / Σ weight. ``agreement`` = share of
    TFs pointing in the consensus direction (same-direction count / total).
    """
    if not tf_signals:
        return Consensus(0.0, 0.0, "")

    total_weight = 0.0
    weighted_score = 0.0
    directions: list[Direction] = []
    for tf, sig in tf_signals.items():
        weight = TF_WEIGHTS.get(tf, 1.0)
        weighted_score += weight * sig.score
        total_weight += weight
        directions.append(sig.direction)

    score = weighted_score / total_weight
    consensus_direction = _direction_from_score(score)
    if consensus_direction == "neutral":
        agreement = 0.0
    else:
        agreement = sum(1 for d in directions if d == consensus_direction) / len(
            directions
        )

    dominant_tf = max(
        tf_signals,
        key=lambda tf: TF_WEIGHTS.get(tf, 1.0) * abs(tf_signals[tf].score),
    )
    return Consensus(score, agreement, dominant_tf)


def classify_regime(klines: pd.DataFrame) -> Regime:
    """Bucket price action into a market regime for ``arbitrate``.

    Regime is derived from price *deviation from trend*, independent of the
    weighted consensus — this is what lets ``arbitrate`` catch a falling
    knife: price can be crashing (panic_down) while lagging EMAs still
    compute a long consensus.
    """
    if klines is None or len(klines) < MIN_CANDLES or "close" not in klines:
        return "ranging"

    close = klines["close"].astype(float)
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema50 = close.ewm(span=50, adjust=False).mean()
    last_price = float(close.iloc[-1])
    last_ema50 = float(ema50.iloc[-1])
    if last_ema50 == 0:
        return "ranging"

    deviation = (last_price - last_ema50) / last_ema50
    ema_spread = (float(ema20.iloc[-1]) - last_ema50) / last_ema50

    if deviation <= -_PANIC_DEVIATION:
        return "panic_down"
    if deviation >= _PANIC_DEVIATION:
        return "panic_up"
    if abs(ema_spread) < _RANGING_SPREAD:
        return "ranging"
    return "trending"


def arbitrate(
    *,
    llm_direction: Direction,
    llm_confidence: float,
    consensus: Consensus,
    regime: Regime,
    quality_ok: bool,
    override_threshold: float = 20.0,  # objective consensus overrides LLM at |score| ≥ this
    ranging_override_mult: float = 1.2,  # ranging regime × this to make override harder
    panic_override_floor: float = 55.0,  # panic_down floor for the override threshold
    quality_hold_threshold: float = 0.7,  # caller-side data-quality gate, reserved for calibration
) -> ArbitrationDecision:
    """Four-layer arbitration over an LLM price-action decision.

    Priority (rule table in the P2-4 spec):
      1. bad data → forced HOLD, confidence halved
      2. panic_down + LLM long + consensus long → reject the long flip
      3. panic_down + LLM long → override threshold raised to the floor
      4. ranging → threshold × mult (harder to override)
      5. |consensus| ≥ effective threshold and conflicting → objective override
      6. otherwise → trust LLM, confidence scaled by cross-TF agreement

    ``quality_hold_threshold`` is passed through for future calibration; the
    operative data-quality switch here is the ``quality_ok`` boolean.
    """
    confidence = float(np.clip(llm_confidence, 0.0, 1.0))

    if not quality_ok:
        return ArbitrationDecision(
            "hold",
            confidence * 0.5,
            "data_quality_hold",
            True,
            consensus.score,
        )

    effective_threshold = override_threshold
    if regime == "ranging":
        effective_threshold *= ranging_override_mult

    if regime == "panic_down":
        if llm_direction == "long":
            effective_threshold = max(effective_threshold, panic_override_floor)
            if _direction_from_score(consensus.score) == "long":
                return ArbitrationDecision(
                    "hold",
                    confidence * 0.5,
                    "panic_no_flip_long",
                    True,
                    consensus.score,
                )

    consensus_direction = _direction_from_score(consensus.score)
    if consensus_direction != "neutral" and consensus_direction != llm_direction:
        if abs(consensus.score) >= effective_threshold:
            override_confidence = min(1.0, abs(consensus.score) / 100.0)
            return ArbitrationDecision(
                consensus_direction,
                override_confidence,
                "objective_override",
                False,
                consensus.score,
            )

    scaled_confidence = confidence * (0.5 + 0.5 * consensus.agreement)
    return ArbitrationDecision(
        llm_direction,
        scaled_confidence,
        None,
        False,
        consensus.score,
    )


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    rsi = 100.0 - 100.0 / (1.0 + rs)
    # Pure-gain streak → rs=inf → RSI 100; flat series → 0/0 NaN → 50.
    return rsi.replace([np.inf, -np.inf], 100.0).fillna(50.0)


def _compute_atr_pct(klines: pd.DataFrame, period: int = 14) -> float:
    high = klines["high"].astype(float)
    low = klines["low"].astype(float)
    close = klines["close"].astype(float)
    prev_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    atr = float(true_range.ewm(alpha=1.0 / period, adjust=False).mean().iloc[-1])
    last_price = float(close.iloc[-1])
    if last_price == 0:
        return 0.0
    return atr / last_price
