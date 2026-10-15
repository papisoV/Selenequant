"""Tests for PortfolioRecommender (M4) — end-to-end BL → optimize pipeline."""

from __future__ import annotations

import numpy as np
import pytest

from selene_space_alpha.portfolio import (
    PortfolioRecommender,
    Recommendation,
    RecommenderConfig,
)


def _three_asset_cov() -> np.ndarray:
    """3x3 PSD annualized covariance for BTC / ETH / SOL."""
    return np.array(
        [
            [0.04, 0.02, 0.01],
            [0.02, 0.06, 0.015],
            [0.01, 0.015, 0.09],
        ]
    )


def _market_weights() -> np.ndarray:
    return np.array([0.6, 0.3, 0.1])


class TestRecommenderHappyPath:
    def test_min_variance_recommends_valid_weights(self) -> None:
        rec = PortfolioRecommender(RecommenderConfig(objective="min_variance"))
        r = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={"BTC": 0.10, "ETH": 0.15},
        )
        assert isinstance(r, Recommendation)
        assert r.opt_result.success
        # Weights sum to ~1.0 (full investment).
        assert np.isclose(np.sum(r.weights), 1.0, atol=1e-4)
        # Long-only constraint: all weights >= 0.
        assert np.all(r.weights >= -1e-6)

    def test_max_sharpe_objective(self) -> None:
        rec = PortfolioRecommender(RecommenderConfig(objective="max_sharpe"))
        r = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={"BTC": 0.08, "ETH": 0.12, "SOL": 0.20},
        )
        assert r.opt_result.objective == "max_sharpe"
        assert r.opt_result.success
        assert np.isclose(np.sum(r.weights), 1.0, atol=1e-4)

    def test_mean_variance_with_risk_aversion(self) -> None:
        rec = PortfolioRecommender(RecommenderConfig(objective="mean_variance"))
        r = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={"BTC": 0.10, "ETH": 0.15},
            risk_aversion=2.0,
        )
        assert r.opt_result.success

    def test_no_signals_still_returns_prior(self) -> None:
        """Empty signals → P/Q empty → BL posterior = prior (scaled cov)."""
        rec = PortfolioRecommender(RecommenderConfig(objective="min_variance"))
        r = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={},
        )
        # Should still succeed with empty views.
        assert r.opt_result.success
        # BL posterior mu should equal equilibrium.
        np.testing.assert_allclose(
            r.bl_result.mu_posterior, r.bl_result.mu_equilibrium
        )


class TestRecommenderValidation:
    def test_empty_assets_raises(self) -> None:
        rec = PortfolioRecommender()
        with pytest.raises(ValueError, match="assets must be non-empty"):
            rec.recommend(
                assets=[],
                market_weights=np.array([]),
                cov=np.zeros((0, 0)),
                signals={},
            )

    def test_market_weights_shape_mismatch_raises(self) -> None:
        rec = PortfolioRecommender()
        with pytest.raises(ValueError, match="market_weights shape"):
            rec.recommend(
                assets=["BTC", "ETH", "SOL"],
                market_weights=np.array([0.5, 0.5]),  # wrong shape
                cov=_three_asset_cov(),
                signals={},
            )

    def test_market_weights_not_summing_to_one_raises(self) -> None:
        rec = PortfolioRecommender()
        with pytest.raises(ValueError, match="must sum to 1.0"):
            rec.recommend(
                assets=["BTC", "ETH", "SOL"],
                market_weights=np.array([0.5, 0.3, 0.1]),  # sums to 0.9
                cov=_three_asset_cov(),
                signals={},
            )

    def test_cov_shape_mismatch_raises(self) -> None:
        rec = PortfolioRecommender()
        with pytest.raises(ValueError, match="cov shape"):
            rec.recommend(
                assets=["BTC", "ETH", "SOL"],
                market_weights=_market_weights(),
                cov=np.zeros((2, 2)),  # wrong shape
                signals={},
            )

    def test_non_symmetric_cov_raises(self) -> None:
        rec = PortfolioRecommender()
        cov = np.array(
            [
                [0.04, 0.02, 0.01],
                [0.025, 0.06, 0.015],  # asymmetric
                [0.01, 0.015, 0.09],
            ]
        )
        with pytest.raises(ValueError, match="cov must be symmetric"):
            rec.recommend(
                assets=["BTC", "ETH", "SOL"],
                market_weights=_market_weights(),
                cov=cov,
                signals={},
            )

    def test_non_psd_cov_raises(self) -> None:
        """Cov with a negative eigenvalue → ValueError."""
        rec = PortfolioRecommender()
        # Construct an indefinite matrix.
        cov = np.array([[1.0, 2.0], [2.0, 1.0]])  # eigenvalues: 3, -1
        with pytest.raises(ValueError, match="cov must be PSD"):
            rec.recommend(
                assets=["BTC", "ETH"],
                market_weights=np.array([0.6, 0.4]),
                cov=cov,
                signals={},
            )


