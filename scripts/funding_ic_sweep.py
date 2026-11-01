"""Funding-rate factor IC sweep (2026-09-22).

Funding rate = perp positioning/crowding signal absent from OHLCV. Data now
backfilled into data/derivatives.db (scripts/backfill_funding_rates.py).
Candidates are merged onto 15m klines via merge_asof (backward = most recent
8h rate before the bar), then scored with the same rolling rank-IC used for
the vocab-gap and vol-estimator sweeps (4-bar forward log return, 1440-bar
window). TF_TREND is the in-harness benchmark.

Candidates:
  funding_level   — raw last-known funding rate (8h cadence, step series)
  funding_z       — z-score vs trailing 24-period (8d) mean/std, mirrors
                    compute_derivatives_factors' FUNDING_RATE_Z
  funding_chg     — change over the last 3 funding periods (24h momentum of
                    crowding)
  funding_neg     — negative funding (sign-flipped level): tests the
                    "extreme positive funding precedes reversal" hypothesis
                    directly with the same rolling-IC machinery
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _p in (
    "packages/core",
    "packages/shared-types",
    "packages/backtest-engine",
    "packages/market-data",
    "packages/exchange-sdk",
    "packages/paper-engine",
    "packages/strategy-engine",
    "packages/llm-adapter",
    "packages/agent-runtime",
    "packages/factor-research",
    "packages/alpha-core",
):
    sys.path.insert(0, str(PROJECT_ROOT / _p))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

import selene_space_alpha.factors.pandas_bridge as pb
from optuna_formula_search_v4 import load_multi_tf_df
from selene_space_core.derivatives_paths import default_derivatives_db_path
from selene_space_core.kline_paths import default_klines_db_path
from selene_space_market.derivatives_db import DerivativesDB

# 单一解析来源(同 kline_paths):此前 klines 指向已废弃的
# services/local-api/data 分支,读的是一份不存在的库 → 静默"无数据"。
DB_PATH = default_klines_db_path()
DERIV_PATH = default_derivatives_db_path()

IC_WINDOW = 1440
HORIZON = 4
Z_WINDOW = 24  # 8h periods = 8 days (matches pandas_bridge FUNDING_RATE_Z)


def load_funding(symbol: str) -> pd.DataFrame:
    db = DerivativesDB(str(DERIV_PATH))
    db.open()
    try:
        return db.get_funding_rates("binance", symbol, 0, 2**62)
    finally:
        db.close()


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


def merge_funding(kline: pd.DataFrame, funding: pd.DataFrame) -> pd.DataFrame:
    df = kline.sort_values("timestamp").copy()
    fr = funding.sort_values("timestamp")[["timestamp", "funding_rate"]]
    df = pd.merge_asof(df, fr, on="timestamp", direction="backward")
    return df


def main() -> None:
    results: dict[str, dict[str, object]] = {}
    out = {
        "date": "2026-09-22",
        "horizon_bars": HORIZON,
        "ic_window_bars": IC_WINDOW,
        "results": results,
    }
    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        df, n = load_multi_tf_df(DB_PATH, symbol, "binance", "15m", "1h", "futures")
        if df.empty:
            continue
        fr = load_funding(symbol)
        if fr.empty:
            print(f"{symbol}: NO FUNDING DATA, skipped")
            continue
        df = merge_funding(df, fr)
        level = df["funding_rate"].fillna(0.0).astype(float)
        z = (
            (level - level.rolling(Z_WINDOW, min_periods=8).mean())
            / (level.rolling(Z_WINDOW, min_periods=8).std() + 1e-9)
        ).fillna(0.0)
        # resample funding to per-period change (only meaningful at 8h steps)
        chg = level.diff(3).fillna(0.0)  # 3 funding periods = 24h

        fwd_ret = np.log(df["close"].shift(-HORIZON) / df["close"]).fillna(0.0)
        ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        fwd_ret.index = ts

        bench = pb.compute_tf_trend(df).fillna(0.0)
        bench.index = ts

        candidates = {
            "TF_TREND(benchmark)": bench,
            "funding_level": level,
            "funding_z": z,
            "funding_chg_24h": chg,
            "funding_neg": -level,
        }

        results[symbol] = {"n_bars": int(n), "n_funding": int(len(fr))}
        print(f"--- {symbol} ({n} bars, {len(fr)} funding obs) ---")
        for name, raw in candidates.items():
            raw = raw.fillna(0.0).astype(float)
            raw.index = ts
            ic_daily = rolling_ic(raw, fwd_ret).resample("1D").mean().dropna()
            recent_90 = float(ic_daily.iloc[-90:].mean())
            prior_90 = float(ic_daily.iloc[-180:-90].mean())
            entry = {
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

    Path(PROJECT_ROOT / "scripts" / "funding_ic_sweep_2026-09-22.json").write_text(
        json.dumps(out, indent=2), encoding="utf-8"
    )
    print("saved -> scripts/funding_ic_sweep_2026-09-22.json")


if __name__ == "__main__":
    main()
