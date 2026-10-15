"""Walk-forward tune Keltner V2 per symbol on OKX D1.

Grid search over key parameter combinations with 3-fold walk-forward,
optimizing OOS Sharpe (not IS). Much more robust than single-shot Optuna.

Usage::

    call activate.bat
    python scripts/tune_keltner_wf.py
"""

from __future__ import annotations

import argparse
import itertools
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selene_space_backtest.engine import BacktestEngine, BacktestConfig
from selene_space_strategy.templates.presets.keltner_v2 import KeltnerChannelV2Strategy

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = Path("data/klines.db")
OUTPUT_DIR = Path("data/keltner_d1_wf_tuning")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# Parameter grid — focused on the most impactful params
PARAM_GRID = {
    "ma_length": [20, 30],
    "atr_length": [20],
    "mult": [1.5, 2.0, 2.5, 3.0],
    "adx_length": [14],
    "adx_threshold": [20.0, 25.0, 30.0, 35.0],
    "stop_pct": [0.04],
    "tp_pct": [0.08, 0.12],
}

N_FOLDS = 3


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Walk-forward tune Keltner V2 per symbol")
    p.add_argument("--symbols", nargs="+", help="Symbols to tune (default: all)")
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return p.parse_args()


def load_df(db_path: str, exchange: str, symbol: str, interval: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM klines WHERE exchange=? AND symbol=? AND interval=? "
        "ORDER BY timestamp ASC",
        conn, params=(exchange, symbol, interval),
    )
    conn.close()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["exchange"] = exchange
    df["symbol"] = symbol
    df["interval"] = interval
    df["closed"] = True
    return df


def backtest_params(params: dict, df: pd.DataFrame, symbol: str) -> dict:
    """Run one backtest with given Keltner V2 params."""
    strategy = KeltnerChannelV2Strategy(symbol=symbol, **params)
    config = BacktestConfig(strategy_id="keltner_wf")
    engine = BacktestEngine(config)
    result = engine.run(strategy=strategy, data=df)
    return {
        "sharpe": _safe(result.metrics.sharpe_ratio),
        "total_return": _safe(result.metrics.total_return),
        "max_drawdown": _safe(result.metrics.max_drawdown),
        "win_rate": _safe(result.metrics.win_rate),
        "profit_factor": _safe(result.metrics.profit_factor),
        "total_trades": result.metrics.total_trades,
    }


def walk_forward_evaluate(params: dict, df: pd.DataFrame, symbol: str) -> dict:
    """3-fold walk-forward: optimize OOS Sharpe."""
    n = len(df)
    fold_size = n // N_FOLDS
    is_ratio = 0.6

    oos_sharpes = []
    is_sharpes = []

    for f in range(N_FOLDS):
        fold_start = f * fold_size
        fold_end = min((f + 1) * fold_size, n)
        fold_data = df.iloc[fold_start:fold_end]

        split = int(len(fold_data) * is_ratio)
        is_df = fold_data.iloc[:split]
        oos_df = fold_data.iloc[split:]

        if len(is_df) < 50 or len(oos_df) < 20:
            continue

        is_r = backtest_params(params, is_df, symbol)
        oos_r = backtest_params(params, oos_df, symbol)
        is_sharpes.append(is_r["sharpe"])
        oos_sharpes.append(oos_r["sharpe"])

    if not oos_sharpes:
        return {"avg_oos_sharpe": -999, "avg_is_sharpe": 0, "oos_sharpes": [], "is_sharpes": []}

    return {
        "avg_oos_sharpe": round(float(np.mean(oos_sharpes)), 4),
        "avg_is_sharpe": round(float(np.mean(is_sharpes)), 4),
        "oos_sharpes": [round(s, 4) for s in oos_sharpes],
        "is_sharpes": [round(s, 4) for s in is_sharpes],
    }


def _safe(val, default=0.0) -> float:
    try:
        v = float(val)
        if v != v or v == float("inf") or v == float("-inf"):
            return default
        return v
    except (TypeError, ValueError):
        return default


