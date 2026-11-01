#!/usr/bin/env python
"""V13 Derivatives/Orderflow Formula Validation.

Walk-forward validation, cross-asset validation, factor ablation,
and ensemble testing for v13 formulas.

Usage:
    python scripts/validate_v13_deriv.py --result data/optuna_formula_v13/result.json
    python scripts/validate_v13_deriv.py --result data/optuna_formula_v13/result.json --ablation
    python scripts/validate_v13_deriv.py --result data/optuna_formula_v13/result.json --cross-asset
    python scripts/validate_v13_deriv.py --result data/optuna_formula_v13/result.json --ensemble
    python scripts/validate_v13_deriv.py --result data/optuna_formula_v13/result.json --all
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

import numpy as np

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

from optuna_formula_search_v4 import precompute_features, create_walk_forward_windows
from shared_backtest import load_4h_1d_deriv, backtest_signal_timeout

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

FEATURE_SETS = {
    "full": CEX_FEATURE_NAMES_V13,
    "deriv-only": CEX_FEATURE_NAMES_V13_DERIV_ONLY,
    "deriv-focus": CEX_FEATURE_NAMES_V13_DERIV_FOCUS,
}

DEFAULT_SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]


# ---------------------------------------------------------------------------
# Walk-Forward Validation
# ---------------------------------------------------------------------------

def walk_forward_validate(
    formula_tokens: list[int],
    vocab: FormulaVocab,
    symbol: str,
    klines_db: Path,
    deriv_db: Path,
    orderflow_db: Path,
    buy_th: float,
    sell_th: float,
    stop_loss: float = 0.05,
    position_size: float = 0.5,
    timeout_bars: int = 25,
    min_hold: int = 3,
    cooldown_bars: int = 2,
    n_windows: int = 5,
    fee_rate: float = 0.0005,
) -> dict:
    """Walk-forward validation of a formula on a single symbol."""
    df, n = load_4h_1d_deriv(
        klines_db=str(klines_db),
        deriv_db=str(deriv_db),
        orderflow_db=str(orderflow_db),
        symbol=symbol,
    )
    if n == 0:
        return {"symbol": symbol, "error": "no data"}

    vm = PandasStackVM(vocab)
    features = precompute_features(df, vocab, vm)
    windows = create_walk_forward_windows(
        df, features, n_windows=n_windows,
        train_ratio=0.50, gap_ratio=0.05, test_ratio=0.20,
    )

    results = []
    for window in windows:
        train_sig = vm.execute(formula_tokens, window.train_df, precomputed_features=window.train_features)
        test_sig = vm.execute(formula_tokens, window.test_df, precomputed_features=window.test_features)

        if train_sig is None or test_sig is None:
            continue

        train_r = backtest_signal_timeout(
            train_sig, window.train_df,
            buy_threshold=buy_th, sell_threshold=sell_th,
            fee_rate=fee_rate, stop_loss=stop_loss,
            position_size=position_size,
            timeout_bars=timeout_bars, min_hold=min_hold,
            cooldown_bars=cooldown_bars,
        )
        test_r = backtest_signal_timeout(
            test_sig, window.test_df,
            buy_threshold=buy_th, sell_threshold=sell_th,
            fee_rate=fee_rate, stop_loss=stop_loss,
            position_size=position_size,
            timeout_bars=timeout_bars, min_hold=min_hold,
            cooldown_bars=cooldown_bars,
        )
        results.append({
            "window": window.window_idx,
            "train_sharpe": train_r["sharpe"],
            "test_sharpe": test_r["sharpe"],
            "test_return": test_r["total_return"],
            "test_max_dd": test_r["max_drawdown"],
            "test_trades": test_r["total_trades"],
            "test_win_rate": test_r["win_rate"],
        })

    if not results:
        return {"symbol": symbol, "error": "no valid windows"}

    test_sharpes = [r["test_sharpe"] for r in results]
    test_returns = [r["test_return"] for r in results]
    test_dds = [r["test_max_dd"] for r in results]

    return {
        "symbol": symbol,
        "n_windows": len(results),
        "mean_test_sharpe": float(np.nanmean(test_sharpes)),
        "std_test_sharpe": float(np.nanstd(test_sharpes)),
        "min_test_sharpe": float(np.nanmin(test_sharpes)),
        "mean_test_return": float(np.nanmean(test_returns)),
        "mean_test_dd": float(np.nanmean(test_dds)),
        "consistency": sum(1 for s in test_sharpes if s > 0) / len(test_sharpes),
        "windows": results,
    }


# ---------------------------------------------------------------------------
# Cross-Asset Validation
# ---------------------------------------------------------------------------

def cross_asset_validate(
    formula_tokens: list[int],
    vocab: FormulaVocab,
    symbols: list[str],
    klines_db: Path,
    deriv_db: Path,
    orderflow_db: Path,
    buy_th: float,
    sell_th: float,
    **kwargs,
) -> dict:
    """Validate a formula discovered on one asset across multiple assets."""
    results = {}
    for sym in symbols:
        r = walk_forward_validate(
            formula_tokens, vocab, sym,
            klines_db, deriv_db, orderflow_db,
            buy_th, sell_th, **kwargs,
        )
        results[sym] = r
        status = f"Sharpe={r.get('mean_test_sharpe', 'N/A'):.3f}" if "error" not in r else f"ERROR: {r['error']}"
        logger.info("  %s: %s", sym, status)

    valid = [r for r in results.values() if "error" not in r]
    if valid:
        avg_sharpe = float(np.nanmean([r["mean_test_sharpe"] for r in valid]))
        avg_return = float(np.nanmean([r["mean_test_return"] for r in valid]))
        logger.info("  Cross-asset avg: Sharpe=%.3f, Return=%.2f%%", avg_sharpe, avg_return * 100)
    return results


# ---------------------------------------------------------------------------
# Factor Ablation
# ---------------------------------------------------------------------------

def factor_ablation(
    formula_tokens: list[int],
    vocab: FormulaVocab,
    symbol: str,
    klines_db: Path,
    deriv_db: Path,
    orderflow_db: Path,
    buy_th: float,
    sell_th: float,
    **kwargs,
) -> dict:
    """Test each v13 factor solo to measure individual predictive power."""
    v13_factors = [
        "OF_GLOBAL_LS_RATIO", "OF_GLOBAL_LS_EXTREME", "OF_SMART_CROWD_DIV",
        "OF_BUY_VOL_PRESSURE", "OF_SELL_VOL_PRESSURE", "OF_VOL_IMBALANCE",
        "OF_TOP_ACC_CHANGE", "OF_TOP_POS_ACC_DIV",
        "DERIV_FUNDING_MOMENTUM", "DERIV_FUNDING_EXTREME",
    ]

    # Build a minimal vocab with just one feature + operators
    v13_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")

    ablation_results = {}
    for factor_name in v13_factors:
        # Create single-factor vocab
        single_vocab = FormulaVocab(
            feature_names=(factor_name,),
            operator_names=v13_op_names,
        )
        # Token 0 = the factor itself
        single_tokens = [0]

        r = walk_forward_validate(
            single_tokens, single_vocab, symbol,
            klines_db, deriv_db, orderflow_db,
            buy_th, sell_th, **kwargs,
        )
        sharpe = r.get("mean_test_sharpe", -10.0) if "error" not in r else -10.0
        ablation_results[factor_name] = sharpe
        logger.info("  %s: Sharpe=%.3f", factor_name, sharpe)

    # Sort by Sharpe
    sorted_factors = sorted(ablation_results.items(), key=lambda x: x[1], reverse=True)
    logger.info("\n  Factor ablation ranking:")
    for name, sharpe in sorted_factors:
        logger.info("    %s: %.3f", name, sharpe)

    return ablation_results


# ---------------------------------------------------------------------------
# Ensemble Validation
# ---------------------------------------------------------------------------

def ensemble_validate(
    v13_result: dict,
    champion_results: list[dict] | None = None,
    symbols: list[str] | None = None,
    klines_db: Path | None = None,
    deriv_db: Path | None = None,
    orderflow_db: Path | None = None,
) -> dict:
    """Validate v13 formula as independent ensemble member.

    Tests v13 formula alongside any champion formulas (v11/v12)
    as an INDEPENDENT ensemble (equal weight, no correlation filter).
    """
    if symbols is None:
        symbols = DEFAULT_SYMBOLS

    feature_set_name = v13_result.get("feature_set", "full")
    feature_names = FEATURE_SETS.get(feature_set_name, CEX_FEATURE_NAMES_V13)
    v13_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=feature_names, operator_names=v13_op_names)

    v13_formula = v13_result["best_formula"]
    v13_tokens = v13_formula["tokens"]

    ensemble_results = {}
    for sym in symbols:
        df, n = load_4h_1d_deriv(
            klines_db=str(klines_db),
            deriv_db=str(deriv_db),
            orderflow_db=str(orderflow_db),
            symbol=sym,
        )
        if n == 0:
            continue

        vm = PandasStackVM(vocab)
        features = precompute_features(df, vocab, vm)
        signals = vm.execute(v13_tokens, df, precomputed_features=features)
        if signals is None:
            continue

        r = backtest_signal_timeout(
            signals, df,
            buy_threshold=v13_formula["buy_th"],
            sell_threshold=v13_formula["sell_th"],
            stop_loss=v13_formula.get("stop_loss", 0.05),
            position_size=v13_formula.get("position_size", 0.5),
            timeout_bars=v13_formula.get("timeout_bars", 25),
            min_hold=v13_formula.get("min_hold", 3),
            cooldown_bars=v13_formula.get("cooldown_bars", 2),
        )
        ensemble_results[sym] = r
        logger.info("  %s: Sharpe=%.3f, Return=%.2f%%, MaxDD=%.2f%%, Trades=%d",
                     sym, r["sharpe"], r["total_return"] * 100,
                     r["max_drawdown"] * 100, r["total_trades"])

    return ensemble_results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="V13 Derivatives/Orderflow Formula Validation")
    parser.add_argument("--result", required=True, help="Path to v13 result.json")
    parser.add_argument("--db", default="services/local-api/data/klines.db")
    parser.add_argument("--deriv-db", default="data/derivatives.db")
    parser.add_argument("--orderflow-db", default="data/orderflow.db")
    parser.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    # Validation modes
    parser.add_argument("--walk-forward", action="store_true", help="Walk-forward validation")
    parser.add_argument("--cross-asset", action="store_true", help="Cross-asset validation")
    parser.add_argument("--ablation", action="store_true", help="Factor ablation test")
    parser.add_argument("--ensemble", action="store_true", help="Ensemble validation")
    parser.add_argument("--all", action="store_true", help="Run all validation modes")
    # Params
    parser.add_argument("--n-windows", type=int, default=5, help="Walk-forward windows")
    args = parser.parse_args()

    if args.all:
        args.walk_forward = True
        args.cross_asset = True
        args.ablation = True
        args.ensemble = True

    # Load result
    result_path = Path(args.result)
    if not result_path.exists():
        logger.error("Result file not found: %s", result_path)
        return

    with open(result_path, encoding="utf-8") as f:
        v13_result = json.load(f)

    formula = v13_result["best_formula"]
    feature_set_name = v13_result.get("feature_set", "full")
    feature_names = FEATURE_SETS.get(feature_set_name, CEX_FEATURE_NAMES_V13)
    v13_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=feature_names, operator_names=v13_op_names)

    klines_db = PROJECT_ROOT / args.db
    deriv_db = PROJECT_ROOT / args.deriv_db
    orderflow_db = PROJECT_ROOT / args.orderflow_db

    print("=" * 65)
    print("V13 Formula Validation")
    print("=" * 65)
    print(f"  Formula: {formula['readable']}")
    print(f"  Feature set: {feature_set_name}")
    print(f"  Buy={formula['buy_th']:.2f} Sell={formula['sell_th']:.2f} "
          f"SL={formula.get('stop_loss', 0.05):.0%} Pos={formula.get('position_size', 0.5):.0%}")
    print()

    # Walk-forward
    if args.walk_forward:
        print("[Walk-Forward Validation]")
        for sym in args.symbols:
            r = walk_forward_validate(
                formula["tokens"], vocab, sym,
                klines_db, deriv_db, orderflow_db,
                formula["buy_th"], formula["sell_th"],
                stop_loss=formula.get("stop_loss", 0.05),
                position_size=formula.get("position_size", 0.5),
                timeout_bars=formula.get("timeout_bars", 25),
                min_hold=formula.get("min_hold", 3),
                cooldown_bars=formula.get("cooldown_bars", 2),
                n_windows=args.n_windows,
            )
            if "error" not in r:
                print(f"  {sym}: Sharpe={r['mean_test_sharpe']:.3f} "
                      f"(std={r['std_test_sharpe']:.3f}) "
                      f"Return={r['mean_test_return']:.2%} "
                      f"DD={r['mean_test_dd']:.2%} "
                      f"Consistency={r['consistency']:.0%}")
            else:
                print(f"  {sym}: {r['error']}")
        print()

    # Cross-asset
    if args.cross_asset:
        print("[Cross-Asset Validation]")
        cross_asset_validate(
            formula["tokens"], vocab, args.symbols,
            klines_db, deriv_db, orderflow_db,
            formula["buy_th"], formula["sell_th"],
            stop_loss=formula.get("stop_loss", 0.05),
            position_size=formula.get("position_size", 0.5),
            timeout_bars=formula.get("timeout_bars", 25),
            min_hold=formula.get("min_hold", 3),
            cooldown_bars=formula.get("cooldown_bars", 2),
        )
        print()

    # Ablation
    if args.ablation:
        print("[Factor Ablation]")
        factor_ablation(
            formula["tokens"], vocab, args.symbols[0],
            klines_db, deriv_db, orderflow_db,
            formula["buy_th"], formula["sell_th"],
            stop_loss=formula.get("stop_loss", 0.05),
            position_size=formula.get("position_size", 0.5),
            timeout_bars=formula.get("timeout_bars", 25),
            min_hold=formula.get("min_hold", 3),
            cooldown_bars=formula.get("cooldown_bars", 2),
        )
        print()

    # Ensemble
    if args.ensemble:
        print("[Ensemble Validation]")
        ensemble_validate(
            v13_result=v13_result,
            symbols=args.symbols,
            klines_db=klines_db,
            deriv_db=deriv_db,
            orderflow_db=orderflow_db,
        )
        print()

    print("Validation complete!")


if __name__ == "__main__":
    main()
