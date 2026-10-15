"""Tests for Black-Litterman model."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

# Direct imports bypassing selene_space_alpha.__init__ (which triggers torch)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from portfolio.black_litterman import (
    BLResult,
    black_litterman,
    bl_posterior_cov,
    he_litterman_omega,
    views_from_signals,
)
from portfolio.optimizers import optimize


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def two_asset_setup():
    """Simple 2-asset setup with known equilibrium returns."""
    mu_eq = np.array([0.05, 0.10])
    cov = np.array([[0.04, 0.01],
                    [0.01, 0.09]])
    return mu_eq, cov


@pytest.fixture
def five_asset_setup():
    """5-asset setup for integration tests."""
    rng = np.random.default_rng(123)
    n = 5
    A = rng.normal(0, 1, size=(n, n))
    cov = A @ A.T + np.eye(n) * 0.5  # ensure well-conditioned
    mu_eq = np.array([0.03, 0.07, 0.05, 0.09, 0.04])
    return mu_eq, cov


# ---------------------------------------------------------------------------
# Core BL formula
# ---------------------------------------------------------------------------

def test_single_absolute_view_shifts_posterior(two_asset_setup):
    """With one absolute view, posterior should shift toward that view."""
    mu_eq, cov = two_asset_setup
    # View: asset 0 will return 15% (higher than equilibrium 5%)
    P = np.array([[1.0, 0.0]])
    Q = np.array([0.15])

    result = black_litterman(mu_eq, cov, P, Q, tau=0.05)

    # Posterior for asset 0 should be between equilibrium (0.05) and view (0.15)
    assert result.mu_posterior[0] > mu_eq[0], "Posterior should shift toward view"
    assert result.mu_posterior[0] < Q[0], "Posterior should not exceed view"
    # Asset 1 should also shift slightly due to correlation
    assert isinstance(result.mu_posterior[1], float | np.floating)


def test_no_views_returns_equilibrium(two_asset_setup):
    """With no views, posterior mean equals equilibrium; posterior cov = (1+tau)*cov."""
    mu_eq, cov = two_asset_setup
    tau = 0.05
    P = np.empty((0, 2))
    Q = np.empty(0)

    result = black_litterman(mu_eq, cov, P, Q, tau=tau)

    np.testing.assert_array_almost_equal(result.mu_posterior, mu_eq)
    np.testing.assert_array_almost_equal(result.cov_posterior, (1.0 + tau) * cov)


def test_bl_result_dataclass_fields(two_asset_setup):
    """BLResult should contain all expected fields."""
    mu_eq, cov = two_asset_setup
    P = np.array([[1.0, 0.0]])
    Q = np.array([0.12])

    result = black_litterman(mu_eq, cov, P, Q, tau=0.05)

    assert isinstance(result, BLResult)
    assert result.mu_posterior.shape == (2,)
    assert result.cov_posterior.shape == (2, 2)
    assert result.mu_equilibrium.shape == (2,)
    assert result.views_P.shape == (1, 2)
    assert result.views_Q.shape == (1,)


# ---------------------------------------------------------------------------
# Posterior covariance
# ---------------------------------------------------------------------------

def test_posterior_cov_positive_definite(two_asset_setup):
    """Posterior covariance must be positive definite."""
    mu_eq, cov = two_asset_setup
    P = np.array([[1.0, 0.0]])
    Q = np.array([0.10])

    result = black_litterman(mu_eq, cov, P, Q, tau=0.05)

    eigvals = np.linalg.eigvalsh(result.cov_posterior)
    assert np.all(eigvals > 0), f"Non-positive eigenvalues: {eigvals}"


def test_bl_posterior_cov_standalone(two_asset_setup):
    """bl_posterior_cov should match the covariance from black_litterman."""
    mu_eq, cov = two_asset_setup
    tau = 0.05
    P = np.array([[1.0, 0.0]])
    Q = np.array([0.10])
    omega = np.array([[0.002]])

    cov_from_func = bl_posterior_cov(cov, tau, P, omega)
    result = black_litterman(mu_eq, cov, P, Q, omega=omega, tau=tau)

    np.testing.assert_array_almost_equal(cov_from_func, result.cov_posterior)


def test_posterior_cov_larger_than_prior(two_asset_setup):
    """Posterior covariance should be >= prior (extra uncertainty from views)."""
    mu_eq, cov = two_asset_setup
    P = np.array([[1.0, 0.0]])
    Q = np.array([0.10])

    result = black_litterman(mu_eq, cov, P, Q, tau=0.05)

    diff = result.cov_posterior - cov
    # Diagonal should be >= 0 (extra uncertainty added)
    assert np.all(np.diag(diff) >= -1e-10), "Posterior diagonal should not shrink"


# ---------------------------------------------------------------------------
# views_from_signals
# ---------------------------------------------------------------------------

def test_views_from_signals_basic():
    """views_from_signals should create correct P and Q matrices."""
    signals = {"AAPL": 0.12, "GOOG": 0.08}
    assets = ["AAPL", "MSFT", "GOOG"]

    P, Q = views_from_signals(signals, assets)

    assert P.shape == (2, 3)
    assert Q.shape == (2,)
    # AAPL is index 0
    np.testing.assert_array_equal(P[0], [1.0, 0.0, 0.0])
    # GOOG is index 2
    np.testing.assert_array_equal(P[1], [0.0, 0.0, 1.0])
    np.testing.assert_array_almost_equal(Q, [0.12, 0.08])


def test_views_from_signals_ignores_unknown():
    """Signals for assets not in the list should be silently ignored."""
    signals = {"AAPL": 0.12, "UNKNOWN": 0.99}
    assets = ["AAPL", "MSFT"]

    P, Q = views_from_signals(signals, assets)

    assert P.shape == (1, 2)
    assert Q.shape == (1,)
    np.testing.assert_array_equal(P[0], [1.0, 0.0])
    assert Q[0] == pytest.approx(0.12)


def test_views_from_signals_empty():
    """No matching signals should produce empty arrays with correct columns."""
    signals = {"UNKNOWN": 0.99}
    assets = ["AAPL", "MSFT", "GOOG"]

    P, Q = views_from_signals(signals, assets)

    assert P.shape == (0, 3)
    assert Q.shape == (0,)


# ---------------------------------------------------------------------------
# he_litterman_omega
# ---------------------------------------------------------------------------

def test_he_litterman_omega_diagonal():
    """He-Litterman omega should be a diagonal matrix."""
    cov = np.array([[0.04, 0.01],
                    [0.01, 0.09]])
    tau_cov = 0.05 * cov
    P = np.array([[1.0, 0.0],
                  [0.0, 1.0]])

    omega = he_litterman_omega(tau_cov, P)

    assert omega.shape == (2, 2)
    # Off-diagonal should be zero
    assert omega[0, 1] == pytest.approx(0.0)
    assert omega[1, 0] == pytest.approx(0.0)
    # Diagonal should be positive
    assert omega[0, 0] > 0
    assert omega[1, 1] > 0


def test_he_litterman_omega_values():
    """He-Litterman omega diagonal should equal P @ tau_cov @ P.T diagonal."""
    cov = np.array([[0.04, 0.01],
                    [0.01, 0.09]])
    tau_cov = 0.05 * cov
    P = np.array([[1.0, 0.5]])

    omega = he_litterman_omega(tau_cov, P)

    expected = P @ tau_cov @ P.T
    assert omega[0, 0] == pytest.approx(expected[0, 0])


# ---------------------------------------------------------------------------
# Integration with optimize()
# ---------------------------------------------------------------------------

def test_bl_posterior_into_optimize(five_asset_setup):
    """BL posterior returns should be usable with the existing optimize() function."""
    mu_eq, cov = five_asset_setup
    assets = [f"ASSET_{i}" for i in range(5)]
    signals = {"ASSET_0": 0.10, "ASSET_3": 0.15}

    P, Q = views_from_signals(signals, assets)
    bl = black_litterman(mu_eq, cov, P, Q, tau=0.05)

    # Feed BL posterior into mean_variance optimizer
    result = optimize("mean_variance", bl.cov_posterior, mu=bl.mu_posterior)

    assert result.success
    weights = result.weights
    assert abs(np.sum(weights) - 1.0) < 0.01
    assert np.all(weights >= -1e-6), "Weights should be non-negative (long-only default)"


def test_bl_posterior_into_max_sharpe(five_asset_setup):
    """BL posterior should work with max_sharpe optimizer."""
    mu_eq, cov = five_asset_setup
    P = np.array([[0.0, 0.0, 1.0, 0.0, 0.0]])
    Q = np.array([0.12])

    bl = black_litterman(mu_eq, cov, P, Q, tau=0.05)

    result = optimize("max_sharpe", bl.cov_posterior, mu=bl.mu_posterior)

    assert result.success
    assert abs(np.sum(result.weights) - 1.0) < 0.01


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

def test_single_asset_single_view():
    """Single asset with a single view — posterior should shift toward view."""
    mu_eq = np.array([0.05])
    cov = np.array([[0.04]])
    P = np.array([[1.0]])
    Q = np.array([0.15])

    result = black_litterman(mu_eq, cov, P, Q, tau=0.05)

    # Posterior should be between equilibrium and view
    assert result.mu_posterior[0] > mu_eq[0]
    assert result.mu_posterior[0] < Q[0]
    # Covariance should be positive
    assert result.cov_posterior[0, 0] > 0


def test_multiple_views(five_asset_setup):
    """Multiple views on different assets should all shift posteriors."""
    mu_eq, cov = five_asset_setup
    # Two views: asset 0 up, asset 3 up
    P = np.array([[1.0, 0.0, 0.0, 0.0, 0.0],
                  [0.0, 0.0, 0.0, 1.0, 0.0]])
    Q = np.array([0.15, 0.20])

    result = black_litterman(mu_eq, cov, P, Q, tau=0.05)

    # Asset 0 posterior should be above equilibrium (view is higher)
    assert result.mu_posterior[0] > mu_eq[0]
    # Asset 3 posterior should be above equilibrium (view is higher)
    assert result.mu_posterior[3] > mu_eq[3]
    # Both should still be below their respective views
    assert result.mu_posterior[0] < Q[0]
    assert result.mu_posterior[3] < Q[1]


def test_custom_omega(two_asset_setup):
    """Passing a custom omega should produce different results than default."""
    mu_eq, cov = two_asset_setup
    P = np.array([[1.0, 0.0]])
    Q = np.array([0.15])

    result_default = black_litterman(mu_eq, cov, P, Q, tau=0.05)
    # Very confident view (small omega)
    omega_confident = np.array([[0.0001]])
    result_confident = black_litterman(mu_eq, cov, P, Q, omega=omega_confident, tau=0.05)

    # More confident view should push posterior closer to the view
    assert result_confident.mu_posterior[0] > result_default.mu_posterior[0]
