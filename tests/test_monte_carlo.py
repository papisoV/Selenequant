"""Tests for Monte Carlo simulation.

Covers:
  - Bootstrap returns (trade sequence resampling)
  - Parameter sensitivity analysis
  - Equity curve distribution (percentile bands)
"""

from __future__ import annotations

import numpy as np
import pytest

from selene_space_backtest.monte_carlo import (
    bootstrap_returns,
    equity_curve_distribution,
    parameter_sensitivity,
)


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

def _make_returns(n: int = 100, seed: int = 42) -> list[float]:
    rng = np.random.default_rng(seed)
    return (rng.normal(0.001, 0.02, size=n) * 100).tolist()  # 0.1% mean, 2% std → bps


def _make_equity_curve(n: int = 100, seed: int = 42) -> list[float]:
    rng = np.random.default_rng(seed)
    returns = rng.normal(0.0005, 0.015, size=n)
    eq = [10000.0]
    for r in returns:
        eq.append(eq[-1] * (1.0 + r))
    return eq


# ---------------------------------------------------------------------------
# Bootstrap returns
# ---------------------------------------------------------------------------


class TestBootstrapReturns:
    def test_produces_expected_samples(self) -> None:
        returns = _make_returns(100)
        result = bootstrap_returns(returns, n_samples=1000)
        assert len(result) == 1000

    def test_each_sample_has_same_length(self) -> None:
        returns = _make_returns(100)
        result = bootstrap_returns(returns, n_samples=500)
        for sample in result:
            assert len(sample) == 100

    def test_sample_is_resampled_with_replacement(self) -> None:
        returns = [1.0, 2.0, 3.0]
        result = bootstrap_returns(returns, n_samples=100)
        # With replacement, samples can differ from original order
        unique_samples = {tuple(s) for s in result}
        assert len(unique_samples) > 1  # Not all identical

    def test_empty_returns_raises(self) -> None:
        with pytest.raises(ValueError):
            bootstrap_returns([], n_samples=100)

    def test_single_return_works(self) -> None:
        result = bootstrap_returns([5.0], n_samples=10)
        assert len(result) == 10
        assert all(s == [5.0] for s in result)

    def test_distribution_stats(self) -> None:
        returns = _make_returns(200)
        result = bootstrap_returns(returns, n_samples=1000)
        # Mean of bootstrap means should approximate original mean
        sample_means = [sum(s) / len(s) for s in result]
        original_mean = sum(returns) / len(returns)
        assert abs(sum(sample_means) / len(sample_means) - original_mean) < 2.0

    def test_seed_reproducibility(self) -> None:
        returns = _make_returns(50)
        r1 = bootstrap_returns(returns, n_samples=100, seed=42)
        r2 = bootstrap_returns(returns, n_samples=100, seed=42)
        assert r1 == r2

    def test_default_n_samples(self) -> None:
        returns = _make_returns(50)
        result = bootstrap_returns(returns)
        assert len(result) == 10000  # default


# ---------------------------------------------------------------------------
# Parameter sensitivity
# ---------------------------------------------------------------------------


def _dummy_strategy(**kwargs: float) -> list[float]:
    """Mock strategy: Sharpe proportional to param closeness to 0.5."""
    import hashlib

    # Deterministic seed from params
    param_str = ",".join(f"{k}={v}" for k, v in sorted(kwargs.items()))
    seed = int(hashlib.md5(param_str.encode()).hexdigest()[:8], 16) % (2**30)
    rng = np.random.default_rng(seed)

    # Use first param value for Sharpe-quality signal
    first_val = float(list(kwargs.values())[0]) if kwargs else 0.5
    sharpe = max(0.0, 1.0 - abs(first_val - 0.5) * 3.0)
    return (rng.normal(sharpe * 0.005, 0.02, size=500) * 100).tolist()


class TestParameterSensitivity:
    def test_returns_result_for_each_param(self) -> None:
        param_grid = {"param": [0.1, 0.3, 0.5, 0.7, 0.9]}
        result = parameter_sensitivity(_dummy_strategy, param_grid, n_runs=50)
        assert len(result) == 5

    def test_result_contains_stats(self) -> None:
        param_grid = {"param": [0.3, 0.5]}
        result = parameter_sensitivity(_dummy_strategy, param_grid, n_runs=30)
        for entry in result:
            assert "params" in entry
            assert "mean_sharpe" in entry
            assert "std_sharpe" in entry
            assert "pctile_5" in entry
            assert "pctile_95" in entry

    def test_optimal_param_near_peak(self) -> None:
        param_grid = {"param": [0.1, 0.3, 0.5, 0.7, 0.9]}
        result = parameter_sensitivity(_dummy_strategy, param_grid, n_runs=200)
        best = max(result, key=lambda x: x["mean_sharpe"])
        assert best["params"]["param"] == 0.5

    def test_multiple_params_grid(self) -> None:
        param_grid = {
            "length": [10, 20],
            "mult": [1.5, 2.0],
        }
        result = parameter_sensitivity(_dummy_strategy, param_grid, n_runs=20)
        # 2x2 = 4 combinations
        assert len(result) == 4

    def test_empty_param_grid_raises(self) -> None:
        with pytest.raises(ValueError):
            parameter_sensitivity(_dummy_strategy, {}, n_runs=10)


# ---------------------------------------------------------------------------
# Equity curve distribution
# ---------------------------------------------------------------------------


class TestEquityCurveDistribution:
    def test_returns_percentile_bands(self) -> None:
        returns = _make_returns(100)
        dist = equity_curve_distribution(returns, initial_equity=10000.0, n_samples=500)
        assert "pctile_5" in dist
        assert "pctile_25" in dist
        assert "pctile_50" in dist
        assert "pctile_75" in dist
        assert "pctile_95" in dist

    def test_all_curves_same_length(self) -> None:
        returns = _make_returns(50)
        dist = equity_curve_distribution(returns, initial_equity=10000.0, n_samples=200)
        n = len(dist["pctile_50"])
        for key in ["pctile_5", "pctile_25", "pctile_75", "pctile_95"]:
            assert len(dist[key]) == n

    def test_median_positive_with_positive_returns(self) -> None:
        """With all positive returns, median equity should be > initial."""
        returns = [0.5] * 30  # All positive
        dist = equity_curve_distribution(returns, initial_equity=10000.0, n_samples=100)
        assert dist["pctile_50"][-1] > 10000.0

    def test_percentiles_monotonic(self) -> None:
        returns = _make_returns(100)
        dist = equity_curve_distribution(returns, initial_equity=10000.0, n_samples=500)
        last_5 = dist["pctile_5"][-1]
        last_25 = dist["pctile_25"][-1]
        last_50 = dist["pctile_50"][-1]
        last_75 = dist["pctile_75"][-1]
        last_95 = dist["pctile_95"][-1]
        assert last_5 <= last_25 <= last_50 <= last_75 <= last_95

    def test_empty_returns_raises(self) -> None:
        with pytest.raises(ValueError):
            equity_curve_distribution([], initial_equity=10000.0, n_samples=100)
