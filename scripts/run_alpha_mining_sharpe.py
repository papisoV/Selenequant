"""Factor mining with real backtest fitness.

Each formula is evaluated by running BacktestStrategyTemplate on
BTC/ETH/SOL real K-lines. REINFORCE directly optimizes Sharpe ratio.

Usage:
    python scripts/run_alpha_mining_sharpe.py
    python scripts/run_alpha_mining_sharpe.py --steps 100 --batch-size 32
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))

from selene_space_alpha.config import AlphaCoreConfig
from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES
from selene_space_alpha.mining.real_engine import RealFitnessAlphaEngine
from selene_space_alpha.mining.real_fitness import RealBacktestFitness
from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.ops.ops import OPS_CONFIG
from selene_space_alpha.strategy_bridge import AlphaFormulaStrategy

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent


def run_backtest(formula_tokens, vocab, symbol, exchange, interval, db_path,
                 initial_capital=10000, fee_rate=0.0004):
    class S(BacktestStrategyTemplate):
        def __init__(self, ft, v, **kw):
            super().__init__(**kw)
            self._alpha = AlphaFormulaStrategy(formula_tokens=ft, vocab=v)
        def get_signal(self, data):
            sig = self._alpha.get_signal(data)
            if sig == "BUY": return SignalType.BUY
            elif sig == "SELL": return SignalType.SELL
            return SignalType.HOLD

    conn = sqlite3.connect(str(db_path))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? ORDER BY timestamp ASC",
        conn, params=(symbol, exchange, interval))
    conn.close()
    for col in ["open","high","low","close","volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    events = [KlineEvent(exchange=exchange, symbol=symbol, interval=interval,
                         timestamp=int(r["timestamp"]), open=float(r["open"]),
                         high=float(r["high"]), low=float(r["low"]),
                         close=float(r["close"]), volume=float(r["volume"]), closed=True)
              for _, r in df.iterrows()]

    s = S(formula_tokens, vocab, initial_capital=initial_capital, fee_rate=fee_rate, long_only=True, max_bars=500)
    return s.run_backtest(events), len(df)


def main():
    parser = argparse.ArgumentParser(description="Factor Mining with Real Backtest Fitness")
    parser.add_argument("--db", default="services/local-api/data/klines.db")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    parser.add_argument("--intervals", nargs="+", default=["4h"])
    parser.add_argument("--steps", type=int, default=50)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--max-formula-len", type=int, default=12)
    parser.add_argument("--output-dir", default="data/alpha_mining_sharpe")
    parser.add_argument("--no-lord", action="store_true")
    args = parser.parse_args()

    db_path = PROJECT_ROOT / args.db
    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    custom_vocab = FormulaVocab(
        feature_names=CEX_FEATURE_NAMES,
        operator_names=tuple(cfg[0] for cfg in OPS_CONFIG),
    )

    print("=" * 60)
    print("Factor Mining — Real Backtest Fitness (Sharpe)")
    print("=" * 60)
    print(f"  Symbols: {args.symbols}")
    print(f"  Intervals: {args.intervals}")
    print(f"  Features: {len(custom_vocab.feature_names)} ({', '.join(custom_vocab.feature_names)})")
    print(f"  Vocab size: {custom_vocab.size}")
    print(f"  Steps: {args.steps} | Batch: {args.batch_size}")
    print("  Fitness: Real Backtest (Sharpe + WR + DD penalty)")
    print()

    # Build fitness evaluator (pre-loads all K-line data)
    print("[1/3] Loading K-line data for fitness evaluation...")
    fitness = RealBacktestFitness(
        db_path=str(db_path),
        symbols=args.symbols,
        intervals=args.intervals,
        initial_capital=10000,
        fee_rate=0.0004,
    )
    for key, events in fitness._events_cache.items():
        print(f"  {key}: {len(events)} bars")
    print()

    # Config
    config = AlphaCoreConfig()
    config.TRAIN_STEPS = args.steps
    config.BATCH_SIZE = args.batch_size
    config.MAX_FORMULA_LEN = args.max_formula_len

    # Run mining
    print("[2/3] Mining formulas (each evaluated by real backtest)...")
    engine = RealFitnessAlphaEngine(
        vocab=custom_vocab,
        fitness=fitness,
        config=config,
        use_lord_regularization=not args.no_lord,
        output_dir=str(output_dir),
    )
    engine.train()
    print()

    if engine.best_formula is None:
        print("No valid formula discovered.")
        return

    formula_readable = custom_vocab.decode(engine.best_formula)
    print(f"Best formula: {formula_readable}")
    print(f"Best fitness: {engine.best_score:.4f}")
    print()

    # Full backtest
    print("[3/3] Full backtest on real data...")
    print()
    print(f"{'Symbol':<12} {'Interval':<8} {'Bars':<6} {'Return':<10} {'Ann.Ret':<10} {'Sharpe':<8} {'MaxDD':<10} {'Trades':<7} {'WinRate':<8} {'PF':<8}")
    print("-" * 90)

    all_results = {}
    for sym in args.symbols:
        for iv in args.intervals:
            r, n = run_backtest(engine.best_formula, custom_vocab, sym, "binance", iv, db_path)
            print(f"{sym:<12} {iv:<8} {n:<6} {r.total_return:>8.2%} {r.annual_return:>8.2%} {r.sharpe_ratio:>6.3f} {r.max_drawdown:>8.2%} {r.total_trades:<7} {r.win_rate:>6.1%} {r.profit_factor:>6.2f}")
            all_results[f"{sym}:{iv}"] = r

    # Save
    result_data = {
        "best_formula_tokens": engine.best_formula,
        "best_formula_readable": formula_readable,
        "best_fitness": engine.best_score,
        "vocab_features": list(custom_vocab.feature_names),
        "backtest": {
            k: {"return": r.total_return, "sharpe": r.sharpe_ratio,
                "max_dd": r.max_drawdown, "trades": r.total_trades,
                "win_rate": r.win_rate, "pf": r.profit_factor}
            for k, r in all_results.items()
        },
    }
    with open(output_dir / "result.json", "w") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)

    print()
    print("Formula breakdown:")
    for i, token in enumerate(engine.best_formula):
        kind = "FEATURE" if token < custom_vocab.feature_count else "OPERATOR"
        name = custom_vocab.token_names[token] if token < custom_vocab.size else f"UNK({token})"
        print(f"  [{i}] {kind}: {name}")


if __name__ == "__main__":
    main()