class TestRecommenderSignalsHandling:
    def test_signals_for_unknown_assets_silently_ignored(self) -> None:
        rec = PortfolioRecommender(RecommenderConfig(objective="min_variance"))
        r = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={"BTC": 0.10, "DOGE": 0.99},  # DOGE not in assets
        )
        # Should succeed — DOGE view silently dropped.
        assert r.opt_result.success
        # P should have 1 row (BTC view only), 3 columns.
        assert r.bl_result.views_P.shape == (1, 3)

    def test_signals_shape_views_matrix(self) -> None:
        rec = PortfolioRecommender(RecommenderConfig(objective="min_variance"))
        r = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={"BTC": 0.10, "ETH": 0.15, "SOL": 0.20},
        )
        assert r.bl_result.views_P.shape == (3, 3)
        np.testing.assert_array_almost_equal(
            r.bl_result.views_Q, [0.10, 0.15, 0.20]
        )

    def test_signals_pull_weights_toward_favored_asset(self) -> None:
        """Bullish view on SOL should pull its weight up vs equilibrium."""
        rec = PortfolioRecommender(RecommenderConfig(objective="max_sharpe"))
        # Without SOL view:
        r_neutral = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={"BTC": 0.08, "ETH": 0.10},
        )
        # With strong SOL view:
        r_bull_sol = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={"BTC": 0.08, "ETH": 0.10, "SOL": 0.30},
        )
        # SOL weight should increase with bullish view.
        sol_idx = 2
        assert r_bull_sol.weights[sol_idx] > r_neutral.weights[sol_idx] - 1e-6


class TestRecommenderTurnover:
    def test_turnover_zero_without_prev_weights(self) -> None:
        rec = PortfolioRecommender(RecommenderConfig(objective="min_variance"))
        r = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={},
        )
        assert r.turnover == 0.0
        assert r.prev_weights is None

    def test_turnover_computed_with_prev_weights(self) -> None:
        rec = PortfolioRecommender(RecommenderConfig(objective="min_variance"))
        prev = np.array([0.5, 0.4, 0.1])
        r = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={},
            prev_weights=prev,
        )
        # Turnover = L1 distance.
        expected = float(np.sum(np.abs(r.weights - prev)))
        assert r.turnover == pytest.approx(expected, abs=1e-6)

    def test_turnover_limit_constraint_applied(self) -> None:
        """With a tight turnover limit, the new weights stay close to prev."""
        rec = PortfolioRecommender(
            RecommenderConfig(
                objective="min_variance",
                turnover_limit=0.05,  # 5% max turnover
            )
        )
        prev = np.array([0.6, 0.3, 0.1])  # equals market weights
        r = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={"SOL": 0.30},  # bullish SOL would otherwise pull weight far
            prev_weights=prev,
        )
        # Should be within the turnover limit (allow small numerical slack).
        assert r.turnover <= 0.05 + 1e-3


class TestRecommenderConfig:
    def test_default_config_values(self) -> None:
        cfg = RecommenderConfig()
        assert cfg.tau == 0.05
        assert cfg.risk_free_rate == 0.02
        assert cfg.objective == "max_sharpe"
        assert cfg.long_only
        assert cfg.full_investment
        assert cfg.weight_cap is None
        assert cfg.turnover_limit is None

    def test_weight_cap_enforced(self) -> None:
        """weight_cap=0.4 should prevent any single asset exceeding 40%."""
        rec = PortfolioRecommender(
            RecommenderConfig(objective="max_sharpe", weight_cap=0.4)
        )
        r = rec.recommend(
            assets=["BTC", "ETH", "SOL"],
            market_weights=_market_weights(),
            cov=_three_asset_cov(),
            signals={"BTC": 0.30},  # would otherwise push BTC very high
        )
        assert np.all(r.weights <= 0.4 + 1e-6)
