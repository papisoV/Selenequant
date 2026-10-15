"""Factor model for risk attribution — exposure estimation, PnL decomposition, risk decomposition.

Provides time-series regression-based factor exposure estimation, portfolio PnL
attribution into factor and specific components, and variance decomposition into
factor risk and idiosyncratic risk.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FactorExposure:
    """Result of time-series factor exposure regression.

    Attributes:
        betas: Factor loadings, shape (n_assets, n_factors).
            Row *i* holds the OLS betas for asset *i*.
        residuals: Regression residuals, shape (n_periods, n_assets).
            Column *j* is the residual series for asset *j*.
        r_squared: Coefficient of determination per asset, shape (n_assets,).
    """

    betas: np.ndarray
    residuals: np.ndarray
    r_squared: np.ndarray


@dataclass(frozen=True)
class FactorAttribution:
    """Decomposition of portfolio returns into factor and specific contributions.

    Attributes:
        factor_returns: Realised factor returns per period, shape (n_periods, n_factors).
        specific_returns: Specific (idiosyncratic) returns per period per asset,
            shape (n_periods, n_assets).
        total_factor_contrib: Cumulative factor contribution per asset,
            shape (n_assets,).
        total_specific_contrib: Cumulative specific contribution per asset,
            shape (n_assets,).
    """

    factor_returns: np.ndarray
    specific_returns: np.ndarray
    total_factor_contrib: np.ndarray
    total_specific_contrib: np.ndarray


@dataclass(frozen=True)
class RiskDecomposition:
    """Variance decomposition into factor and specific risk.

    Attributes:
        total_risk: Total volatility (std dev) per asset, shape (n_assets,).
        factor_risk: Volatility attributable to factor exposure, shape (n_assets,).
        specific_risk: Idiosyncratic volatility, shape (n_assets,).
        factor_contrib_pct: Percentage of total variance from factors,
            shape (n_assets,).  Values in [0, 1].
    """

    total_risk: np.ndarray
    factor_risk: np.ndarray
    specific_risk: np.ndarray
    factor_contrib_pct: np.ndarray


# ---------------------------------------------------------------------------
# Predefined crypto factor definitions
# ---------------------------------------------------------------------------

CRYPTO_FACTORS: dict[str, dict[str, Any]] = {
    "market": {
        "description": "Market beta — BTC simple returns",
        "construction": "btc_returns",
    },
    "momentum": {
        "description": "20-day momentum — (price / price_20d_ago) - 1",
        "construction": "momentum_20d",
    },
    "volatility": {
        "description": "20-day realised volatility",
        "construction": "realised_vol_20d",
    },
    "size": {
        "description": "Size proxy — log(volume) or log(market_cap)",
        "construction": "log_volume",
    },
}


# ---------------------------------------------------------------------------
# Core functions
# ---------------------------------------------------------------------------


def build_factor_exposures(
    returns: pd.DataFrame,
    factor_returns: pd.DataFrame,
) -> FactorExposure:
    """Estimate factor exposures via time-series OLS regression.

    For each asset *i* the model is::

        r_{i,t} = alpha_i + beta_{i,1} * f_{1,t} + ... + beta_{i,K} * f_{K,t} + eps_{i,t}

    Parameters
    ----------
    returns : pd.DataFrame
        Asset returns, shape (n_periods, n_assets).  Index is time.
    factor_returns : pd.DataFrame
        Factor returns, shape (n_periods, n_factors).  Index must align with
        *returns*.

    Returns
    -------
    FactorExposure
        betas (n_assets, n_factors), residuals (n_periods, n_assets),
        r_squared (n_assets,).

    Notes
    -----
    Uses numpy OLS (``np.linalg.lstsq``) with an intercept column prepended.
    The intercept is **not** returned in *betas* — only the K factor loadings.
    """
    # Align indices
    common_idx = returns.index.intersection(factor_returns.index)
    if len(common_idx) == 0:
        raise ValueError("returns and factor_returns have no overlapping index")
    r = returns.loc[common_idx].values  # (T, N)
    f = factor_returns.loc[common_idx].values  # (T, K)

    T, N = r.shape
    K = f.shape[1]

    # Design matrix with intercept
    X = np.column_stack([np.ones(T), f])  # (T, K+1)

    # Solve OLS for all assets simultaneously: X @ [alpha, betas].T = r
    # lstsq returns (K+1, N) — first row is alpha, rest are betas
    coeffs, _, _, _ = np.linalg.lstsq(X, r, rcond=None)
    betas = coeffs[1:].T  # (N, K)
    alpha = coeffs[0]  # (N,)

    # Residuals
    fitted = X @ coeffs  # (T, N)
    residuals = r - fitted  # (T, N)

    # R-squared per asset
    ss_res = np.sum(residuals ** 2, axis=0)  # (N,)
    ss_tot = np.sum((r - r.mean(axis=0)) ** 2, axis=0)  # (N,)
    r_squared = np.where(ss_tot > 0, 1.0 - ss_res / ss_tot, 0.0)  # (N,)

    return FactorExposure(betas=betas, residuals=residuals, r_squared=r_squared)


def factor_attribution(
    portfolio_weights: np.ndarray,
    returns: pd.DataFrame,
    factor_returns: pd.DataFrame,
    exposures: FactorExposure | None = None,
) -> FactorAttribution:
    """Decompose portfolio PnL into factor and specific contributions.

    Parameters
    ----------
    portfolio_weights : np.ndarray
        Portfolio weights, shape (n_assets,).  Must sum to ~1.
    returns : pd.DataFrame
        Asset returns, shape (n_periods, n_assets).
    factor_returns : pd.DataFrame
        Factor returns, shape (n_periods, n_factors).
    exposures : FactorExposure | None
        Pre-computed exposures.  If *None*, they are estimated from the same
        data via :func:`build_factor_exposures`.

    Returns
    -------
    FactorAttribution
        factor_returns, specific_returns, total_factor_contrib,
        total_specific_contrib.

    Notes
    -----
    For each period *t*::

        portfolio_return_t = w @ r_t
        factor_contrib_t   = w @ (betas @ f_t)   (per-asset then weighted)
        specific_t         = r_t - betas @ f_t    (per-asset residual + alpha)

    Cumulative contributions are the sum across periods, weighted by *w*.
    """
    if exposures is None:
        exposures = build_factor_exposures(returns, factor_returns)

    # Align indices
    common_idx = returns.index.intersection(factor_returns.index)
    if len(common_idx) == 0:
        raise ValueError("returns and factor_returns have no overlapping index")
    r = returns.loc[common_idx].values  # (T, N)
    f = factor_returns.loc[common_idx].values  # (T, K)

    betas = exposures.betas  # (N, K)

    # Factor-fitted returns per asset per period: (T, N) = (T, K) @ (K, N)
    factor_fitted = f @ betas.T  # (T, N)

    # Specific returns = total - factor-fitted (includes alpha)
    specific_returns = r - factor_fitted  # (T, N)

    # Weighted cumulative contributions
    w = portfolio_weights  # (N,)
    total_factor_contrib = np.sum(factor_fitted * w, axis=0)  # (N,)
    total_specific_contrib = np.sum(specific_returns * w, axis=0)  # (N,)

    return FactorAttribution(
        factor_returns=f,
        specific_returns=specific_returns,
        total_factor_contrib=total_factor_contrib,
        total_specific_contrib=total_specific_contrib,
    )


def decompose_risk(
    cov_factor: np.ndarray,
    exposures: FactorExposure,
    idio_var: np.ndarray,
) -> RiskDecomposition:
    """Decompose asset risk into factor and specific components.

    Parameters
    ----------
    cov_factor : np.ndarray
        Factor covariance matrix, shape (n_factors, n_factors).  Must be PSD.
    exposures : FactorExposure
        Factor exposures (betas) from :func:`build_factor_exposures`.
    idio_var : np.ndarray
        Idiosyncratic variance per asset, shape (n_assets,).  Typically the
        variance of the residual series.

    Returns
    -------
    RiskDecomposition
        total_risk, factor_risk, specific_risk, factor_contrib_pct.

    Notes
    -----
    For asset *i*::

        factor_var_i   = beta_i @ Sigma_f @ beta_i^T
        specific_var_i = idio_var_i
        total_var_i    = factor_var_i + specific_var_i
        factor_risk_i  = sqrt(factor_var_i)
        specific_risk_i = sqrt(specific_var_i)
        total_risk_i   = sqrt(total_var_i)
        factor_contrib_pct_i = factor_var_i / total_var_i
    """
    betas = exposures.betas  # (N, K)
    N = betas.shape[0]

    # Factor variance per asset: diag(betas @ cov_factor @ betas.T)
    factor_var = np.array([
        betas[i] @ cov_factor @ betas[i] for i in range(N)
    ])  # (N,)

    specific_var = np.asarray(idio_var, dtype=float)  # (N,)
    total_var = factor_var + specific_var  # (N,)

    # Guard against zero total variance
    total_risk = np.sqrt(np.maximum(total_var, 0.0))
    factor_risk = np.sqrt(np.maximum(factor_var, 0.0))
    specific_risk = np.sqrt(np.maximum(specific_var, 0.0))

    factor_contrib_pct = np.where(
        total_var > 0,
        factor_var / total_var,
        0.0,
    )

    return RiskDecomposition(
        total_risk=total_risk,
        factor_risk=factor_risk,
        specific_risk=specific_risk,
        factor_contrib_pct=factor_contrib_pct,
    )


# ---------------------------------------------------------------------------
# Crypto factor construction
# ---------------------------------------------------------------------------


def build_crypto_factor_returns(
    price_df: pd.DataFrame,
    volume_df: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """Build a DataFrame of crypto factor returns from price data.

    Uses the definitions in :data:`CRYPTO_FACTORS`:

    - **market**: BTC simple returns (first column is treated as BTC).
    - **momentum**: 20-day momentum = (price / price_20d_ago) - 1, cross-section
      mean of all assets.
    - **volatility**: 20-day realised volatility, cross-section mean.
    - **size**: log(volume) cross-section mean (requires *volume_df*; otherwise
      filled with 0).

    Parameters
    ----------
    price_df : pd.DataFrame
        Price data, shape (n_periods, n_assets).  The first column is assumed
        to be BTC for the market factor.
    volume_df : pd.DataFrame | None
        Volume data aligned with *price_df*.  Required for the size factor.

    Returns
    -------
    pd.DataFrame
        Factor returns, shape (n_periods, n_factors).  Columns are the keys of
        :data:`CRYPTO_FACTORS`.

    Notes
    -----
    Early rows that contain NaN (due to rolling windows) are **dropped** so
    that downstream regression receives clean data.
    """
    result = pd.DataFrame(index=price_df.index)

    # Market factor: BTC returns (first column)
    btc_col = price_df.columns[0]
    result["market"] = price_df[btc_col].pct_change()

    # Momentum factor: cross-section average of 20d momentum
    mom = (price_df / price_df.shift(20)) - 1.0
    result["momentum"] = mom.mean(axis=1)

    # Volatility factor: cross-section average of 20d realised vol
    daily_ret = price_df.pct_change()
    realised_vol = daily_ret.rolling(20).std() * np.sqrt(252)
    result["volatility"] = realised_vol.mean(axis=1)

    # Size factor: cross-section average of log(volume)
    if volume_df is not None:
        # Align with price_df
        common_idx = price_df.index.intersection(volume_df.index)
        common_cols = price_df.columns.intersection(volume_df.columns)
        vol_aligned = volume_df.loc[common_idx, common_cols]
        log_vol = np.log(vol_aligned.replace(0, np.nan).clip(lower=1.0))
        size_series = log_vol.mean(axis=1)
        # Reindex to price_df index, filling missing with NaN
        result["size"] = size_series.reindex(price_df.index)
    else:
        # No volume data — use log(price) as a crude size proxy
        result["size"] = np.log(price_df.replace(0, np.nan).clip(lower=1.0)).mean(axis=1)

    # Drop rows with NaN (from rolling windows / pct_change)
    result = result.dropna()

    return result
