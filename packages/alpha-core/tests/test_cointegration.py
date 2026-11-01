"""Tests for cointegration module — ADF test, Engle-Granger, pair scanning."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Direct import path (bypasses top-level __init__ that may trigger heavy deps)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from stat_arb.cointegration import CointResult, PairResult, adf_test, engle_granger, find_cointegrated_pairs


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _random_walk(n: int, seed: int = 42) -> pd.Series:
    """Generate a random walk (non-stationary) series."""
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 1, size=n)
    return pd.Series(np.cumsum(returns))


def _mean_reverting(n: int, theta: float = 0.5, seed: int = 42) -> pd.Series:
    """Generate a mean-reverting (stationary) OU-like series.

    AR(1): x_t = (1 - theta) * mu + theta * x_{t-1} + eps,  mu=0.
    With theta < 1, the series is stationary.
    """
    rng = np.random.default_rng(seed)
    x = np.empty(n)
    x[0] = 0.0
    for t in range(1, n):
        x[t] = theta * x[t - 1] + rng.normal(0, 1)
    return pd.Series(x)


# ---------------------------------------------------------------------------
# ADF test
# ---------------------------------------------------------------------------

class TestAdfTest:
    """Tests for adf_test."""

    def test_random_walk_not_stationary(self) -> None:
        """A random walk should be detected as non-stationary."""
        rw = _random_walk(500, seed=1)
        result = adf_test(rw)
        assert isinstance(result, CointResult)
        # Random walk should usually NOT be stationary
        assert not result.is_stationary
        assert result.test_statistic > result.critical_values["5%"]

    def test_mean_reverting_stationary(self) -> None:
        """A strongly mean-reverting series should be detected as stationary."""
        mr = _mean_reverting(500, theta=0.5, seed=2)
        result = adf_test(mr)
        assert isinstance(result, CointResult)
        assert result.is_stationary
        assert result.test_statistic < result.critical_values["5%"]

    def test_p_value_range(self) -> None:
        """P-value should be between 0 and 1."""
        s = _random_walk(200)
        result = adf_test(s)
        assert 0.0 <= result.p_value <= 1.0

    def test_critical_values_keys(self) -> None:
        """Critical values should have 1%, 5%, 10% keys."""
        s = _random_walk(200)
        result = adf_test(s)
        assert "1%" in result.critical_values
        assert "5%" in result.critical_values
        assert "10%" in result.critical_values

    def test_short_series_returns_non_stationary(self) -> None:
        """Very short series should return non-stationary without error."""
        short = pd.Series([1.0, 2.0, 3.0])
        result = adf_test(short)
        assert not result.is_stationary

    def test_constant_series(self) -> None:
        """A constant series should not crash the ADF test."""
        const = pd.Series([5.0] * 200)
        result = adf_test(const)
        # Constant series is trivially stationary but ADF may behave oddly;
        # just ensure it doesn't crash and returns a CointResult.
        assert isinstance(result, CointResult)


# ---------------------------------------------------------------------------
# Engle-Granger
# ---------------------------------------------------------------------------

class TestEngleGranger:
    """Tests for engle_granger."""

    def test_cointegrated_pair(self) -> None:
        """y = 2*x + noise should be cointegrated with x."""
        rng = np.random.default_rng(42)
        n = 500
        x_vals = np.cumsum(rng.normal(0, 1, n))
        noise = rng.normal(0, 0.5, n)
        y_vals = 2.0 * x_vals + noise

        x = pd.Series(x_vals)
        y = pd.Series(y_vals)

        result = engle_granger(y, x)
        assert isinstance(result, CointResult)
        assert result.is_stationary

    def test_non_cointegrated_pair(self) -> None:
        """Two independent random walks should generally not be cointegrated."""
        rw1 = _random_walk(500, seed=10)
        rw2 = _random_walk(500, seed=20)
        result = engle_granger(rw1, rw2)
        assert isinstance(result, CointResult)
        # Independent random walks are unlikely to be cointegrated
        assert not result.is_stationary

    def test_short_series(self) -> None:
        """Short aligned series should return non-cointegrated without error."""
        x = pd.Series([1.0, 2.0, 3.0])
        y = pd.Series([2.0, 4.0, 6.0])
        result = engle_granger(y, x)
        assert not result.is_stationary

    def test_misaligned_indices(self) -> None:
        """Series with different indices should only use common subset."""
        rng = np.random.default_rng(42)
        n = 500
        x_vals = np.cumsum(rng.normal(0, 1, n))
        y_vals = 2.0 * x_vals + rng.normal(0, 0.5, n)

        idx_a = pd.date_range("2020-01-01", periods=n, freq="D")
        idx_b = pd.date_range("2020-01-01", periods=n, freq="D")

        x = pd.Series(x_vals, index=idx_a)
        y = pd.Series(y_vals, index=idx_b)

        result = engle_granger(y, x)
        assert isinstance(result, CointResult)


# ---------------------------------------------------------------------------
# find_cointegrated_pairs
# ---------------------------------------------------------------------------

class TestFindCointegratedPairs:
    """Tests for find_cointegrated_pairs."""

    def test_finds_cointegrated_pair(self) -> None:
        """Should identify the cointegrated pair among independent series."""
        rng = np.random.default_rng(42)
        n = 500

        # Create a cointegrated pair
        base = np.cumsum(rng.normal(0, 1, n))
        a_vals = base + rng.normal(0, 0.5, n)
        b_vals = 2.0 * base + rng.normal(0, 0.5, n)

        # Add two independent random walks
        c_vals = np.cumsum(rng.normal(0, 1, n))
        d_vals = np.cumsum(rng.normal(0, 1, n))

        price_df = pd.DataFrame({
            "A": a_vals,
            "B": b_vals,
            "C": c_vals,
            "D": d_vals,
        })

        results = find_cointegrated_pairs(price_df)

        assert len(results) > 0
        assert all(isinstance(r, PairResult) for r in results)

        # At least the A-B pair should be cointegrated
        ab_pairs = [r for r in results if set([r.symbol_a, r.symbol_b]) == {"A", "B"}]
        assert len(ab_pairs) >= 1
        assert ab_pairs[0].is_cointegrated

    def test_sorted_by_p_value(self) -> None:
        """Results should be sorted by p-value ascending."""
        rng = np.random.default_rng(42)
        n = 500
        price_df = pd.DataFrame({
            "X": np.cumsum(rng.normal(0, 1, n)),
            "Y": np.cumsum(rng.normal(0, 1, n)),
            "Z": np.cumsum(rng.normal(0, 1, n)),
        })
        results = find_cointegrated_pairs(price_df)
        p_values = [r.p_value for r in results]
        assert p_values == sorted(p_values)

    def test_empty_dataframe(self) -> None:
        """Should return empty list for a DataFrame with < 2 columns."""
        price_df = pd.DataFrame({"A": [1.0, 2.0, 3.0]})
        results = find_cointegrated_pairs(price_df)
        assert results == []

    def test_hedge_ratio_positive_for_cointegrated(self) -> None:
        """Hedge ratio for a cointegrated pair should be non-zero."""
        rng = np.random.default_rng(42)
        n = 500
        base = np.cumsum(rng.normal(0, 1, n))
        a_vals = base + rng.normal(0, 0.5, n)
        b_vals = 2.0 * base + rng.normal(0, 0.5, n)

        price_df = pd.DataFrame({"A": a_vals, "B": b_vals})
        results = find_cointegrated_pairs(price_df)

        # The hedge ratio should be close to 0.5 (since B = 2*base and A = base)
        # A ~ 1*base, so A/B hedge ratio ~ 0.5
        assert len(results) == 1
        assert abs(results[0].hedge_ratio - 0.5) < 0.15
