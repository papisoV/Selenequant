"""Parametric VaR — variance-covariance method."""

from __future__ import annotations

import numpy as np
from scipy.stats import norm


def compute_parametric_var(
    returns: list[float] | np.ndarray,
    confidence: float = 0.95,
) -> float:
    """Compute VaR using parametric (variance-covariance) method.

    Assumes normally distributed returns.

    Args:
        returns: Array of historical returns.
        confidence: Confidence level.

    Returns:
        The VaR value (negative for loss).
    """
    if len(returns) == 0:
        raise ValueError("Returns list must not be empty.")

    arr = np.asarray(returns, dtype=np.float64)
    mu = float(np.mean(arr))
    sigma = float(np.std(arr, ddof=1))

    z_score = norm.ppf(1.0 - confidence)
    return mu + z_score * sigma
