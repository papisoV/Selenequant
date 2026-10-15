"""Tests for cross-sectional IC/ICIR metrics."""

from __future__ import annotations

import numpy as np
import pytest

from selene_space_backtest.metrics import ICSummary, compute_ic, compute_ic_summary


def test_ic_perfect_predictor():
    """Factor = forward returns -> IC ~1.0."""
    rng = np.random.default_rng(42)
    n_dates, n_assets = 100, 50
    factor = rng.normal(0, 1, size=(n_dates, n_assets))
    forward = factor.copy()  # perfect predictor
    ic_vals = compute_ic(factor, forward, method="pearson")
    valid = ic_vals[~np.isnan(ic_vals)]
    assert np.mean(valid) > 0.95


def test_ic_random_factor():
    """Random factor -> IC ~0.0."""
    rng = np.random.default_rng(42)
    n_dates, n_assets = 200, 50
    factor = rng.normal(0, 1, size=(n_dates, n_assets))
    forward = rng.normal(0, 1, size=(n_dates, n_assets))
    summary = compute_ic_summary(factor, forward, method="spearman")
    assert abs(summary.ic_mean) < 0.15


def test_icir_calculation():
    """ICIR should equal ic_mean / ic_std."""
    rng = np.random.default_rng(42)
    n_dates, n_assets = 100, 30
    factor = rng.normal(0, 1, size=(n_dates, n_assets))
    forward = factor + rng.normal(0, 0.5, size=(n_dates, n_assets))
    summary = compute_ic_summary(factor, forward)
    if summary.ic_std > 1e-15:
        expected_icir = summary.ic_mean / summary.ic_std
        assert abs(summary.icir - round(expected_icir, 4)) < 0.01


def test_ic_with_nans():
    """NaN handling should drop assets per-date."""
    rng = np.random.default_rng(42)
    n_dates, n_assets = 50, 30
    factor = rng.normal(0, 1, size=(n_dates, n_assets))
    forward = rng.normal(0, 1, size=(n_dates, n_assets))
    # Inject NaNs
    factor[0, 0] = np.nan
    forward[5, 3] = np.nan
    ic_vals = compute_ic(factor, forward)
    # NaN-heavy rows should produce NaN IC, others should work
    valid = ic_vals[~np.isnan(ic_vals)]
    assert len(valid) > 0


def test_spearman_vs_pearson():
    """Both methods should produce valid results."""
    rng = np.random.default_rng(42)
    n_dates, n_assets = 100, 30
    factor = rng.normal(0, 1, size=(n_dates, n_assets))
    forward = factor + rng.normal(0, 0.3, size=(n_dates, n_assets))
    s_spearman = compute_ic_summary(factor, forward, method="spearman")
    s_pearson = compute_ic_summary(factor, forward, method="pearson")
    assert s_spearman.method == "spearman"
    assert s_pearson.method == "pearson"
    # Both should detect positive correlation
    assert s_spearman.ic_mean > 0
    assert s_pearson.ic_mean > 0


def test_ic_summary_fields():
    """All ICSummary fields should be populated correctly."""
    rng = np.random.default_rng(42)
    n_dates, n_assets = 100, 30
    factor = rng.normal(0, 1, size=(n_dates, n_assets))
    forward = factor + rng.normal(0, 0.5, size=(n_dates, n_assets))
    summary = compute_ic_summary(factor, forward)
    assert isinstance(summary, ICSummary)
    assert summary.n_dates > 0
    assert 0.0 <= summary.hit_rate <= 1.0


def test_ic_shape_mismatch():
    """Mismatched shapes should raise ValueError."""
    factor = np.ones((10, 5))
    forward = np.ones((10, 4))
    with pytest.raises(ValueError, match="same shape"):
        compute_ic(factor, forward)


def test_ic_too_few_assets():
    """Rows with < 3 valid assets should produce NaN IC."""
    factor = np.array([[1.0, np.nan], [2.0, np.nan]])
    forward = np.array([[0.1, np.nan], [0.2, np.nan]])
    ic_vals = compute_ic(factor, forward)
    assert all(np.isnan(ic_vals))
