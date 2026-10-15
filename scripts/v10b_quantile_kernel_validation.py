"""V10b Quantile Kernel Validation — A/B driver for the quantile-kernel experiment.

Zero-mutation companion to scripts/v10_macro_guard_validation.py (plan:
internal-plan). Imports v10's machinery unchanged
and swaps the scoring kernel via scripts/scoring_kernels.py:

  arm A (zscore):   identical kernel to v10/v9 — must EXACTLY reproduce v10 output
  arm B (quantile): rolling(60, min_periods=10).rank(pct=True) kernel with
                    thresholds IS-calibrated to arm A's per-bar trigger rate (D2)

Usage:
    python scripts/v10b_quantile_kernel_validation.py --kernel zscore              # cross-harness gate
    python scripts/v10b_quantile_kernel_validation.py --kernel quantile            # arm B, frozen thresholds
    python scripts/v10b_quantile_kernel_validation.py --kernel quantile --calibrate  # calibrate on IS, then run
    python scripts/v10b_quantile_kernel_validation.py --kernel quantile --calibrate \
        --calibration-file scripts/v10b_calibration_2026-09-20.json               # reuse frozen thresholds
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES_V8
from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS
from selene_space_alpha.vm import PandasStackVM
from optuna_formula_search_v4 import load_multi_tf_df, precompute_features
from v7_trend_filter_backtest import compute_regime, load_funding_rate_series
from v9_exit_opt import backtest_flexible_exit
from v9_vol_shock_filter_backtest import compute_metrics

from scoring_kernels import compute_sigmoid_scores, compute_signals
import v10_macro_guard_validation as v10

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"
V52_FORMULA_NAMES = v10.V52_FORMULA_NAMES
PERSYM_THRESHOLDS = v10.PERSYM_THRESHOLDS

# D2 hard-stop tolerance: achieved trigger rate must be within ±10% relative
# of the arm-A target, else calibration HALTS for that symbol.
CAL_TOLERANCE = 0.10


# ---------------------------------------------------------------------------
# Calibration (D2): grid-search arm-B thresholds to match arm-A trigger rates
# ---------------------------------------------------------------------------


def calibrate_thresholds(
    scores: np.ndarray,
    target_buy_rate: float,
    target_sell_rate: float,
    tolerance: float = CAL_TOLERANCE,
) -> dict:
    """Grid-search (buy_th, sell_th) over observed quantile levels.

    Grid restricted to the observed score distribution's quantile levels
    (Architect amendment 1): thresholds beyond observed grid levels would be
    extrapolation, which D2 forbids. Returns either a calibration result or a
    hard-stop finding.
    """
    levels = np.unique(scores[np.isfinite(scores)])
    if len(levels) < 3:
        return {
            "status": "halted",
            "reason": f"degenerate score distribution ({len(levels)} distinct levels)",
        }

    # Buy threshold: fraction of bars with score > th equals target_buy_rate.
    # For a uniform-ish distribution, sweep each observed level as threshold.
    buy_grid = levels[:-1]  # th == max level would trigger 0 bars
    sell_grid = levels[1:]  # th == min level would trigger everything

    def trigger_rate(grid: np.ndarray, side: int) -> np.ndarray:
        # side=+1: bars with score > th; side=-1: bars with score < th
        if side == 1:
            return (scores[None, :] > grid[:, None]).mean(axis=1)
        return (scores[None, :] < grid[:, None]).mean(axis=1)

    buy_rates = trigger_rate(buy_grid, 1)
    sell_rates = trigger_rate(sell_grid, -1)

    def _best(
        grid: np.ndarray, rates: np.ndarray, target: float
    ) -> tuple[float, float, bool]:
        rel_err = np.abs(rates - target) / max(target, 1e-12)
        i = int(np.argmin(rel_err))
        return float(grid[i]), float(rates[i]), bool(rel_err[i] <= tolerance)

    buy_th, buy_achieved, buy_ok = _best(buy_grid, buy_rates, target_buy_rate)
    sell_th, sell_achieved, sell_ok = _best(sell_grid, sell_rates, target_sell_rate)

    ok = buy_ok and sell_ok
    result = {
        "status": "calibrated" if ok else "halted",
        "target_buy_rate": target_buy_rate,
        "target_sell_rate": target_sell_rate,
        "buy_th": buy_th,
        "buy_achieved_rate": buy_achieved,
        "buy_within_tolerance": buy_ok,
        "sell_th": sell_th,
        "sell_achieved_rate": sell_achieved,
        "sell_within_tolerance": sell_ok,
        "grid_size": int(len(levels)),
    }
    if not ok:
        result["reason"] = (
            "unreachable target on discrete rank grid (D2 hard-stop): "
            f"buy {buy_achieved:.4%} vs {target_buy_rate:.4%} target, "
            f"sell {sell_achieved:.4%} vs {target_sell_rate:.4%} target"
        )
    return result


# ---------------------------------------------------------------------------
# Per-symbol run: mirrors v10.run_backtest_with_macro_tags exactly, but the
# kernel is selected and thresholds come from calibration.
# ---------------------------------------------------------------------------


def run_symbol(
    symbol: str,
    kernel: str,
    thresholds: tuple[float, float],
    split_frac: float,
    use_momentum: bool,
    position_size: float,
    cooldown_bars: int,
    vocab: FormulaVocab,
    v52_tokens: list[int],
    vm: PandasStackVM,
) -> dict:
    """Run one symbol; return IS+OOS metrics and per-trade lists (untagged)."""
    buy_th, sell_th = thresholds
    config = {
        "base_interval": "15m",
        "aux_interval": "1h",
        "buy_th": buy_th,
        "sell_th": sell_th,
        "stop_loss": 0.04,
        "position_size": position_size,
        "hold_bars": 2,
        "cooldown_bars": cooldown_bars,
        "fee_mode": "maker",
    }

    df, n = load_multi_tf_df(DB_PATH, symbol, "binance", "15m", "1h", "futures")
    if df.empty:
        return {"symbol": symbol, "error": "NO DATA"}

    features = precompute_features(df, vocab, vm)
    signals = compute_signals(
        v52_tokens, vocab, df, features, buy_th, sell_th, kernel=kernel
    )
    scores = compute_sigmoid_scores(v52_tokens, vocab, df, features, kernel=kernel)
    regime_series, trend_dir_series = compute_regime(
        DB_PATH, symbol, "binance", df["timestamp"]
    )
    fr_series = load_funding_rate_series(symbol, "binance", df["timestamp"])

    split_idx = int(n * split_frac)
    out: dict = {
        "symbol": symbol,
        "kernel": kernel,
        "buy_th": buy_th,
        "sell_th": sell_th,
        "n_bars": int(n),
        "split_idx": split_idx,
    }

    for label, sl in (("IS", slice(0, split_idx)), ("OOS", slice(split_idx, None))):
        df_seg = df.iloc[sl].reset_index(drop=True)
        sig_seg = signals[sl]
        score_seg = scores[sl]

        eq, trades = backtest_flexible_exit(
            df_seg,
            sig_seg,
            config,
            regime_series=regime_series.iloc[sl].reset_index(drop=True),
            trend_dir_series=trend_dir_series.iloc[sl].reset_index(drop=True),
            funding_rate_series=fr_series.iloc[sl].reset_index(drop=True),
            pos_size_mult=1.0,
            momentum_exit=use_momentum,
            signal_scores=score_seg,
        )
        closed = [t for t in trades if "pnl" in t]
        pnls = np.array([t["pnl"] for t in closed]) if closed else np.array([])

        metrics = compute_metrics(eq, closed, len(df_seg))
        out[label] = {
            "trades": len(closed),
            "wr": float(np.mean(pnls > 0)) if len(pnls) else 0.0,
            "pnl": float(np.sum(pnls)) if len(pnls) else 0.0,
            "max_dd": float(metrics["max_dd"]),
            "buy_trigger_rate": float((sig_seg == 1).mean()),
            "sell_trigger_rate": float((sig_seg == -1).mean()),
        }
        # per-trade records kept for audit (entry/exit/pnl only, keep files lean)
        out[label]["trade_records"] = [
            {
                k: t[k]
                for k in (
                    "entry_ts",
                    "exit_ts",
                    "entry_price",
                    "exit_price",
                    "side",
                    "pnl",
                    "exit_reason",
                )
                if k in t
            }
            for t in closed
        ]
    return out


# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="V10b Quantile Kernel Validation")
    parser.add_argument(
        "--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT"]
    )
    parser.add_argument("--kernel", choices=["zscore", "quantile"], required=True)
    parser.add_argument("--split", type=float, default=0.5)
    parser.add_argument("--no-momentum", action="store_true")
    parser.add_argument("--position-size", type=float, default=0.60)
    parser.add_argument("--cooldown", type=int, default=4)
    parser.add_argument(
        "--calibrate",
        action="store_true",
        help="arm B only: calibrate thresholds on IS to arm-A trigger rates (D2)",
    )
    parser.add_argument(
        "--calibration-file",
        default=None,
        help="reuse frozen calibration thresholds from a prior artifact",
    )
    parser.add_argument(
        "--armA-rates-file",
        default=str(
            PROJECT_ROOT / "scripts" / "v10_armA_is_trigger_rates_2026-09-20.json"
        ),
    )
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    if args.calibrate and args.kernel != "quantile":
        parser.error("--calibrate applies to --kernel quantile only")

    v4_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=CEX_FEATURE_NAMES_V8, operator_names=v4_op_names)
    v52_tokens = [vocab.token_names.index(n) for n in V52_FORMULA_NAMES]
    vm = PandasStackVM(vocab)

    calibration: dict[str, dict] = {}
    threshold_map: dict[str, tuple[float, float]] = dict(PERSYM_THRESHOLDS)

    if args.kernel == "quantile":
        if args.calibration_file:
            cal_art = json.loads(
                Path(args.calibration_file).read_text(encoding="utf-8")
            )
            calibration = cal_art["calibration"]
        elif args.calibrate:
            armA = json.loads(Path(args.armA_rates_file).read_text(encoding="utf-8"))

        # Resolve per-symbol thresholds: frozen artifact > fresh calibration
        for symbol in args.symbols:
            if args.calibration_file:
                entry = calibration[symbol]
                if entry["status"] != "calibrated":
                    print(f"HALTED (frozen artifact): {symbol} — {entry.get('reason')}")
                    return
                threshold_map[symbol] = (entry["buy_th"], entry["sell_th"])
            elif args.calibrate:
                a = armA[symbol]
                df_full, n_bars = load_multi_tf_df(
                    DB_PATH, symbol, "binance", "15m", "1h", "futures"
                )
                feats = precompute_features(df_full, vocab, vm)
                raw_scores = compute_sigmoid_scores(
                    v52_tokens, vocab, df_full, feats, kernel="quantile"
                )
                split_idx = int(n_bars * args.split)
                res = calibrate_thresholds(
                    raw_scores[:split_idx],
                    a["is_buy_trigger_rate"],
                    a["is_sell_trigger_rate"],
                )
                calibration[symbol] = res
                if res["status"] != "calibrated":
                    print(
                        f"D2 HARD-STOP: {symbol} calibration halted — {res['reason']}"
                    )
                    return
                threshold_map[symbol] = (res["buy_th"], res["sell_th"])

    results = []
    for symbol in args.symbols:
        print(f"  {symbol}: kernel={args.kernel} thresholds={threshold_map[symbol]}")
        r = run_symbol(
            symbol,
            args.kernel,
            threshold_map[symbol],
            args.split,
            not args.no_momentum,
            args.position_size,
            args.cooldown,
            vocab,
            v52_tokens,
            vm,
        )
        results.append(r)
        for seg in ("IS", "OOS"):
            m = r.get(seg, {})
            print(
                f"    {seg}: trades={m.get('trades')} WR={m.get('wr', 0):.2%} "
                f"PnL={m.get('pnl', 0):+.2f} maxDD={m.get('max_dd', 0):.2%}"
            )

    artifact = {
        "date": "2026-09-20",
        "kernel": args.kernel,
        "split": args.split,
        "momentum": not args.no_momentum,
        "position_size": args.position_size,
        "cooldown": args.cooldown,
        "symbols": args.symbols,
        "thresholds": {s: list(threshold_map[s]) for s in args.symbols},
        "calibration": calibration,
        "results": results,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, default=str), encoding="utf-8")
    print(f"  artifact -> {out}")


if __name__ == "__main__":
    main()
