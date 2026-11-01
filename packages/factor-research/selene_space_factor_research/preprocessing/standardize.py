# Ported from AlphaPurify (MIT)
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

# polars "ordinal" ranking (ties get distinct ranks by position) == pandas "first".
_RANK_METHODS: dict[str, str] = {"ordinal": "first"}


def zscore_standardize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
) -> pd.DataFrame:
    """Cross-sectional Z-score standardization within each trade_date."""
    grouped = df.groupby(trade_date, sort=False)[factor_col]
    mu = grouped.transform("mean")
    std = grouped.transform(lambda x: x.std(ddof=1))
    out = df.copy()
    out[factor_col] = (df[factor_col] - mu) / std
    return out


def robust_zscore_standardize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    c: float = 1.4826,
) -> pd.DataFrame:
    """Cross-sectional robust Z-score using median and MAD."""
    med = df.groupby(trade_date, sort=False)[factor_col].transform("median")
    abs_dev = (df[factor_col] - med).abs()
    mad = abs_dev.groupby(df[trade_date], sort=False).transform("median")
    out = df.copy()
    out[factor_col] = (df[factor_col] - med) / (mad * c)
    return out


def minmax_standardize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    clip: bool = True,
) -> pd.DataFrame:
    """Cross-sectional Min-Max scaling into [0, 1]."""
    grouped = df.groupby(trade_date, sort=False)[factor_col]
    x_min = grouped.transform("min")
    x_max = grouped.transform("max")
    scaled = (df[factor_col] - x_min) / (x_max - x_min)
    if clip:
        scaled = scaled.clip(0, 1)
    out = df.copy()
    out[factor_col] = scaled
    return out


def rank_standardize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    method: str = "average",
) -> pd.DataFrame:
    """Cross-sectional percentile ranks: rank / group size."""
    grouped = df.groupby(trade_date, sort=False)[factor_col]
    rank_val = grouped.transform("rank", method=_RANK_METHODS.get(method, method))
    count_val = grouped.transform("size")
    out = df.copy()
    out[factor_col] = rank_val / count_val
    return out


def rank_gaussianize_standardize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    method: str = "average",
) -> pd.DataFrame:
    """Rank-to-normal via inverse normal CDF of rank/(count + 1)."""
    grouped = df.groupby(trade_date, sort=False)[factor_col]
    rank_val = grouped.transform("rank", method=_RANK_METHODS.get(method, method))
    count_val = grouped.transform("size")
    pct = rank_val / (count_val + 1)
    zscores = norm.ppf(np.clip(pct.to_numpy(), 1e-6, 1 - 1e-6))
    out = df.copy()
    out[factor_col] = zscores
    return out


def rolling_standardize(
    df: pd.DataFrame,
    trade_date: str,
    symbol_col: str,
    factor_col: str,
    window: int = 20,
    min_periods: int | None = None,
) -> pd.DataFrame:
    """Rolling Z-score per symbol over a trailing window."""
    if min_periods is None:
        min_periods = window
    out = df.sort_values([symbol_col, trade_date]).copy()
    grouped = out.groupby(symbol_col, sort=False)[factor_col]
    mean = grouped.transform(
        lambda x: x.rolling(window, min_periods=min_periods).mean()
    )
    std = grouped.transform(
        lambda x: x.rolling(window, min_periods=min_periods).std(ddof=1)
    )
    out[factor_col] = (out[factor_col] - mean) / std
    return out


def rolling_robust_standardize(
    df: pd.DataFrame,
    trade_date: str,
    symbol_col: str,
    factor_col: str,
    window: int = 20,
    min_periods: int | None = None,
) -> pd.DataFrame:
    """Rolling robust Z-score per symbol using median and MAD."""
    if min_periods is None:
        min_periods = window
    out = df.sort_values([symbol_col, trade_date]).copy()
    med = out.groupby(symbol_col, sort=False)[factor_col].transform(
        lambda x: x.rolling(window, min_periods=min_periods).median()
    )
    abs_dev = (out[factor_col] - med).abs()
    mad = abs_dev.groupby(out[symbol_col], sort=False).transform(
        lambda x: x.rolling(window, min_periods=min_periods).median()
    )
    out[factor_col] = (out[factor_col] - med) / (1.4826 * mad)
    return out


def rolling_minmax_standardize(
    df: pd.DataFrame,
    factor_col: str,
    trade_date: str,
    symbol_col: str,
    window: int = 20,
    min_periods: int | None = None,
) -> pd.DataFrame:
    """Rolling Min-Max scaling into [0, 1] per symbol."""
    if min_periods is None:
        min_periods = window
    out = df.sort_values([symbol_col, trade_date]).copy()
    grouped = out.groupby(symbol_col, sort=False)[factor_col]
    roll_min = grouped.transform(
        lambda x: x.rolling(window, min_periods=min_periods).min()
    )
    roll_max = grouped.transform(
        lambda x: x.rolling(window, min_periods=min_periods).max()
    )
    out[factor_col] = (out[factor_col] - roll_min) / (roll_max - roll_min)
    return out


def volatility_scaling_standardize(
    df: pd.DataFrame,
    trade_date: str,
    symbol_col: str,
    factor_col: str,
    window: int = 20,
    min_periods: int | None = None,
    shift_vol: bool = True,
) -> pd.DataFrame:
    """Scale factor by rolling volatility, optionally shifted one period."""
    if min_periods is None:
        min_periods = window
    out = df.sort_values([symbol_col, trade_date]).copy()
    vol = out.groupby(symbol_col, sort=False)[factor_col].transform(
        lambda x: x.rolling(window, min_periods=min_periods).std(ddof=1)
    )
    if shift_vol:
        vol = vol.groupby(out[symbol_col], sort=False).shift(1)
    out[factor_col] = out[factor_col] / vol
    return out


