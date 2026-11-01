"""Serenity-to-Portfolio pipeline — build portfolios from Serenity scores."""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from ..serenity.models import SerenityScore
from .constraints import build_constraints
from .covariance import estimate_cov
from .optimizers import optimize, risk_contributions
from .result import PortfolioResult

logger = logging.getLogger(__name__)


class SerenityPortfolioBuilder:
    """Build portfolios from Serenity scores.

    Pipeline:
    1. Score multiple assets with SerenityScorer
    2. Extract normalized_score as alpha signal (mu)
    3. Estimate covariance from historical returns
    4. Build sector-neutral constraints
    5. Optimize portfolio
    """

    def __init__(
        self,
        objective: str = "max_sharpe",
        long_only: bool = False,
        sector_neutral: bool = True,
        weight_cap: float = 0.15,
        net_exposure: float = 0.0,
        turnover_limit: float | None = None,
        cov_method: str = "ledoit_wolf",
    ) -> None:
        self.objective = objective
        self.long_only = long_only
        self.sector_neutral = sector_neutral
        self.weight_cap = weight_cap
        self.net_exposure = net_exposure
        self.turnover_limit = turnover_limit
        self.cov_method = cov_method

    def build_portfolio(
        self,
        scores: list[SerenityScore],
        returns_df: pd.DataFrame,
        sectors: np.ndarray | None = None,
    ) -> PortfolioResult:
        """Build portfolio from Serenity scores and historical returns.

        Args:
            scores: SerenityScore for each asset.
            returns_df: Historical returns (dates x symbols).
            sectors: Sector labels per symbol (optional).
        """
        n = len(scores)
        if n < 2:
            raise ValueError("Need at least 2 assets for portfolio construction")

        # Map Serenity scores to expected returns (mu)
        mu = np.array([s.normalized_score for s in scores])

        # Estimate covariance
        cov_df = estimate_cov(returns_df, method=self.cov_method)
        cov = cov_df.values

        # Build constraints
        sector_targets = None
        if sectors is not None and self.sector_neutral:
            unique_sectors = np.unique(sectors)
            sector_targets = {int(s): 0.0 for s in unique_sectors}

        constraints = build_constraints(
            n=n,
            long_only=self.long_only,
            full_investment=False,
            weight_cap=self.weight_cap,
            sectors=sectors,
            sector_targets=sector_targets,
            net_exposure=self.net_exposure,
        )

        # Optimize
        result = optimize(self.objective, cov, mu=mu, constraints=constraints)

        # Compute portfolio metrics
        w = result.weights
        expected_return = float(w @ mu)
        volatility = np.sqrt(float(w @ cov @ w) + 1e-15)
        sharpe = expected_return / volatility if volatility > 1e-15 else 0.0
        rc = risk_contributions(w, cov)

        return PortfolioResult(
            weights=w,
            objective=result.objective,
            success=result.success,
            message=result.message,
            expected_return=round(expected_return, 6),
            volatility=round(volatility, 6),
            sharpe_ratio=round(sharpe, 4),
            risk_contributions=rc,
        )

    def rank_and_select(
        self,
        scores: list[SerenityScore],
        n_long: int = 10,
        n_short: int = 10,
    ) -> tuple[list[str], list[str]]:
        """Simple long-short selection from Serenity rankings.

        Top n_long = long, bottom n_short = short.
        """
        sorted_scores = sorted(scores, key=lambda s: s.normalized_score, reverse=True)
        long_symbols = [s.symbol for s in sorted_scores[:n_long]]
        short_symbols = [s.symbol for s in sorted_scores[-n_short:]]
        return long_symbols, short_symbols
