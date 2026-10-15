"""Serenity 8-factor ablation analysis for multicollinearity and marginal contribution.

Checks whether the 8 factors are redundant via:
1. Correlation matrix analysis
2. Variance Inflation Factor (VIF)
3. Leave-one-out ablation (marginal contribution)
4. Condition number of the factor correlation matrix
"""

from __future__ import annotations

import logging

import numpy as np
from dataclasses import dataclass

from .models import SerenitySnapshot
from .scorer import SerenityScorer

logger = logging.getLogger(__name__)

FACTOR_NAMES = (
    "consensus_scarcity",
    "liquidity_scarcity",
    "trust_scarcity",
    "replication_difficulty",
    "certification_progress",
    "prepayment_signal",
    "decentralization",
    "evidence_strength",
)


@dataclass(frozen=True)
class FactorAblationResult:
    factor_name: str
    standalone_r2: float
    marginal_r2: float
    correlation_with_score: float
    vif: float
    is_redundant: bool


@dataclass(frozen=True)
class AblationReport:
    factor_results: tuple[FactorAblationResult, ...]
    correlation_matrix: np.ndarray
    condition_number: float
    n_redundant: int
    recommendation: str


class SerenityAblation:
    """Analyze Serenity 8-factor multicollinearity and marginal contribution."""

    def __init__(
        self,
        scorer: SerenityScorer,
        vif_threshold: float = 10.0,
        marginal_r2_threshold: float = 0.01,
    ) -> None:
        self.scorer = scorer
        self.vif_threshold = vif_threshold
        self.marginal_r2_threshold = marginal_r2_threshold

    def analyze(self, snapshots: list[SerenitySnapshot]) -> AblationReport:
        """Run full ablation analysis on a set of scored snapshots."""
        if len(snapshots) < 5:
            raise ValueError("Need at least 5 snapshots for ablation analysis")

        factor_matrix = self._compute_factor_matrix(snapshots)
        scores = np.array([
            self.scorer.score(s).normalized_score for s in snapshots
        ])

        # Correlation analysis
        corr_matrix = np.corrcoef(factor_matrix.T)
        # Replace NaN from constant factors with 0
        corr_matrix = np.nan_to_num(corr_matrix, nan=0.0)
        np.fill_diagonal(corr_matrix, 1.0)

        # Condition number
        eigvals = np.linalg.eigvalsh(corr_matrix)
        eigvals = eigvals[eigvals > 1e-15]
        condition_number = float(np.max(eigvals) / np.min(eigvals)) if len(eigvals) > 0 else float("inf")

        # VIF for each factor
        vifs = self._compute_vif(factor_matrix)

        # Leave-one-out ablation
        factor_results_list = self._leave_one_out_ablation(
            factor_matrix, scores, vifs
        )

        n_redundant = sum(1 for r in factor_results_list if r.is_redundant)

        if n_redundant == 0:
            recommendation = "keep_all"
        elif n_redundant <= 2:
            recommendation = f"drop_{n_redundant}_factors"
        else:
            recommendation = "restructure"

        return AblationReport(
            factor_results=tuple(factor_results_list),
            correlation_matrix=corr_matrix,
            condition_number=round(condition_number, 2),
            n_redundant=n_redundant,
            recommendation=recommendation,
        )

    def _compute_factor_matrix(self, snapshots: list[SerenitySnapshot]) -> np.ndarray:
        """Extract 8-factor values as (n_samples, 8) matrix."""
        rows = []
        for s in snapshots:
            score = self.scorer.score(s)
            row = [
                score.consensus_scarcity,
                score.liquidity_scarcity,
                score.trust_scarcity,
                score.replication_difficulty,
                score.certification_progress,
                score.prepayment_signal,
                score.decentralization,
                score.evidence_strength,
            ]
            rows.append(row)
        return np.array(rows, dtype=float)

    def _compute_vif(self, factor_matrix: np.ndarray) -> list[float]:
        """VIF for each factor: 1 / (1 - R^2_j)."""
        n_factors = factor_matrix.shape[1]
        vifs: list[float] = []
        for j in range(n_factors):
            y = factor_matrix[:, j]
            X = np.delete(factor_matrix, j, axis=1)
            # Add intercept
            X_aug = np.column_stack([np.ones(len(y)), X])
            try:
                beta = np.linalg.lstsq(X_aug, y, rcond=None)[0]
                y_hat = X_aug @ beta
                ss_res = float(np.sum((y - y_hat) ** 2))
                ss_tot = float(np.sum((y - y.mean()) ** 2))
                r2 = 1.0 - ss_res / ss_tot if ss_tot > 1e-15 else 0.0
                vif = 1.0 / (1.0 - r2) if (1.0 - r2) > 1e-10 else float("inf")
            except Exception:
                vif = float("inf")
            vifs.append(vif)
        return vifs

    def _leave_one_out_ablation(
        self,
        factor_matrix: np.ndarray,
        scores: np.ndarray,
        vifs: list[float],
    ) -> list[FactorAblationResult]:
        """For each factor, measure standalone R^2 and marginal R^2 when removed."""
        n_factors = factor_matrix.shape[1]
        results: list[FactorAblationResult] = []

        # Full model R^2
        X_full = np.column_stack([np.ones(len(scores)), factor_matrix])
        beta_full = np.linalg.lstsq(X_full, scores, rcond=None)[0]
        y_hat_full = X_full @ beta_full
        ss_res_full = float(np.sum((scores - y_hat_full) ** 2))
        ss_tot = float(np.sum((scores - scores.mean()) ** 2))
        r2_full = 1.0 - ss_res_full / ss_tot if ss_tot > 1e-15 else 0.0

        for j in range(n_factors):
            # Standalone R^2
            x_j = np.column_stack([np.ones(len(scores)), factor_matrix[:, j]])
            beta_j = np.linalg.lstsq(x_j, scores, rcond=None)[0]
            y_hat_j = x_j @ beta_j
            ss_res_j = float(np.sum((scores - y_hat_j) ** 2))
            r2_standalone = 1.0 - ss_res_j / ss_tot if ss_tot > 1e-15 else 0.0

            # Marginal R^2: drop in R^2 when this factor is removed
            X_reduced = np.column_stack([
                np.ones(len(scores)),
                np.delete(factor_matrix, j, axis=1),
            ])
            beta_reduced = np.linalg.lstsq(X_reduced, scores, rcond=None)[0]
            y_hat_reduced = X_reduced @ beta_reduced
            ss_res_reduced = float(np.sum((scores - y_hat_reduced) ** 2))
            r2_reduced = 1.0 - ss_res_reduced / ss_tot if ss_tot > 1e-15 else 0.0
            marginal_r2 = max(0.0, r2_full - r2_reduced)

            # Correlation with score (0.0 for constant factors)
            corr = float(np.corrcoef(factor_matrix[:, j], scores)[0, 1])
            if np.isnan(corr):
                corr = 0.0

            is_redundant = vifs[j] > self.vif_threshold or marginal_r2 < self.marginal_r2_threshold

            results.append(FactorAblationResult(
                factor_name=FACTOR_NAMES[j],
                standalone_r2=round(r2_standalone, 4),
                marginal_r2=round(marginal_r2, 4),
                correlation_with_score=round(corr, 4),
                vif=round(vifs[j], 2),
                is_redundant=is_redundant,
            ))

        return results
