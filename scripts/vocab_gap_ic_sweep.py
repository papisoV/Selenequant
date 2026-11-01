"""Vocab-gap factor IC sweep (2026-09-21).

Five QuantSkills base formulas absent from the V8 vocab, computed on 15m
klines and scored with the same rolling rank-IC used for the TF_TREND
diagnostic (4-bar forward log return, 1440-bar window). TF_TREND is included
as the in-harness benchmark. Read-only probe: candidates with real edge get
promoted to vocab via a separate pre-registered experiment.

Candidates (QuantSkills base definitions, single-symbol time-series form):
  efficiency    — |close - close.shift(w)| / sum|close.diff()|   (Kaufman)
  breakout      — close / rolling-max(high.shift(1), w) - 1
  breakdown     — -(close / rolling-min(low.shift(1), w) - 1)
  downside_vol  — rolling std of negative-only returns
  obv_slope     — rolling-slope of OBV normalized by mean volume
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "shared-types"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "backtest-engine"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "factor-research"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import selene_space_alpha.factors.pandas_bridge as pb
from optuna_formula_search_v4 import load_multi_tf_df

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"

WINDOW = 288  # ~3 days of 15m bars for rolling primitives
IC_WINDOW = 1440  # same as TF_TREND diagnostic
HORIZON = 4


def _rolling_slope(series: pd.Series, window: int) -> pd.Series:
    w = max(3, int(window))
    x = np.arange(w, dtype=float)
    x = x - x.mean()
    denom = float(np.sum(x * x))

    def calc(values: np.ndarray) -> float:
        if np.isnan(values).any():
            return np.nan
        y = values - values.mean()
        return float(np.sum(x * y) / denom)

    return series.rolling(w, min_periods=w).apply(calc, raw=True)


def efficiency(df: pd.DataFrame) -> pd.Series:
    close = df["close"].astype(float)
    net = (close - close.shift(WINDOW)).abs()
    path = close.diff().abs().rolling(WINDOW, min_periods=WINDOW // 4).sum()
    return (net / path.replace(0, np.nan)).fillna(0.0)


def breakout(df: pd.DataFrame) -> pd.Series:
    high, close = df["high"].astype(float), df["close"].astype(float)
    prior_max = high.shift(1).rolling(WINDOW, min_periods=WINDOW // 4).max()
    return (close / prior_max.replace(0, np.nan) - 1).fillna(0.0)


def breakdown(df: pd.DataFrame) -> pd.Series:
    low, close = df["low"].astype(float), df["close"].astype(float)
    prior_min = low.shift(1).rolling(WINDOW, min_periods=WINDOW // 4).min()
    return (-(close / prior_min.replace(0, np.nan) - 1)).fillna(0.0)


def downside_vol(df: pd.DataFrame) -> pd.Series:
    ret = df["close"].astype(float).pct_change()
    neg = ret.where(ret < 0, 0.0)
    return neg.rolling(WINDOW, min_periods=WINDOW // 4).std(ddof=0).fillna(0.0)


def obv_slope(df: pd.DataFrame) -> pd.Series:
    close, volume = df["close"].astype(float), df["volume"].astype(float)
    obv = (np.sign(close.diff()).fillna(0) * volume).cumsum()
    slope = _rolling_slope(obv, min(WINDOW, 60))
    return (
        slope
        / volume.rolling(WINDOW, min_periods=WINDOW // 4).mean().replace(0, np.nan)
    ).fillna(0.0)


def rolling_ic(
    raw: pd.Series, fwd_ret: pd.Series, window: int = IC_WINDOW
) -> pd.Series:
    r1 = raw.rank(pct=True)
    r2 = fwd_ret.rank(pct=True)
    prod = (r1 * r2).rolling(window, min_periods=window // 4).mean()
    m1 = r1.rolling(window, min_periods=window // 4).mean()
    m2 = r2.rolling(window, min_periods=window // 4).mean()
    s1 = (r1**2).rolling(window, min_periods=window // 4).mean() - m1**2
    s2 = (r2**2).rolling(window, min_periods=window // 4).mean() - m2**2
    denom = np.sqrt(s1 * s2)
    return ((prod - m1 * m2) / denom).replace([np.inf, -np.inf], np.nan)


CANDIDATES: dict[str, Callable[[pd.DataFrame], pd.Series]] = {
    "TF_TREND(benchmark)": pb.compute_tf_trend,
    "efficiency": efficiency,
    "breakout": breakout,
    "breakdown": breakdown,
    "downside_vol": downside_vol,
    "obv_slope": obv_slope,
}


def main() -> None:
    out = {
        "date": "2026-09-21",
        "horizon_bars": HORIZON,
        "ic_window_bars": IC_WINDOW,
        "primitive_window_bars": WINDOW,
        "results": {},
    }
    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        df, n = load_multi_tf_df(DB_PATH, symbol, "binance", "15m", "1h", "futures")
        if df.empty:
            continue
        fwd_ret = np.log(df["close"].shift(-HORIZON) / df["close"]).fillna(0.0)
        ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        fwd_ret.index = ts
        out["results"][symbol] = {}
        print(f"--- {symbol} ({n} bars) ---")
        for name, fn in CANDIDATES.items():
            raw = fn(df).fillna(0.0)
            raw.index = ts
            ic_daily = rolling_ic(raw, fwd_ret).resample("1D").mean().dropna()
            recent_90 = float(ic_daily.iloc[-90:].mean())
            prior_90 = float(ic_daily.iloc[-180:-90].mean())
            entry = {
                "n_bars": int(n),
                "overall_mean_ic": float(ic_daily.mean()),
                "recent_90d_mean_ic": recent_90,
                "prior_90d_mean_ic": prior_90,
                "trend_90d_vs_prior": recent_90 - prior_90,
            }
            out["results"][symbol][name] = entry
            print(
                f"  {name:22s} IC {entry['overall_mean_ic']:+.4f} "
                f"(last90d {recent_90:+.4f}, trend {recent_90 - prior_90:+.4f})"
            )

    Path(PROJECT_ROOT / "scripts" / "vocab_gap_ic_sweep_2026-09-21.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    print("saved -> scripts/vocab_gap_ic_sweep_2026-09-21.json")


if __name__ == "__main__":
    main()
