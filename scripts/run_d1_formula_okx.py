"""Run D1-tuned Optuna V3 formulas on OKX 19-symbol D1 data.

Evaluates the D1-discovered formulas:
  - LOG_VOL RET TS_MEAN ADD (OOS Sharpe 0.63)
  - LOG_VOL ABS TS_MEAN RANK (OOS Sharpe 0.61)

Usage::

    call activate.bat
    python scripts/run_d1_formula_okx.py
"""

from __future__ import annotations

import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packages" / "alpha-core"))

from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.vm.vm_pandas import PandasStackVM

# D1-tuned formulas from data/optuna_formula_v3_d1/result.json
D1_FORMULAS = [
    {
        "name": "LOG_VOL RET TS_MEAN ADD",
        "tokens": [4, 0, 23, 11],
        "buy_th": 0.64,
        "sell_th": 0.30,
        "stop_loss": 0.09,
        "position_size": 0.40,
    },
    {
        "name": "LOG_VOL ABS TS_MEAN RANK",
        "tokens": [4, 16, 23, 27],
        "buy_th": 0.64,
        "sell_th": 0.30,
        "stop_loss": 0.09,
        "position_size": 0.40,
    },
]

V3_FEATURES = (
    "RET", "PRESSURE", "FOMO", "DEV", "LOG_VOL",
    "VOL_CLUSTER", "MOMENTUM_REV", "REL_STRENGTH",
    "HL_RANGE", "CLOSE_POS", "VOL_TREND",
)
V3_OPERATORS = (
    "ADD", "SUB", "MUL", "DIV", "NEG", "ABS", "SIGN",
    "JUMP", "DECAY", "DELAY1", "MAX3", "MEAN2",
    "TS_MEAN", "TS_STD", "TS_RANK", "TS_CORR",
    "RANK", "TS_DELTA", "TS_MAX", "TS_MIN", "IND_NEUTRALIZE",
)

DB_PATH = Path("data/klines.db")
OUTPUT_PATH = Path("data/okx_backtest_results") / "d1_formula_okx.json"


def make_d1_vocab() -> FormulaVocab:
    return FormulaVocab(feature_names=V3_FEATURES, operator_names=V3_OPERATORS)


def load_klines(db_path: str, exchange: str, symbol: str, interval: str) -> pd.DataFrame:
    conn = sqlite3.connect(db_path)
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume "
        "FROM klines WHERE exchange=? AND symbol=? AND interval=? "
        "ORDER BY timestamp ASC",
        conn, params=(exchange, symbol, interval),
    )
    conn.close()
    if df.empty:
        return df
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def backtest_formula(
    tokens: list[int],
    vocab: FormulaVocab,
    df: pd.DataFrame,
    buy_th: float,
    sell_th: float,
    stop_loss: float,
    position_size: float,
    initial_capital: float = 10_000.0,
    fee_rate: float = 0.0004,
) -> dict:
    vm = PandasStackVM(vocab)
    score_series = vm.execute(tokens, df)
    if score_series is None:
        return {"sharpe": 0, "total_return": 0, "max_drawdown": 0, "win_rate": 0,
                "profit_factor": 0, "total_trades": 0}

    sigmoid = 1.0 / (1.0 + np.exp(-score_series.values.clip(-500, 500)))
    signals = np.where(sigmoid > buy_th, 1, np.where(sigmoid < sell_th, -1, 0))

    closes = df["close"].values.astype(float)
    lows = df["low"].values.astype(float)
    position = 0
    entry_price = 0.0
    position_capital = 0.0
    capital = initial_capital
    equity_curve = [initial_capital]
    trades = []

    for i in range(1, len(closes)):
        sig = signals[i - 1]
        price = closes[i]
        low = lows[i]

        if position == 0 and sig == 1:
            position = 1
            entry_price = price
            position_capital = capital * position_size
        elif position == 1:
            unrealized_pct = (low - entry_price) / entry_price
            stopped_out = stop_loss > 0 and unrealized_pct <= -stop_loss

            if stopped_out:
                stop_price = entry_price * (1 - stop_loss)
                pnl_pct = (stop_price - entry_price) / entry_price
                gross_pnl = position_capital * pnl_pct
                fee = position_capital * fee_rate * 2
                net_pnl = gross_pnl - fee
                capital += net_pnl
                trades.append({"pnl": net_pnl})
                position = 0
                entry_price = 0.0
                position_capital = 0.0
            elif sig == -1:
                pnl_pct = (price - entry_price) / entry_price
                gross_pnl = position_capital * pnl_pct
                fee = position_capital * fee_rate * 2
                net_pnl = gross_pnl - fee
                capital += net_pnl
                trades.append({"pnl": net_pnl})
                position = 0
                entry_price = 0.0
                position_capital = 0.0

        if position == 1:
            equity = capital - position_capital + position_capital * (1 + (price - entry_price) / entry_price)
        else:
            equity = capital
        equity_curve.append(equity)

    n = len(equity_curve)
    if n < 2:
        return {"sharpe": 0, "total_return": 0, "max_drawdown": 0, "win_rate": 0,
                "profit_factor": 0, "total_trades": len(trades)}

    total_return = (equity_curve[-1] - initial_capital) / initial_capital

    peak = initial_capital
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak
        if dd > max_dd:
            max_dd = dd

    returns = pd.Series(equity_curve).pct_change().dropna()
    sharpe = float(returns.mean() / returns.std() * (365 ** 0.5)) if returns.std() > 1e-10 else 0.0

    win_rate = 0.0
    profit_factor = 0.0
    if trades:
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        win_rate = len(wins) / len(trades)
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    return {
        "sharpe": _safe_float(sharpe),
        "total_return": _safe_float(total_return),
        "max_drawdown": _safe_float(-max_dd),
        "win_rate": _safe_float(win_rate),
        "profit_factor": _safe_float(profit_factor),
        "total_trades": len(trades),
    }


