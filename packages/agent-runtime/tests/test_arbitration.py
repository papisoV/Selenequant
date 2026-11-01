"""Behaviour locks for the objective-signal arbitration layer (P2-4).

Covers the pure function domain only: objective signal construction on
deterministic K-lines, multi-TF consensus math, and the four-layer
arbitration gate (data quality / panic / regime-scaled threshold /
objective override).
"""

from __future__ import annotations

import pandas as pd
import pytest

from selene_space_agent.arbitration import (
    Consensus,
    arbitrate,
    classify_regime,
    compute_consensus,
    compute_objective_signal,
)

_TS0 = 1_711_000_000_000


def _monotonic_df(
    n: int = 60, *, start: float = 100.0, step: float = 1.0
) -> pd.DataFrame:
    """Steadily rising K-lines (open/high/low/close all step up)."""
    rows = []
    for i in range(n):
        o = start + i * step
        c = o + step * 0.5
        rows.append(
            {
                "timestamp": _TS0 + i * 3_600_000,
                "open": o,
                "high": c + step * 0.3,
                "low": o - step * 0.1,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
    return pd.DataFrame(rows)


def _falling_df(
    n: int = 60, *, start: float = 100.0, step: float = 1.0
) -> pd.DataFrame:
    rows = []
    for i in range(n):
        o = start - i * step
        c = o - step * 0.5
        rows.append(
            {
                "timestamp": _TS0 + i * 3_600_000,
                "open": o,
                "high": o + step * 0.1,
                "low": c - step * 0.3,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
    return pd.DataFrame(rows)


def _flat_df(n: int = 60, *, price: float = 100.0) -> pd.DataFrame:
    rows = [
        {
            "timestamp": _TS0 + i * 3_600_000,
            "open": price,
            "high": price + 0.1,
            "low": price - 0.1,
            "close": price,
            "volume": 1000.0 + i,
        }
        for i in range(n)
    ]
    return pd.DataFrame(rows)


class TestComputeObjectiveSignal:
    def test_uptrend_is_long_with_positive_score(self) -> None:
        sig = compute_objective_signal(_monotonic_df())
        assert sig.direction == "long"
        assert sig.score > 0
        assert 0.0 <= sig.strength <= 1.0

    def test_downtrend_is_short_with_negative_score(self) -> None:
        sig = compute_objective_signal(_falling_df())
        assert sig.direction == "short"
        assert sig.score < 0

    def test_flat_market_is_neutral(self) -> None:
        sig = compute_objective_signal(_flat_df())
        assert sig.direction == "neutral"

    def test_insufficient_data_is_neutral_zero_strength(self) -> None:
        sig = compute_objective_signal(_monotonic_df(n=10))
        assert sig.direction == "neutral"
        assert sig.strength == 0.0
        assert sig.score == 0.0

    def test_missing_close_column_is_neutral(self) -> None:
        df = pd.DataFrame({"timestamp": [1, 2, 3]})
        sig = compute_objective_signal(df)
        assert sig.direction == "neutral"
        assert sig.score == 0.0

    def test_extreme_rsi_penalizes_uptrend_score(self) -> None:
        strong_up = _monotonic_df(n=120, step=2.0)
        sig = compute_objective_signal(strong_up)
        assert sig.direction == "long"
        assert sig.score < 100.0  # RSI extreme discount keeps it off the ceiling


class TestComputeConsensus:
    def test_all_same_direction_agreement_is_one(self) -> None:
        up = compute_objective_signal(_monotonic_df())
        c = compute_consensus({"1d": up, "4h": up, "1h": up})
        assert c.agreement == pytest.approx(1.0)
        assert c.score > 0

    def test_split_direction_agreement_below_one(self) -> None:
        up = compute_objective_signal(_monotonic_df())
        down = compute_objective_signal(_falling_df())
        c = compute_consensus({"1d": up, "4h": up, "1h": down})
        assert c.agreement < 1.0

    def test_long_timeframe_dominates_weighted_score(self) -> None:
        up = compute_objective_signal(_monotonic_df())
        down = compute_objective_signal(_falling_df())
        c = compute_consensus({"1d": down, "4h": up, "1h": up})
        # 1d weight 1.30 vs 1.15+1.0 → long should still win despite 1d short.
        assert c.score > 0
        assert c.dominant_tf == "1d"

    def test_empty_signals_neutral_consensus(self) -> None:
        c = compute_consensus({})
        assert c.score == 0.0
        assert c.agreement == 0.0
        assert c.dominant_tf == ""


def _crash_df(n: int = 60, *, start: float = 100.0, drop: float = 0.4) -> pd.DataFrame:
    """Steep sell-off: price ends far below the EMA50 anchor (panic regime)."""
    rows = []
    for i in range(n):
        c = start * (1.0 - drop * (i / max(n - 1, 1)))
        o = c * 1.002
        rows.append(
            {
                "timestamp": _TS0 + i * 3_600_000,
                "open": o,
                "high": o * 1.001,
                "low": c * 0.999,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
    return pd.DataFrame(rows)


def _surge_df(n: int = 60, *, start: float = 100.0, rise: float = 0.6) -> pd.DataFrame:
    """Steep rally: price ends far above the EMA50 anchor (panic_up regime)."""
    rows = []
    for i in range(n):
        c = start * (1.0 + rise * (i / max(n - 1, 1)))
        o = c * 0.998
        rows.append(
            {
                "timestamp": _TS0 + i * 3_600_000,
                "open": o,
                "high": o * 1.001,
                "low": c * 0.999,
                "close": c,
                "volume": 1000.0 + i,
            }
        )
    return pd.DataFrame(rows)


class TestClassifyRegime:
    def test_panic_and_ranging_buckets(self) -> None:
        assert classify_regime(_crash_df()) == "panic_down"
        assert classify_regime(_surge_df()) == "panic_up"
        assert classify_regime(_flat_df()) == "ranging"
        assert classify_regime(_monotonic_df(step=0.5)) == "trending"


class TestArbitrate:
    def test_bad_quality_forces_hold_and_halves_confidence(self) -> None:
        d = arbitrate(
            llm_direction="long",
            llm_confidence=0.8,
            consensus=Consensus(40.0, 1.0, "1d"),
            regime="trending",
            quality_ok=False,
        )
        assert d.forced_hold is True
        assert d.final_direction == "hold"
        assert d.confidence == pytest.approx(0.4)
        assert d.override_reason == "data_quality_hold"

    def test_panic_down_with_llm_long_and_consensus_long_rejected(self) -> None:
        # Falling knife: price crashed (regime=panic_down) but lagging EMAs
        # still compute a long consensus. LLM wants to buy the dip → rejected.
        d = arbitrate(
            llm_direction="long",
            llm_confidence=0.8,
            consensus=Consensus(40.0, 0.8, "1d"),
            regime="panic_down",
            quality_ok=True,
        )
        assert d.forced_hold is True
        assert d.final_direction == "hold"
        assert d.override_reason == "panic_no_flip_long"

    def test_panic_down_llm_long_neutral_consensus_raises_threshold(self) -> None:
        # Consensus neutral (score 0) but panic_down + LLM long → threshold floor 55.
        d = arbitrate(
            llm_direction="long",
            llm_confidence=0.8,
            consensus=Consensus(0.0, 0.5, "1h"),
            regime="panic_down",
            quality_ok=True,
        )
        assert d.forced_hold is False
        assert d.final_direction == "long"
        assert d.override_reason is None
        # Consensus score 0 < 55 floor, so no override; agreement scaling applied.
        assert d.confidence == pytest.approx(0.8 * (0.5 + 0.5 * 0.5))

    def test_panic_down_threshold_floor_blocks_mid_strength_conflict(self) -> None:
        # consensus -30: would override at normal 20, but panic_down floor 55 blocks.
        d = arbitrate(
            llm_direction="long",
            llm_confidence=0.7,
            consensus=Consensus(-30.0, 0.8, "1d"),
            regime="panic_down",
            quality_ok=True,
        )
        assert d.final_direction == "long"  # not overridden

    def test_ranging_threshold_multiplied(self) -> None:
        # |consensus| = 25 ≥ 20 normal threshold, but ranging ×1.2 → 24 still
        # overrides. Use 22 to prove the multiplier: 22 < 24 → no override.
        d = arbitrate(
            llm_direction="long",
            llm_confidence=0.6,
            consensus=Consensus(-22.0, 0.7, "4h"),
            regime="ranging",
            quality_ok=True,
        )
        assert d.final_direction == "long"
        assert d.override_reason is None

    def test_ranging_override_above_scaled_threshold(self) -> None:
        # |consensus| = 30 ≥ 24 (20×1.2) → override to short.
        d = arbitrate(
            llm_direction="long",
            llm_confidence=0.6,
            consensus=Consensus(-30.0, 0.7, "4h"),
            regime="ranging",
            quality_ok=True,
        )
        assert d.final_direction == "short"
        assert d.override_reason == "objective_override"

    def test_conflict_above_threshold_overrides(self) -> None:
        d = arbitrate(
            llm_direction="long",
            llm_confidence=0.6,
            consensus=Consensus(-40.0, 1.0, "1d"),
            regime="trending",
            quality_ok=True,
        )
        assert d.final_direction == "short"
        assert d.override_reason == "objective_override"
        assert d.forced_hold is False

    def test_weak_consensus_trusts_llm_with_agreement_scaling(self) -> None:
        d = arbitrate(
            llm_direction="long",
            llm_confidence=0.8,
            consensus=Consensus(10.0, 0.6, "1h"),
            regime="trending",
            quality_ok=True,
        )
        assert d.final_direction == "long"
        assert d.override_reason is None
        assert d.confidence == pytest.approx(0.8 * (0.5 + 0.5 * 0.6))

    def test_injectable_thresholds_change_outcome(self) -> None:
        # Default threshold 20 would override; inject 50 to keep LLM direction.
        d = arbitrate(
            llm_direction="long",
            llm_confidence=0.7,
            consensus=Consensus(-30.0, 1.0, "1d"),
            regime="trending",
            quality_ok=True,
            override_threshold=50.0,
        )
        assert d.final_direction == "long"

    def test_override_confidence_scaled_from_consensus(self) -> None:
        d = arbitrate(
            llm_direction="long",
            llm_confidence=0.9,
            consensus=Consensus(-80.0, 1.0, "1d"),
            regime="trending",
            quality_ok=True,
        )
        assert d.final_direction == "short"
        assert d.confidence == pytest.approx(0.8)
