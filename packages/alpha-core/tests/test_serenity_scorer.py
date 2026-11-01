"""Tests for Serenity scorer — missing-data symmetric degradation (P0-2)
and sigmoid slope calibration (P0-4).

P0-2: the 4 "soft" penalties (_penalty_oi_divergence, _penalty_whale_exodus,
_penalty_governance_risk, _penalty_liquidity_trap) previously defaulted to a
lenient 0.3 when their data source was absent, while factors defaulted to 0.5.
A fully missing-data symbol therefore scored ~0.537 (mildly bullish) — a
fabricated bullish signal. Missing penalties now default to 0.5 (neutral), so a
no-data symbol lands below the 0.5 midpoint in the neutral tier.

P0-4: the sigmoid slope was 6.0, putting the bullish threshold at raw ~0.033 —
any profile with a sliver of data read bullish. The slope is now configurable
(default 3.0), raising the bullish threshold to raw ~0.067 and strong_bullish
to raw ~0.28, so tiers align with meaningful raw values.

P1-2: evidence_strength grades by data VALUE, not just field presence — a
zero-TVL protocol record or an inactive repo earns reduced credit, so two
field-complete snapshots with different values score different evidence.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Direct imports bypassing selene_space_alpha.__init__ (which triggers torch)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from serenity.config import SerenityConfig  # noqa: E402
from serenity.models import (  # noqa: E402
    GitHubActivity,
    ProtocolMetrics,
    SerenitySnapshot,
)
from serenity.scorer import SerenityScorer  # noqa: E402


def _empty_snapshot(symbol: str = "AAA") -> SerenitySnapshot:
    return SerenitySnapshot(
        symbol=symbol,
        protocol_metrics=None,
        github_activity=None,
        funding_rate=None,
        oi_change=None,
        ls_ratio=None,
        tvl_chain_share=0.0,
        price_change_7d=0.0,
        volume_change_7d=0.0,
        timestamp=1735689600,
    )


def _rich_snapshot() -> SerenitySnapshot:
    pm = ProtocolMetrics(
        protocol_id="aaa",
        name="AAA",
        category="chain",
        chains=("eth", "sol", "arb", "base", "bsc"),
        tvl_usd=1e10,
        tvl_change_1d=0.05,
        tvl_change_7d=0.2,
        mcap_to_tvl=1.0,
        fdv_to_tvl=1.2,
        revenue_24h=1e6,
        revenue_7d=1e7,
        timestamp=1735689600,
    )
    ga = GitHubActivity(
        protocol_id="aaa",
        repo_name="aaa",
        stars=10000,
        commits_4w=500,
        active_devs_4w=100,
        open_issues=10,
        closed_issues_4w=50,
        timestamp=1735689600,
    )
    return SerenitySnapshot(
        symbol="AAA",
        protocol_metrics=pm,
        github_activity=ga,
        funding_rate=0.01,
        oi_change=0.02,
        ls_ratio=1.1,
        tvl_chain_share=0.5,
        price_change_7d=0.1,
        volume_change_7d=0.05,
        timestamp=1735689600,
    )


# --- Missing-data symmetric degradation ---


def test_all_missing_data_scores_below_neutral():
    scorer = SerenityScorer()
    score = scorer.score(_empty_snapshot())
    # Regression: was ~0.537 (mildly bullish) with lenient 0.3 penalty defaults.
    assert score.normalized_score < 0.5
    # Stays inside the neutral tier (0.35..0.55) — no false bearish either.
    assert score.normalized_score > SerenityConfig().bearish_th
    assert score.tier == "neutral"


def test_all_missing_penalties_default_neutral():
    scorer = SerenityScorer()
    empty = _empty_snapshot()
    assert scorer._penalty_fork_risk(empty) == 0.5
    assert scorer._penalty_tvl_concentration(empty) == 0.5
    assert scorer._penalty_dev_exodus(empty) == 0.5
    assert scorer._penalty_revenue_decline(empty) == 0.5
    # Previously-lenient penalties (0.3) now default neutral.
    assert scorer._penalty_oi_divergence(empty) == 0.5
    assert scorer._penalty_whale_exodus(empty) == 0.5
    assert scorer._penalty_governance_risk(empty) == 0.5
    assert scorer._penalty_liquidity_trap(empty) == 0.5


def test_all_missing_factors_default_neutral_except_evidence():
    scorer = SerenityScorer()
    empty = _empty_snapshot()
    assert scorer._consensus_scarcity(empty) == 0.5
    assert scorer._liquidity_scarcity(empty) == 0.5
    assert scorer._trust_scarcity(empty) == 0.5
    assert scorer._replication_difficulty(empty) == 0.5
    assert scorer._certification_progress(empty) == 0.5
    assert scorer._prepayment_signal(empty) == 0.5
    assert scorer._decentralization(empty) == 0.5
    # Evidence measures data presence itself — no data, no evidence.
    assert scorer._evidence_strength(empty) == 0.0


def test_partial_data_evidence_not_below_no_data():
    scorer = SerenityScorer()
    partial = SerenitySnapshot(
        symbol="PART",
        protocol_metrics=_rich_snapshot().protocol_metrics,
        github_activity=None,
        funding_rate=None,
        oi_change=None,
        ls_ratio=None,
        tvl_chain_share=0.0,
        price_change_7d=0.0,
        volume_change_7d=0.0,
        timestamp=1735689600,
    )
    # More data must never score lower on the evidence factor than no data.
    assert scorer._evidence_strength(partial) > scorer._evidence_strength(_empty_snapshot())


# --- Full-data signals must be preserved (no suppression) ---


def test_full_data_snapshot_scores_bullish():
    scorer = SerenityScorer()
    score = scorer.score(_rich_snapshot())
    assert score.evidence_strength == 1.0
    assert score.normalized_score > 0.55
    # A genuinely strong profile still reaches the top tier under slope 3.0.
    assert score.tier == "strong_bullish"


# --- 0.0 (flat) vs None (missing) distinction (P0-3) ---


def _snapshot_with(oi_change, price_change, volume_change, protocol_metrics=None):
    return SerenitySnapshot(
        symbol="AAA",
        protocol_metrics=protocol_metrics,
        github_activity=None,
        funding_rate=None,
        oi_change=oi_change,
        ls_ratio=None,
        tvl_chain_share=0.0,
        price_change_7d=price_change,
        volume_change_7d=volume_change,
        timestamp=1735689600,
    )


def test_oi_divergence_flat_price_not_penalized():
    # Genuine 0% move is NOT "no data" — no divergence to flag.
    scorer = SerenityScorer()
    snap = _snapshot_with(oi_change=0.1, price_change=0.0, volume_change=0.0)
    assert scorer._penalty_oi_divergence(snap) == 0.1


def test_oi_divergence_missing_price_neutral():
    scorer = SerenityScorer()
    snap = _snapshot_with(oi_change=0.1, price_change=None, volume_change=0.0)
    assert scorer._penalty_oi_divergence(snap) == 0.5


def test_liquidity_trap_missing_volume_neutral():
    # None volume must not crash the `None < -0.05` comparison.
    scorer = SerenityScorer()
    snap = _snapshot_with(
        oi_change=None, price_change=None, volume_change=None,
        protocol_metrics=_rich_snapshot().protocol_metrics,
    )
    assert scorer._penalty_liquidity_trap(snap) == 0.5


# --- Sigmoid slope calibration (P0-4) ---


def _market_data_only_snapshot() -> SerenitySnapshot:
    """Flat derivatives data, no protocol/GitHub: all factors/penalties neutral
    except evidence (funding/OI/LS present)."""
    return SerenitySnapshot(
        symbol="MDO",
        protocol_metrics=None,
        github_activity=None,
        funding_rate=0.0,
        oi_change=0.0,
        ls_ratio=1.0,
        tvl_chain_share=0.0,
        price_change_7d=0.0,
        volume_change_7d=0.0,
        timestamp=1735689600,
    )


def test_market_data_only_snapshot_not_bullish():
    # Regression (P0-4): with slope 6.0 this raw (~0.033) landed right on the
    # bullish boundary (0.5501). Only flat market data must stay neutral; the
    # 0.55 bullish threshold now requires raw >= ~0.067.
    scorer = SerenityScorer()
    score = scorer.score(_market_data_only_snapshot())
    assert 0.02 < score.raw_score < 0.067
    assert score.tier == "neutral"


def test_sigmoid_slope_configurable():
    snap = _market_data_only_snapshot()
    gentle = SerenityScorer().score(snap)  # default slope 3.0
    steep = SerenityScorer(SerenityConfig(sigmoid_slope=6.0)).score(snap)
    assert gentle.normalized_score < steep.normalized_score
    assert gentle.tier == "neutral"
    assert steep.tier == "bullish"


def _protocol_only_snapshot() -> SerenitySnapshot:
    pm = ProtocolMetrics(
        protocol_id="c",
        name="C",
        category="chain",
        chains=("eth",),
        tvl_usd=3e8,
        tvl_change_1d=0.0,
        tvl_change_7d=0.0,
        mcap_to_tvl=2.5,
        fdv_to_tvl=3.0,
        revenue_24h=2e4,
        revenue_7d=1.4e5,
        timestamp=1735689600,
    )
    return SerenitySnapshot(
        symbol="C",
        protocol_metrics=pm,
        github_activity=None,
        funding_rate=None,
        oi_change=None,
        ls_ratio=None,
        tvl_chain_share=0.0,
        price_change_7d=0.01,
        volume_change_7d=0.0,
        timestamp=1735689600,
    )


def test_moderate_positive_raw_is_bullish_not_strong():
    # Slope 6.0 mapped this modest profile (raw ~0.15) to 0.717 = strong_bullish.
    # "Strong" must be reserved for genuinely strong profiles; slope 3.0 keeps it bullish.
    scorer = SerenityScorer()
    score = scorer.score(_protocol_only_snapshot())
    assert 0.10 < score.raw_score < 0.20
    assert score.tier == "bullish"


# --- Evidence grading by value, not presence (P1-2) ---


def _all_fields_snapshot(
    *,
    tvl: float,
    stars: int,
    commits: int,
    chains: tuple[str, ...] = ("eth",),
    revenue_7d: float = 0.0,
) -> SerenitySnapshot:
    """Full field coverage with parameterized values (for evidence grading)."""
    pm = ProtocolMetrics(
        protocol_id="aaa",
        name="AAA",
        category="chain",
        chains=chains,
        tvl_usd=tvl,
        tvl_change_1d=0.0,
        tvl_change_7d=0.0,
        mcap_to_tvl=2.0,
        fdv_to_tvl=2.5,
        revenue_24h=0.0,
        revenue_7d=revenue_7d,
        timestamp=1735689600,
    )
    ga = GitHubActivity(
        protocol_id="aaa",
        repo_name="aaa",
        stars=stars,
        commits_4w=commits,
        active_devs_4w=commits,
        open_issues=0,
        closed_issues_4w=0,
        timestamp=1735689600,
    )
    return SerenitySnapshot(
        symbol="AAA",
        protocol_metrics=pm,
        github_activity=ga,
        funding_rate=0.01,
        oi_change=0.02,
        ls_ratio=1.1,
        tvl_chain_share=0.5,
        price_change_7d=0.0,
        volume_change_7d=0.0,
        timestamp=1735689600,
    )


def test_evidence_grades_by_value_not_presence():
    # Acceptance (P1-2): same field coverage, different values -> different evidence.
    rich = _all_fields_snapshot(tvl=1e9, stars=1000, commits=100, revenue_7d=7e5)
    degenerate = _all_fields_snapshot(
        tvl=0.0, stars=0, commits=0, chains=(), revenue_7d=0.0,
    )
    scorer = SerenityScorer()
    assert scorer._evidence_strength(rich) == 1.0
    # Zero-TVL protocol + inactive repo earn partial credit, not full.
    assert scorer._evidence_strength(degenerate) == pytest.approx(0.5)
    assert scorer._evidence_strength(rich) > scorer._evidence_strength(degenerate)


def test_empty_protocol_record_partial_evidence():
    # Record exists (chain listed) but TVL/revenue are zero -> 0.1 partial credit.
    partial = _all_fields_snapshot(tvl=0.0, stars=0, commits=0, chains=("eth",))
    scorer = SerenityScorer()
    # 0.1 protocol + 0.05 github + 0.45 derivatives.
    assert scorer._evidence_strength(partial) == pytest.approx(0.6)


def test_flat_derivatives_still_count_as_evidence():
    # P0-3: 0.0 is a genuine flat reading, not missing data — full evidence credit.
    base = _all_fields_snapshot(tvl=1e9, stars=1000, commits=100)
    flat = SerenitySnapshot(
        symbol=base.symbol,
        protocol_metrics=base.protocol_metrics,
        github_activity=base.github_activity,
        funding_rate=0.0,
        oi_change=0.0,
        ls_ratio=1.0,
        tvl_chain_share=0.5,
        price_change_7d=0.0,
        volume_change_7d=0.0,
        timestamp=base.timestamp,
    )
    assert SerenityScorer()._evidence_strength(flat) == 1.0


# --- Configurable consensus/governance constants (P2-2) ---


def test_consensus_scarcity_uses_config_btc_eth_scores():
    # Defaults preserve the previous hardcoded behavior.
    scorer = SerenityScorer()
    assert scorer._consensus_scarcity(_empty_snapshot("BTC")) == 0.95
    assert scorer._consensus_scarcity(_empty_snapshot("ETH")) == 0.70


def test_consensus_scarcity_respects_config_override():
    cfg = SerenityConfig(btc_consensus_score=0.8, eth_consensus_score=0.6)
    scorer = SerenityScorer(cfg)
    assert scorer._consensus_scarcity(_empty_snapshot("BTC")) == 0.8
    assert scorer._consensus_scarcity(_empty_snapshot("ETH")) == 0.6


def test_governance_risk_uses_configurable_mcap_tvl_threshold():
    pm = ProtocolMetrics(
        protocol_id="g", name="G", category="chain", chains=("eth",),
        tvl_usd=1e8, tvl_change_1d=0.0, tvl_change_7d=0.0,
        mcap_to_tvl=25.0, fdv_to_tvl=30.0, revenue_24h=0.0, revenue_7d=0.0,
        timestamp=1735689600,
    )
    snap = SerenitySnapshot(
        symbol="G", protocol_metrics=pm, github_activity=None,
        funding_rate=None, oi_change=None, ls_ratio=None, tvl_chain_share=0.0,
        price_change_7d=0.0, volume_change_7d=0.0, timestamp=1735689600,
    )
    # 25 > default threshold 20 -> penalized.
    assert SerenityScorer()._penalty_governance_risk(snap) == 0.6
    # Raise the threshold past 25 -> no longer flagged.
    lenient = SerenityScorer(SerenityConfig(governance_mcap_tvl_threshold=30.0))
    assert lenient._penalty_governance_risk(snap) == 0.2
