"""Run factor mining with REAL multi-symbol CEX data.

No synthetic noise — BTC+ETH+SOL real K-lines as tokens.
11 real features (no LIQ_SCORE). Full backtest after mining.

Usage:
    python scripts/run_alpha_mining_real.py
    python scripts/run_alpha_mining_real.py --interval 1d --steps 300
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import torch

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))

from selene_space_alpha.config import AlphaCoreConfig
from selene_space_alpha.real_data_loader import RealMultiSymbolLoader, CEX_FEATURE_NAMES
from selene_space_alpha.mining.engine import AlphaEngine
from selene_space_alpha.mining.model import FormulaPolicyNet
from selene_space_alpha.strategy_bridge import AlphaFormulaStrategy
from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.ops.ops import OPS_CONFIG

from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent


def run_backtest(formula_tokens, vocab, symbol, exchange, interval, db_path,
                 initial_capital=10000, fee_rate=0.0004, long_only=True):
    """Run full backtest with BacktestStrategyTemplate."""

    class AlphaBacktestStrategy(BacktestStrategyTemplate):
        def __init__(self, ft, v, **kwargs):
            super().__init__(**kwargs)
            self._alpha = AlphaFormulaStrategy(formula_tokens=ft, vocab=v)

        def get_signal(self, data):
            sig = self._alpha.get_signal(data)
            if sig == "BUY":
                return SignalType.BUY
            elif sig == "SELL":
                return SignalType.SELL
            return SignalType.HOLD

    conn = sqlite3.connect(str(db_path))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? ORDER BY timestamp ASC",
        conn, params=(symbol, exchange, interval),
    )
    conn.close()

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    events = []
    for _, row in df.iterrows():
        events.append(KlineEvent(
            exchange=exchange, symbol=symbol, interval=interval,
            timestamp=int(row["timestamp"]),
            open=float(row["open"]), high=float(row["high"]),
            low=float(row["low"]), close=float(row["close"]),
            volume=float(row["volume"]), closed=True,
        ))

    strategy = AlphaBacktestStrategy(
        formula_tokens, vocab,
        initial_capital=initial_capital,
        fee_rate=fee_rate,
        long_only=long_only,
        max_bars=500,
    )
    result = strategy.run_backtest(events)
    return result, len(df)


def main():
    parser = argparse.ArgumentParser(description="Factor Mining with Real CEX Data")
    parser.add_argument("--db", default="services/local-api/data/klines.db")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    parser.add_argument("--intervals", nargs="+", default=["4h", "1d"])
    parser.add_argument("--steps", type=int, default=200)
    parser.add_argument("--batch-size", type=int, default=512)
    parser.add_argument("--max-formula-len", type=int, default=12)
    parser.add_argument("--limit-bars", type=int, default=500)
    parser.add_argument("--output-dir", default="data/alpha_mining_real")
    parser.add_argument("--no-lord", action="store_true")
    args = parser.parse_args()

    db_path = PROJECT_ROOT / args.db
    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Build custom vocab with 11 real features (no LIQ_SCORE)
    custom_vocab = FormulaVocab(
        feature_names=CEX_FEATURE_NAMES,
        operator_names=tuple(cfg[0] for cfg in OPS_CONFIG),
    )

    print("=" * 60)
    print("Factor Mining — REAL Multi-Symbol CEX Data")
    print("=" * 60)
    print(f"  Symbols: {args.symbols}")
    print(f"  Intervals: {args.intervals}")
    print(f"  Features: {len(custom_vocab.feature_names)} real ({', '.join(custom_vocab.feature_names)})")
    print(f"  Operators: {len(custom_vocab.operator_names)} ({', '.join(custom_vocab.operator_names)})")
    print(f"  Vocab size: {custom_vocab.size}")
    print(f"  Steps: {args.steps} | Batch: {args.batch_size}")
    print(f"  Device: {'CUDA' if AlphaCoreConfig.DEVICE.type == 'cuda' else 'CPU'}")
    print()

    # Load real data
    print("[1/3] Loading real K-line data...")
    loader = RealMultiSymbolLoader()
    loader.load_from_sqlite(
        db_path=str(db_path),
        symbols=args.symbols,
        intervals=args.intervals,
        limit_bars=args.limit_bars,
    )
    print(f"  feat_tensor: {loader.feat_tensor.shape}")
    print(f"  target_ret:  {loader.target_ret.shape}")
    print(f"  tokens (symbols): {loader.addresses}")
    print()

    # Monkey-patch the vocab in the model and engine
    import selene_space_alpha.vocab as vocab_mod
    vocab_mod.FORMULA_VOCAB = custom_vocab
    import selene_space_alpha.vm.vm as vm_mod

    # Config
    config = AlphaCoreConfig()
    config.TRAIN_STEPS = args.steps
    config.BATCH_SIZE = args.batch_size
    config.MAX_FORMULA_LEN = args.max_formula_len

    # Run mining
    print("[2/3] Mining alpha formulas...")
    engine = AlphaEngine(
        data_loader=loader,
        config=config,
        use_lord_regularization=not args.no_lord,
        output_dir=str(output_dir),
    )
    # Replace model's vocab with custom vocab
    engine.model = FormulaPolicyNet(config).to(config.DEVICE)
    engine.opt = torch.optim.AdamW(engine.model.parameters(), lr=1e-3)  # noqa
    engine.vm = vm_mod.StackVM(custom_vocab)

    engine.train()
    print()

    if engine.best_formula is None:
        print("No valid formula discovered. Try more steps.")
        return

    formula_readable = custom_vocab.decode(engine.best_formula)
    print(f"Best formula: {formula_readable}")
    print(f"Best score: {engine.best_score:.4f}")
    print()

    # Full backtest on each symbol × interval combo
    print("[3/3] Full backtest on real data...")
    print()
    print(f"{'Symbol':<12} {'Interval':<8} {'Bars':<6} {'Return':<10} {'Ann.Ret':<10} {'Sharpe':<8} {'MaxDD':<10} {'Trades':<7} {'WinRate':<8} {'PF':<8} {'Expect':<10}")
    print("-" * 100)

    all_results = {}
    for symbol in args.symbols:
        for interval in args.intervals:
            key = f"{symbol}:{interval}"
            bt_result, n_bars = run_backtest(
                engine.best_formula, custom_vocab,
                symbol=symbol, exchange="binance", interval=interval,
                db_path=db_path, initial_capital=10000, fee_rate=0.0004,
            )
            r = bt_result
            print(f"{symbol:<12} {interval:<8} {n_bars:<6} {r.total_return:>8.2%} {r.annual_return:>8.2%} {r.sharpe_ratio:>6.3f} {r.max_drawdown:>8.2%} {r.total_trades:<7} {r.win_rate:>6.1%} {r.profit_factor:>6.2f} {r.expectancy:>8.1f}")
            all_results[key] = r

    # Save results
    result_data = {
        "symbols": args.symbols,
        "intervals": args.intervals,
        "best_formula_tokens": engine.best_formula,
        "best_formula_readable": formula_readable,
        "best_score": engine.best_score,
        "vocab_features": list(custom_vocab.feature_names),
        "vocab_operators": list(custom_vocab.operator_names),
        "backtest_results": {
            sym: {
                "total_return": r.total_return,
                "annual_return": r.annual_return,
                "sharpe_ratio": r.sharpe_ratio,
                "max_drawdown": r.max_drawdown,
                "total_trades": r.total_trades,
                "win_rate": r.win_rate,
                "profit_factor": r.profit_factor,
                "expectancy": r.expectancy,
            }
            for sym, r in all_results.items()
        },
    }

    with open(output_dir / "mining_result_real.json", "w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)

    print()
    print(f"Result saved to: {output_dir / 'mining_result_real.json'}")

    # Formula breakdown
    print()
    print("Formula breakdown:")
    for i, token in enumerate(engine.best_formula):
        if token < custom_vocab.feature_count:
            kind = "FEATURE"
        else:
            kind = "OPERATOR"
        name = custom_vocab.token_names[token] if token < custom_vocab.size else f"UNK({token})"
        print(f"  [{i}] token={token} -> {kind}: {name}")


if __name__ == "__main__":
    main()
