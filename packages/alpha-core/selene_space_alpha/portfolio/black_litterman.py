"""Black-Litterman model — posterior expected returns and covariance from equilibrium + views."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class BLResult:
    """Result container for the Black-Litterman posterior.

    Attributes:
        mu_posterior: Posterior expected excess returns (n_assets,).
        cov_posterior: Posterior covariance matrix (n_assets, n_assets).
        mu_equilibrium: Prior equilibrium excess returns (n_assets,).
        views_P: Pick matrix (n_views, n_assets).
        views_Q: View return vector (n_views,).
    """

    mu_posterior: np.ndarray
    cov_posterior: np.ndarray
    mu_equilibrium: np.ndarray
    views_P: np.ndarray
    views_Q: np.ndarray


def he_litterman_omega(tau_cov: np.ndarray, P: np.ndarray) -> np.ndarray:
    """Compute He-Litterman proportional view-uncertainty matrix.

    Each diagonal entry is proportional to the variance of the linear
    combination of assets specified by the corresponding row of *P*.

    Args:
        tau_cov: Scaled prior covariance ``tau * cov`` (n_assets, n_assets).
        P: Pick matrix (n_views, n_assets).

    Returns:
        Diagonal uncertainty matrix (n_views, n_views).
    """
    view_var = np.diag(P @ tau_cov @ P.T)
    return np.diag(view_var)


def views_from_signals(
    signals: dict[str, float],
    assets: list[str],
) -> tuple[np.ndarray, np.ndarray]:
    """Convert a signal dict to Black-Litterman *P* and *Q* matrices.

    Each key-value pair in *signals* is treated as one absolute view on a
    single asset: the asset is expected to deliver the stated excess return.

    Args:
        signals: Mapping ``{symbol: expected_return}``.  Only symbols present
            in *assets* are included; others are silently ignored.
        assets: Ordered list of asset symbols defining the column order.

    Returns:
        ``(P, Q)`` where *P* is (n_views, n_assets) and *Q* is (n_views,).
        If no valid signals are found, returns empty arrays with the correct
        column dimension.
    """
    asset_index = {a: i for i, a in enumerate(assets)}
    n_assets = len(assets)

    rows: list[np.ndarray] = []
    q_vals: list[float] = []

    for symbol, expected_return in signals.items():
        if symbol in asset_index:
            row = np.zeros(n_assets)
            row[asset_index[symbol]] = 1.0
            rows.append(row)
            q_vals.append(expected_return)

    if not rows:
        return np.empty((0, n_assets)), np.empty(0)

    P = np.vstack(rows)
    Q = np.array(q_vals)
    return P, Q


def bl_posterior_cov(
    cov: np.ndarray,
    tau: float,
    P: np.ndarray,
    omega: np.ndarray,
) -> np.ndarray:
    """Compute the Black-Litterman posterior covariance matrix.

    ``Sigma_BL = Sigma + [(tau*Sigma)^-1 + P' Omega^-1 P]^-1``

    Args:
        cov: Prior covariance matrix (n_assets, n_assets).
        tau: Scalar uncertainty in the prior.
        P: Pick matrix (n_views, n_assets).
        omega: View uncertainty matrix (n_views, n_views).

    Returns:
        Posterior covariance matrix (n_assets, n_assets).
    """
    tau_cov = tau * cov
    tau_cov_inv = np.linalg.inv(tau_cov)
    omega_inv = np.linalg.inv(omega)
    M = tau_cov_inv + P.T @ omega_inv @ P
    return cov + np.linalg.inv(M)


def black_litterman(
    mu_eq: np.ndarray,
    cov: np.ndarray,
    P: np.ndarray,
    Q: np.ndarray,
    omega: np.ndarray | None = None,
    tau: float = 0.05,
) -> BLResult:
    """Black-Litterman posterior expected returns and covariance.

    Combines the equilibrium (prior) with investor views to produce a
    posterior distribution over asset returns.

    Posterior mean::

        mu_BL = [(tau*Sigma)^-1 + P' Omega^-1 P]^-1
                @ [(tau*Sigma)^-1 mu_eq + P' Omega^-1 Q]

    Posterior covariance::

        Sigma_BL = Sigma + [(tau*Sigma)^-1 + P' Omega^-1 P]^-1

    Args:
        mu_eq: Equilibrium excess returns (n_assets,).
        cov: Covariance matrix (n_assets, n_assets).
        P: Pick matrix (n_views, n_assets) — which assets each view applies to.
        Q: View returns (n_views,) — expected return for each view.
        omega: View uncertainty (n_views, n_views).  Defaults to the
            He-Litterman proportional specification ``diag(P @ (tau*Sigma) @ P')``.
        tau: Scalar uncertainty in the prior (default 0.05).

    Returns:
        :class:`BLResult` with posterior mean, posterior covariance, and the
        original inputs for traceability.
    """
    n_assets = cov.shape[0]
    tau_cov = tau * cov
    tau_cov_inv = np.linalg.inv(tau_cov)

    # No views — posterior equals prior with uncertainty scaling
    if P.shape[0] == 0:
        return BLResult(
            mu_posterior=mu_eq.copy(),
            cov_posterior=(1.0 + tau) * cov,
            mu_equilibrium=mu_eq.copy(),
            views_P=P,
            views_Q=Q,
        )

    # Default omega: He-Litterman proportional
    if omega is None:
        omega = he_litterman_omega(tau_cov, P)

    omega_inv = np.linalg.inv(omega)

    # Posterior precision
    M = tau_cov_inv + P.T @ omega_inv @ P
    M_inv = np.linalg.inv(M)

    # Posterior mean
    mu_bl = M_inv @ (tau_cov_inv @ mu_eq + P.T @ omega_inv @ Q)

    # Posterior covariance
    cov_bl = cov + M_inv

    return BLResult(
        mu_posterior=mu_bl,
        cov_posterior=cov_bl,
        mu_equilibrium=mu_eq.copy(),
        views_P=P,
        views_Q=Q,
    )
