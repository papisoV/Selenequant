"""Optuna-based formula search over the formula-policy space.

Uses Bayesian optimization (TPE) instead of REINFORCE to search for
profitable alpha formulas. Each trial = one formula → full backtest.
Much more sample-efficient than RL for this problem size.

Usage:
    python scripts/optuna_formula_search.py
    python scripts/optuna_formula_search.py --trials 200 --interval 4h
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import optuna

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))

from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.ops.ops import OPS_CONFIG
from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES
from selene_space_alpha.strategy_bridge import AlphaFormulaStrategy
from selene_space_strategy.template import BacktestStrategyTemplate, SignalType
from selene_space_types.market import KlineEvent


def load_events(db_path, symbol, exchange, interval):
    conn = sqlite3.connect(str(db_path))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? ORDER BY timestamp ASC",
        conn, params=(symbol, exchange, interval),
    )
    conn.close()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    events = [KlineEvent(
        exchange=exchange, symbol=symbol, interval=interval,
        timestamp=int(r["timestamp"]), open=float(r["open"]),
        high=float(r["high"]), low=float(r["low"]),
        close=float(r["close"]), volume=float(r["volume"]), closed=True,
    ) for _, r in df.iterrows()]
    return events, len(df)


def backtest_formula(tokens, vocab, events, buy_th=0.65, sell_th=0.35,
                     capital=10000, fee=0.0004):
    class S(BacktestStrategyTemplate):
        def __init__(self, ft, v, bt, st, **kw):
            super().__init__(**kw)
            self._alpha = AlphaFormulaStrategy(formula_tokens=ft, vocab=v, buy_threshold=bt, sell_threshold=st)
        def get_signal(self, data):
            sig = self._alpha.get_signal(data)
            if sig == "BUY": return SignalType.BUY
            elif sig == "SELL": return SignalType.SELL
            return SignalType.HOLD

    s = S(tokens, vocab, buy_th, sell_th, initial_capital=capital, fee_rate=fee, long_only=True, max_bars=500)
    return s.run_backtest(events)


def main():
    parser = argparse.ArgumentParser(description="Optuna Formula Search")
    parser.add_argument("--db", default="services/local-api/data/klines.db")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    parser.add_argument("--interval", default="4h")
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--formula-len", type=int, default=8)
    parser.add_argument("--output-dir", default="data/optuna_formula")
    args = parser.parse_args()

    db_path = PROJECT_ROOT / args.db
    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    vocab = FormulaVocab(
        feature_names=CEX_FEATURE_NAMES,
        operator_names=tuple(cfg[0] for cfg in OPS_CONFIG),
    )

    print("=" * 60)
    print("Optuna Formula Search (Bayesian Optimization)")
    print("=" *60)
    print(f"  Vocab: {vocab.size} tokens ({vocab.feature_count} features + {len(vocab.operator_names)} ops)")
    print(f"  Formula length: {args.formula_len}")
    print(f"  Search space: ~{vocab.size ** args.formula_len:.0e} combinations")
    print(f"  Trials: {args.trials}")
    print(f"  Symbols: {args.symbols} @ {args.interval}")
    print()

    # Pre-load events
    all_events = {}
    for sym in args.symbols:
        events, n = load_events(db_path, sym, "binance", args.interval)
        all_events[sym] = events
        print(f"  {sym}: {n} bars")

    def objective(trial):
        # Suggest each token in the formula
        tokens = []
        for i in range(args.formula_len):
            t = trial.suggest_int(f"t{i}", 0, vocab.size - 1)
            tokens.append(t)

        # Suggest buy/sell thresholds
        buy_th = trial.suggest_float("buy_th", 0.55, 0.75)
        sell_th = trial.suggest_float("sell_th", 0.25, 0.45)

        # Quick validity: check with PandasStackVM
        from selene_space_alpha.vm import PandasStackVM
        vm = PandasStackVM(vocab)
        test_df = pd.DataFrame({"open": [100], "high": [101], "low": [99], "close": [100.5], "volume": [1e6]})
        try:
            res = vm.execute(tokens, test_df)
            if res is None:
                return -10.0
        except Exception:
            return -10.0

        # Run backtest on each symbol
        sharpes = []
        returns = []
        max_dds = []
        win_rates = []
        trade_counts = []

        for sym, events in all_events.items():
            try:
                r = backtest_formula(tokens, vocab, events, buy_th, sell_th)
                if r.total_trades < 3:
                    sharpes.append(-2.0)
                    returns.append(-1.0)
                    continue
                sharpes.append(r.sharpe_ratio)
                returns.append(r.total_return)
                max_dds.append(r.max_drawdown)
                win_rates.append(r.win_rate)
                trade_counts.append(r.total_trades)
            except Exception:
                sharpes.append(-3.0)

        if not sharpes:
            return -10.0

        avg_sharpe = np.mean(sharpes)
        avg_dd = np.mean(max_dds) if max_dds else 0
        avg_wr = np.mean(win_rates) if win_rates else 0
        avg_trades = np.mean(trade_counts) if trade_counts else 0

        # Overtrading penalty
        n_bars = len(next(iter(all_events.values())))
        if n_bars > 0 and avg_trades > 0:
            trade_ratio = avg_trades / n_bars
            if trade_ratio > 0.25:
                avg_sharpe -= (trade_ratio - 0.15) * 5

        # Drawdown penalty: >30% is unacceptable
        if avg_dd < -0.30:
            avg_sharpe -= abs(avg_dd + 0.30) * 3

        # Win rate bonus
        if avg_wr > 0.45:
            avg_sharpe += (avg_wr - 0.45) * 2

        return avg_sharpe

    # Run Optuna
    study = optuna.create_study(direction="maximize",
                                sampler=optuna.samplers.TPESampler(seed=42))
    study.optimize(objective, n_trials=args.trials, show_progress_bar=True)

    print()
    print(f"Best trial: {study.best_trial.number}")
    print(f"Best score (Sharpe-based): {study.best_value:.4f}")
    print()

    # Reconstruct best formula
    best_params = study.best_params
    best_tokens = [best_params[f"t{i}"] for i in range(args.formula_len)]
    best_buy = best_params["buy_th"]
    best_sell = best_params["sell_th"]
    best_readable = vocab.decode(best_tokens)

    print(f"Best formula: {best_readable}")
    print(f"Buy threshold: {best_buy:.3f}")
    print(f"Sell threshold: {best_sell:.3f}")
    print()

    # Full backtest results
    print(f"{'Symbol':<12} {'Return':<10} {'Sharpe':<8} {'MaxDD':<10} {'Trades':<7} {'WinRate':<8} {'PF':<8}")
    print("-" * 65)

    for sym, events in all_events.items():
        r = backtest_formula(best_tokens, vocab, events, best_buy, best_sell)
        print(f"{sym:<12} {r.total_return:>8.2%} {r.sharpe_ratio:>6.3f} {r.max_drawdown:>8.2%} {r.total_trades:<7} {r.win_rate:>6.1%} {r.profit_factor:>6.2f}")

    # Save
    result = {
        "best_tokens": best_tokens,
        "best_readable": best_readable,
        "best_buy_th": best_buy,
        "best_sell_th": best_sell,
        "best_score": study.best_value,
        "vocab_features": list(vocab.feature_names),
        "vocab_operators": list(vocab.operator_names),
    }
    with open(output_dir / "result.json", "w") as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"\nResult saved to: {output_dir / 'result.json'}")

    # Formula breakdown
    print("\nFormula breakdown:")
    for i, t in enumerate(best_tokens):
        kind = "FEATURE" if t < vocab.feature_count else "OPERATOR"
        name = vocab.token_names[t] if t < vocab.size else f"UNK({t})"
        print(f"  [{i}] {kind}: {name}")


if __name__ == "__main__":
    main()