def main() -> None:
    args = parse_args()

    conn = sqlite3.connect(args.db)
    query = (
        "SELECT DISTINCT exchange, symbol, interval, COUNT(*) as cnt "
        "FROM klines WHERE interval='D1' "
    )
    params_tuple: tuple = ()
    if args.symbols:
        placeholders = ",".join("?" for _ in args.symbols)
        query += f"AND symbol IN ({placeholders}) "
        params_tuple = tuple(args.symbols)
    query += "GROUP BY 1,2,3 ORDER BY 2"
    partitions = conn.execute(query, params_tuple).fetchall()
    conn.close()

    # Generate all param combinations
    keys = list(PARAM_GRID)
    combinations = list(itertools.product(*PARAM_GRID.values()))
    total_combos = len(combinations)
    print("Walk-Forward Keltner V2 D1 Tuning")
    print(f"  Param grid: {total_combos} combinations × {N_FOLDS} folds")
    print(f"  Symbols: {len(partitions)}\n")

    all_results = []

    for exchange, symbol, interval, row_count in partitions:
        t0 = time.perf_counter()
        df = load_df(args.db, exchange, symbol, interval)
        if len(df) < 200:
            print(f"  SKIP {symbol}: only {len(df)} bars")
            continue

        best_oos = -999.0
        best_params = {}
        best_is = 0.0
        best_full = {}

        for combo in combinations:
            params = dict(zip(keys, combo))
            wf = walk_forward_evaluate(params, df, symbol)
            if wf["avg_oos_sharpe"] > best_oos:
                best_oos = wf["avg_oos_sharpe"]
                best_params = params
                best_is = wf["avg_is_sharpe"]

        # Full-sample backtest with best params
        best_full = backtest_params(best_params, df, symbol)
        elapsed = time.perf_counter() - t0

        entry = {
            "symbol": symbol,
            "best_params": best_params,
            "wf_avg_oos_sharpe": best_oos,
            "wf_avg_is_sharpe": best_is,
            "full_sharpe": best_full["sharpe"],
            "full_return": best_full["total_return"],
            "full_max_dd": best_full["max_drawdown"],
            "full_win_rate": best_full["win_rate"],
            "full_profit_factor": best_full["profit_factor"],
            "full_trades": best_full["total_trades"],
        }
        all_results.append(entry)

        print(
            f"  {symbol:12s}  WF_OOS={best_oos:6.2f}  "
            f"Full_S={best_full['sharpe']:6.2f}  "
            f"Ret={best_full['total_return']:7.1%}  "
            f"DD={best_full['max_drawdown']:7.1%}  "
            f"T={best_full['total_trades']:3d}  "
            f"({elapsed:.0f}s)  "
            f"ma={best_params['ma_length']} atr={best_params['atr_length']} "
            f"mult={best_params['mult']} adx_th={best_params['adx_threshold']}"
        )

    # Summary
    oos_positive = [r for r in all_results if r["wf_avg_oos_sharpe"] > 0]
    full_positive = [r for r in all_results if r["full_sharpe"] > 0]
    avg_oos = sum(r["wf_avg_oos_sharpe"] for r in all_results) / len(all_results) if all_results else 0

    print("\n--- Summary ---")
    print(f"WF OOS positive: {len(oos_positive)}/{len(all_results)}")
    print(f"Full Sharpe positive: {len(full_positive)}/{len(all_results)}")
    print(f"Avg WF OOS Sharpe: {avg_oos:.4f}")

    # Compare with default params
    print("\n--- Default vs Tuned ---")
    default_params = {"ma_length": 20, "atr_length": 20, "mult": 2.0, "adx_length": 14,
                      "adx_threshold": 25.0, "stop_pct": 0.04, "tp_pct": 0.10}
    print(f"{'Symbol':<12} {'Default_S':>10} {'Tuned_S':>10} {'Tuned_WF_OOS':>12} {'Best_Params'}")
    print("-" * 90)
    for r in all_results:
        df = load_df(args.db, "okx", r["symbol"], "D1")
        def_r = backtest_params(default_params, df, r["symbol"])
        print(
            f"{r['symbol']:<12} {def_r['sharpe']:10.2f} {r['full_sharpe']:10.2f} "
            f"{r['wf_avg_oos_sharpe']:12.2f}  "
            f"ma={r['best_params']['ma_length']} mult={r['best_params']['mult']} "
            f"adx_th={r['best_params']['adx_threshold']}"
        )

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output_dir) / f"keltner_wf_d1_{ts}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"param_grid": PARAM_GRID, "results": all_results}, f, indent=2, ensure_ascii=False)

    print(f"\nSaved → {output_path}")


if __name__ == "__main__":
    main()
