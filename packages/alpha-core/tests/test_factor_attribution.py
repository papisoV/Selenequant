"""Tests for factor risk attribution module."""

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

from attribution.factor_model import (
    CRYPTO_FACTORS,
    build_crypto_factor_returns,
    build_factor_exposures,
    decompose_risk,
    factor_attribution,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_synthetic_data(
    n_periods: int = 200,
    n_assets: int = 5,
    n_factors: int = 3,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame, np.ndarray, np.ndarray]:
    """Create synthetic returns with known factor structure.

    Returns (returns_df, factor_returns_df, true_betas, true_idio_var).
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=n_periods)

    # Factor returns
    f = rng.normal(0.0, 0.01, size=(n_periods, n_factors))
    factor_returns_df = pd.DataFrame(
        f, index=dates, columns=[f"FACTOR_{i}" for i in range(n_factors)]
    )

    # True betas and idiosyncratic noise
    true_betas = rng.normal(0.5, 0.3, size=(n_assets, n_factors))
    idio_std = rng.uniform(0.005, 0.02, size=n_assets)
    true_idio_var = idio_std ** 2

    # Asset returns = betas @ f.T + noise  (plus alpha=0)
    alpha = rng.normal(0.0001, 0.001, size=n_assets)
    r = alpha + f @ true_betas.T + rng.normal(0, 1, size=(n_periods, n_assets)) * idio_std
    returns_df = pd.DataFrame(
        r, index=dates, columns=[f"ASSET_{i}" for i in range(n_assets)]
    )

    return returns_df, factor_returns_df, true_betas, true_idio_var


# ---------------------------------------------------------------------------
# build_factor_exposures
# ---------------------------------------------------------------------------


class TestBuildFactorExposures:
    """Tests for time-series OLS factor exposure estimation."""

    def test_recover_known_betas(self) -> None:
        """Estimated betas should be close to the true generating betas."""
        returns, factor_returns, true_betas, _ = _make_synthetic_data(
            n_periods=500, seed=123
        )
        result = build_factor_exposures(returns, factor_returns)

        assert result.betas.shape == true_betas.shape
        # Betas within tolerance — OLS on 500 obs with moderate noise
        # Some true betas are near 0.2, so absolute error can reach ~0.25
        np.testing.assert_allclose(result.betas, true_betas, atol=0.25)

    def test_residuals_shape(self) -> None:
        returns, factor_returns, _, _ = _make_synthetic_data(n_periods=100, n_assets=3, n_factors=2)
        result = build_factor_exposures(returns, factor_returns)

        assert result.residuals.shape == (100, 3)
        assert result.r_squared.shape == (3,)

    def test_r_squared_in_unit_interval(self) -> None:
        returns, factor_returns, _, _ = _make_synthetic_data(n_periods=100, n_assets=4, n_factors=2)
        result = build_factor_exposures(returns, factor_returns)

        assert np.all(result.r_squared >= -1e-10)
        assert np.all(result.r_squared <= 1.0 + 1e-10)

    def test_high_r_squared_with_strong_factor(self) -> None:
        """When factors explain most variance, R² should be high."""
        rng = np.random.default_rng(99)
        n_periods, n_factors, n_assets = 300, 1, 3
        dates = pd.bdate_range("2024-01-01", periods=n_periods)
        f = rng.normal(0.01, 0.02, size=(n_periods, n_factors))
        factor_returns = pd.DataFrame(f, index=dates, columns=["F0"])

        # Very strong betas, tiny noise
        betas = np.array([[1.5], [2.0], [0.8]])
        noise = rng.normal(0, 0.001, size=(n_periods, n_assets))
        r = f @ betas.T + noise
        returns = pd.DataFrame(r, index=dates, columns=["A0", "A1", "A2"])

        result = build_factor_exposures(returns, factor_returns)
        assert np.all(result.r_squared > 0.9)

    def test_no_overlapping_index_raises(self) -> None:
        returns = pd.DataFrame(np.zeros((5, 2)), index=range(5), columns=["A", "B"])
        factor_returns = pd.DataFrame(np.zeros((5, 1)), index=range(10, 15), columns=["F"])
        with pytest.raises(ValueError, match="no overlapping index"):
            build_factor_exposures(returns, factor_returns)


# ---------------------------------------------------------------------------
# factor_attribution
# ---------------------------------------------------------------------------


class TestFactorAttribution:
    """Tests for portfolio PnL decomposition."""

    def test_factor_plus_specific_equals_total(self) -> None:
        """Factor contribution + specific contribution should approximate total PnL."""
        returns, factor_returns, _, _ = _make_synthetic_data(n_periods=200, n_assets=5, n_factors=3)
        n_assets = returns.shape[1]
        weights = np.ones(n_assets) / n_assets

        result = factor_attribution(weights, returns, factor_returns)

        # Per-asset: total cumulative ≈ factor + specific
        total_cumulative = (returns.values * weights).sum(axis=0)
        reconstructed = result.total_factor_contrib + result.total_specific_contrib
        np.testing.assert_allclose(reconstructed, total_cumulative, atol=1e-10)

    def test_per_period_decomposition(self) -> None:
        """Each period: factor_fitted + specific = total return."""
        returns, factor_returns, _, _ = _make_synthetic_data(n_periods=100, n_assets=4, n_factors=2)
        weights = np.ones(4) / 4

        result = factor_attribution(weights, returns, factor_returns)

        # specific_returns + factor_fitted should equal total returns
        exposures = build_factor_exposures(returns, factor_returns)
        common_idx = returns.index.intersection(factor_returns.index)
        r = returns.loc[common_idx].values
        f = factor_returns.loc[common_idx].values
        factor_fitted = f @ exposures.betas.T

        reconstructed = factor_fitted + result.specific_returns
        np.testing.assert_allclose(reconstructed, r, atol=1e-10)

    def test_precomputed_exposures(self) -> None:
        """Passing pre-computed exposures should give the same result."""
        returns, factor_returns, _, _ = _make_synthetic_data(n_periods=100, n_assets=3, n_factors=2)
        weights = np.array([0.5, 0.3, 0.2])

        exposures = build_factor_exposures(returns, factor_returns)
        result1 = factor_attribution(weights, returns, factor_returns, exposures=exposures)
        result2 = factor_attribution(weights, returns, factor_returns, exposures=None)

        np.testing.assert_allclose(result1.total_factor_contrib, result2.total_factor_contrib, atol=1e-10)
        np.testing.assert_allclose(result1.total_specific_contrib, result2.total_specific_contrib, atol=1e-10)


# ---------------------------------------------------------------------------
# decompose_risk
# ---------------------------------------------------------------------------


class TestDecomposeRisk:
    """Tests for variance decomposition."""

    def test_total_squared_equals_factor_squared_plus_specific_squared(self) -> None:
        """total_risk² ≈ factor_risk² + specific_risk²."""
        returns, factor_returns, _, true_idio_var = _make_synthetic_data(
            n_periods=300, n_assets=5, n_factors=3
        )
        exposures = build_factor_exposures(returns, factor_returns)

        # Factor covariance from the data
        common_idx = returns.index.intersection(factor_returns.index)
        f = factor_returns.loc[common_idx].values
        cov_factor = np.cov(f, rowvar=False)

        result = decompose_risk(cov_factor, exposures, true_idio_var)

        total_var = result.total_risk ** 2
        factor_var = result.factor_risk ** 2
        specific_var = result.specific_risk ** 2
        np.testing.assert_allclose(total_var, factor_var + specific_var, atol=1e-10)

    def test_factor_contrib_pct_in_unit_interval(self) -> None:
        returns, factor_returns, _, true_idio_var = _make_synthetic_data(
            n_periods=200, n_assets=4, n_factors=2
        )
        exposures = build_factor_exposures(returns, factor_returns)
        f = factor_returns.loc[returns.index.intersection(factor_returns.index)].values
        cov_factor = np.cov(f, rowvar=False)

        result = decompose_risk(cov_factor, exposures, true_idio_var)

        assert np.all(result.factor_contrib_pct >= -1e-10)
        assert np.all(result.factor_contrib_pct <= 1.0 + 1e-10)

    def test_zero_idio_var_gives_full_factor_pct(self) -> None:
        """When idiosyncratic variance is zero, factor contribution should be 100%."""
        returns, factor_returns, _, _ = _make_synthetic_data(n_periods=200, n_assets=3, n_factors=2)
        exposures = build_factor_exposures(returns, factor_returns)
        f = factor_returns.loc[returns.index.intersection(factor_returns.index)].values
        cov_factor = np.cov(f, rowvar=False)

        zero_idio = np.zeros(3)
        result = decompose_risk(cov_factor, exposures, zero_idio)

        np.testing.assert_allclose(result.factor_contrib_pct, 1.0, atol=1e-10)


# ---------------------------------------------------------------------------
# build_crypto_factor_returns
# ---------------------------------------------------------------------------


class TestBuildCryptoFactorReturns:
    """Tests for crypto factor construction from price data."""

    def _make_price_data(
        self, n_periods: int = 120, n_assets: int = 4, seed: int = 42
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        rng = np.random.default_rng(seed)
        dates = pd.bdate_range("2024-01-01", periods=n_periods)
        # Random walk prices
        returns = rng.normal(0.001, 0.02, size=(n_periods, n_assets))
        prices = 100 * np.exp(np.cumsum(returns, axis=0))
        price_df = pd.DataFrame(
            prices, index=dates, columns=["BTC", "ETH", "SOL", "DOGE"]
        )
        volumes = rng.uniform(1e6, 1e9, size=(n_periods, n_assets))
        volume_df = pd.DataFrame(
            volumes, index=dates, columns=["BTC", "ETH", "SOL", "DOGE"]
        )
        return price_df, volume_df

    def test_output_columns_match_crypto_factors(self) -> None:
        price_df, volume_df = self._make_price_data()
        result = build_crypto_factor_returns(price_df, volume_df)

        expected_cols = set(CRYPTO_FACTORS.keys())
        assert set(result.columns) == expected_cols

    def test_no_nan_in_output(self) -> None:
        price_df, volume_df = self._make_price_data()
        result = build_crypto_factor_returns(price_df, volume_df)

        assert not result.isna().any().any()

    def test_output_shorter_than_input(self) -> None:
        """Rolling windows cause early rows to be dropped."""
        price_df, volume_df = self._make_price_data(n_periods=120)
        result = build_crypto_factor_returns(price_df, volume_df)

        assert len(result) < len(price_df)

    def test_without_volume_df(self) -> None:
        """Should work without volume data (uses log-price as size proxy)."""
        price_df, _ = self._make_price_data()
        result = build_crypto_factor_returns(price_df, volume_df=None)

        assert "size" in result.columns
        assert not result.isna().any().any()

    def test_market_factor_is_btc_returns(self) -> None:
        price_df, volume_df = self._make_price_data()
        result = build_crypto_factor_returns(price_df, volume_df)

        # Reconstruct BTC returns manually
        btc_returns = price_df["BTC"].pct_change().dropna()
        # Result index is a subset (rolling window drops early rows), so align
        aligned = result["market"].reindex(btc_returns.index).dropna()
        expected = btc_returns.reindex(aligned.index)
        np.testing.assert_allclose(aligned.values, expected.values, atol=1e-10)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge cases: single factor, single asset."""

    def test_single_factor(self) -> None:
        """Regression with one factor should work and produce (N, 1) betas."""
        rng = np.random.default_rng(77)
        n_periods, n_assets = 150, 3
        dates = pd.bdate_range("2024-01-01", periods=n_periods)
        f = rng.normal(0.01, 0.02, size=(n_periods, 1))
        factor_returns = pd.DataFrame(f, index=dates, columns=["MKT"])
        betas_true = np.array([[1.2], [0.8], [1.5]])
        r = f @ betas_true.T + rng.normal(0, 0.005, size=(n_periods, n_assets))
        returns = pd.DataFrame(r, index=dates, columns=["A", "B", "C"])

        result = build_factor_exposures(returns, factor_returns)
        assert result.betas.shape == (3, 1)
        np.testing.assert_allclose(result.betas, betas_true, atol=0.15)

    def test_single_asset(self) -> None:
        """Regression with one asset should work and produce (1, K) betas."""
        rng = np.random.default_rng(88)
        n_periods, n_factors = 150, 2
        dates = pd.bdate_range("2024-01-01", periods=n_periods)
        f = rng.normal(0.01, 0.02, size=(n_periods, n_factors))
        factor_returns = pd.DataFrame(f, index=dates, columns=["F0", "F1"])
        beta_true = np.array([[0.9, 1.3]])
        r = f @ beta_true.T + rng.normal(0, 0.005, size=(n_periods, 1))
        returns = pd.DataFrame(r, index=dates, columns=["SINGLE"])

        result = build_factor_exposures(returns, factor_returns)
        assert result.betas.shape == (1, 2)
        np.testing.assert_allclose(result.betas, beta_true, atol=0.15)

    def test_single_asset_attribution(self) -> None:
        """Attribution with one asset and one factor."""
        rng = np.random.default_rng(55)
        n_periods = 100
        dates = pd.bdate_range("2024-01-01", periods=n_periods)
        f = rng.normal(0.01, 0.02, size=(n_periods, 1))
        factor_returns = pd.DataFrame(f, index=dates, columns=["MKT"])
        r = 1.2 * f + rng.normal(0, 0.005, size=(n_periods, 1))
        returns = pd.DataFrame(r, index=dates, columns=["A"])

        weights = np.array([1.0])
        result = factor_attribution(weights, returns, factor_returns)

        assert result.specific_returns.shape == (n_periods, 1)
        assert result.total_factor_contrib.shape == (1,)


# ---------------------------------------------------------------------------
# Integration: full pipeline
# ---------------------------------------------------------------------------


class TestIntegration:
    """End-to-end pipeline: price -> factors -> exposures -> attribution -> risk."""

    def test_full_pipeline(self) -> None:
        rng = np.random.default_rng(2024)
        n_periods = 200
        n_assets = 5
        dates = pd.bdate_range("2024-01-01", periods=n_periods)

        # Generate price data
        daily_ret = rng.normal(0.001, 0.02, size=(n_periods, n_assets))
        prices = 100 * np.exp(np.cumsum(daily_ret, axis=0))
        price_df = pd.DataFrame(
            prices, index=dates, columns=["BTC", "ETH", "SOL", "AVAX", "MATIC"]
        )
        volumes = rng.uniform(1e6, 1e9, size=(n_periods, n_assets))
        volume_df = pd.DataFrame(
            volumes, index=dates, columns=price_df.columns
        )

        # Step 1: Build factor returns
        factor_returns = build_crypto_factor_returns(price_df, volume_df)
        assert len(factor_returns) > 0
        assert not factor_returns.isna().any().any()

        # Step 2: Build asset returns aligned with factor returns
        asset_returns = price_df.pct_change().loc[factor_returns.index]
        asset_returns = asset_returns.dropna()
        factor_returns = factor_returns.loc[asset_returns.index]

        # Step 3: Estimate exposures
        exposures = build_factor_exposures(asset_returns, factor_returns)
        assert exposures.betas.shape[0] == n_assets
        assert exposures.betas.shape[1] == len(CRYPTO_FACTORS)
        assert np.all(exposures.r_squared >= -1e-10)

        # Step 4: Attribution
        weights = np.ones(n_assets) / n_assets
        attrib = factor_attribution(weights, asset_returns, factor_returns, exposures=exposures)

        total_cum = (asset_returns.values * weights).sum(axis=0)
        reconstructed = attrib.total_factor_contrib + attrib.total_specific_contrib
        np.testing.assert_allclose(reconstructed, total_cum, atol=1e-8)

        # Step 5: Risk decomposition
        f_data = factor_returns.values
        cov_factor = np.cov(f_data, rowvar=False)
        idio_var = np.var(exposures.residuals, axis=0, ddof=1)

        risk = decompose_risk(cov_factor, exposures, idio_var)

        # Variance additivity
        total_var = risk.total_risk ** 2
        factor_var = risk.factor_risk ** 2
        specific_var = risk.specific_risk ** 2
        np.testing.assert_allclose(total_var, factor_var + specific_var, atol=1e-10)

        # Factor contribution percentage sanity
        assert np.all(risk.factor_contrib_pct >= -1e-10)
        assert np.all(risk.factor_contrib_pct <= 1.0 + 1e-10)

    def test_pipeline_single_asset_single_factor(self) -> None:
        """Pipeline works even with minimal dimensions."""
        rng = np.random.default_rng(2025)
        n_periods = 150
        dates = pd.bdate_range("2024-01-01", periods=n_periods)

        # Single asset price series
        ret = rng.normal(0.001, 0.02, size=n_periods)
        prices = 100 * np.exp(np.cumsum(ret))
        price_df = pd.DataFrame(prices, index=dates, columns=["BTC"])
        volume_df = pd.DataFrame(
            rng.uniform(1e7, 1e8, size=n_periods), index=dates, columns=["BTC"]
        )

        factor_returns = build_crypto_factor_returns(price_df, volume_df)
        asset_returns = price_df.pct_change().loc[factor_returns.index].dropna()
        factor_returns = factor_returns.loc[asset_returns.index]

        exposures = build_factor_exposures(asset_returns, factor_returns)
        assert exposures.betas.shape[0] == 1

        weights = np.array([1.0])
        attrib = factor_attribution(weights, asset_returns, factor_returns, exposures=exposures)
        assert attrib.total_factor_contrib.shape == (1,)

        f_data = factor_returns.values
        cov_factor = np.cov(f_data, rowvar=False)
        if cov_factor.ndim == 0:
            cov_factor = np.array([[float(cov_factor)]])
        idio_var = np.var(exposures.residuals, axis=0, ddof=1)

        risk = decompose_risk(cov_factor, exposures, idio_var)
        assert risk.total_risk.shape == (1,)
