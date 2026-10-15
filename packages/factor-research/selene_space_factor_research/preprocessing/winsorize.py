# Ported from AlphaPurify (MIT)
#
# Cross-sectional / time-series winsorization, ported from the Polars
# implementation in APr_utils.py. Polars `.std()` defaults to ddof=1, so all
# standard deviations here use ddof=1 to stay numerically identical.

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm
from sklearn.linear_model import LinearRegression, RANSACRegressor


def mean_std_winsorize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    n: float = 3.0,
) -> pd.DataFrame:
    grouped = df.groupby(trade_date_col, sort=False)[factor_col]
    mean = grouped.transform("mean")
    std = grouped.transform(lambda s: s.std(ddof=1))
    lower = mean - n * std
    upper = mean + n * std
    out = df.copy()
    x = out[factor_col]
    if pd.api.types.is_integer_dtype(x):
        # Polars clip casts float bounds to the column dtype (truncation).
        lower = np.trunc(lower)
        upper = np.trunc(upper)
        out[factor_col] = pd.Series(
            np.where(x < lower, lower, np.where(x > upper, upper, x)),
            index=out.index,
        ).astype(x.dtype)
    else:
        out[factor_col] = np.where(
            x < lower,
            lower,
            np.where(x > upper, upper, x),
        )
    return out


def mad_winsorize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    n: float = 3.0,
) -> pd.DataFrame:
    grouped = df.groupby(trade_date_col, sort=False)[factor_col]
    median = grouped.transform("median")
    mad = grouped.transform(lambda s: (s - s.median()).abs().median())
    lower = median - n * mad
    upper = median + n * mad
    out = df.copy()
    x = out[factor_col]
    if pd.api.types.is_integer_dtype(x):
        lower = np.trunc(lower)
        upper = np.trunc(upper)
        out[factor_col] = pd.Series(
            np.where(x < lower, lower, np.where(x > upper, upper, x)),
            index=out.index,
        ).astype(x.dtype)
    else:
        out[factor_col] = np.where(
            x < lower,
            lower,
            np.where(x > upper, upper, x),
        )
    return out


def volatility_winsorize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    k: float = 2.0,
) -> pd.DataFrame:
    grouped = df.groupby(trade_date_col, sort=False)[factor_col]
    mean = grouped.transform("mean")
    std = grouped.transform(lambda s: s.std(ddof=1))
    lower = mean - k * std
    upper = mean + k * std
    out = df.copy()
    x = out[factor_col]
    # Polars: NaN bounds compare as finite < NaN = True and finite > NaN = False
    # (a group containing inf yields NaN std). Null values fall through to the
    # otherwise branch and are preserved.
    lower_comp = np.where(np.isnan(lower), np.inf, lower)
    upper_comp = np.where(np.isnan(upper), -np.inf, upper)
    out[factor_col] = np.where(
        x > upper_comp,
        upper,
        np.where(x < lower_comp, lower, x),
    )
    return out


def iqr_winsorize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    k: float = 1.5,
) -> pd.DataFrame:
    out = df.copy()
    out[factor_col] = np.where(np.isfinite(out[factor_col]), out[factor_col], np.nan)
    grouped = out.groupby(trade_date_col, sort=False)[factor_col]
    q1 = grouped.transform(lambda s: s.quantile(0.25, interpolation="linear"))
    q3 = grouped.transform(lambda s: s.quantile(0.75, interpolation="linear"))
    iqr = q3 - q1
    lower = q1 - k * iqr
    upper = q3 + k * iqr
    out[factor_col] = np.where(
        out[factor_col] < lower,
        lower,
        np.where(out[factor_col] > upper, upper, out[factor_col]),
    )
    return out


def quantile_winsorize(
    df: pd.DataFrame,
    trade_date_col: str,
    factor_col: str,
    lower_q: float = 0.01,
    upper_q: float = 0.99,
) -> pd.DataFrame:
    out = df.copy()
    out[factor_col] = np.where(np.isfinite(out[factor_col]), out[factor_col], np.nan)
    grouped = out.groupby(trade_date_col, sort=False)[factor_col]
    q_low = grouped.transform(lambda s: s.quantile(lower_q, interpolation="linear"))
    q_high = grouped.transform(lambda s: s.quantile(upper_q, interpolation="linear"))
    out[factor_col] = np.where(
        out[factor_col] < q_low,
        q_low,
        np.where(out[factor_col] > q_high, q_high, out[factor_col]),
    )
    return out


