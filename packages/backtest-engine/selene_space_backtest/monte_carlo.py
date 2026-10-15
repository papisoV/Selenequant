"""Monte Carlo simulation for backtest robustness.

Supports:
  - Bootstrap returns (trade sequence resampling)
  - Parameter sensitivity analysis
  - Equity curve percentile distribution
"""

from __future__ import annotations

import itertools
from typing import Any, Callable

import numpy as np


def bootstrap_returns(
    returns: list[float],
    n_samples: int = 10_000,
    seed: int | None = None,
) -> list[list[float]]:
    """Bootstrap trade returns by resampling with replacement.

    Args:
        returns: List of trade returns (in basis points or fractional).
        n_samples: Number of bootstrap samples to generate.
        seed: RNG seed for reproducibility.

    Returns:
        List of n_samples lists, each being a resampled return sequence.
    """
    if not returns:
        raise ValueError("Returns list must not be empty.")

    rng = np.random.default_rng(seed)
    n = len(returns)
    arr = np.asarray(returns, dtype=np.float64)

    indices = rng.integers(0, n, size=(n_samples, n))
    return arr[indices].tolist()


def parameter_sensitivity(
    strategy_fn: Callable[..., list[float]],
    param_grid: dict[str, list[Any]],
    n_runs: int = 100,
    seed: int | None = None,
) -> list[dict[str, Any]]:
    """Analyze how strategy performance varies with parameter changes.

    Args:
        strategy_fn: Function that takes keyword args for each param and
            returns a list of returns (in bps). Must accept all keys in
            param_grid as keyword arguments.
        param_grid: Dict of parameter name → list of values to test.
        n_runs: Number of simulation runs per parameter combination.
        seed: RNG seed for reproducibility.

    Returns:
        List of dicts with keys: params, mean_sharpe, std_sharpe,
        pctile_5, pctile_95.
    """
    if not param_grid:
        raise ValueError("param_grid must not be empty.")

    rng = np.random.default_rng(seed)
    keys = list(param_grid.keys())
    values = list(param_grid.values())

    results: list[dict[str, Any]] = []

    for combo in itertools.product(*values):
        params = dict(zip(keys, combo))
        sharpes: list[float] = []

        for _ in range(n_runs):
            returns = strategy_fn(**params)
            if not returns:
                continue
            arr = np.asarray(returns, dtype=np.float64)
            mu = float(np.mean(arr))
            sigma = float(np.std(arr))
            # Annualized Sharpe (approximate, assumes daily)
            if sigma > 0:
                sharpe = (mu / sigma) * np.sqrt(252)
            else:
                sharpe = 0.0
            sharpes.append(sharpe)

        sharpe_arr = np.asarray(sharpes, dtype=np.float64)
        results.append({
            "params": params,
            "mean_sharpe": round(float(np.mean(sharpe_arr)), 6),
            "std_sharpe": round(float(np.std(sharpe_arr)), 6),
            "pctile_5": round(float(np.percentile(sharpe_arr, 5)), 6),
            "pctile_95": round(float(np.percentile(sharpe_arr, 95)), 6),
        })

    return results


def equity_curve_distribution(
    returns: list[float],
    initial_equity: float = 10_000.0,
    n_samples: int = 10_000,
    seed: int | None = None,
) -> dict[str, list[float]]:
    """Generate percentile bands for equity curve via bootstrap.

    Resamples the return sequence (with replacement) to produce a
    distribution of ending equity curves.

    Args:
        returns: List of period returns (fractional, e.g. 0.01 = 1%).
        initial_equity: Starting equity.
        n_samples: Number of bootstrap samples.
        seed: RNG seed for reproducibility.

    Returns:
        Dict with keys pctile_5, pctile_25, pctile_50, pctile_75, pctile_95,
        each being a list of equity values over time.
    """
    if not returns:
        raise ValueError("Returns list must not be empty.")

    rng = np.random.default_rng(seed)
    n = len(returns)
    arr = np.asarray(returns, dtype=np.float64)

    indices = rng.integers(0, n, size=(n_samples, n))
    sampled_returns = arr[indices]  # (n_samples, n)

    # Cumulative product for each sample
    eq_curves = np.zeros((n_samples, n + 1), dtype=np.float64)
    eq_curves[:, 0] = initial_equity

    for i in range(n):
        eq_curves[:, i + 1] = eq_curves[:, i] * (1.0 + sampled_returns[:, i])

    percentiles = [5, 25, 50, 75, 95]
    result: dict[str, list[float]] = {}

    for p in percentiles:
        band = np.percentile(eq_curves, p, axis=0)
        result[f"pctile_{p}"] = [round(float(v), 2) for v in band]

    return result
