"""Cointegration testing — ADF test, Engle-Granger, and pair scanning."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats


@dataclass(frozen=True)
class CointResult:
    """Result of a stationarity / cointegration test.

    Attributes:
        is_stationary: Whether the series (or residual) is stationary at the
            given significance level.
        test_statistic: The ADF test statistic (more negative => more
            evidence against a unit root).
        p_value: Approximate p-value for the test.
        critical_values: Mapping of significance level label to critical
            value (e.g. {"1%": -3.43, "5%": -2.86, "10%": -2.57}).
    """

    is_stationary: bool
    test_statistic: float
    p_value: float
    critical_values: dict[str, float]


@dataclass(frozen=True)
class PairResult:
    """Result of cointegration testing for a single pair.

    Attributes:
        symbol_a: First symbol name.
        symbol_b: Second symbol name.
        p_value: P-value from the Engle-Granger cointegration test.
        hedge_ratio: OLS hedge ratio (price_a ~ hedge_ratio * price_b).
        is_cointegrated: Whether the pair is cointegrated at the given
            significance level.
    """

    symbol_a: str
    symbol_b: str
    p_value: float
    hedge_ratio: float
    is_cointegrated: bool


# ---------------------------------------------------------------------------
# ADF test (manual implementation via OLS regression)
# ---------------------------------------------------------------------------

def _ols_coefficients(y: np.ndarray, x: np.ndarray) -> tuple[float, float]:
    """Simple OLS: y = alpha + beta * x.  Returns (alpha, beta)."""
    x = x.reshape(-1, 1)
    ones = np.ones_like(x)
    X = np.hstack([ones, x])
    # (X'X)^-1 X'y
    beta_vec = np.linalg.lstsq(X, y, rcond=None)[0]
    return float(beta_vec[0]), float(beta_vec[1])


def adf_test(
    series: pd.Series,
    significance: float = 0.05,
) -> CointResult:
    """Augmented Dickey-Fuller test for stationarity.

    Implements the ADF test via OLS regression of the form::

        delta_y_t = alpha + gamma * y_{t-1} + sum(beta_i * delta_y_{t-i}) + eps

    The null hypothesis is that the series has a unit root (non-stationary).
    We reject (conclude stationary) when the test statistic is sufficiently
    negative.

    Uses lag order = int(12 * (len(series) / 100) ** (1/4)) as a simple
    rule-of-thumb, clamped to at least 0 and at most len(series)//3.

    Args:
        series: Time series to test.
        significance: Significance level for the test (default 0.05).

    Returns:
        CointResult with stationarity decision, test statistic, approximate
        p-value, and critical values.
    """
    y = series.dropna().values.astype(float)
    n = len(y)

    if n < 10:
        return CointResult(
            is_stationary=False,
            test_statistic=0.0,
            p_value=1.0,
            critical_values={"1%": -3.43, "5%": -2.86, "10%": -2.57},
        )

    # Lag order selection (Schwert rule-of-thumb)
    max_lag = max(0, min(int(12 * (n / 100) ** 0.25), n // 3))

    # Differenced series
    dy = np.diff(y)

    # Build regression: delta_y_t = alpha + gamma * y_{t-1} + sum lags
    # We lose (max_lag + 1) observations at the start
    if max_lag > 0:
        # Lagged differences
        lag_cols = []
        for lag in range(1, max_lag + 1):
            lag_cols.append(dy[max_lag - lag : n - 1 - lag])
        X_lag = np.column_stack(lag_cols) if lag_cols else np.empty((0, 0))
        y_dep = dy[max_lag:]  # delta y from index max_lag onward
        y_lag1 = y[max_lag : n - 1]  # y_{t-1}
        ones = np.ones(len(y_dep))
        X = np.column_stack([ones, y_lag1] + ([X_lag] if X_lag.size > 0 else []))
    else:
        y_dep = dy
        y_lag1 = y[:-1]
        ones = np.ones(len(y_dep))
        X = np.column_stack([ones, y_lag1])

    # OLS
    beta_vec = np.linalg.lstsq(X, y_dep, rcond=None)[0]
    residuals = y_dep - X @ beta_vec
    k = X.shape[1]

    # Standard error of gamma (index 1 in beta_vec)
    sigma2 = np.sum(residuals**2) / (len(y_dep) - k)
    try:
        xtx_inv = np.linalg.inv(X.T @ X)
        se_gamma = np.sqrt(sigma2 * xtx_inv[1, 1])
    except np.linalg.LinAlgError:
        return CointResult(
            is_stationary=False,
            test_statistic=0.0,
            p_value=1.0,
            critical_values={"1%": -3.43, "5%": -2.86, "10%": -2.57},
        )

    gamma = beta_vec[1]
    t_stat = gamma / se_gamma if se_gamma != 0 else 0.0

    # Approximate critical values for the ADF test (with constant, no trend)
    # These are the standard MacKinnon (1991) approximate critical values
    # for the model with drift (intercept only).
    critical = _adf_critical_values(n)

    # Approximate p-value using the Dickey-Fuller distribution.
    # The DF distribution is not standard normal; we use a lookup-style
    # approximation based on the critical values.
    p_value = _adf_approx_pvalue(t_stat, n)

    is_stationary = p_value < significance

    return CointResult(
        is_stationary=is_stationary,
        test_statistic=float(t_stat),
        p_value=float(p_value),
        critical_values=critical,
    )


def _adf_critical_values(n: int) -> dict[str, float]:
    """Return approximate ADF critical values (MacKinnon 1991) for model
    with intercept, no trend.

    Uses finite-sample correction based on sample size.

    Args:
        n: Sample size.

    Returns:
        Dictionary with keys "1%", "5%", "10%" mapping to critical values.
    """
    # MacKinnon (1991) asymptotic values for model with constant
    # These are adjusted by a finite-sample correction term.
    c = np.array([-2.5658, -1.9410, -1.6165])  # 1%, 5%, 10%
    # Finite-sample adjustment coefficients (MacKinnon Table 1, case 2)
    b1 = np.array([-2.78, -1.68, -1.23])
    b2 = np.array([-3.67, -2.30, -1.47])

    adj = b1 / n + b2 / (n * n)
    crit = c + adj

    return {
        "1%": float(crit[0]),
        "5%": float(crit[1]),
        "10%": float(crit[2]),
    }


def _adf_approx_pvalue(t_stat: float, n: int) -> float:
    """Approximate p-value for the ADF test statistic.

    Uses the MacKinnon (1996) response surface approximation.  We use a
    simplified version: the DF distribution is approximately normal but
    shifted.  For a rough approximation we map through the CDF of a
    shifted-and-scaled normal.

    For the model with intercept (case 2), the mean is approximately -1.5
    and standard deviation approximately 0.75 for large samples.

    This is an approximation; for production use, prefer statsmodels.

    Args:
        t_stat: ADF test statistic.
        n: Sample size.

    Returns:
        Approximate p-value (one-sided, left-tail).
    """
    # Shifted normal approximation for the DF distribution (case 2)
    # Mean ~ -1.5, Std ~ 0.75 for large n
    mu = -1.5 + (-0.5 / n)  # slight finite-sample shift
    sigma = 0.75 + (0.3 / np.sqrt(n))
    p_val = float(scipy_stats.norm.cdf(t_stat, loc=mu, scale=sigma))
    return min(max(p_val, 0.0), 1.0)


# ---------------------------------------------------------------------------
# Engle-Granger two-step cointegration test
# ---------------------------------------------------------------------------

def engle_granger(
    y: pd.Series,
    x: pd.Series,
    significance: float = 0.05,
) -> CointResult:
    """Engle-Granger two-step cointegration test.

    Step 1: Regress y on x via OLS to obtain residuals.
    Step 2: Run the ADF test on the residuals.

    If the residuals are stationary, y and x are cointegrated.

    Args:
        y: Dependent price series.
        x: Independent price series.
        significance: Significance level for the ADF test on residuals.

    Returns:
        CointResult indicating whether the pair is cointegrated.
    """
    # Align indices
    common_idx = y.index.intersection(x.index)
    if len(common_idx) < 10:
        return CointResult(
            is_stationary=False,
            test_statistic=0.0,
            p_value=1.0,
            critical_values={"1%": -3.43, "5%": -2.86, "10%": -2.57},
        )

    y_aligned = y.loc[common_idx].values.astype(float)
    x_aligned = x.loc[common_idx].values.astype(float)

    # OLS: y = alpha + beta * x + epsilon
    alpha, beta = _ols_coefficients(y_aligned, x_aligned)
    residuals = pd.Series(y_aligned - alpha - beta * x_aligned)

    return adf_test(residuals, significance=significance)


# ---------------------------------------------------------------------------
# Pair scanning
# ---------------------------------------------------------------------------

def find_cointegrated_pairs(
    price_df: pd.DataFrame,
    significance: float = 0.05,
) -> list[PairResult]:
    """Scan all pairs in a DataFrame of price series for cointegration.

    For each unique pair of columns, runs the Engle-Granger test and
    computes the OLS hedge ratio.

    Args:
        price_df: DataFrame where each column is a price series and each
            row is a time observation.  Column names are used as symbols.
        significance: Significance level for cointegration testing.

    Returns:
        List of PairResult sorted by p-value (ascending).  Only pairs
        with at least 10 valid observations are tested.
    """
    symbols = list(price_df.columns)
    results: list[PairResult] = []

    for sym_a, sym_b in combinations(symbols, 2):
        series_a = price_df[sym_a].dropna()
        series_b = price_df[sym_b].dropna()

        common_idx = series_a.index.intersection(series_b.index)
        if len(common_idx) < 10:
            continue

        y_vals = series_a.loc[common_idx].values.astype(float)
        x_vals = series_b.loc[common_idx].values.astype(float)

        # Hedge ratio via OLS
        _, hedge_ratio = _ols_coefficients(y_vals, x_vals)

        # Engle-Granger test
        coint_result = engle_granger(
            series_a.loc[common_idx],
            series_b.loc[common_idx],
            significance=significance,
        )

        results.append(
            PairResult(
                symbol_a=sym_a,
                symbol_b=sym_b,
                p_value=coint_result.p_value,
                hedge_ratio=hedge_ratio,
                is_cointegrated=coint_result.is_stationary,
            )
        )

    results.sort(key=lambda r: r.p_value)
    return results
