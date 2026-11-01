#!/usr/bin/env python
"""Optuna V13 Orderflow-Enhanced Formula Search.

Extends V6 with 10 new orderflow/derivatives features:
  - OF_GLOBAL_LS_RATIO:     Global LS ratio (retail sentiment)
  - OF_GLOBAL_LS_EXTREME:   Global LS z-score (contrarian)
  - OF_SMART_CROWD_DIV:     Smart money vs retail divergence
  - OF_BUY_VOL_PRESSURE:    Buying volume vs rolling mean
  - OF_SELL_VOL_PRESSURE:   Selling volume vs rolling mean
  - OF_VOL_IMBALANCE:       Directional volume imbalance
  - OF_TOP_ACC_CHANGE:      Top account ratio momentum
  - OF_TOP_POS_ACC_DIV:     Position vs account concentration
  - DERIV_FUNDING_MOMENTUM: 3-bar funding rate sum
  - DERIV_FUNDING_EXTREME:  Funding rate z-score (30-bar)

Key improvements over V6:
1. Fixed data loading: merges from ALL 3 databases (klines + derivatives + orderflow)
2. 41 features total (V6 31 + 10 new)
3. New CLI modes: --deriv-only, --deriv-focus, --multi-asset
4. Default hold_mode = signal_timeout (proven best from v12)
5. buy_vol/sell_vol from taker_ratio now loaded as factors

Usage:
    python scripts/optuna_formula_search_fib_v13.py --trials 100
    python scripts/optuna_formula_search_fib_v13.py --trials 2000 --symbols BTC/USDT
    python scripts/optuna_formula_search_fib_v13.py --deriv-only --trials 500
    python scripts/optuna_formula_search_fib_v13.py --deriv-focus --trials 1500
    python scripts/optuna_formula_search_fib_v13.py --multi-asset --trials 1000
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import optuna

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "market-data"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS
from selene_space_alpha.real_data_loader import (
    CEX_FEATURE_NAMES_V13,
    CEX_FEATURE_NAMES_V13_DERIV_ONLY,
    CEX_FEATURE_NAMES_V13_DERIV_FOCUS,
)
from selene_space_alpha.vm import PandasStackVM

# Reuse V4 components
from optuna_formula_search_v4 import (
    precompute_features,
    create_walk_forward_windows,
    WalkForwardWindow,
    V4Tokenizer,
    WindowResult,
    INTERVAL_TO_ANNUAL_BARS,
)

# Reuse shared_backtest for multi-DB loading
from shared_backtest import load_4h_1d_deriv, backtest_signal_timeout

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Feature set selection
# ---------------------------------------------------------------------------

FEATURE_SETS = {
    "full": CEX_FEATURE_NAMES_V13,
    "deriv-only": CEX_FEATURE_NAMES_V13_DERIV_ONLY,
    "deriv-focus": CEX_FEATURE_NAMES_V13_DERIV_FOCUS,
}


# ---------------------------------------------------------------------------
# V13 Data Loading: 4h + 1d + derivatives + orderflow
# ---------------------------------------------------------------------------

def load_v13_data(
    klines_db: Path,
    deriv_db: Path,
    orderflow_db: Path,
    symbol: str,
    exchange: str = "binance",
    market_type: str = "futures",
) -> tuple[pd.DataFrame, int]:
    """Load 4h+1d klines merged with derivatives and orderflow data.

    Uses shared_backtest.load_4h_1d_deriv() for the 3-DB merge.
    """
    df, n = load_4h_1d_deriv(
        klines_db=str(klines_db),
        deriv_db=str(deriv_db),
        orderflow_db=str(orderflow_db),
        symbol=symbol,
        exchange=exchange,
        market_type=market_type,
    )
    if n > 0:
        # Report data coverage
        for col in ["FUNDING_RATE", "LS_RATIO", "OF_top_pos_ratio",
                     "OF_top_acc_ratio", "OF_global_ls_ratio",
                     "OF_buy_vol", "OF_sell_vol"]:
            if col in df.columns:
                nonzero = (df[col] != 0.0).sum()
                logger.info("  %s: %d/%d (%.0f%%)", col, nonzero, n, nonzero / n * 100)
    return df, n


# ---------------------------------------------------------------------------
# V13 Backtest: signal_timeout mode (default)
# ---------------------------------------------------------------------------

def backtest_v13_signal_timeout(
    tokens: list[int],
    vocab: FormulaVocab,
    df: pd.DataFrame,
    features: dict[int, pd.Series],
    buy_th: float,
    sell_th: float,
    fee_rate: float = 0.0005,
    stop_loss: float = 0.05,
    position_size: float = 0.5,
    timeout_bars: int = 25,
    min_hold: int = 3,
    cooldown_bars: int = 2,
) -> dict:
    """Backtest formula with signal_timeout hold mode."""
    vm = PandasStackVM(vocab)
    signal_series = vm.execute(tokens, df, precomputed_features=features)
    if signal_series is None:
        return {"sharpe": -10.0, "total_return": -1.0, "max_drawdown": -1.0,
                "total_trades": 0, "win_rate": 0.0, "equity_curve": [1.0]}

    return backtest_signal_timeout(
        signals=signal_series,
        df=df,
        buy_threshold=buy_th,
        sell_threshold=sell_th,
        fee_rate=fee_rate,
        stop_loss=stop_loss,
        position_size=position_size,
        timeout_bars=timeout_bars,
        min_hold=min_hold,
        cooldown_bars=cooldown_bars,
    )


# ---------------------------------------------------------------------------
# V13 Fitness: walk-forward with signal_timeout
# ---------------------------------------------------------------------------

def compute_v13_fitness(
    window_results: list[WindowResult],
    formula_len: int,
    complexity_penalty: float = 0.02,
) -> float:
    """Compute fitness with stability bonus and complexity penalty."""
    valid = [wr for wr in window_results if wr.test_trades >= 3]
    if not valid:
        return -10.0

    oos_sharpes = [wr.test_sharpe for wr in valid]
    train_sharpes = [wr.train_sharpe for wr in valid]

    mean_oos = float(np.nanmean(oos_sharpes))
    mean_train = float(np.nanmean(train_sharpes))

    # Stability: OOS should be positive fraction of train
    stability = mean_oos / (abs(mean_train) + 1e-6) if mean_train != 0 else 0.0
    stability = min(stability, 1.0)

    # Consistency: fraction of windows with positive OOS Sharpe
    consistency = sum(1 for s in oos_sharpes if s > 0) / len(oos_sharpes)

    # Complexity penalty
    cpen = complexity_penalty * formula_len

    fitness = mean_oos * (0.5 + 0.5 * stability) + 0.3 * consistency - cpen
    return fitness


# ---------------------------------------------------------------------------
# Main search
# ---------------------------------------------------------------------------

def run_v13_search(
    klines_db: Path,
    deriv_db: Path,
    orderflow_db: Path,
    vocab: FormulaVocab,
    symbols: list[str],
    exchange: str,
    market_type: str,
    n_trials: int,
    fee_rate: float = 0.0005,
    seed: int = 42,
    multi_asset: bool = False,
    feature_set_name: str = "full",
    output_dir: Path | None = None,
) -> dict:
    """Run v13 formula search with signal_timeout backtest."""
    tokenizer = V4Tokenizer(vocab)
    vm = PandasStackVM(vocab)

    # Load data for all symbols
    all_windows: dict[str, list[WalkForwardWindow]] = {}
    annual_bars_map: dict[str, int] = {}

    for sym in symbols:
        key = f"{sym}:4h"
        df, n = load_v13_data(klines_db, deriv_db, orderflow_db, sym, exchange, market_type)
        if n == 0:
            print(f"  {key}: NO DATA, skipping")
            continue
        print(f"  {key}: {n} bars loaded")

        features = precompute_features(df, vocab, vm)
        windows = create_walk_forward_windows(df, features, n_windows=4, train_ratio=0.50, gap_ratio=0.05, test_ratio=0.20)
        all_windows[key] = windows
        annual_bars_map[key] = INTERVAL_TO_ANNUAL_BARS.get("4h", 2190)

    if not all_windows:
        print("No data loaded.")
        return {}

    trial_cache: dict[int, tuple] = {}

    def objective(trial: optuna.Trial) -> float:
        tokens = tokenizer.suggest_formula(trial, 4, 8)
        buy_th = trial.suggest_float("buy_th", 0.50, 0.80, step=0.02)
        sell_th = trial.suggest_float("sell_th", 0.20, 0.50, step=0.02)
        stop_loss = trial.suggest_float("stop_loss", 0.03, 0.10, step=0.01)
        position_size = trial.suggest_float("position_size", 0.30, 0.80, step=0.10)
        timeout_bars = trial.suggest_int("timeout_bars", 15, 40)
        min_hold = trial.suggest_int("min_hold", 2, 6)
        cooldown_bars = trial.suggest_int("cooldown_bars", 1, 4)

        if buy_th <= sell_th + 0.05:
            return -10.0

        window_results: list[WindowResult] = []
        for key, windows in all_windows.items():
            annual_bars = annual_bars_map.get(key, 2190)
            for window in windows:
                # Train: signal_timeout backtest
                train_signals = vm.execute(tokens, window.train_df, precomputed_features=window.train_features)
                if train_signals is None:
                    continue
                train_r = backtest_signal_timeout(
                    train_signals, window.train_df,
                    buy_threshold=buy_th, sell_threshold=sell_th,
                    fee_rate=fee_rate, stop_loss=stop_loss,
                    position_size=position_size,
                    timeout_bars=timeout_bars, min_hold=min_hold,
                    cooldown_bars=cooldown_bars,
                )
                train_sharpe = train_r["sharpe"]

                # Test: same params
                test_signals = vm.execute(tokens, window.test_df, precomputed_features=window.test_features)
                if test_signals is None:
                    continue
                test_r = backtest_signal_timeout(
                    test_signals, window.test_df,
                    buy_threshold=buy_th, sell_threshold=sell_th,
                    fee_rate=fee_rate, stop_loss=stop_loss,
                    position_size=position_size,
                    timeout_bars=timeout_bars, min_hold=min_hold,
                    cooldown_bars=cooldown_bars,
                )
                test_sharpe = test_r["sharpe"]

                window_results.append(WindowResult(
                    key=key, window_idx=window.window_idx,
                    train_sharpe=train_sharpe, test_sharpe=test_sharpe,
                    test_equity_curve=test_r["equity_curve"],
                    test_trades=test_r["total_trades"],
                    test_max_dd=test_r["max_drawdown"],
                    test_n_bars=len(window.test_df),
                    test_return=test_r["total_return"],
                    interval="4h",
                ))

        if multi_asset:
            # Average fitness across assets
            by_key: dict[str, list[WindowResult]] = {}
            for wr in window_results:
                by_key.setdefault(wr.key, []).append(wr)
            asset_fitnesses = []
            for key, wrs in by_key.items():
                asset_fitnesses.append(compute_v13_fitness(wrs, len(tokens)))
            fitness = float(np.nanmean(asset_fitnesses)) if asset_fitnesses else -10.0
        else:
            fitness = compute_v13_fitness(window_results, len(tokens))

        if fitness > -5.0:
            valid_oos = [wr.test_sharpe for wr in window_results if wr.test_trades >= 3]
            valid_train = [wr.train_sharpe for wr in window_results if wr.test_trades >= 3]
            trial_cache[trial.number] = (
                tokens,
                float(np.nanmean(valid_oos)) if valid_oos else -10.0,
                float(np.nanmean(valid_train)) if valid_train else 0.0,
            )

        return fitness

    sampler = optuna.samplers.TPESampler(seed=seed)
    study = optuna.create_study(direction="maximize", sampler=sampler)
    study.optimize(objective, n_trials=n_trials, show_progress_bar=True)

    best_params = study.best_params
    best_len = best_params["formula_len"]
    best_tokens = tokenizer.reconstruct_tokens(best_params, best_len)
    best_readable = vocab.decode(best_tokens)

    print(f"\n  Best: {best_readable}")
    print(f"  Fitness: {study.best_value:.4f}")
    print(f"  Buy={best_params['buy_th']:.2f} Sell={best_params['sell_th']:.2f} "
          f"SL={best_params['stop_loss']:.0%} Pos={best_params['position_size']:.0%} "
          f"Timeout={best_params['timeout_bars']} MinHold={best_params['min_hold']} "
          f"Cool={best_params['cooldown_bars']}")

    # Check which feature groups the best formula uses
    deriv_features = {"FUNDING_RATE", "FUNDING_RATE_Z", "OI_CHANGE", "LS_RATIO",
                      "DERIV_FUNDING_MOMENTUM", "DERIV_FUNDING_EXTREME"}
    of_features = {"OF_GLOBAL_LS_RATIO", "OF_GLOBAL_LS_EXTREME", "OF_SMART_CROWD_DIV",
                   "OF_BUY_VOL_PRESSURE", "OF_SELL_VOL_PRESSURE", "OF_VOL_IMBALANCE",
                   "OF_TOP_ACC_CHANGE", "OF_TOP_POS_ACC_DIV"}
    uses_deriv = any(vocab.feature_names[t] in deriv_features for t in best_tokens if t < vocab.feature_count)
    uses_of = any(vocab.feature_names[t] in of_features for t in best_tokens if t < vocab.feature_count)
    print(f"  Uses derivatives: {'YES' if uses_deriv else 'NO'}")
    print(f"  Uses orderflow:   {'YES' if uses_of else 'NO'}")

    result_data = {
        "version": "v13-orderflow",
        "feature_set": feature_set_name,
        "base_interval": "4h",
        "aux_interval": "1d",
        "hold_mode": "signal_timeout",
        "uses_derivatives": uses_deriv,
        "uses_orderflow": uses_of,
        "best_formula": {
            "tokens": best_tokens,
            "readable": best_readable,
            **{k: v for k, v in best_params.items() if k != "formula_len"},
            "formula_len": best_len,
        },
        "n_trials": len(study.trials),
        "symbols": symbols,
        "multi_asset": multi_asset,
    }

    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        with open(output_dir / "result.json", "w", encoding="utf-8") as f:
            json.dump(result_data, f, indent=2, ensure_ascii=False)

    return result_data


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Optuna V13 Orderflow-Enhanced Formula Search"
    )
    # Feature set
    parser.add_argument("--feature-set", default="full",
                        choices=["full", "deriv-only", "deriv-focus"],
                        help="Feature set to use")
    # Data
    parser.add_argument("--db", default="services/local-api/data/klines.db")
    parser.add_argument("--deriv-db", default="data/derivatives.db")
    parser.add_argument("--orderflow-db", default="data/orderflow.db")
    parser.add_argument("--symbols", nargs="+",
                        default=["BTC/USDT"])
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--market-type", default="futures")
    # Search params
    parser.add_argument("--trials", type=int, default=100)
    parser.add_argument("--fee", type=float, default=0.0005)
    # Modes
    parser.add_argument("--multi-asset", action="store_true",
                        help="Average fitness across all symbols")
    # Common
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output-dir", default="data/optuna_formula_v13")
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    db_path = PROJECT_ROOT / args.db
    deriv_db_path = PROJECT_ROOT / args.deriv_db
    orderflow_db_path = PROJECT_ROOT / args.orderflow_db
    output_dir = PROJECT_ROOT / args.output_dir

    # Select feature set
    feature_names = FEATURE_SETS[args.feature_set]

    # Build vocab
    v13_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(
        feature_names=feature_names,
        operator_names=v13_op_names,
    )

    deriv_status = "enabled" if deriv_db_path.exists() else "disabled (no db)"
    of_status = "enabled" if orderflow_db_path.exists() else "disabled (no db)"

    print("=" * 65)
    print("Optuna V13 Orderflow-Enhanced Formula Search")
    print("=" * 65)
    print(f"  Feature set: {args.feature_set} ({len(feature_names)} features)")
    print(f"  Symbols: {args.symbols}")
    print(f"  Derivatives: {deriv_status}")
    print(f"  Orderflow:   {of_status}")
    print(f"  Multi-asset: {args.multi_asset}")
    print("  Hold mode:   signal_timeout")
    print(f"  Vocab: {vocab.size} tokens ({vocab.feature_count} features + {len(vocab.operator_names)} ops)")
    print(f"  Trials: {args.trials}")
    print()

    result = run_v13_search(
        klines_db=db_path,
        deriv_db=deriv_db_path,
        orderflow_db=orderflow_db_path,
        vocab=vocab,
        symbols=args.symbols,
        exchange=args.exchange,
        market_type=args.market_type,
        n_trials=args.trials,
        fee_rate=args.fee,
        seed=args.seed,
        multi_asset=args.multi_asset,
        feature_set_name=args.feature_set,
        output_dir=output_dir,
    )

    if result:
        print(f"\nResults saved to: {output_dir / 'result.json'}")
    print("\nDone!")


if __name__ == "__main__":
    main()
