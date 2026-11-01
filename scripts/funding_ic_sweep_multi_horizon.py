"""Funding-rate factor IC sweep — multi-horizon extension (2026-09-25).

The 2026-09-22 sweep scored funding candidates against the 4-bar (1h)
forward return and found |IC|<=0.02 for the whole family. Caveat recorded
then: funding is a slow variable (8h cadence, multi-day crowding state) and
a 1h horizon may be the wrong ruler. This script re-runs the identical
candidates against 1d/3d/7d forward horizons.

Gate meaning: if 7d still shows |IC|~0, the "slow variable carries
information for this time-series strategy" hypothesis is closed for funding
(note: macro events are pulse variables — this result does NOT transfer).

Candidates: identical to funding_ic_sweep.py (funding_level/z/chg_24h/neg),
TF_TREND as in-harness benchmark.
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

# 单一解析来源(同 kline_paths);旧路径指向已废弃的 services/local-api/data 分支。
DB_PATH = default_klines_db_path()
DERIV_PATH = default_derivatives_db_path()

IC_WINDOW = 1440
# 15m bars: 96=1d, 288=3d, 672=7d (4-bar 1h baseline comes from the 0922 run)
HORIZONS = {"1d": 96, "3d": 288, "7d": 672}
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
        "date": "2026-09-25",
        "horizons_bars": HORIZONS,
        "ic_window_bars": IC_WINDOW,
        "note": "4-bar 1h baseline = funding_ic_sweep_2026-09-22.json",
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

        ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        bench = pb.compute_tf_trend(df).fillna(0.0)
        bench.index = ts

        candidates = {
            "TF_TREND(benchmark)": bench,
            "funding_level": level,
            "funding_z": z,
            "funding_chg_24h": chg,
            "funding_neg": -level,
        }
        for name in candidates:
            candidates[name] = candidates[name].fillna(0.0).astype(float)
            candidates[name].index = ts

        results[symbol] = {"n_bars": int(n), "n_funding": int(len(fr))}
        per_factor: dict[str, dict[str, dict[str, object]]] = {}
        print(f"--- {symbol} ({n} bars, {len(fr)} funding obs) ---")
        for h_name, h in HORIZONS.items():
            # trailing NaN tail (shift(-h)) must be dropped, not zero-filled:
            # zero forward return would fake a zero-IC zone at the series end
            fwd_ret = np.log(df["close"].shift(-h) / df["close"])
            fwd_ret = fwd_ret.iloc[:-h].dropna()
            fwd_ret.index = ts[fwd_ret.index]
            for name, raw in candidates.items():
                raw_h = raw.loc[fwd_ret.index]
                ic_daily = rolling_ic(raw_h, fwd_ret).resample("1D").mean().dropna()
                entry: dict[str, object] = {
                    "overall_mean_ic": float(ic_daily.mean()),
                    "recent_90d_mean_ic": float(ic_daily.iloc[-90:].mean()),
                    "n_ic_days": int(len(ic_daily)),
                }
                per_factor.setdefault(name, {})[h_name] = entry
                print(
                    f"  [{h_name}] {name:22s} IC {entry['overall_mean_ic']:+.4f} "
                    f"({entry['n_ic_days']} ic-days)"
                )
        results[symbol].update(per_factor)

    Path(
        PROJECT_ROOT / "scripts" / "funding_ic_sweep_multi_horizon_2026-09-25.json"
    ).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("saved -> scripts/funding_ic_sweep_multi_horizon_2026-09-25.json")


if __name__ == "__main__":
    main()
