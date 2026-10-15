"""Tests for VaR / CVaR engine.

Covers:
  - Historical VaR (percentile-based)
  - Parametric VaR (variance-covariance)
  - Monte Carlo VaR
  - CVaR (expected shortfall)
"""

from __future__ import annotations

import numpy as np
import pytest

from selene_space_risk.var.cvar import compute_cvar
from selene_space_risk.var.historical_var import compute_historical_var
from selene_space_risk.var.parametric_var import compute_parametric_var
from selene_space_risk.var.monte_carlo_var import compute_monte_carlo_var


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------


def _make_returns(n: int = 1000, seed: int = 42) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.normal(0.0005, 0.02, size=n)


# ---------------------------------------------------------------------------
# Historical VaR
# ---------------------------------------------------------------------------


class TestHistoricalVaR:
    def test_95_var(self) -> None:
        returns = _make_returns(1000)
        var = compute_historical_var(returns, confidence=0.95)
        # 5th percentile should be negative
        assert var < 0

    def test_99_var_more_extreme(self) -> None:
        returns = _make_returns(1000)
        var95 = compute_historical_var(returns, confidence=0.95)
        var99 = compute_historical_var(returns, confidence=0.99)
        assert abs(var99) > abs(var95)

    def test_empty_returns_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_historical_var([], confidence=0.95)

    def test_constant_returns(self) -> None:
        var = compute_historical_var([0.01] * 100, confidence=0.95)
        assert var == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Parametric VaR
# ---------------------------------------------------------------------------


class TestParametricVaR:
    def test_normal_distribution_var(self) -> None:
        returns = _make_returns(1000)
        var = compute_parametric_var(returns, confidence=0.95)
        assert var < 0

    def test_zero_vol_returns_zero_var(self) -> None:
        returns = [0.001] * 100
        var = compute_parametric_var(returns, confidence=0.95)
        assert var == pytest.approx(0.001)

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_parametric_var([], confidence=0.95)


# ---------------------------------------------------------------------------
# Monte Carlo VaR
# ---------------------------------------------------------------------------


class TestMonteCarloVaR:
    def test_mc_var_close_to_parametric(self) -> None:
        returns = _make_returns(500)
        mc_var = compute_monte_carlo_var(returns, confidence=0.95, n_simulations=5000)
        assert mc_var < 0

    def test_mc_var_more_extreme_higher_confidence(self) -> None:
        returns = _make_returns(500)
        var95 = compute_monte_carlo_var(returns, confidence=0.95, n_simulations=3000)
        var99 = compute_monte_carlo_var(returns, confidence=0.99, n_simulations=3000)
        assert abs(var99) > abs(var95)

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_monte_carlo_var([], confidence=0.95)


# ---------------------------------------------------------------------------
# CVaR
# ---------------------------------------------------------------------------


class TestCVaR:
    def test_cvar_worse_than_var(self) -> None:
        returns = _make_returns(1000)
        var = compute_historical_var(returns, confidence=0.95)
        cvar = compute_cvar(returns, confidence=0.95)
        # CVaR (tail mean) is worse (more negative) than VaR
        assert cvar <= var

    def test_cvar_from_historical(self) -> None:
        returns = [-0.05, -0.03, -0.02, -0.01, 0.0, 0.01, 0.02, 0.03, 0.04, 0.05]
        cvar = compute_cvar(returns, confidence=0.8)
        # Bottom 20% = 2 worst returns: mean(-0.05, -0.03) = -0.04
        assert cvar == pytest.approx(-0.04)

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError):
            compute_cvar([], confidence=0.95)
