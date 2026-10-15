"""Portfolio optimizers — min_variance, mean_variance, max_sharpe, risk_parity, max_diversification."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

from .constraints import Constraints


@dataclass(frozen=True)
class OptResult:
    weights: np.ndarray
    objective: str
    success: bool
    message: str


def risk_contributions(weights: np.ndarray, cov: np.ndarray) -> np.ndarray:
    """Risk contribution of each asset: w_i * (Sigma @ w)_i / (w' Sigma w)."""
    sigma_w = cov @ weights
    port_var = weights @ sigma_w
    if abs(port_var) < 1e-15:
        return np.zeros_like(weights)
    return weights * sigma_w / port_var


def _run_minimize(
    fun: object,
    x0: np.ndarray,
    constraints_obj: Constraints,
) -> OptResult:
    res = minimize(
        fun, x0,
        method="SLSQP",
        bounds=constraints_obj.bounds,
        constraints=list(constraints_obj.scipy_constraints),
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    return OptResult(
        weights=res.x,
        objective="",
        success=res.success,
        message=res.message,
    )


def min_variance(cov: np.ndarray, constraints: Constraints) -> OptResult:
    """Minimize portfolio variance: w' Sigma w."""
    def obj(w):
        return float(w @ cov @ w)

    x0 = np.ones(constraints.n) / constraints.n
    result = _run_minimize(obj, x0, constraints)
    return OptResult(
        weights=result.weights,
        objective="min_variance",
        success=result.success,
        message=result.message,
    )


def mean_variance(
    mu: np.ndarray,
    cov: np.ndarray,
    constraints: Constraints,
    risk_aversion: float = 5.0,
) -> OptResult:
    """Maximize: w'mu - (lambda/2) w' Sigma w."""
    lam = risk_aversion

    def obj(w):
        return -(float(w @ mu) - lam / 2.0 * float(w @ cov @ w))

    x0 = np.ones(constraints.n) / constraints.n
    result = _run_minimize(obj, x0, constraints)
    return OptResult(
        weights=result.weights,
        objective="mean_variance",
        success=result.success,
        message=result.message,
    )


def max_sharpe(
    mu: np.ndarray,
    cov: np.ndarray,
    constraints: Constraints,
) -> OptResult:
    """Maximize Sharpe ratio: w'mu / sqrt(w' Sigma w)."""
    def obj(w):
        ret = float(w @ mu)
        vol = np.sqrt(float(w @ cov @ w) + 1e-15)
        return -ret / vol

    x0 = np.ones(constraints.n) / constraints.n
    result = _run_minimize(obj, x0, constraints)
    return OptResult(
        weights=result.weights,
        objective="max_sharpe",
        success=result.success,
        message=result.message,
    )


def risk_parity(cov: np.ndarray, constraints: Constraints) -> OptResult:
    """Equal Risk Contribution (ERC) portfolio."""
    def obj(w):
        rc = risk_contributions(w, cov)
        target = np.ones_like(rc) / len(rc)
        return float(np.sum((rc - target) ** 2))

    x0 = np.ones(constraints.n) / constraints.n
    result = _run_minimize(obj, x0, constraints)
    return OptResult(
        weights=result.weights,
        objective="risk_parity",
        success=result.success,
        message=result.message,
    )


def max_diversification(cov: np.ndarray, constraints: Constraints) -> OptResult:
    """Maximize diversification ratio: w' sigma / sqrt(w' Sigma w)."""
    sigma = np.sqrt(np.diag(cov))

    def obj(w):
        port_vol = np.sqrt(float(w @ cov @ w) + 1e-15)
        weighted_vol = float(w @ sigma)
        return -weighted_vol / port_vol

    x0 = np.ones(constraints.n) / constraints.n
    result = _run_minimize(obj, x0, constraints)
    return OptResult(
        weights=result.weights,
        objective="max_diversification",
        success=result.success,
        message=result.message,
    )


def optimize(
    objective: str,
    cov: np.ndarray,
    mu: np.ndarray | None = None,
    constraints: Constraints | None = None,
    **kwargs,
) -> OptResult:
    """Unified optimization entry point.

    Args:
        objective: One of ``min_variance``, ``mean_variance``, ``max_sharpe``,
            ``risk_parity``, ``max_diversification``.
        cov: Annualized covariance matrix (n x n).
        mu: Expected return vector (n,). Required for mean_variance and max_sharpe.
        constraints: Constraints object from build_constraints().
    """
    if constraints is None:
        n = cov.shape[0]
        from .constraints import build_constraints
        constraints = build_constraints(n)

    if objective == "min_variance":
        return min_variance(cov, constraints)
    if objective == "mean_variance":
        if mu is None:
            raise ValueError("mu required for mean_variance")
        return mean_variance(mu, cov, constraints, **kwargs)
    if objective == "max_sharpe":
        if mu is None:
            raise ValueError("mu required for max_sharpe")
        return max_sharpe(mu, cov, constraints)
    if objective == "risk_parity":
        return risk_parity(cov, constraints)
    if objective == "max_diversification":
        return max_diversification(cov, constraints)
    raise ValueError(f"unknown objective: {objective}")
