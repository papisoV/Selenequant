"""Tests for spread module — spread computation, hedge ratios, Z-score, half-life."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

# Direct import path (bypasses top-level __init__ that may trigger heavy deps)
_alpha_core = str(Path(__file__).parent.parent / "selene_space_alpha")
if _alpha_core not in sys.path:
    sys.path.insert(0, _alpha_core)

from stat_arb.spread import (
    compute_hedge_ratio_kalman,
    compute_hedge_ratio_ols,
    compute_spread,
    half_life,
    z_score_spread,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def cointegrated_pair() -> tuple[pd.Series, pd.Series, float]:
    """Return (price_a, price_b, true_hedge_ratio) for a cointegrated pair.

    y = 2.5 * x + noise, so true hedge ratio = 2.5.
    """
    rng = np.random.default_rng(42)
    n = 500
    x_vals = np.cumsum(rng.normal(0, 1, n))
    noise = rng.normal(0, 0.3, n)
    y_vals = 2.5 * x_vals + noise
    idx = pd.date_range("2020-01-01", periods=n, freq="D")
    return pd.Series(y_vals, index=idx), pd.Series(x_vals, index=idx), 2.5


@pytest.fixture
def ou_spread() -> pd.Series:
    """Generate a known OU process with half-life ~20 periods."""
    rng = np.random.default_rng(99)
    n = 2000
    theta = 0.05  # mean reversion speed
    # half_life = -ln(2) / -theta = ln(2)/theta ~ 13.86
    spread = np.empty(n)
    spread[0] = 0.0
    for t in range(1, n):
        spread[t] = spread[t - 1] * (1 - theta) + rng.normal(0, 1)
    return pd.Series(spread)


# ---------------------------------------------------------------------------
# compute_spread
# ---------------------------------------------------------------------------

class TestComputeSpread:
    """Tests for compute_spread."""

    def test_known_hedge_ratio(self) -> None:
        """Spread with known ratio should match manual computation."""
        price_a = pd.Series([10.0, 20.0, 30.0])
        price_b = pd.Series([5.0, 10.0, 15.0])
        hedge = 2.0
        spread = compute_spread(price_a, price_b, hedge)
        expected = pd.Series([0.0, 0.0, 0.0])
        pd.testing.assert_series_equal(spread, expected)

    def test_nonzero_spread(self) -> None:
        """Spread with different ratio should produce nonzero values."""
        price_a = pd.Series([10.0, 20.0, 30.0])
        price_b = pd.Series([5.0, 10.0, 15.0])
        spread = compute_spread(price_a, price_b, 1.0)
        expected = pd.Series([5.0, 10.0, 15.0])
        pd.testing.assert_series_equal(spread, expected)

    def test_misaligned_indices(self) -> None:
        """Only common indices should be used."""
        price_a = pd.Series([1.0, 2.0, 3.0], index=[0, 1, 2])
        price_b = pd.Series([1.0, 2.0, 3.0], index=[1, 2, 3])
        spread = compute_spread(price_a, price_b, 1.0)
        assert len(spread) == 2
        pd.testing.assert_series_equal(
            spread,
            pd.Series([1.0, 1.0], index=[1, 2]),
        )


# ---------------------------------------------------------------------------
# compute_hedge_ratio_ols
# ---------------------------------------------------------------------------

class TestComputeHedgeRatioOls:
    """Tests for compute_hedge_ratio_ols."""

    def test_recovers_known_ratio(self, cointegrated_pair: tuple) -> None:
        """OLS should recover the true hedge ratio approximately."""
        price_a, price_b, true_ratio = cointegrated_pair
        estimated = compute_hedge_ratio_ols(price_a, price_b)
        assert abs(estimated - true_ratio) < 0.1

    def test_exact_linear(self) -> None:
        """For y = 3 * x exactly, OLS should return 3.0."""
        x = pd.Series(np.arange(100, dtype=float))
        y = 3.0 * x
        assert abs(compute_hedge_ratio_ols(y, x) - 3.0) < 1e-10


# ---------------------------------------------------------------------------
# compute_hedge_ratio_kalman
# ---------------------------------------------------------------------------

class TestComputeHedgeRatioKalman:
    """Tests for compute_hedge_ratio_kalman."""

    def test_produces_reasonable_ratios(self, cointegrated_pair: tuple) -> None:
        """Kalman filter ratios should be in a reasonable range near the true ratio."""
        price_a, price_b, true_ratio = cointegrated_pair
        ratios = compute_hedge_ratio_kalman(price_a, price_b)
        assert isinstance(ratios, pd.Series)
        assert len(ratios) == len(price_a)

        # Median of ratios should be near the true ratio
        median_ratio = ratios.median()
        assert abs(median_ratio - true_ratio) < 0.5

    def test_constant_series(self) -> None:
        """Kalman filter should handle constant x gracefully."""
        y = pd.Series(np.arange(50, dtype=float))
        x = pd.Series([1.0] * 50)
        ratios = compute_hedge_ratio_kalman(y, x)
        assert isinstance(ratios, pd.Series)
        # Should not crash; values may vary but should be finite
        assert ratios.notna().any()

    def test_output_index_matches_input(self, cointegrated_pair: tuple) -> None:
        """Output index should match the common index of inputs."""
        price_a, price_b, _ = cointegrated_pair
        ratios = compute_hedge_ratio_kalman(price_a, price_b)
        pd.testing.assert_index_equal(ratios.index, price_a.index)


# ---------------------------------------------------------------------------
# z_score_spread
# ---------------------------------------------------------------------------

class TestZScoreSpread:
    """Tests for z_score_spread."""

    def test_normalization(self) -> None:
        """Z-score of a long series should have mean ~0 and std ~1."""
        rng = np.random.default_rng(42)
        spread = pd.Series(rng.normal(0, 1, size=10000))
        zscore = z_score_spread(spread, window=100)

        # Drop initial NaNs
        valid = zscore.dropna()
        assert abs(valid.mean()) < 0.1
        assert abs(valid.std() - 1.0) < 0.15

    def test_window_produces_nans(self) -> None:
        """First (window - 1) values should be NaN."""
        spread = pd.Series(np.arange(50, dtype=float))
        zscore = z_score_spread(spread, window=20)
        assert zscore.iloc[:19].isna().all()
        assert zscore.iloc[19:].notna().all()

    def test_known_values(self) -> None:
        """Z-score at a specific point should match manual computation."""
        vals = list(np.arange(1, 31, dtype=float))  # 1..30
        spread = pd.Series(vals)
        zscore = z_score_spread(spread, window=5)

        # At index 4 (0-based): window = [1,2,3,4,5]
        window_vals = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
        expected_z = (5.0 - window_vals.mean()) / window_vals.std(ddof=1)
        assert abs(zscore.iloc[4] - expected_z) < 1e-10


# ---------------------------------------------------------------------------
# half_life
# ---------------------------------------------------------------------------

class TestHalfLife:
    """Tests for half_life."""

    def test_known_ou_process(self, ou_spread: pd.Series) -> None:
        """Half-life estimation should be in the right ballpark for known OU."""
        hl = half_life(ou_spread)
        assert not np.isnan(hl)
        # True half-life ~ ln(2)/0.05 ~ 13.86
        # Allow generous tolerance (factor of 2)
        assert 5.0 < hl < 30.0

    def test_random_walk_nan(self) -> None:
        """A random walk should have NaN or very large half-life (not mean-reverting)."""
        rng = np.random.default_rng(42)
        rw = pd.Series(np.cumsum(rng.normal(0, 1, 1000)))
        hl = half_life(rw)
        # Random walk: lambda should be near 0 or positive => NaN
        assert np.isnan(hl) or hl > 1000

    def test_short_series_nan(self) -> None:
        """Very short series should return NaN."""
        short = pd.Series([1.0, 2.0, 3.0])
        assert np.isnan(half_life(short))

    def test_fast_mean_reversion(self) -> None:
        """A fast mean-reverting series should have a short half-life."""
        rng = np.random.default_rng(42)
        n = 5000
        theta = 0.5  # fast reversion
        spread = np.empty(n)
        spread[0] = 0.0
        for t in range(1, n):
            spread[t] = spread[t - 1] * (1 - theta) + rng.normal(0, 1)
        hl = half_life(pd.Series(spread))
        # True half-life = ln(2)/0.5 ~ 1.39
        assert hl < 5.0
