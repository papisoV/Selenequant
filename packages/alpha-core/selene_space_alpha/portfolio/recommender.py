"""Portfolio recommender — productized Black-Litterman pipeline.

Glues together the equilibrium prior, signal-derived views, BL posterior,
and a downstream mean-variance / max-Sharpe optimizer into a single
callable. Designed for strategy-engine consumers that want a turnkey
"signals → target weights" function without assembling the BL math
themselves.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from selene_space_alpha.portfolio.black_litterman import (
    BLResult,
    black_litterman,
    views_from_signals,
)
from selene_space_alpha.portfolio.constraints import Constraints, build_constraints
from selene_space_alpha.portfolio.optimizers import OptResult, optimize


@dataclass
class RecommenderConfig:
    """Configuration for the portfolio recommender.

    Attributes:
        tau: BL prior uncertainty scalar (typical 0.025-0.10).
        risk_free_rate: Annualized risk-free rate, used in Sharpe and
            to shift equilibrium excess returns to absolute if needed.
        objective: Downstream optimizer objective — one of
            ``max_sharpe``, ``mean_variance``, ``min_variance``,
            ``risk_parity``, ``max_diversification``.
        long_only: If True, weights ∈ [0, 1].
        full_investment: If True, weights sum to 1.0.
        weight_cap: Optional per-asset weight ceiling (e.g. 0.30 for
            a 30% max concentration).
        turnover_limit: Optional L1 distance from current weights
            (e.g. 0.20 = max 20% turnover). Requires ``prev_weights``.
    """

    tau: float = 0.05
    risk_free_rate: float = 0.02
    objective: str = "max_sharpe"
    long_only: bool = True
    full_investment: bool = True
    weight_cap: float | None = None
    turnover_limit: float | None = None


@dataclass
class Recommendation:
    """Full output of a portfolio recommendation pass.

    Attributes:
        assets: Ordered asset symbols (matches weight vector shape).
        weights: Target portfolio weights (n_assets,).
        bl_result: BLResult with posterior mu, posterior cov, views.
        opt_result: OptResult from the downstream optimizer.
        prev_weights: Optional previous weights used for turnover calc.
        turnover: L1 distance between prev_weights and new weights (0 if
            no prev_weights provided).
        config: The RecommenderConfig used for this pass.
    """

    assets: list[str]
    weights: np.ndarray
    bl_result: BLResult
    opt_result: OptResult
    prev_weights: np.ndarray | None
    turnover: float
    config: RecommenderConfig


class PortfolioRecommender:
    """End-to-end signal → target-weights recommender.

    Wraps the BL model + optimizer. Call :meth:`recommend` with the
    current market cap weights (equilibrium prior), a covariance matrix,
    and a signal dict {symbol: expected_excess_return}; returns a
    :class:`Recommendation` containing target weights, BL posterior,
    and downstream optimization result.

    Typical caller (strategy engine):

        >>> recommender = PortfolioRecommender()
        >>> rec = recommender.recommend(
        ...     assets=["BTC", "ETH", "SOL"],
        ...     market_weights=np.array([0.6, 0.3, 0.1]),
        ...     cov=cov_matrix,             # 3x3 annualized cov
        ...     signals={"BTC": 0.10, "ETH": 0.15},  # absolute views
        ... )
        >>> rec.weights
        array([...])  # target weights summing to 1.0
    """

    def __init__(self, config: RecommenderConfig | None = None) -> None:
        self._config = config or RecommenderConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def recommend(
        self,
        assets: list[str],
        market_weights: np.ndarray,
        cov: np.ndarray,
        signals: dict[str, float],
        *,
        prev_weights: np.ndarray | None = None,
        risk_aversion: float = 1.0,
    ) -> Recommendation:
        """Run equilibrium → BL → optimize → target weights.

        Args:
            assets: Ordered asset symbols.
            market_weights: Market-cap weights (n_assets,). Must sum to
                1.0 and align with ``assets``. Used to derive the
                equilibrium excess returns via reverse optimization.
            cov: Annualized covariance matrix (n_assets, n_assets).
            signals: Strategy views — {symbol: expected_excess_return}.
                Only assets present in ``assets`` contribute views.
            prev_weights: Optional current portfolio weights for
                turnover-constrained optimization.
            risk_aversion: Risk aversion scalar λ; only used when the
                downstream objective is ``mean_variance`` (passed as
                ``risk_aversion`` kwarg).

        Returns:
            Recommendation with target weights, BL posterior, and
            optimizer diagnostics.

        Raises:
            ValueError: On shape mismatch, non-PSD cov, or empty assets.
        """
        self._validate_inputs(assets, market_weights, cov, signals)
        n = len(assets)

        # 1. Reverse optimization: equilibrium excess returns.
        #    mu_eq = lambda * Sigma * w_mkt
        mu_eq = risk_aversion * cov @ market_weights

        # 2. Build views from signals.
        P, Q = views_from_signals(signals, assets)

        # 3. BL posterior.
        bl_result = black_litterman(
            mu_eq=mu_eq,
            cov=cov,
            P=P,
            Q=Q,
            tau=self._config.tau,
        )

        # 4. Downstream optimization.
        constraints = self._build_constraints(n, prev_weights)
        opt_kwargs: dict[str, Any] = {}
        if self._config.objective == "mean_variance":
            opt_kwargs["risk_aversion"] = risk_aversion
        opt_result = optimize(
            objective=self._config.objective,
            cov=bl_result.cov_posterior,
            mu=bl_result.mu_posterior,
            constraints=constraints,
            **opt_kwargs,
        )

        # 5. Turnover accounting.
        turnover = 0.0
        if prev_weights is not None:
            turnover = float(np.sum(np.abs(opt_result.weights - prev_weights)))

        return Recommendation(
            assets=list(assets),
            weights=opt_result.weights,
            bl_result=bl_result,
            opt_result=opt_result,
            prev_weights=prev_weights,
            turnover=turnover,
            config=self._config,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_constraints(
        self, n: int, prev_weights: np.ndarray | None
    ) -> Constraints:
        """Build Constraints from config + optional prev_weights."""
        return build_constraints(
            n=n,
            long_only=self._config.long_only,
            full_investment=self._config.full_investment,
            weight_cap=self._config.weight_cap,
            w_prev=prev_weights,
            turnover_limit=self._config.turnover_limit,
        )

    @staticmethod
    def _validate_inputs(
        assets: list[str],
        market_weights: np.ndarray,
        cov: np.ndarray,
        signals: dict[str, float],
    ) -> None:
        if not assets:
            raise ValueError("assets must be non-empty")
        n = len(assets)
        if market_weights.shape != (n,):
            raise ValueError(
                f"market_weights shape {market_weights.shape} != ({n},)"
            )
        if cov.shape != (n, n):
            raise ValueError(
                f"cov shape {cov.shape} != ({n}, {n})"
            )
        if not np.allclose(np.sum(market_weights), 1.0, atol=1e-6):
            raise ValueError(
                f"market_weights must sum to 1.0 (got {np.sum(market_weights)})"
            )
        # Validate cov is PSD (symmetric + non-negative eigenvalues).
        if not np.allclose(cov, cov.T, atol=1e-8):
            raise ValueError("cov must be symmetric")
        eigvals = np.linalg.eigvalsh(cov)
        if eigvals.min() < -1e-8:
            raise ValueError(
                f"cov must be PSD; min eigenvalue {eigvals.min():.2e}"
            )
        # Signals not in assets are silently ignored by views_from_signals,
        # but we validate the structure here.
        if not isinstance(signals, dict):
            raise TypeError("signals must be a dict[str, float]")
