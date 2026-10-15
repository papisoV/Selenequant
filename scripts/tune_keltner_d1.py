"""Tune Keltner V2 per symbol on OKX D1 data via Optuna Bayesian optimization.

Usage::

    call activate.bat
    python scripts/tune_keltner_d1.py
    python scripts/tune_keltner_d1.py --symbols BTC/USDT ETH/USDT
    python scripts/tune_keltner_d1.py --trials 30  # quick test
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from selene_space_strategy.optimizer import OptimizationResult, StrategyOptimizer
from selene_space_strategy.templates.presets.keltner_v2 import KeltnerChannelV2Strategy

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = Path("data/klines.db")
OUTPUT_DIR = Path("data/keltner_d1_tuning")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

PARAM_SPACE = {
    "ma_length": (10, 40),
    "atr_length": (10, 30),
    "mult": (1.2, 3.5),
    "adx_length": (10, 21),
    "adx_threshold": (15.0, 40.0),
    "stop_pct": (0.02, 0.08),
    "tp_pct": (0.05, 0.20),
}

DEFAULT_TRIALS = 50


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Tune Keltner V2 per symbol on OKX D1")
    p.add_argument("--symbols", nargs="+", help="Symbols to tune (default: all)")
    p.add_argument("--trials", type=int, default=DEFAULT_TRIALS)
    p.add_argument("--db", default=str(DB_PATH))
    p.add_argument("--output-dir", default=str(OUTPUT_DIR))
    return p.parse_args()


def load_klines_events(db_path: str, exchange: str, symbol: str, interval: str) -> list:
    """Load klines as KlineEvent list for StrategyOptimizer."""
    from selene_space_types.market import KlineEvent

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

    return [
        KlineEvent(
            exchange=exchange, symbol=symbol, interval=interval,
            timestamp=int(r["timestamp"]), open=float(r["open"]),
            high=float(r["high"]), low=float(r["low"]),
            close=float(r["close"]), volume=float(r["volume"]),
            closed=True,
        )
        for _, r in df.iterrows()
    ]


def tune_symbol(
    symbol: str,
    events: list,
    n_trials: int,
) -> dict:
    """Run Bayesian optimization for one symbol."""
    optimizer = StrategyOptimizer(
        strategy_class=KeltnerChannelV2Strategy,
        objective_metric="sharpe_ratio",
        initial_capital=10_000.0,
        fee_rate=0.0005,
        long_only=False,  # allow short for Keltner V2
    )

    result: OptimizationResult = optimizer.bayesian_optimize(
        data=events,
        param_space=PARAM_SPACE,
        symbol=symbol,
        n_trials=n_trials,
    )

    metrics = {}
    if result.best_result is not None:
        br = result.best_result
        metrics = {
            "sharpe": _safe(br.sharpe_ratio),
            "total_return": _safe(br.total_return),
            "max_drawdown": _safe(br.max_drawdown),
            "win_rate": _safe(br.win_rate),
            "profit_factor": _safe(br.profit_factor),
            "total_trades": br.total_trades,
            "calmar": _safe(br.calmar_ratio),
        }

    return {
        "symbol": symbol,
        "best_params": result.best_params,
        "best_sharpe": result.best_value,
        "n_trials": result.n_trials,
        "optimization_time": result.optimization_time,
        **metrics,
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
    params: tuple = ()
    if args.symbols:
        placeholders = ",".join("?" for _ in args.symbols)
        query += f"AND symbol IN ({placeholders}) "
        params = tuple(args.symbols)
    query += "GROUP BY 1,2,3 ORDER BY 2"
    partitions = conn.execute(query, params).fetchall()
    conn.close()

    print(f"Keltner V2 D1 Tuning — {len(partitions)} symbols, {args.trials} trials each\n")

    all_results = []
    for exchange, symbol, interval, row_count in partitions:
        t0 = time.perf_counter()
        events = load_klines_events(args.db, exchange, symbol, interval)
        if len(events) < 200:
            print(f"  SKIP {symbol}: only {len(events)} bars")
            continue

        result = tune_symbol(symbol, events, args.trials)
        elapsed = time.perf_counter() - t0

        print(
            f"  {symbol:12s}  S={result['best_sharpe']:6.2f}  "
            f"Ret={result.get('total_return', 0):7.1%}  "
            f"DD={result.get('max_drawdown', 0):7.1%}  "
            f"T={result.get('total_trades', 0):3d}  "
            f"({elapsed:.0f}s)  params={result['best_params']}"
        )
        all_results.append(result)

    # Summary
    sharpes = [r["best_sharpe"] for r in all_results if r.get("total_trades", 0) >= 3]
    avg_sharpe = sum(sharpes) / len(sharpes) if sharpes else 0.0
    print("\n--- Summary ---")
    print(f"Tuned symbols: {len(all_results)}")
    print(f"Avg best Sharpe: {avg_sharpe:.4f}")
    print(f"Best symbol: {max(all_results, key=lambda r: r['best_sharpe'])['symbol']} "
          f"(Sharpe {max(r['best_sharpe'] for r in all_results):.2f})")

    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    output_path = Path(args.output_dir) / f"keltner_d1_tuning_{ts}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({
            "param_space": {k: list(v) for k, v in PARAM_SPACE.items()},
            "n_trials_per_symbol": args.trials,
            "results": all_results,
            "summary": {"avg_sharpe": avg_sharpe, "n_symbols": len(all_results)},
        }, f, indent=2, ensure_ascii=False)

    print(f"Saved → {output_path}")


if __name__ == "__main__":
    main()