def rolling_quantile_winsorize(
    df: pd.DataFrame,
    trade_date_col: str,
    symbol_col: str,
    factor_col: str,
    window: int = 252,
    lower_q: float = 0.01,
    upper_q: float = 0.99,
) -> pd.DataFrame:
    # Polars rolling_quantile defaults to interpolation "nearest": index
    # round((n-1)*q) with half-up rounding. pandas rolling.apply converts inf
    # to NaN inside windows, so the quantile is computed manually on raw slices.
    def _nearest_quantile(a: np.ndarray, q: float) -> float:
        a = a[~np.isnan(a)]
        if a.size == 0:
            return np.nan
        idx = int(np.floor((a.size - 1) * q + 0.5))
        return float(np.sort(a)[idx])

    work = df.reset_index(drop=True)
    order = work.sort_values([symbol_col, trade_date_col], kind="stable").index.to_numpy()
    sorted_df = work.iloc[order]
    vals = sorted_df[factor_col].to_numpy()
    q_low = np.empty(len(sorted_df), dtype=float)
    q_high = np.empty(len(sorted_df), dtype=float)
    for _, pos in sorted_df.groupby(symbol_col, sort=False).indices.items():
        v = vals[pos]
        for j, i in enumerate(pos):
            lo = max(0, j - window + 1)
            windowed = v[lo : j + 1]
            q_low[i] = _nearest_quantile(windowed, lower_q)
            q_high[i] = _nearest_quantile(windowed, upper_q)
    clipped = np.where(
        vals < q_low,
        q_low,
        np.where(vals > q_high, q_high, vals),
    )
    back = np.empty_like(clipped)
    back[order] = clipped
    out = df.copy()
    out[factor_col] = back
    return out


def boxcox_compress_winsorize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    λ: float = 0.5,
) -> pd.DataFrame:
    grouped = df.groupby(trade_date, sort=False)[factor_col]
    shifted = df[factor_col] - grouped.transform("min") + 1e-9
    if λ == 0:
        transformed = np.log(shifted)
    else:
        transformed = (shifted**λ - 1) / λ
    out = df.copy()
    out[factor_col] = transformed
    return out


def zscore_winsorize(
    df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    k: float = 3.0,
) -> pd.DataFrame:
    grouped = df.groupby(trade_date, sort=False)[factor_col]
    mean = grouped.transform("mean")
    std = grouped.transform(lambda s: s.std(ddof=1))
    std = np.where(std == 0, 1e-9, std)
    x = df[factor_col]
    z = (x - mean) / std
    # NaN z-scores from a degenerate group compare greater than k in Polars;
    # null factor values instead fall through and are preserved.
    z_comp = np.where(np.isnan(z), np.inf, z)
    out = df.copy()
    out[factor_col] = np.where(
        z_comp > k,
        mean + k * std,
        np.where(z_comp < -k, mean - k * std, x),
    )
    out.loc[x.isna(), factor_col] = x
    return out


def rankgauss_winsorize(
    base_df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
) -> pd.DataFrame:
    df = base_df.copy()
    grouped = df.groupby(trade_date, sort=False)[factor_col]
    rank = grouped.rank(method="average")
    n = grouped.transform("size")
    quantile = ((rank - 0.5) / n).clip(1e-6, 1 - 1e-6)
    df[factor_col] = norm.ppf(quantile)
    return df


def tanh_winsorize(
    base_df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    scale: float = 1.0,
) -> pd.DataFrame:
    grouped = base_df.groupby(trade_date, sort=False)[factor_col]
    mean = grouped.transform("mean")
    std = grouped.transform(lambda s: s.std(ddof=1))
    zscore = (base_df[factor_col] - mean) / (std + 1e-12)
    out = base_df.copy()
    out[factor_col] = np.tanh(zscore / scale)
    return out


def huber_winsorize(
    base_df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    c: float = 2.0,
) -> pd.DataFrame:
    grouped = base_df.groupby(trade_date, sort=False)[factor_col]
    mean = grouped.transform("mean")
    std = grouped.transform(lambda s: s.std(ddof=1))
    zscore = (base_df[factor_col] - mean) / (std + 1e-12)
    z_clipped = np.sign(zscore) * np.minimum(np.abs(zscore), c)
    out = base_df.copy()
    out[factor_col] = z_clipped * std + mean
    return out


def ransac_winsorize(
    base_df: pd.DataFrame,
    trade_date: str,
    factor_col: str,
    residual_threshold: float = 2.5,
    replace_with_fit: bool = True,
) -> pd.DataFrame:
    work = base_df.reset_index(drop=True)
    factor_new = work[factor_col].to_numpy(dtype=float).copy()
    for positions in work.groupby(trade_date, sort=False).indices.values():
        grp = work.iloc[positions]
        y = grp[factor_col].to_numpy(dtype=float).reshape(-1, 1)
        x = np.arange(len(y)).reshape(-1, 1)
        if len(y) < 5:
            continue
        try:
            model = RANSACRegressor(estimator=LinearRegression(), random_state=0)
            model.fit(x, y)
            y_pred = model.predict(x)
            resid = y - y_pred
            std = np.std(resid)
            mask = np.abs(resid) <= residual_threshold * std
            if replace_with_fit:
                y_new = np.where(mask, y, y_pred)
            else:
                low = y_pred - residual_threshold * std
                high = y_pred + residual_threshold * std
                y_new = np.clip(y, low, high)
            factor_new[positions] = y_new.flatten()
        except Exception:
            continue
    out = base_df.copy()
    out[factor_col] = factor_new
    return out
