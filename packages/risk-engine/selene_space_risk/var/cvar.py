"""CVaR — conditional value-at-risk (expected shortfall)."""

from __future__ import annotations

import numpy as np


def compute_cvar(
    returns: list[float] | np.ndarray,
    confidence: float = 0.95,
) -> float:
    """Compute CVaR (expected shortfall) — mean of returns below VaR.

    Args:
        returns: Array of historical returns.
        confidence: Confidence level.

    Returns:
        The CVaR value (mean of worst (1-confidence)% returns).
    """
    if len(returns) == 0:
        raise ValueError("Returns list must not be empty.")

    arr = np.asarray(returns, dtype=np.float64)
    threshold = np.percentile(arr, (1.0 - confidence) * 100.0)
    tail = arr[arr <= threshold]

    if len(tail) == 0:
        return float(threshold)

    return float(np.mean(tail))
