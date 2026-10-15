"""Spread computation and analysis — OLS/Kalman hedge ratios, Z-score, half-life."""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_spread(
    price_a: pd.Series,
    price_b: pd.Series,
    hedge_ratio: float,
) -> pd.Series:
    """Compute the spread between two price series.

    spread = price_a - hedge_ratio * price_b

    The two input series are aligned on their common index before
    computation.  Any index present in only one series is dropped.

    Args:
        price_a: First (dependent) price series.
        price_b: Second (independent) price series.
        hedge_ratio: Hedge ratio from OLS or Kalman filter.

    Returns:
        Spread series aligned on the common index of the inputs.
    """
    common_idx = price_a.index.intersection(price_b.index)
    spread = price_a.loc[common_idx] - hedge_ratio * price_b.loc[common_idx]
    spread.name = None  # clear name to avoid confusion
    return spread


def compute_hedge_ratio_ols(
    price_a: pd.Series,
    price_b: pd.Series,
) -> float:
    """Compute the OLS hedge ratio: price_a = alpha + beta * price_b.

    Uses numpy least-squares to find beta (the hedge ratio).

    Args:
        price_a: Dependent price series.
        price_b: Independent price series.

    Returns:
        Hedge ratio (beta coefficient) as a float.
    """
    common_idx = price_a.index.intersection(price_b.index)
    y = price_a.loc[common_idx].values.astype(float)
    x = price_b.loc[common_idx].values.astype(float)

    # OLS with intercept: y = alpha + beta * x
    ones = np.ones(len(x))
    X = np.column_stack([ones, x])
    beta_vec = np.linalg.lstsq(X, y, rcond=None)[0]
    return float(beta_vec[1])


def compute_hedge_ratio_kalman(
    price_a: pd.Series,
    price_b: pd.Series,
    delta: float = 1e-4,
    obs_var: float = 1e-3,
) -> pd.Series:
    """Kalman filter for dynamic hedge ratio estimation.

    State model::

        [alpha_t]   = [alpha_{t-1}]   + w_t
        [beta_t ]     [beta_{t-1} ]
        w_t ~ N(0, Q),  Q = delta * I

    Observation model::

        y_t = [1, x_t] @ [alpha_t, beta_t]^T + v_t
        v_t ~ N(0, R),  R = obs_var

    Args:
        price_a: Dependent price series.
        price_b: Independent price series.
        delta: State transition noise scaling (higher => more adaptive).
        obs_var: Observation noise variance.

    Returns:
        Series of rolling hedge ratios (beta_t) indexed by the common
        index of the input series.
    """
    common_idx = price_a.index.intersection(price_b.index)
    y = price_a.loc[common_idx].values.astype(float)
    x = price_b.loc[common_idx].values.astype(float)
    n = len(y)

    if n < 2:
        return pd.Series(dtype=float)

    Q = delta * np.eye(2)
    R = obs_var

    # Initialise state: [alpha, beta]
    # Use simple OLS for initial estimates
    ones = np.ones(n)
    X_ols = np.column_stack([ones, x])
    beta_init = np.linalg.lstsq(X_ols, y, rcond=None)[0]

    state = beta_init.copy()  # [alpha, beta]
    P = np.eye(2)  # state covariance

    hedge_ratios = np.empty(n)

    for t in range(n):
        # --- Predict ---
        # state_pred = state  (random walk)
        P_pred = P + Q

        # --- Update ---
        H = np.array([1.0, x[t]])  # observation row
        y_hat = H @ state
        innovation = y[t] - y_hat
        S = float(H @ P_pred @ H.T) + R  # innovation variance (scalar)

        if abs(S) < 1e-15:
            # Near-singular: skip update
            hedge_ratios[t] = state[1]
            continue

        K = P_pred @ H / S  # Kalman gain (2,)

        state = state + K * innovation
        P = P_pred - np.outer(K, H) @ P_pred

        # Ensure P stays symmetric
        P = (P + P.T) / 2.0

        hedge_ratios[t] = state[1]

    return pd.Series(hedge_ratios, index=common_idx, name="hedge_ratio")


def z_score_spread(
    spread: pd.Series,
    window: int = 20,
) -> pd.Series:
    """Compute the rolling Z-score of a spread series.

    z_t = (spread_t - mu_{t}) / sigma_{t}

    where mu and sigma are the rolling mean and standard deviation over
    the specified window.

    Args:
        spread: Spread series (output of ``compute_spread``).
        window: Rolling window length for mean and std.

    Returns:
        Rolling Z-score series.  The first ``window - 1`` values are NaN.
    """
    rolling_mean = spread.rolling(window=window, min_periods=window).mean()
    rolling_std = spread.rolling(window=window, min_periods=window).std(ddof=1)

    zscore = (spread - rolling_mean) / rolling_std
    zscore.name = None
    return zscore


def half_life(spread: pd.Series) -> float:
    """Estimate the Ornstein-Uhlenbeck half-life of a mean-reverting spread.

    Fits an AR(1) model::

        spread_t - spread_{t-1} = lambda * spread_{t-1} + epsilon

    The half-life is then::

        h = -ln(2) / lambda

    A negative half-life indicates the series is not mean-reverting.

    Args:
        spread: Spread series (output of ``compute_spread``).

    Returns:
        Estimated half-life in units of the spread's time index.
        Returns ``float('nan')`` if lambda is non-negative (no
        mean reversion) or if the series has fewer than 10 observations.
    """
    s = spread.dropna().values.astype(float)
    n = len(s)

    if n < 10:
        return float("nan")

    # AR(1): ds = lambda * s_lag + eps
    ds = np.diff(s)
    s_lag = s[:-1]

    # OLS: ds = lambda * s_lag  (no intercept for OU half-life)
    # Using least squares: lambda = (s_lag' * ds) / (s_lag' * s_lag)
    denominator = s_lag @ s_lag
    if abs(denominator) < 1e-15:
        return float("nan")

    lam = float(s_lag @ ds / denominator)

    if lam >= 0:
        # Not mean-reverting
        return float("nan")

    hl = -np.log(2.0) / lam
    return float(hl)
