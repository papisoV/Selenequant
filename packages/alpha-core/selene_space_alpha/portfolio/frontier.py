"""Efficient frontier computation."""

from __future__ import annotations

import numpy as np

from .constraints import Constraints, build_constraints
from .optimizers import mean_variance


def efficient_frontier(
    mu: np.ndarray,
    cov: np.ndarray,
    constraints: Constraints | None = None,
    n_points: int = 25,
    lambda_range: tuple[float, float] = (0.5, 50.0),
) -> list[dict]:
    """Compute efficient frontier by sweeping risk aversion.

    Returns:
        List of dicts with keys: risk_aversion, expected_return, volatility,
        weights, success.
    """
    n = cov.shape[0]
    if constraints is None:
        constraints = build_constraints(n)

    lambdas = np.geomspace(lambda_range[0], lambda_range[1], n_points)
    frontier: list[dict] = []

    for lam in lambdas:
        result = mean_variance(mu, cov, constraints, risk_aversion=lam)
        w = result.weights
        ret = float(w @ mu)
        vol = np.sqrt(float(w @ cov @ w))
        frontier.append({
            "risk_aversion": float(lam),
            "expected_return": round(ret, 6),
            "volatility": round(vol, 6),
            "weights": w,
            "success": result.success,
        })

    return frontier
