"""Monte Carlo VaR — simulated return distribution."""

from __future__ import annotations

import numpy as np


def compute_monte_carlo_var(
    returns: list[float] | np.ndarray,
    confidence: float = 0.95,
    n_simulations: int = 10_000,
    seed: int | None = None,
) -> float:
    """Compute VaR via Monte Carlo simulation.

    Fits mean/variance to historical returns, then simulates
    from that distribution.

    Args:
        returns: Historical returns for mean/variance estimation.
        confidence: Confidence level.
        n_simulations: Number of Monte Carlo draws.
        seed: RNG seed.

    Returns:
        The VaR value (negative for loss).
    """
    if len(returns) == 0:
        raise ValueError("Returns list must not be empty.")

    arr = np.asarray(returns, dtype=np.float64)
    mu = float(np.mean(arr))
    sigma = float(np.std(arr, ddof=1))

    rng = np.random.default_rng(seed)
    simulated = rng.normal(mu, sigma, size=n_simulations)

    return float(np.percentile(simulated, (1.0 - confidence) * 100.0))
