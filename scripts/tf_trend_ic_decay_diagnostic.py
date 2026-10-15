"""TF_TREND IC decay diagnostic (design review check, 2026-09-21).

Question: does the DECAYING signal from the earlier liveness audit still hold
on current data? Rolling forward IC of TF_TREND raw series vs forward returns,
monthly + overall trend. Cheap read-only probe — no backtest, no patching.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

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


def rolling_ic(raw: pd.Series, fwd_ret: pd.Series, window: int = 1440) -> pd.Series:
    """Rolling Spearman-ish IC via rank correlation over a trailing window."""
    r1 = raw.rank(pct=True)
    r2 = fwd_ret.rank(pct=True)
    prod = (r1 * r2).rolling(window, min_periods=window // 4).mean()
    m1 = r1.rolling(window, min_periods=window // 4).mean()
    m2 = r2.rolling(window, min_periods=window // 4).mean()
    s1 = (r1**2).rolling(window, min_periods=window // 4).mean() - m1**2
    s2 = (r2**2).rolling(window, min_periods=window // 4).mean() - m2**2
    denom = np.sqrt(s1 * s2)
    return ((prod - m1 * m2) / denom).replace([np.inf, -np.inf], np.nan)


def main() -> None:
    out: dict[str, Any] = {
        "date": "2026-09-21",
        "horizon_bars": 4,
        "ic_window_bars": 1440,
        "symbols": {},
    }
    for symbol in ("BTC/USDT", "ETH/USDT", "SOL/USDT"):
        df, n = load_multi_tf_df(DB_PATH, symbol, "binance", "15m", "1h", "futures")
        if df.empty:
            continue
        raw = pb.compute_tf_trend(df).fillna(0.0)
        fwd_ret = np.log(df["close"].shift(-4) / df["close"]).fillna(0.0)
        ts = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        raw.index = ts
        fwd_ret.index = ts

        ic = rolling_ic(raw, fwd_ret)
        ic_daily = ic.resample("1D").mean().dropna()

        # monthly means + recent trend
        monthly = ic_daily.resample("ME").agg(["mean", "std", "count"])
        recent_90 = ic_daily.iloc[-90:].mean()
        prior_90 = ic_daily.iloc[-180:-90].mean()
        out["symbols"][symbol] = {
            "n_bars": int(n),
            "overall_mean_ic": float(ic_daily.mean()),
            "recent_90d_mean_ic": float(recent_90),
            "prior_90d_mean_ic": float(prior_90),
            "trend_90d_vs_prior": float(recent_90 - prior_90),
            "monthly": [
                {
                    "month": str(idx.strftime("%Y-%m")),
                    "mean_ic": float(row["mean"]),
                    "std": float(row["std"]),
                    "days": int(row["count"]),
                }
                for idx, row in monthly.iterrows()
            ],
        }
        print(
            f"{symbol}: overall IC {ic_daily.mean():+.4f} | last90d {recent_90:+.4f} "
            f"| prior90d {prior_90:+.4f} | trend {recent_90 - prior_90:+.4f}"
        )
        for m in out["symbols"][symbol]["monthly"]:
            print(f"    {m['month']}: {m['mean_ic']:+.4f}")

    Path(
        PROJECT_ROOT / "scripts" / "tf_trend_ic_decay_diagnostic_2026-09-21.json"
    ).write_text(json.dumps(out, indent=2), encoding="utf-8")
    print("saved -> scripts/tf_trend_ic_decay_diagnostic_2026-09-21.json")


if __name__ == "__main__":
    main()