def _safe_float(val, default=0.0) -> float:
    try:
        v = float(val)
        if v != v or v == float("inf") or v == float("-inf"):
            return default
        return v
    except (TypeError, ValueError):
        return default


def main() -> None:
    vocab = make_d1_vocab()

    conn = sqlite3.connect(str(DB_PATH))
    partitions = conn.execute(
        "SELECT DISTINCT exchange, symbol, interval, COUNT(*) as cnt "
        "FROM klines WHERE interval='D1' GROUP BY 1,2,3 ORDER BY 2"
    ).fetchall()
    conn.close()

    all_results = []

    for formula in D1_FORMULAS:
        print(f"\n=== {formula['name']} ===")
        print(f"    buy_th={formula['buy_th']}, sell_th={formula['sell_th']}, "
              f"sl={formula['stop_loss']}, pos={formula['position_size']}\n")

        for exchange, symbol, interval, row_count in partitions:
            t0 = time.perf_counter()
            df = load_klines(str(DB_PATH), exchange, symbol, interval)
            if df.empty:
                continue

            result = backtest_formula(
                tokens=formula["tokens"],
                vocab=vocab,
                df=df,
                buy_th=formula["buy_th"],
                sell_th=formula["sell_th"],
                stop_loss=formula["stop_loss"],
                position_size=formula["position_size"],
            )
            elapsed = time.perf_counter() - t0

            entry = {
                "formula": formula["name"],
                "symbol": symbol,
                "interval": interval,
                "bars": len(df),
                **result,
            }
            all_results.append(entry)

            print(
                f"  {symbol:12s} {interval:4s}  S={result['sharpe']:6.2f}  "
                f"Ret={result['total_return']:7.1%}  DD={result['max_drawdown']:7.1%}  "
                f"WR={result['win_rate']:5.1%}  PF={result['profit_factor']:5.2f}  "
                f"T={result['total_trades']:3d}  ({elapsed:.1f}s)"
            )

    # Summary per formula
    print(f"\n{'='*70}")
    print("SUMMARY")
    print(f"{'='*70}")
    for formula in D1_FORMULAS:
        name = formula["name"]
        results = [r for r in all_results if r["formula"] == name]
        sharpes = [r["sharpe"] for r in results if r["total_trades"] >= 3]
        profitable = sum(1 for r in results if r["total_return"] > 0)
        avg_sharpe = sum(sharpes) / len(sharpes) if sharpes else 0.0
        avg_dd = sum(r["max_drawdown"] for r in results) / len(results) if results else 0.0
        print(f"  {name:30s}  Profitable={profitable}/{len(results)}  "
              f"Avg Sharpe={avg_sharpe:.4f}  Avg DD={avg_dd:.1%}")

    output = {
        "formulas": D1_FORMULAS,
        "generated": datetime.now(timezone.utc).isoformat(),
        "results": all_results,
    }
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\nSaved → {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
