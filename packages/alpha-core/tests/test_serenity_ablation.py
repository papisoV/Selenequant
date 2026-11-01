"""Tests for Serenity 8-factor ablation analysis."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Direct imports bypassing selene_space_alpha.__init__ (which triggers torch)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from serenity.ablation import (
    FACTOR_NAMES,
    AblationReport,
    SerenityAblation,
)
from serenity.models import (
    GitHubActivity,
    ProtocolMetrics,
    SerenitySnapshot,
)
from serenity.scorer import SerenityScorer


def _make_snapshot(
    symbol: str = "TEST",
    tvl: float = 1e9,
    tvl_change: float = 0.05,
    stars: int = 500,
    devs: int = 20,
) -> SerenitySnapshot:
    return SerenitySnapshot(
        symbol=symbol,
        protocol_metrics=ProtocolMetrics(
            protocol_id=symbol,
            name=symbol,
            category="chain",
            chains=("ethereum",),
            tvl_usd=tvl,
            tvl_change_1d=tvl_change * 0.3,
            tvl_change_7d=tvl_change,
            mcap_to_tvl=3.0,
            fdv_to_tvl=5.0,
            revenue_24h=1e5,
            revenue_7d=7e5,
            timestamp=1700000000,
        ),
        github_activity=GitHubActivity(
            protocol_id=symbol,
            repo_name=f"{symbol}/core",
            stars=stars,
            commits_4w=100,
            active_devs_4w=devs,
            open_issues=50,
            closed_issues_4w=30,
            timestamp=1700000000,
        ),
        funding_rate=0.01,
        oi_change=0.02,
        ls_ratio=1.1,
        tvl_chain_share=0.5,
        price_change_7d=0.03,
        volume_change_7d=-0.02,
        timestamp=1700000000,
    )


def test_ablation_basic():
    """Ablation should produce a report with all 8 factors."""
    scorer = SerenityScorer()
    ablation = SerenityAblation(scorer)
    snapshots = [_make_snapshot(f"TK{i}", tvl=1e9 + i * 1e8) for i in range(10)]
    report = ablation.analyze(snapshots)
    assert isinstance(report, AblationReport)
    assert len(report.factor_results) == 8
    assert report.condition_number > 0


def test_ablation_factor_names():
    """Each factor result should have a recognized name."""
    scorer = SerenityScorer()
    ablation = SerenityAblation(scorer)
    snapshots = [_make_snapshot(f"TK{i}") for i in range(10)]
    report = ablation.analyze(snapshots)
    for r in report.factor_results:
        assert r.factor_name in FACTOR_NAMES


def test_ablation_vif_positive():
    """VIF should be positive for all factors."""
    scorer = SerenityScorer()
    ablation = SerenityAblation(scorer)
    snapshots = [_make_snapshot(f"TK{i}") for i in range(10)]
    report = ablation.analyze(snapshots)
    for r in report.factor_results:
        assert r.vif > 0


def test_ablation_correlation_bounded():
    """Correlation with score should be in [-1, 1]."""
    scorer = SerenityScorer()
    ablation = SerenityAblation(scorer)
    snapshots = [_make_snapshot(f"TK{i}") for i in range(10)]
    report = ablation.analyze(snapshots)
    for r in report.factor_results:
        assert -1.0 <= r.correlation_with_score <= 1.0


def test_ablation_too_few_snapshots():
    """Should raise ValueError with < 5 snapshots."""
    scorer = SerenityScorer()
    ablation = SerenityAblation(scorer)
    with pytest.raises(ValueError, match="at least 5"):
        ablation.analyze([_make_snapshot("A"), _make_snapshot("B")])


def test_ablation_recommendation():
    """Recommendation should be one of the known values."""
    scorer = SerenityScorer()
    ablation = SerenityAblation(scorer)
    snapshots = [_make_snapshot(f"TK{i}") for i in range(10)]
    report = ablation.analyze(snapshots)
    assert report.recommendation in ("keep_all", "restructure") or report.recommendation.startswith("drop_")
