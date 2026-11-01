"""Volatility-estimator factor IC sweep (2026-09-22).

Last cheap probe of the OHLC-derived factor family: four volatility
estimators (Parkinson / GarmanKlass / RogersSatchell / YangZhang — the
Sinclair vol-trading corpus) computed on 15m klines, scored with the same
rolling rank-IC used for the vocab-gap sweep (4-bar forward log return,
1440-bar window). TF_TREND is the in-harness benchmark. Read-only probe:
candidates with real edge get promoted to vocab via a separate pre-registered
experiment.

Estimators (annualization-free, per-bar, then trailing-window summed):
  parkinson      — ln(H/L)^2 mean, /4ln2
  garman_klass   — (ln(H/L)^2/2 - (2ln2-1)ln(C/O)^2) mean
  rogers_satchell— ln(H/C)ln(H/O) + ln(L/C)ln(L/O)
  yang_zhang     — overnight var + k*close-to-close var + (1-k)RS var
All trailing (rolling sum over WINDOW bars), no look-ahead.
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

WINDOW = 288  # ~3 days of 15m bars for the rolling vol aggregation
IC_WINDOW = 1440  # same as TF_TREND diagnostic
HORIZON = 4


def parkinson(df: pd.DataFrame) -> pd.Series:
    hi, lo = df["high"].astype(float), df["low"].astype(float)
    hl2 = np.log(hi / lo) ** 2
    return hl2.rolling(WINDOW, min_periods=WINDOW // 4).mean() / (4 * np.log(2))


def garman_klass(df: pd.DataFrame) -> pd.Series:
    hi, lo = df["high"].astype(float), df["low"].astype(float)
    co = np.log(df["close"].astype(float) / df["open"].astype(float))
    term = 0.5 * np.log(hi / lo) ** 2 - (2 * np.log(2) - 1) * co**2
    return term.rolling(WINDOW, min_periods=WINDOW // 4).mean()


def rogers_satchell(df: pd.DataFrame) -> pd.Series:
    hi, lo = df["high"].astype(float), df["low"].astype(float)
    term = np.log(hi / df["close"].astype(float)) * np.log(
        hi / df["open"].astype(float)
    ) + np.log(lo / df["close"].astype(float)) * np.log(lo / df["open"].astype(float))
    return term.rolling(WINDOW, min_periods=WINDOW // 4).mean()


def yang_zhang(df: pd.DataFrame) -> pd.Series:
    o, c = df["open"].astype(float), df["close"].astype(float)
    hi, lo = df["high"].astype(float), df["low"].astype(float)
    overnight = np.log(o / c.shift(1)) ** 2
    cc = np.log(c / o) ** 2
    rs = np.log(hi / c) * np.log(hi / o) + np.log(lo / c) * np.log(lo / o)
    k = 0.34 / (1.34 + WINDOW / 3)
    var = (
        overnight.rolling(WINDOW, min_periods=WINDOW // 4).mean()
        + k * cc.rolling(WINDOW, min_periods=WINDOW // 4).mean()
        + (1 - k) * rs.rolling(WINDOW, min_periods=WINDOW // 4).mean()
    )
    return var


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
    "parkinson": parkinson,
    "garman_klass": garman_klass,
    "rogers_satchell": rogers_satchell,
    "yang_zhang": yang_zhang,
}


def main() -> None:
    results: dict[str, dict[str, object]] = {}
    out = {
        "date": "2026-09-22",
        "horizon_bars": HORIZON,
        "ic_window_bars": IC_WINDOW,
        "primitive_window_bars": WINDOW,
        "results": results,
    }
    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        df, n = load_multi_tf_df(DB_PATH, symbol, "binance", "15m", "1h", "futures")
        if df.empty:
            continue
        fwd_ret = np.log(df["close"].shift(-HORIZON) / df["close"]).fillna(0.0)
        ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        fwd_ret.index = ts
        results[symbol] = {}
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
            results[symbol][name] = entry
            print(
                f"  {name:22s} IC {entry['overall_mean_ic']:+.4f} "
                f"(last90d {recent_90:+.4f}, trend {recent_90 - prior_90:+.4f})"
            )

    Path(
        PROJECT_ROOT / "scripts" / "vol_estimators_ic_sweep_2026-09-22.json"
    ).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("saved -> scripts/vol_estimators_ic_sweep_2026-09-22.json")


if __name__ == "__main__":
    main()
