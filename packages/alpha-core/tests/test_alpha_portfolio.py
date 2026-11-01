"""Tests for portfolio optimization and Serenity portfolio builder."""

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

from portfolio.constraints import build_constraints
from portfolio.covariance import estimate_cov, nearest_psd
from portfolio.frontier import efficient_frontier
from portfolio.optimizers import optimize, risk_contributions
from portfolio.result import PortfolioResult


@pytest.fixture
def sample_data():
    rng = np.random.default_rng(42)
    n_assets = 5
    n_days = 200
    returns = pd.DataFrame(
        rng.normal(0.001, 0.02, size=(n_days, n_assets)),
        columns=[f"ASSET_{i}" for i in range(n_assets)],
    )
    cov_df = estimate_cov(returns)
    mu = returns.mean().values * 252
    return returns, cov_df.values, mu


def test_min_variance_converges(sample_data):
    _, cov, _ = sample_data
    result = optimize("min_variance", cov)
    assert result.success
    assert abs(np.sum(result.weights) - 1.0) < 0.01


def test_max_sharpe_with_mu(sample_data):
    _, cov, mu = sample_data
    result = optimize("max_sharpe", cov, mu=mu)
    assert result.success
    w = result.weights
    assert abs(np.sum(w) - 1.0) < 0.01


def test_risk_parity_equal_contribution(sample_data):
    _, cov, _ = sample_data
    result = optimize("risk_parity", cov)
    rc = risk_contributions(result.weights, cov)
    # Risk contributions should be approximately equal (within 5%)
    target = 1.0 / len(rc)
    assert np.all(np.abs(rc - target) < 0.05)


def test_sector_neutral_constraint(sample_data):
    _, cov, mu = sample_data
    sectors = np.array([0, 0, 1, 1, 2])
    constraints = build_constraints(
        n=5, long_only=True, full_investment=True,
        weight_cap=0.5, sectors=sectors,
        sector_targets={0: 0.4, 1: 0.4, 2: 0.2},
    )
    result = optimize("mean_variance", cov, mu=mu, constraints=constraints, risk_aversion=2.0)
    # Sector exposures should match targets (within tolerance)
    w = result.weights
    for sec_id, target in [(0, 0.4), (1, 0.4), (2, 0.2)]:
        sector_sum = float(np.sum(w[sectors == sec_id]))
        assert abs(sector_sum - target) < 0.05, f"Sector {sec_id}: got {sector_sum}, expected {target}"


def test_ledoit_wolf_cov_psd(sample_data):
    returns, _, _ = sample_data
    cov = estimate_cov(returns, method="ledoit_wolf")
    eigvals = np.linalg.eigvalsh(cov.values)
    assert np.all(eigvals > -1e-10), "Covariance matrix is not PSD"


def test_efficient_frontier_points(sample_data):
    _, cov, mu = sample_data
    frontier = efficient_frontier(mu, cov, n_points=10)
    assert len(frontier) == 10
    # Volatility should generally increase across frontier
    vols = [p["volatility"] for p in frontier]
    # At least some variation in volatility
    assert max(vols) - min(vols) > 0.01


def test_nearest_psd():
    rng = np.random.default_rng(42)
    A = rng.normal(0, 1, size=(5, 5))
    cov = pd.DataFrame(A @ A.T)
    psd = nearest_psd(cov)
    eigvals = np.linalg.eigvalsh(psd.values)
    assert np.all(eigvals > 0)


def test_portfolio_result_fields():
    r = PortfolioResult(
        weights=np.array([0.5, 0.5]),
        objective="min_variance",
        success=True,
        message="ok",
        expected_return=0.1,
        volatility=0.05,
        sharpe_ratio=2.0,
        risk_contributions=np.array([0.5, 0.5]),
    )
    assert r.objective == "min_variance"
    assert r.sharpe_ratio == 2.0
