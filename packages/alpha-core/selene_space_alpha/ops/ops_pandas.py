"""Formula operators (pandas/numpy-native).

Numerically equivalent to ops.py but operates on pandas Series / numpy arrays
instead of torch tensors. This is the bridge that lets AI-discovered formulas
run in SeleneQuant's pandas-based backtest engine.
"""

import numpy as np
import pandas as pd

from ..factors.neutralize import apply_ind_neutralize


def _ts_delay_np(x: np.ndarray, d: int) -> np.ndarray:
    if d == 0:
        return x
    pad = np.zeros(d, dtype=x.dtype)
    return np.concatenate([pad, x[:-d]])


def op_add(x: pd.Series, y: pd.Series) -> pd.Series:
    return x + y


def op_sub(x: pd.Series, y: pd.Series) -> pd.Series:
    return x - y


def op_mul(x: pd.Series, y: pd.Series) -> pd.Series:
    return x * y


def op_div(x: pd.Series, y: pd.Series) -> pd.Series:
    return x / (y + 1e-6)


def op_neg(x: pd.Series) -> pd.Series:
    return -x


def op_abs(x: pd.Series) -> pd.Series:
    return x.abs()


def op_sign(x: pd.Series) -> pd.Series:
    return np.sign(x)


def op_gate(condition: pd.Series, x: pd.Series, y: pd.Series) -> pd.Series:
    mask = (condition > 0).astype(float)
    return mask * x + (1.0 - mask) * y


def op_jump(x: pd.Series) -> pd.Series:
    mean = x.mean()
    std = x.std() + 1e-6
    z = (x - mean) / std
    return np.maximum(z - 3.0, 0.0)


def op_decay(x: pd.Series) -> pd.Series:
    vals = x.values.copy()
    delayed1 = _ts_delay_np(vals, 1)
    delayed2 = _ts_delay_np(vals, 2)
    result = vals + 0.8 * delayed1 + 0.6 * delayed2
    return pd.Series(result, index=x.index)


def op_delay1(x: pd.Series) -> pd.Series:
    result = _ts_delay_np(x.values.copy(), 1)
    return pd.Series(result, index=x.index)


def op_max3(x: pd.Series) -> pd.Series:
    vals = x.values.copy()
    delayed1 = _ts_delay_np(vals, 1)
    delayed2 = _ts_delay_np(vals, 2)
    result = np.maximum(vals, np.maximum(delayed1, delayed2))
    return pd.Series(result, index=x.index)


def op_mean2(x: pd.Series, y: pd.Series) -> pd.Series:
    return (x + y) / 2.0


# --- V4: Time-series operators (8) ---


def op_ts_mean(x: pd.Series) -> pd.Series:
    return x.rolling(window=5, min_periods=1).mean().fillna(0.0)


def op_ts_std(x: pd.Series) -> pd.Series:
    return x.rolling(window=5, min_periods=1).std().fillna(0.0)


def op_ts_std_20(x: pd.Series) -> pd.Series:
    p = np.concatenate([np.zeros(19, dtype=float), x.astype(float).to_numpy()])
    m = pd.Series(p).rolling(window=20, min_periods=20).mean()
    m2 = pd.Series(p * p).rolling(window=20, min_periods=20).mean()
    std = (m2 - m * m).clip(lower=0.0).pow(0.5) + 1e-6
    return std.iloc[19:].reset_index(drop=True).fillna(0.0)


def op_ts_rank(x: pd.Series) -> pd.Series:
    return x.rolling(window=20, min_periods=3).rank(pct=True).fillna(0.5)


def op_ts_corr(x: pd.Series, y: pd.Series) -> pd.Series:
    return x.rolling(window=20, min_periods=5).corr(y).fillna(0.0)


def op_rank(x: pd.Series) -> pd.Series:
    return (x.rank(pct=True) - 0.5) * 2.0


def op_ts_delta(x: pd.Series) -> pd.Series:
    return (x - x.shift(1)).fillna(0.0)


def op_ts_max(x: pd.Series) -> pd.Series:
    return x.rolling(window=10, min_periods=1).max().fillna(0.0)


def op_ts_min(x: pd.Series) -> pd.Series:
    return x.rolling(window=10, min_periods=1).min().fillna(0.0)


def ind_neutralize(x: pd.DataFrame, groups: pd.Series | None = None) -> pd.DataFrame:
    """Industry neutralization operator.

    Applies group-mean subtraction to each column of *x*.  When *groups*
    is provided, uses it directly; otherwise falls back to KMeans clustering.

    Args:
        x: DataFrame of factor values (rows = assets, columns = factors).
        groups: Optional group labels aligned with *x* index.

    Returns:
        Neutralized DataFrame with same shape as *x*.
    """
    return apply_ind_neutralize(x, group_series=groups)


# (name, callable, arity) - mirrors OPS_CONFIG in ops.py
OPS_CONFIG_PANDAS: list[tuple[str, object, int]] = [
    ("ADD", op_add, 2),
    ("SUB", op_sub, 2),
    ("MUL", op_mul, 2),
    ("DIV", op_div, 2),
    ("NEG", op_neg, 1),
    ("ABS", op_abs, 1),
    ("SIGN", op_sign, 1),
    ("GATE", op_gate, 3),
    ("JUMP", op_jump, 1),
    ("DECAY", op_decay, 1),
    ("DELAY1", op_delay1, 1),
    ("MAX3", op_max3, 1),
    ("MEAN2", op_mean2, 2),
    # V4 time-series operators
    ("TS_MEAN", op_ts_mean, 1),
    ("TS_STD", op_ts_std, 1),
    ("TS_RANK", op_ts_rank, 1),
    ("TS_CORR", op_ts_corr, 2),
    ("RANK", op_rank, 1),
    ("TS_DELTA", op_ts_delta, 1),
    ("TS_MAX", op_ts_max, 1),
    ("TS_MIN", op_ts_min, 1),
    # 20-bar std variant for artifact chains: zero-padded causal
    # window, ddof=0 via the E[x²]-E[x]² identity, +1e-6 floor (producer op_lib).
    ("TS_STD_20", op_ts_std_20, 1),
    # Industry neutralization (DataFrame-level operator)
    ("IND_NEUTRALIZE", ind_neutralize, -1),
]
