"""Systematic backtest runner — evaluate strategies on OKX K-line data.

Runs DCA Grid, Keltner V2, MA Extended Runs, EMA Triple Cross, P-Signal
across all symbols × intervals in klines.db. Outputs JSON results.

Usage::

    call activate.bat
    python scripts/backtest_okx_all.py
    python scripts/backtest_okx_all.py --interval D1 --strategy dca_grid
    python scripts/backtest_okx_all.py --interval D1 --walk-forward
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

from selene_space_backtest.engine import BacktestEngine, BacktestConfig
from selene_space_strategy.templates.presets.dca_grid import DCAGridStrategy
from selene_space_strategy.templates.presets.ema_triple_cross import EMATripleCrossStrategy
from selene_space_strategy.templates.presets.keltner_v2 import KeltnerChannelV2Strategy
from selene_space_strategy.templates.presets.ma_extended_runs import MAExtendedRunsStrategy
from selene_space_strategy.templates.presets.p_signal import PSignalStrategy

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

DB_PATH = Path("data/klines.db")
OUTPUT_DIR = Path("data/okx_backtest_results")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

STRATEGY_CLASSES = {
    "dca_grid": DCAGridStrategy,
    "keltner_v2": KeltnerChannelV2Strategy,
    "ma_extended_runs": MAExtendedRunsStrategy,
    "ema_triple_cross": EMATripleCrossStrategy,
    "p_signal": PSignalStrategy,
}

MIN_BARS = {"D1": 200, "H1": 500, "M15": 1000, "M5": 3000}
WALK_FORWARD_FOLDS = 3
WALK_FORWARD_SPLIT = 0.6  # 60% IS, 40% OOS per fold


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Systematic backtest runner for OKX data")
    p.add_argument("--interval", choices=["D1", "H1", "M15", "M5"], help="Single interval only")
    p.add_argument("--strategy", choices=list(STRATEGY_CLASSES), help="Single strategy only")
    p.add_argument("--symbol", help="Single symbol only (e.g. BTC/USDT)")
    p.add_argument("--walk-forward", action="store_true", help="Run walk-forward validation")
    p.add_argument("--db", default=str(DB_PATH), help="SQLite database path")
    p.add_argument("--output-dir", default=str(OUTPUT_DIR), help="Output directory for results")
    return p.parse_args()


def load_klines(
    db_path: str,
    exchange: str,
    symbol: str,
    interval: str,
) -> pd.DataFrame:
    """Load klines from SQLite into a DataFrame suitable for BacktestEngine."""
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM klines "
        "WHERE exchange = ? AND symbol = ? AND interval = ? "
        "ORDER BY timestamp ASC",
        conn,
        params=(exchange, symbol, interval),
    )
    conn.close()

    if df.empty:
        return df

    df["exchange"] = exchange
    df["symbol"] = symbol
    df["interval"] = interval
    df["closed"] = True
    return df


def run_single_backtest(
    strategy_instance,
    df: pd.DataFrame,
    strategy_id: str,
) -> dict:
    """Run one backtest and return metrics dict."""
    config = BacktestConfig(strategy_id=strategy_id)
    engine = BacktestEngine(config)
    result = engine.run(strategy=strategy_instance, data=df)

    return {
        "sharpe": _safe_float(result.metrics.sharpe_ratio),
        "total_return": _safe_float(result.metrics.total_return),
        "annual_return": _safe_float(result.metrics.annual_return),
        "max_drawdown": _safe_float(result.metrics.max_drawdown),
        "win_rate": _safe_float(result.metrics.win_rate),
        "profit_factor": _safe_float(result.metrics.profit_factor),
        "total_trades": result.metrics.total_trades,
        "calmar": _safe_float(result.metrics.calmar_ratio),
        "sortino": _safe_float(result.metrics.sortino_ratio),
        "dsr": _safe_float(result.metrics.dsr),
        "pbo": _safe_float(result.metrics.pbo),
        "haircut_sharpe": _safe_float(result.metrics.haircut_sharpe),
    }


def run_walk_forward(
    strategy_class,
    df: pd.DataFrame,
    symbol: str,
    strategy_id: str,
    n_folds: int = WALK_FORWARD_FOLDS,
) -> dict:
    """Walk-forward validation: sliding IS/OOS windows."""
    n = len(df)
    fold_size = n // n_folds
    is_pct = WALK_FORWARD_SPLIT

    folds = []
    for f in range(n_folds):
        fold_start = f * fold_size
        fold_end = min((f + 1) * fold_size, n)
        fold_data = df.iloc[fold_start:fold_end].copy()

        split = int(len(fold_data) * is_pct)
        is_df = fold_data.iloc[:split]
        oos_df = fold_data.iloc[split:]

        if len(is_df) < 50 or len(oos_df) < 20:
            continue

        is_instance = strategy_class(symbol=symbol)
        is_result = run_single_backtest(is_instance, is_df, strategy_id)
        oos_instance = strategy_class(symbol=symbol)
        oos_result = run_single_backtest(oos_instance, oos_df, strategy_id)

        folds.append({"is": is_result, "oos": oos_result})

    if not folds:
        return {"folds": [], "avg_is_sharpe": 0, "avg_oos_sharpe": 0}

    avg_is = sum(f["is"]["sharpe"] for f in folds) / len(folds)
    avg_oos = sum(f["oos"]["sharpe"] for f in folds) / len(folds)

    return {
        "folds": folds,
        "avg_is_sharpe": round(avg_is, 4),
        "avg_oos_sharpe": round(avg_oos, 4),
        "stability": round(avg_oos - avg_is, 4),
    }


def _safe_float(val, default=0.0) -> float:
    """Convert numpy types to native float, handling NaN/Inf."""
    try:
        v = float(val)
        if v != v or v == float("inf") or v == float("-inf"):
            return default
        return v
    except (TypeError, ValueError):
        return default


def main() -> None:
    args = parse_args()

    # Discover available data
    conn = sqlite3.connect(args.db)
    partitions = conn.execute(
        "SELECT DISTINCT exchange, symbol, interval, COUNT(*) as cnt "
        "FROM klines GROUP BY 1, 2, 3 ORDER BY 2, 3"
    ).fetchall()
    conn.close()

    strategies_to_run = [args.strategy] if args.strategy else list(STRATEGY_CLASSES)
    intervals_to_run = [args.interval] if args.interval else ["D1", "H1", "M15", "M5"]

    all_results = []
    total_runs = 0

    for strat_id in strategies_to_run:
        strategy_class = STRATEGY_CLASSES[strat_id]
        print(f"\n=== {strat_id} ===")

        for exchange, symbol, interval, row_count in partitions:
            if interval not in intervals_to_run:
                continue
            if args.symbol and symbol != args.symbol:
                continue
            if row_count < MIN_BARS.get(interval, 200):
                print(f"  SKIP {symbol} {interval} ({row_count} bars < min)")
                continue

            t0 = time.perf_counter()
            df = load_klines(args.db, exchange, symbol, interval)
            if df.empty:
                continue

            instance = strategy_class(symbol=symbol)
            result = run_single_backtest(instance, df, strat_id)
            elapsed = time.perf_counter() - t0

            entry = {
                "strategy": strat_id,
                "symbol": symbol,
                "interval": interval,
                "bars": len(df),
                **result,
            }

            if args.walk_forward:
                wf = run_walk_forward(strategy_class, df, symbol, strat_id)
                entry["walk_forward"] = wf
                oos_s = wf.get("avg_oos_sharpe", 0)
                print(
                    f"  {symbol:12s} {interval:4s}  S={result['sharpe']:6.2f}  "
                    f"Ret={result['total_return']:7.1%}  DD={result['max_drawdown']:7.1%}  "
                    f"T={result['total_trades']:3d}  WF_OOS={oos_s:6.2f}  ({elapsed:.1f}s)"
                )
            else:
                print(
                    f"  {symbol:12s} {interval:4s}  S={result['sharpe']:6.2f}  "
                    f"Ret={result['total_return']:7.1%}  DD={result['max_drawdown']:7.1%}  "
                    f"T={result['total_trades']:3d}  ({elapsed:.1f}s)"
                )

            all_results.append(entry)
            total_runs += 1

    # Save results
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    wf_suffix = "_wf" if args.walk_forward else ""
    output_path = Path(args.output_dir) / f"okx_backtest{wf_suffix}_{ts}.json"
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump({"total_runs": total_runs, "results": all_results}, f, indent=2, ensure_ascii=False)

    print(f"\n=== Done: {total_runs} backtests → {output_path} ===")


if __name__ == "__main__":
    main()
