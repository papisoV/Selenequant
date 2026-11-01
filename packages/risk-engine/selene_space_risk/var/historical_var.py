"""Historical VaR — percentile-based value-at-risk."""

from __future__ import annotations

import numpy as np


def compute_historical_var(
    returns: list[float] | np.ndarray,
    confidence: float = 0.95,
) -> float:
    """Compute VaR using historical simulation (percentile method).

    Args:
        returns: Array of historical returns.
        confidence: Confidence level (0.95 = 95% VaR).

    Returns:
        The VaR value (negative number representing loss).
    """
    if len(returns) == 0:
        raise ValueError("Returns list must not be empty.")

    arr = np.asarray(returns, dtype=np.float64)
    return float(np.percentile(arr, (1.0 - confidence) * 100.0))
