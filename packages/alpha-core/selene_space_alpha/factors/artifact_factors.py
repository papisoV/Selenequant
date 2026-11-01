"""Artifact bridge factors — 7 producer feature ports (cleanroom relocation).

Bridge artifacts (``*_autogen_factor.py``) consume these 7 feature names
in formula chains. The implementations mirror the user's pipeline
``feature_ports.py`` ports that already passed the ruling-B numeric wall
(corr >= 0.999 + warmup mask + Jaccard against the torch fixtures); the
relocated versions here are re-verified by
``scripts/verify_artifact_feature_ports.py``.

Normalization primitives mirror the producer's ``features.py`` semantics:

- ``_clean``       = nan_to_num(nan=0, posinf=1, neginf=-1)
- ``_robust_norm`` = (x - med200) / MAD200 clip ±5; first 199 bars = 0
- ``_norm``        = clean -> robust_norm(200)

RVOL naming trap: the producer's RVOL is *realized return volatility*
(rolling std of log returns), NOT a relative-volume ratio.
RET_ENTROPY_20 must compute returns in float32 — tiny float64 deltas
collapse to exact 0 under float32, which changes the zero-bin counts.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _clean(s: pd.Series) -> pd.Series:
    a = s.astype(float).to_numpy()
    a = np.where(
        np.isnan(a),
        0.0,
        np.where(np.isposinf(a), 1.0, np.where(np.isneginf(a), -1.0, a)),
    )
    return pd.Series(a, index=range(len(a)))


def _pad0(s: pd.Series, k: int) -> pd.Series:
    return pd.concat(
        [pd.Series([0.0] * k, dtype=float), s.astype(float).reset_index(drop=True)],
        ignore_index=True,
    )


def _roll(s: pd.Series, w: int) -> pd.Series:
    p = _pad0(s, w - 1)
    return p.rolling(w).mean().iloc[w - 1 :].reset_index(drop=True)


def _robust_norm(s: pd.Series, w: int = 200) -> pd.Series:
    s = s.astype(float).reset_index(drop=True)
    a = s.to_numpy()
    n = len(a)
    padded = np.concatenate([np.zeros(w - 1), a])
    k = (w - 1) // 2
    med = np.array([np.partition(padded[i : i + w], k)[k] for i in range(n)])
    mad = (
        np.array(
            [np.partition(np.abs(padded[i : i + w] - med[i]), k)[k] for i in range(n)]
        )
        + 1e-6
    )
    out = np.clip((a - med) / mad, -5.0, 5.0)
    out[: w - 1] = 0.0
    out = np.where(np.isfinite(out), out, 0.0)
    return pd.Series(out)


def _norm(s: pd.Series) -> pd.Series:
    return _robust_norm(s.fillna(0.0), 200)


def _ts_ema(x: pd.Series, span: int) -> pd.Series:
    s = _clean(x.astype(float))
    alpha = 2.0 / (span + 1.0)
    a = s.to_numpy()
    out = np.empty(len(a))
    out[0] = a[0]
    for t in range(1, len(a)):
        out[t] = alpha * a[t] + (1 - alpha) * out[t - 1]
    return pd.Series(out, index=range(len(a)))


def _ts_max(x: pd.Series, d: int) -> pd.Series:
    p = _pad0(x, d - 1)
    return _clean(p.rolling(d).max().iloc[d - 1 :].reset_index(drop=True))


def _ts_min(x: pd.Series, d: int) -> pd.Series:
    p = _pad0(x, d - 1)
    return _clean(p.rolling(d).min().iloc[d - 1 :].reset_index(drop=True))


def _ts_sum(x: pd.Series, d: int) -> pd.Series:
    p = _pad0(x, d - 1)
    return _clean(p.rolling(d).sum().iloc[d - 1 :].reset_index(drop=True))


def _zero(data: pd.DataFrame) -> pd.Series:
    return pd.Series(0.0, index=data.index)


def compute_art_gk_vol(data: pd.DataFrame) -> pd.Series:
    """Garman-Klass volatility: _norm(log1p(sqrt(roll20(GK_bar))))."""
    if not {"open", "high", "low", "close"}.issubset(data.columns):
        return _zero(data)
    eps = 1e-9
    o = _clean(data["open"].astype(float)).to_numpy()
    h = _clean(data["high"].astype(float)).to_numpy()
    lo = _clean(data["low"].astype(float)).to_numpy()
    c = _clean(data["close"].astype(float)).to_numpy()
    ln2 = math.log(2.0)
    hl_term = 0.5 * (np.log((h + eps) / (lo + eps))) ** 2
    co_term = (2.0 * ln2 - 1.0) * (np.log((c + eps) / (o + eps))) ** 2
    gk_bar = np.maximum(hl_term - co_term, 0.0)
    gk_mean = _roll(pd.Series(gk_bar), 20)
    raw_vol = gk_mean.clip(lower=0.0).pow(0.5)
    logv = raw_vol.map(lambda v: math.log1p(v) if v > 0 and math.isfinite(v) else 0.0)
    return _norm(pd.Series(logv))


def compute_art_rvol(data: pd.DataFrame) -> pd.Series:
    """Realized return volatility (NOT volume ratio): _norm(log1p(rollstd20(ret)+eps))."""
    if "close" not in data.columns:
        return _zero(data)
    eps = 1e-9
    c = _clean(data["close"].astype(float)).to_numpy()
    n = len(c)
    ret = np.zeros(n)
    for i in range(1, n):
        if c[i - 1] > 0:
            ret[i] = math.log(c[i] / (c[i - 1] + eps))
    w = 20
    padded = np.concatenate([np.zeros(w - 1), ret])
    out = np.zeros(n)
    for i in range(n):
        wnd = padded[i : i + w]
        m = wnd.mean()
        out[i] = math.sqrt(float(((wnd - m) ** 2).mean())) + 1e-9
    logv = pd.Series(out).map(
        lambda v: math.log1p(v) if v > 0 and math.isfinite(v) else 0.0
    )
    return _norm(logv)


def compute_art_ichimoku_kijun_dev(data: pd.DataFrame) -> pd.Series:
    """close vs Kijun(26 mid of high/low range) deviation: _norm((C-kijun)/(kijun+eps))."""
    if not {"high", "low", "close"}.issubset(data.columns):
        return _zero(data)
    eps = 1e-9
    c = _clean(data["close"].astype(float)).reset_index(drop=True)
    h = _clean(data["high"].astype(float)).reset_index(drop=True)
    lo = _clean(data["low"].astype(float)).reset_index(drop=True)
    kijun = (_ts_max(h, 26) + _ts_min(lo, 26)) / 2.0
    dev = (c - kijun) / (kijun + eps)
    return _norm(_clean(dev))


def compute_art_ret_entropy_20(data: pd.DataFrame) -> pd.Series:
    """Return-sign Shannon entropy (20, pos/neg/zero bins) / log(3) in [0,1], no norm.

    Returns are computed in float32 (float64 deltas collapse to exact 0 under
    float32, and the zero bin counts differ) — keep this discipline.
    """
    if "close" not in data.columns:
        return _zero(data)
    eps = 1e-9
    c32 = data["close"].astype(np.float32).to_numpy()
    n = len(c32)
    ret32 = np.zeros(n, dtype=np.float32)
    for i in range(1, n):
        ret32[i] = np.float32(math.log(c32[i] / (c32[i - 1] + eps)))
    w = 20
    padded = np.concatenate([np.zeros(w - 1), ret32])
    log3 = math.log(3.0)
    out = np.zeros(n)
    for i in range(n):
        wnd = padded[i : i + w]
        p_pos = float((wnd > 0).mean())
        p_neg = float((wnd < 0).mean())
        p_zero = float((wnd == 0).mean())
        hh = 0.0
        for p in (p_pos, p_neg, p_zero):
            if p > 0:
                hh += -p * math.log(p + eps)
        out[i] = min(max(hh / log3, 0.0), 1.0)
    return pd.Series(out).replace([np.inf, -np.inf], 0.0).fillna(0.0)


def compute_art_macd_hist(data: pd.DataFrame) -> pd.Series:
    """MACD histogram: (EMA12-EMA26) - EMA9(of that), recursive EMA (out[0]=x[0])."""
    if "close" not in data.columns:
        return _zero(data)
    c = _clean(data["close"].astype(float))
    macd = _ts_ema(c, 12) - _ts_ema(c, 26)
    signal = _ts_ema(_clean(macd), 9)
    return _norm(_clean(macd - signal))


def compute_art_trix_15(data: pd.DataFrame) -> pd.Series:
    """Triple-EMA(15) one-step rate of change: _norm((e3-prev)/(|prev|+eps))."""
    if "close" not in data.columns:
        return _zero(data)
    eps = 1e-9
    c = _clean(data["close"].astype(float))
    e3 = _ts_ema(_ts_ema(_ts_ema(c, 15), 15), 15)
    prev = e3.shift(1)
    prev.iloc[0] = e3.iloc[0]
    t = (e3 - prev) / (prev.abs() + eps)
    return _norm(_clean(t))


def compute_art_hma_dev_20(data: pd.DataFrame) -> pd.Series:
    """close vs HMA(20) deviation: _norm((C-hma)/(hma+eps)), hma=WMA(2*WMA10-WMA20, 4)."""
    if "close" not in data.columns:
        return _zero(data)
    eps = 1e-9
    c = _clean(data["close"].astype(float)).reset_index(drop=True)

    def _wma(s: pd.Series, w: int) -> pd.Series:
        p = _pad0(s, w - 1)
        wt = np.arange(1, w + 1, dtype=float)
        return (
            p.rolling(w)
            .apply(lambda wnd: float((wnd * wt).sum() / wt.sum()), raw=True)
            .iloc[w - 1 :]
            .reset_index(drop=True)
        )

    half = _wma(c, 10)
    full = _wma(c, 20)
    hma = _wma(2.0 * half - full, 4)
    dev = (c - hma) / (hma + eps)
    return _norm(_clean(dev))


ARTIFACT_FACTORS: dict[str, tuple[object, str]] = {
    "art_ichimoku_kijun_dev": (
        compute_art_ichimoku_kijun_dev,
        "close vs Kijun(26) deviation, robust-normed",
    ),
    "art_gk_vol": (compute_art_gk_vol, "Garman-Klass vol (20-bar), robust-normed"),
    "art_ret_entropy_20": (
        compute_art_ret_entropy_20,
        "return-sign Shannon entropy (20, 3-bin), no norm",
    ),
    "art_rvol": (
        compute_art_rvol,
        "realized return volatility (20), robust-normed",
    ),
    "art_macd_hist": (compute_art_macd_hist, "MACD histogram (12/26/9), robust-normed"),
    "art_trix_15": (
        compute_art_trix_15,
        "triple-EMA(15) rate of change, robust-normed",
    ),
    "art_hma_dev_20": (
        compute_art_hma_dev_20,
        "close vs HMA(20) deviation, robust-normed",
    ),
}