def EWMA_standardize(
    df: pd.DataFrame,
    trade_date: str,
    symbol_col: str,
    factor_col: str,
    lambda_: float = 0.94,
    eps: float = 1e-12,
) -> pd.DataFrame:
    """EWMA volatility scaling via reversed weighted accumulation."""
    out = df.sort_values([symbol_col, trade_date]).copy()

    def _ewma_variance(x: pd.Series) -> pd.Series:
        xarr = x.to_numpy(dtype=float)
        n = len(xarr)
        t = np.arange(n, dtype=float)
        weight = (1 - lambda_) * (lambda_ ** t)
        rev = (xarr**2 * weight)[::-1]
        null_mask = np.isnan(xarr)[::-1]
        # polars cum_sum: nulls are skipped, ±inf propagates, and a computed
        # NaN (e.g. inf*0) poisons every subsequent output.
        cum = np.cumsum(np.where(np.isnan(rev), 0.0, rev))
        computed = np.isnan(rev) & ~null_mask
        if computed.any():
            poisoned = np.zeros(n, dtype=bool)
            poisoned[int(np.argmax(computed)):] = True
            cum = np.where(poisoned & ~null_mask, np.nan, cum)
        cum[null_mask] = np.nan
        return pd.Series(cum[::-1], index=x.index)

    ewma_var = out.groupby(symbol_col, sort=False)[factor_col].transform(
        _ewma_variance
    )
    out[factor_col] = out[factor_col] / (np.sqrt(ewma_var) + eps)
    return out


def normal_scores_standardize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    eps: float = 1e-9,
) -> pd.DataFrame:
    """Normal scores from empirical CDF (rank - 0.5) / n."""
    out = df.sort_values([trade_date, factor_col]).copy()
    grouped = out.groupby(trade_date, sort=False)
    rank = grouped[factor_col].transform(
        lambda x: np.arange(1, len(x) + 1, dtype=float)
    )
    n = grouped[factor_col].transform("size")
    cdf = (rank - 0.5) / n
    zscores = norm.ppf(np.clip(cdf.to_numpy(), eps, 1 - eps))
    out[factor_col] = zscores
    return out


def quantile_binning_standardize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    q: int = 5,
) -> pd.DataFrame:
    """Cross-sectional quantile binning into integer labels [0, q-1]."""
    out = df.sort_values([trade_date, factor_col]).copy()
    grouped = out.groupby(trade_date, sort=False)
    rank = grouped[factor_col].transform(
        lambda x: np.arange(1, len(x) + 1, dtype=float)
    )
    n = grouped[factor_col].transform("size")
    bins = np.floor((rank - 1) * q / n).clip(0, q - 1).astype(np.int32)
    out[factor_col] = bins
    return out


def log_zscore_standardize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    eps: float = 1e-9,
) -> pd.DataFrame:
    """Log transform then cross-sectional Z-score."""
    x = df[factor_col]
    log_x = np.log(x + eps)
    # polars log(non-positive) yields NaN (not null) which poisons the
    # cross-sectional mean/std; original nulls are skipped by the aggregations.
    computed_nan = log_x.isna() & x.notna()
    grouped = log_x.groupby(df[trade_date], sort=False)
    mu = grouped.transform("mean")
    sigma = grouped.transform(lambda x: x.std(ddof=1))
    poison = computed_nan.groupby(df[trade_date], sort=False).transform("max").astype(bool)
    out = df.copy()
    z = (log_x - mu) / (sigma + eps)
    out[factor_col] = z.where(~poison, np.nan)
    return out


def yeo_johnson_standardize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    lambda_: float = 0.0,
    eps: float = 1e-9,
) -> pd.DataFrame:
    """Yeo-Johnson power transform then cross-sectional Z-score."""
    x = df[factor_col]
    pos = x >= 0
    if lambda_ == 0:
        y_pos = np.log(x + 1)
    else:
        y_pos = ((x + 1) ** lambda_ - 1) / lambda_
    if lambda_ == 2:
        y_neg = -np.log(1 - x)
    else:
        y_neg = -(((1 - x) ** (2 - lambda_) - 1) / (2 - lambda_))
    y = np.where(pos, y_pos, y_neg)
    y = pd.Series(y, index=df.index, dtype=float)
    grouped = y.groupby(df[trade_date], sort=False)
    mu = grouped.transform("mean")
    sigma = grouped.transform(lambda x: x.std(ddof=1))
    out = df.copy()
    out[factor_col] = (y - mu) / (sigma + eps)
    return out


def boxcox_standardize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    lambda_: float = 0.0,
    eps: float = 1e-9,
) -> pd.DataFrame:
    """Box-Cox transform (with positive shift) then cross-sectional Z-score."""
    min_val = df[factor_col].min()
    shift = float(-min_val + eps) if min_val <= 0 else 0.0
    x = df[factor_col] + shift
    if lambda_ == 0:
        pt = np.log(x)
    else:
        pt = (x**lambda_ - 1) / lambda_
    pt = pd.Series(pt, index=df.index, dtype=float)
    # polars arithmetic on ±inf can yield a computed NaN (e.g. -inf + shift)
    # which poisons the cross-sectional mean/std; original nulls are skipped.
    computed_nan = pt.isna() & df[factor_col].notna()
    grouped = pt.groupby(df[trade_date], sort=False)
    mu = grouped.transform("mean")
    sigma = grouped.transform(lambda x: x.std(ddof=1))
    poison = computed_nan.groupby(df[trade_date], sort=False).transform("max").astype(bool)
    out = df.copy()
    z = (pt - mu) / (sigma + eps)
    out[factor_col] = z.where(~poison, np.nan)
    return out
