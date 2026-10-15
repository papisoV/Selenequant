"""Covariance estimation — Ledoit-Wolf shrinkage and sample covariance."""

from __future__ import annotations

import numpy as np
import pandas as pd


def ledoit_wolf_cov(returns: pd.DataFrame) -> pd.DataFrame:
    """Ledoit-Wolf shrinkage estimator for covariance matrix.

    Shrinks toward scaled identity for better conditioning.
    """
    T, n = returns.shape
    X = returns.values - returns.values.mean(axis=0)
    S = (X.T @ X) / T

    # Shrinkage intensity (Ledoit-Wolf 2004 simplified)
    target = np.trace(S) / n * np.eye(n)

    # Compute optimal shrinkage
    X2 = X ** 2
    phi_mat = (X2.T @ X2) / T - S ** 2
    phi = float(np.sum(phi_mat))

    gamma = float(np.sum((S - target) ** 2))

    kappa = phi / gamma if gamma > 1e-15 else 1.0
    delta = max(0.0, min(1.0, kappa / T))

    shrunk = delta * target + (1 - delta) * S

    # Annualize (assume daily returns)
    shrunk *= 252

    # Ensure PSD
    shrunk = nearest_psd(pd.DataFrame(shrunk, index=returns.columns, columns=returns.columns))
    return shrunk


def sample_cov(returns: pd.DataFrame) -> pd.DataFrame:
    """Sample covariance, annualized."""
    S = returns.cov() * 252
    return nearest_psd(S)


def estimate_cov(
    returns: pd.DataFrame,
    method: str = "ledoit_wolf",
    periods_per_year: int = 252,
) -> pd.DataFrame:
    """Estimate covariance matrix from returns DataFrame.

    Args:
        returns: T x n DataFrame of per-period returns.
        method: ``"ledoit_wolf"`` or ``"sample"``.
        periods_per_year: Annualization factor.
    """
    if method == "ledoit_wolf":
        return ledoit_wolf_cov(returns)
    if method == "sample":
        return sample_cov(returns)
    raise ValueError(f"unknown method: {method}")


def nearest_psd(cov: pd.DataFrame, epsilon: float = 1e-10) -> pd.DataFrame:
    """Project a covariance matrix to the nearest positive semi-definite matrix."""
    A = cov.values
    sym = (A + A.T) / 2
    eigvals, eigvecs = np.linalg.eigh(sym)
    eigvals = np.maximum(eigvals, epsilon)
    psd = eigvecs @ np.diag(eigvals) @ eigvecs.T
    return pd.DataFrame(psd, index=cov.index, columns=cov.columns)
