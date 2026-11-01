"""Tests for Serenity signal_bridge — ser_* factor fail-loud + live-only behavior.

Covers the P0-1 fix: compute_serenity_* previously returned silent constant
neutral scores (0.5 / 0.0) when the SERENITY_* column was absent, fabricating
fake-neutral factors for bare kline DataFrames. They now raise
SerenityDataMissingError instead.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Direct imports bypassing selene_space_alpha.__init__ (which triggers torch)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from serenity.models import SerenityScore  # noqa: E402
from serenity.pool import PoolCandidate, SerenityPoolScan  # noqa: E402
from serenity.signal_bridge import (  # noqa: E402
    SERENITY_FACTORS,
    SERENITY_LIVE_ONLY,
    SERENITY_POOL_FACTORS,
    SerenityDataMissingError,
    compute_serenity_penalty,
    compute_serenity_pool_rank,
    compute_serenity_score,
    merge_pool_rank_into_df,
    merge_serenity_into_df,
)


def _kline_df(n: int = 10) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime("2026-01-01")
            + pd.TimedeltaIndex(range(n), unit="min"),
            "open": np.linspace(100, 110, n),
            "high": np.linspace(101, 112, n),
            "low": np.linspace(99, 108, n),
            "close": np.linspace(100.5, 111, n),
            "volume": np.full(n, 1000.0),
        }
    )


def _make_score(
    symbol: str,
    timestamp: int,
    normalized: float = 0.7,
    consensus: float = 0.6,
    liquidity: float = 0.5,
    prepay: float = 0.4,
    evidence: float = 0.65,
    penalties: tuple[float, ...] = (0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1, 0.1),
) -> SerenityScore:
    return SerenityScore(
        symbol=symbol,
        consensus_scarcity=consensus,
        liquidity_scarcity=liquidity,
        trust_scarcity=0.5,
        replication_difficulty=0.5,
        certification_progress=0.5,
        prepayment_signal=prepay,
        decentralization=0.5,
        evidence_strength=evidence,
        penalty_fork_risk=penalties[0],
        penalty_tvl_concentration=penalties[1],
        penalty_dev_exodus=penalties[2],
        penalty_revenue_decline=penalties[3],
        penalty_oi_divergence=penalties[4],
        penalty_whale_exodus=penalties[5],
        penalty_governance_risk=penalties[6],
        penalty_liquidity_trap=penalties[7],
        raw_score=0.0,
        normalized_score=normalized,
        tier="high",
        timestamp=timestamp,
    )


# --- Fail-loud: bare kline DataFrame must raise, not return constants ---


def test_missing_score_column_raises():
    with pytest.raises(SerenityDataMissingError):
        compute_serenity_score(_kline_df())


def test_missing_penalty_column_raises():
    with pytest.raises(SerenityDataMissingError):
        compute_serenity_penalty(_kline_df())


def test_missing_column_error_message_mentions_live_only():
    with pytest.raises(SerenityDataMissingError, match="live-only"):
        compute_serenity_score(_kline_df())


# --- Present-column path still works (sparse scores filled neutral) ---


def test_score_column_returns_series_with_nan_filled_0_5():
    df = _kline_df()
    df["SERENITY_SCORE"] = [np.nan, 0.8, 0.6, 0.7, np.nan, 0.55, 0.65, 0.75, 0.85, 0.5]
    result = compute_serenity_score(df)
    assert len(result) == len(df)
    assert result.iloc[0] == 0.5
    assert result.iloc[1] == 0.8
    assert result.iloc[4] == 0.5
    assert result.iloc[9] == 0.5


def test_penalty_column_returns_series_with_nan_filled_0_0():
    df = _kline_df()
    df["SERENITY_PENALTY"] = [np.nan, 0.3, 0.1, np.nan, 0.0, 0.2, 0.4, 0.5, 0.6, 0.1]
    result = compute_serenity_penalty(df)
    assert result.iloc[0] == 0.0
    assert result.iloc[1] == 0.3
    assert result.iloc[3] == 0.0


# --- Live-only markers ---


def test_serenity_live_only_flag_true():
    assert SERENITY_LIVE_ONLY is True


def test_all_factor_descriptions_marked_live_only():
    for name, (_fn, desc) in SERENITY_FACTORS.items():
        assert "live-only" in desc, f"{name} description not marked live-only"


# --- merge_serenity_into_df round-trip (the documented feeding path) ---


def test_merge_serenity_into_df_populates_all_columns():
    df = _kline_df(n=6)
    scores = [_make_score("BTC", int(ts)) for ts in [1735689600, 1735689720]]
    merged = merge_serenity_into_df(df, scores)

    expected = {
        "SERENITY_SCORE", "SERENITY_CONSENSUS", "SERENITY_LIQUIDITY",
        "SERENITY_PREPAY", "SERENITY_EVIDENCE", "SERENITY_PENALTY",
    }
    assert expected <= set(merged.columns)
    # First score row: normalized 0.7 -> SERENITY_SCORE 0.7
    assert merged["SERENITY_SCORE"].iloc[0] == 0.7
    # Penalty mean of (0.1 x8) -> 0.1
    assert merged["SERENITY_PENALTY"].iloc[0] == pytest.approx(0.1)


def test_merge_serenity_into_df_empty_scores_returns_nan_columns():
    df = _kline_df(n=4)
    merged = merge_serenity_into_df(df, [])
    for col in ["SERENITY_SCORE", "SERENITY_PENALTY"]:
        assert merged[col].isna().all()


def test_merge_serenity_into_df_empty_scores_does_not_mutate_input():
    df = _kline_df(n=4)
    merge_serenity_into_df(df, [])
    assert not any(c.startswith("SERENITY_") for c in df.columns)


def test_merge_serenity_into_df_epoch_ms_klines_aligned_with_epoch_s_scores():
    # Kline frames carry epoch-ms timestamps; SerenityScore.timestamp is epoch s.
    base_s = 1735689600
    df = pd.DataFrame(
        {
            "timestamp": [(base_s + i) * 1000 for i in range(6)],
            "close": np.linspace(100, 110, 6),
        }
    )
    scores = [
        _make_score("BTC", base_s, normalized=0.8),
        _make_score("BTC", base_s + 4, normalized=0.3),
    ]
    merged = merge_serenity_into_df(df, scores)

    assert "SERENITY_SCORE" in merged.columns
    # Klines at t0..t3 fall back to the first score (0.8, backward asof).
    assert merged["SERENITY_SCORE"].iloc[0] == 0.8
    assert merged["SERENITY_SCORE"].iloc[3] == 0.8
    # Klines at t4..t5 pick the second score (0.3).
    assert merged["SERENITY_SCORE"].iloc[4] == 0.3
    assert merged["SERENITY_SCORE"].iloc[5] == 0.3


# --- Theme-pool rank seam (F4, dead until a live caller wires it) ---


def _pool_scan() -> SerenityPoolScan:
    def cand(symbol: str, scarce: float) -> PoolCandidate:
        return PoolCandidate(
            symbol=symbol, name=symbol, category="Lending", chains=("eth",),
            normalized_score=0.7, tier="bullish", scarce_layer_score=scarce,
            evidence_strength=0.5, evidence_label="强", rationale="x",
            top_penalty="fork_risk:0.5",
        )

    return SerenityPoolScan(
        theme="lending", theme_name="Lending", scanned_at=1735689600000,
        universe_size=3,
        layer_summary=(),
        ranked=(cand("AAA", 0.9), cand("BBB", 0.6), cand("CCC", 0.3)),
        top_picks=("AAA",),
        weak_evidence_count=0,
    )


def test_pool_rank_missing_column_raises():
    with pytest.raises(SerenityDataMissingError):
        compute_serenity_pool_rank(_kline_df())


def test_pool_rank_known_symbol_top_of_pool_scores_1():
    df = _kline_df(n=6)
    df["symbol"] = "AAA"  # rank 1 of a 3-pool -> 1 - 0/3 = 1.0
    merged = merge_pool_rank_into_df(df, _pool_scan())
    result = compute_serenity_pool_rank(merged)
    assert (result == 1.0).all()


def test_pool_rank_mid_pool_positions_scaled():
    df = _kline_df(n=3)
    df["symbol"] = ["AAA", "BBB", "CCC"]
    merged = merge_pool_rank_into_df(df, _pool_scan())
    result = compute_serenity_pool_rank(merged)
    assert result.iloc[0] == pytest.approx(1.0)       # rank 1/3
    assert result.iloc[1] == pytest.approx(1 - 1 / 3)  # rank 2/3
    assert result.iloc[2] == pytest.approx(1 - 2 / 3)  # rank 3/3


def test_pool_rank_unknown_symbol_resolves_neutral_0_5():
    df = _kline_df(n=4)
    df["symbol"] = "ZZZ"  # not in the scanned pool
    merged = merge_pool_rank_into_df(df, _pool_scan())
    result = compute_serenity_pool_rank(merged)
    assert (result == 0.5).all()


def test_pool_rank_merge_does_not_mutate_input():
    df = _kline_df(n=4)
    df["symbol"] = "AAA"
    merge_pool_rank_into_df(df, _pool_scan())
    assert "SERENITY_POOL_RANK" not in df.columns


def test_pool_factor_descriptions_marked_live_only():
    for name, (_fn, desc) in SERENITY_POOL_FACTORS.items():
        assert "live-only" in desc, f"{name} description not marked live-only"
