"""Tests for backtest overfitting detection (DSR, PBO, haircut, PurgedKFold)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_backtest.overfit import (
    DSRResult,
    HaircutResult,
    OverfitReport,
    PBOResult,
    PurgedKFold,
    build_overfit_report,
    deflated_sharpe_ratio,
    haircut_sharpe,
    probability_of_backtest_overfitting,
    render_text,
    sharpe_ratio,
)


# ---------------------------------------------------------------------------
# DSR tests
# ---------------------------------------------------------------------------


def test_dsr_pure_noise_fails():
    """Random returns selected as best out of many trials should fail DSR."""
    rng = np.random.default_rng(42)
    ret = rng.normal(0, 0.01, size=500)
    result = deflated_sharpe_ratio(ret, n_trials=200)
    assert isinstance(result, DSRResult)
    assert result.deflated_sharpe_ratio < 0.95
    assert not result.passed


def test_dsr_genuine_edge_passes():
    """Returns with real positive drift should pass DSR."""
    rng = np.random.default_rng(42)
    ret = rng.normal(0.002, 0.01, size=500)
    result = deflated_sharpe_ratio(ret, n_trials=10)
    assert result.deflated_sharpe_ratio >= 0.95
    assert result.passed


def test_dsr_result_frozen():
    """DSRResult should be frozen (immutable)."""
    rng = np.random.default_rng(42)
    ret = rng.normal(0, 0.01, size=100)
    result = deflated_sharpe_ratio(ret, n_trials=10)
    with pytest.raises(AttributeError):
        result.passed = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PBO tests
# ---------------------------------------------------------------------------


def test_pbo_pure_noise():
    """PBO for pure-noise strategies should be measurable (typically 0.1-0.6)."""
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 1, size=(500, 20))
    result = probability_of_backtest_overfitting(noise, n_blocks=14)
    assert isinstance(result, PBOResult)
    assert 0.0 < result.pbo < 1.0


def test_pbo_one_real_edge():
    """PBO should drop when one strategy has real edge."""
    rng = np.random.default_rng(1)
    noise = rng.normal(0, 1, size=(500, 20))
    edge = noise.copy()
    edge[:, 0] += 0.5
    result = probability_of_backtest_overfitting(edge, n_blocks=14)
    assert result.pbo < 0.3


def test_pbo_requires_two_strategies():
    """PBO requires at least 2 strategy columns."""
    with pytest.raises(ValueError, match="at least 2"):
        probability_of_backtest_overfitting(np.ones((100, 1)), n_blocks=4)


# ---------------------------------------------------------------------------
# Haircut tests
# ---------------------------------------------------------------------------


def test_haircut_bonferroni():
    """Haircut should increase with n_tests (more stringent correction)."""
    sr = 0.1  # per-period Sharpe
    n_obs = 1000
    h1 = haircut_sharpe(sr, n_obs=n_obs, n_tests=10)
    h2 = haircut_sharpe(sr, n_obs=n_obs, n_tests=100)
    assert isinstance(h1, HaircutResult)
    assert h1.haircut < h2.haircut


def test_haircut_result_frozen():
    """HaircutResult should be frozen."""
    h = haircut_sharpe(0.1, n_obs=1000, n_tests=10)
    with pytest.raises(AttributeError):
        h.haircut = 0.0  # type: ignore[misc]


# ---------------------------------------------------------------------------
# PurgedKFold tests
# ---------------------------------------------------------------------------


def test_purged_kfold_no_leakage():
    """Train and test indices should not overlap."""
    n = 100
    idx = pd.RangeIndex(n)
    label_times = pd.Series(idx + 5, index=idx)
    cv = PurgedKFold(n_splits=5, label_times=label_times, embargo_pct=0.02)
    for train_idx, test_idx in cv.split():
        assert len(np.intersect1d(train_idx, test_idx)) == 0


def test_purged_kfold_all_samples_covered():
    """Every sample should appear in at least one test fold."""
    n = 100
    idx = pd.RangeIndex(n)
    label_times = pd.Series(idx + 5, index=idx)
    cv = PurgedKFold(n_splits=5, label_times=label_times)
    all_test = set()
    for _, test_idx in cv.split():
        all_test.update(test_idx.tolist())
    assert len(all_test) == n


# ---------------------------------------------------------------------------
# Full report tests
# ---------------------------------------------------------------------------


def test_build_report_pass():
    """Genuine strategy should pass the full overfit report."""
    rng = np.random.default_rng(42)
    ret = rng.normal(0.002, 0.01, size=500)
    report = build_overfit_report(ret, n_trials=10)
    assert isinstance(report, OverfitReport)
    assert report.passed
    assert "PASS" in report.verdict


def test_build_report_fail():
    """Noise strategy selected from many trials should fail."""
    rng = np.random.default_rng(42)
    ret = rng.normal(0, 0.01, size=500)
    report = build_overfit_report(ret, n_trials=500)
    assert not report.passed
    assert "FAIL" in report.verdict


def test_build_report_with_pbo():
    """Report with trials_matrix should compute PBO."""
    rng = np.random.default_rng(42)
    T, N = 500, 20
    trials = rng.normal(0, 0.01, size=(T, N))
    selected = trials[:, 0]
    report = build_overfit_report(selected, n_trials=N, trials_matrix=trials)
    assert report.pbo is not None


def test_render_text():
    """render_text should produce a readable string."""
    rng = np.random.default_rng(42)
    ret = rng.normal(0.001, 0.01, size=200)
    report = build_overfit_report(ret, n_trials=10)
    text = render_text(report)
    assert "BACKTEST OVERFITTING REPORT" in text
    assert "Verdict" in text


# ---------------------------------------------------------------------------
# sharpe_ratio utility
# ---------------------------------------------------------------------------


def test_sharpe_ratio_basic():
    """Sharpe ratio of constant positive returns should be positive."""
    ret = np.ones(100) * 0.01
    sr = sharpe_ratio(ret)
    assert sr > 0


def test_sharpe_ratio_nan_for_short():
    """Sharpe ratio should be NaN for fewer than 2 observations."""
    assert np.isnan(sharpe_ratio(np.array([0.01])))
