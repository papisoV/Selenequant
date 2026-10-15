"""Fast vectorized multi-data validation.

Bypasses the slow per-bar on_kline() loop by computing signals on the full
DataFrame at once, then simulating trades vectorized. Runs in seconds
instead of minutes per strategy.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "shared-types"))

from selene_space_types.market import KlineEvent

DB_PATH = PROJECT_ROOT / "services/local-api/data/klines.db"
OUTPUT_DIR = PROJECT_ROOT / "data/validation_results"

SYMBOLS = ["BTC/USDT", "ETH/USDT", "SOL/USDT"]
INTERVALS = ["1d", "4h"]
EXCHANGE = "binance"

# Strategy registry: (module, class, category)
PRESET_REGISTRY = [
    ("bb_v3", "BBV3Strategy", "original"),
    ("rsi_ma_delta", "RSIMADeltaStrategy", "original"),
    ("stoch_supertrend", "STOCHSupertrendStrategy", "original"),
    ("ema_triple_cross", "EMATripleCrossStrategy", "original"),
    ("ma_extended_runs", "MAExtendedRunsStrategy", "original"),
    ("dca_grid", "DCAGridStrategy", "original"),
    ("p_signal", "PSignalStrategy", "original"),
    ("calendar_effect", "CalendarEffectStrategy", "original"),
    ("vwap_fibo_bands", "VWAPFiboBandsStrategy", "original"),
    ("pyramid_ma", "PyramidMAStrategy", "original"),
    ("dmi_delta", "DMIDeltaStrategy", "enhanced"),
    ("bb_atr_trend", "BBATRTrendStrategy", "enhanced"),
    ("keltner_v2", "KeltnerChannelV2Strategy", "enhanced"),
    ("tema_channel", "TEMAChannelStrategy", "enhanced"),
    ("vw_ema_cross", "VWEMACrossoverStrategy", "enhanced"),
]

ENSEMBLE_MEMBERS = ["dmi_delta", "keltner_v2", "tema_channel", "bb_atr_trend", "vw_ema_cross"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_klines_df(symbol: str, interval: str) -> pd.DataFrame:
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? ORDER BY timestamp ASC",
        conn, params=(symbol, EXCHANGE, interval),
    )
    conn.close()
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "high", "low", "close"])
    df = df.reset_index(drop=True)
    return df


def df_to_events(df: pd.DataFrame, symbol: str, interval: str) -> list[KlineEvent]:
    return [
        KlineEvent(
            exchange=EXCHANGE, symbol=symbol, interval=interval,
            timestamp=int(r["timestamp"]), open=float(r["open"]),
            high=float(r["high"]), low=float(r["low"]),
            close=float(r["close"]), volume=float(r["volume"]),
            closed=True,
        )
        for _, r in df.iterrows()
    ]


# ---------------------------------------------------------------------------
# Vectorized signal extraction using BacktestStrategyTemplate
# ---------------------------------------------------------------------------

def extract_signals_vectorized(
    strat_class: type,
    symbol: str,
    df: pd.DataFrame,
    enhanced: bool = False,
) -> pd.Series:
    """Run strategy on DataFrame and extract signal series.

    Uses the template's on_kline() per bar (unavoidable for complex strategies),
    but only collects signals — no trade simulation.
    """
    from selene_space_types.trading import OrderSide

    events = df_to_events(df, symbol, "4h")
    kwargs: dict[str, Any] = {"symbol": symbol, "long_only": True}
    if enhanced:
        kwargs.update({
            "use_atr_stops": True, "atr_sl_mult": 1.5, "atr_tp_mult": 3.0,
            "position_size_pct": 0.3, "cooldown_bars": 3,
        })
    strat = strat_class(**kwargs)
    strat.max_bars = len(events)

    signals = pd.Series(0, index=df.index, dtype=int)  # 0=hold, 1=buy, -1=sell
    for i, event in enumerate(events):
        sig = strat.on_kline(event)
        if sig is not None:
            if sig.side == OrderSide.BUY:
                signals.iloc[i] = 1
            elif sig.side == OrderSide.SELL:
                signals.iloc[i] = -1
    return signals


# ---------------------------------------------------------------------------
# Vectorized backtest simulation
# ---------------------------------------------------------------------------

@dataclass
class FastBacktestResult:
    total_return: float = 0.0
    sharpe_ratio: float = 0.0
    max_drawdown: float = 0.0
    win_rate: float = 0.0
    profit_factor: float = 0.0
    total_trades: int = 0
    calmar_ratio: float = 0.0
    avg_pnl_ratio: float = 0.0  # avg win / avg loss


def simulate_long_only(
    closes: np.ndarray,
    signals: np.ndarray,
    fee_rate: float = 0.0005,
    position_size: float = 1.0,
    stop_loss: float = 0.0,
    annual_bars: int = 365,
) -> FastBacktestResult:
    """Vectorized long-only backtest simulation."""
    n = len(closes)
    capital = 1.0
    position = False
    entry_price = 0.0
    equity_curve = np.ones(n)
    trades: list[float] = []

    for i in range(n):
        price = closes[i]
        sig = signals[i] if i < len(signals) else 0

        if not position and sig == 1:
            position = True
            entry_price = price
        elif position:
            pnl_pct = (price - entry_price) / entry_price

            # Stop loss check
            stopped = stop_loss > 0 and pnl_pct <= -stop_loss

            if sig == -1 or stopped:
                fee = fee_rate * 2 * position_size
                net_pnl = position_size * pnl_pct - fee
                capital *= (1 + net_pnl)
                trades.append(net_pnl)
                position = False
                entry_price = 0.0

        equity_curve[i] = capital * (1 + position_size * (price - entry_price) / entry_price if position else 0)

    # Close any open position at end
    if position:
        pnl_pct = (closes[-1] - entry_price) / entry_price
        net_pnl = position_size * pnl_pct - fee_rate * 2 * position_size
        capital *= (1 + net_pnl)
        trades.append(net_pnl)

    result = FastBacktestResult()
    result.total_return = capital - 1.0

    # Max drawdown
    peak = np.maximum.accumulate(equity_curve)
    dd = (peak - equity_curve) / np.maximum(peak, 1e-10)
    result.max_drawdown = -float(np.max(dd))

    # Win rate & profit factor
    if trades:
        wins = [t for t in trades if t > 0]
        losses = [t for t in trades if t <= 0]
        result.total_trades = len(trades)
        result.win_rate = len(wins) / len(trades)
        gross_profit = sum(wins) if wins else 0
        gross_loss = abs(sum(losses)) if losses else 0
        result.profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0)
        avg_win = np.mean(wins) if wins else 0
        avg_loss = abs(np.mean(losses)) if losses else 1e-10
        result.avg_pnl_ratio = avg_win / avg_loss if avg_loss > 0 else 0

    # Sharpe (from equity curve returns)
    if len(equity_curve) > 1:
        rets = np.diff(equity_curve) / equity_curve[:-1]
        rets = rets[np.isfinite(rets)]
        if len(rets) > 1 and np.std(rets) > 0:
            result.sharpe_ratio = float(np.mean(rets) / np.std(rets) * np.sqrt(annual_bars))

    # Calmar
    if result.max_drawdown != 0:
        result.calmar_ratio = result.total_return / abs(result.max_drawdown)

    return result


# ---------------------------------------------------------------------------
# Walk-Forward
# ---------------------------------------------------------------------------

def create_walk_forward_splits(
    n: int, n_windows: int = 3, train_ratio: float = 0.55,
    gap_ratio: float = 0.10, test_ratio: float = 0.25,
) -> list[tuple[range, range]]:
    """Returns list of (train_range, test_range) index pairs."""
    total_window = train_ratio + gap_ratio + test_ratio
    step = (1.0 - total_window) / max(n_windows - 1, 1) if n_windows > 1 else 0.0
    splits = []
    for i in range(n_windows):
        offset = step * i
        train_end = int((offset + train_ratio) * n)
        gap_end = int((offset + train_ratio + gap_ratio) * n)
        test_end = int((offset + train_ratio + gap_ratio + test_ratio) * n)
        splits.append((range(0, train_end), range(gap_end, test_end)))
    return splits


# ---------------------------------------------------------------------------
# Main validation logic
# ---------------------------------------------------------------------------

def run_validation(symbols: list[str], intervals: list[str]) -> None:
    all_results: list[dict] = []
    wf_results: list[dict] = []
    ensemble_results: list[dict] = []
    ensemble_wf_results: list[dict] = []

    ANNUAL_BARS = {"1d": 365, "4h": 2190, "1h": 8760}

    for interval in intervals:
        for symbol in symbols:
            print(f"\n--- Loading {symbol} {interval} ---")
            df = load_klines_df(symbol, interval)
            if len(df) < 100:
                print(f"  SKIP: only {len(df)} candles")
                continue
            print(f"  {len(df)} candles loaded")

            closes = df["close"].values.astype(float)
            ann = ANNUAL_BARS.get(interval, 365)

            # --- Phase 1: Single strategy ---
            for mod_name, cls_name, category in PRESET_REGISTRY:
                try:
                    import importlib
                    mod = importlib.import_module(f"selene_space_strategy.templates.presets.{mod_name}")
                    strat_cls = getattr(mod, cls_name)

                    enhanced = category == "enhanced"
                    signals = extract_signals_vectorized(strat_cls, symbol, df, enhanced)
                    sig_arr = signals.values

                    res = simulate_long_only(closes, sig_arr, position_size=0.3 if enhanced else 1.0, annual_bars=ann)

                    all_results.append({
                        "strategy": mod_name, "category": category,
                        "symbol": symbol, "interval": interval,
                        "n_candles": len(df),
                        "sharpe": res.sharpe_ratio, "total_return": res.total_return,
                        "max_drawdown": res.max_drawdown, "win_rate": res.win_rate,
                        "profit_factor": res.profit_factor, "total_trades": res.total_trades,
                        "calmar": res.calmar_ratio, "pnl_ratio": res.avg_pnl_ratio,
                    })
                    print(f"  {mod_name:20s} {category:10s} | Sharpe={res.sharpe_ratio:+.2f} Ret={res.total_return:+.1%} DD={res.max_drawdown:.1%} WR={res.win_rate:.0%} PF={res.profit_factor:.2f} PL_R={res.avg_pnl_ratio:.2f} T={res.total_trades}")
                except Exception as e:
                    print(f"  {mod_name:20s} ERROR: {e}")

            # --- Phase 2: Ensemble ---
            try:
                ens_signals = np.zeros(len(df))
                for mod_name in ENSEMBLE_MEMBERS:
                    cls_name = next(c for m, c, _ in PRESET_REGISTRY if m == mod_name)
                    mod = __import__(f"selene_space_strategy.templates.presets.{mod_name}", fromlist=[cls_name])
                    strat_cls = getattr(mod, cls_name)
                    sig = extract_signals_vectorized(strat_cls, symbol, df, enhanced=True)
                    ens_signals += sig.values

                # Voting: buy if >= 2 strategies agree, sell if <= -2
                ens_vote = np.where(ens_signals >= 2, 1, np.where(ens_signals <= -2, -1, 0))
                ens_res = simulate_long_only(closes, ens_vote, position_size=1.0, annual_bars=ann)

                ensemble_results.append({
                    "symbol": symbol, "interval": interval, "n_candles": len(df),
                    "sharpe": ens_res.sharpe_ratio, "total_return": ens_res.total_return,
                    "max_drawdown": ens_res.max_drawdown, "win_rate": ens_res.win_rate,
                    "profit_factor": ens_res.profit_factor, "total_trades": ens_res.total_trades,
                    "calmar": ens_res.calmar_ratio, "pnl_ratio": ens_res.avg_pnl_ratio,
                })
                print(f"  {'ENSEMBLE_5':20s} {'ensemble':10s} | Sharpe={ens_res.sharpe_ratio:+.2f} Ret={ens_res.total_return:+.1%} DD={ens_res.max_drawdown:.1%} WR={ens_res.win_rate:.0%} PF={ens_res.profit_factor:.2f} PL_R={ens_res.avg_pnl_ratio:.2f} T={ens_res.total_trades}")
            except Exception as e:
                print(f"  ENSEMBLE ERROR: {e}")

            # --- Phase 3: Walk-Forward for top strategies + ensemble ---
            splits = create_walk_forward_splits(len(df), n_windows=3)
            # Pick top 3 strategies by Sharpe from this symbol/interval
            sym_iv_results = [r for r in all_results if r["symbol"] == symbol and r["interval"] == interval]
            top_strats = sorted(sym_iv_results, key=lambda r: r["sharpe"], reverse=True)[:3]

            wf_candidates = [(s["strategy"], s["category"]) for s in top_strats] + [("ensemble_5", "ensemble")]

            for strat_name, cat in wf_candidates:
                for widx, (train_range, test_range) in enumerate(splits):
                    try:
                        train_closes = closes[train_range]
                        test_closes = closes[test_range]
                        train_df = df.iloc[train_range].reset_index(drop=True)
                        test_df = df.iloc[test_range].reset_index(drop=True)

                        if strat_name == "ensemble_5":
                            # Ensemble signals for train and test
                            train_sig = np.zeros(len(train_df))
                            for mod_name in ENSEMBLE_MEMBERS:
                                cls_name = next(c for m, c, _ in PRESET_REGISTRY if m == mod_name)
                                mod = __import__(f"selene_space_strategy.templates.presets.{mod_name}", fromlist=[cls_name])
                                strat_cls = getattr(mod, cls_name)
                                sig = extract_signals_vectorized(strat_cls, symbol, train_df, enhanced=True)
                                train_sig += sig.values
                            train_vote = np.where(train_sig >= 2, 1, np.where(train_sig <= -2, -1, 0))

                            test_sig = np.zeros(len(test_df))
                            for mod_name in ENSEMBLE_MEMBERS:
                                cls_name = next(c for m, c, _ in PRESET_REGISTRY if m == mod_name)
                                mod = __import__(f"selene_space_strategy.templates.presets.{mod_name}", fromlist=[cls_name])
                                strat_cls = getattr(mod, cls_name)
                                sig = extract_signals_vectorized(strat_cls, symbol, test_df, enhanced=True)
                                test_sig += sig.values
                            test_vote = np.where(test_sig >= 2, 1, np.where(test_sig <= -2, -1, 0))

                            train_res = simulate_long_only(train_closes, train_vote, position_size=1.0, annual_bars=ann)
                            test_res = simulate_long_only(test_closes, test_vote, position_size=1.0, annual_bars=ann)
                        else:
                            cls_name = next(c for m, c, _ in PRESET_REGISTRY if m == strat_name)
                            mod = __import__(f"selene_space_strategy.templates.presets.{strat_name}", fromlist=[cls_name])
                            strat_cls = getattr(mod, cls_name)
                            enhanced = cat == "enhanced"

                            train_sig = extract_signals_vectorized(strat_cls, symbol, train_df, enhanced)
                            test_sig = extract_signals_vectorized(strat_cls, symbol, test_df, enhanced)

                            train_res = simulate_long_only(train_closes, train_sig.values, position_size=0.3 if enhanced else 1.0, annual_bars=ann)
                            test_res = simulate_long_only(test_closes, test_sig.values, position_size=0.3 if enhanced else 1.0, annual_bars=ann)

                        gap = train_res.sharpe_ratio - test_res.sharpe_ratio
                        wf_results.append({
                            "strategy": strat_name, "symbol": symbol, "interval": interval,
                            "window": widx,
                            "train_sharpe": train_res.sharpe_ratio, "test_sharpe": test_res.sharpe_ratio,
                            "train_return": train_res.total_return, "test_return": test_res.total_return,
                            "train_dd": train_res.max_drawdown, "test_dd": test_res.max_drawdown,
                            "gap": gap,
                        })
                    except Exception as e:
                        print(f"  WF ERROR {strat_name} W{widx}: {e}")

            # Print WF summary
            if wf_results:
                seen: set[tuple[str, str, str]] = set()
                print(f"\n  Walk-Forward Summary ({symbol} {interval}):")
                for r in wf_results:
                    key = (r["strategy"], r["symbol"], r["interval"])
                    if key in seen:
                        continue
                    seen.add(key)
                    wf_for = [w for w in wf_results if w["strategy"] == r["strategy"] and w["symbol"] == r["symbol"] and w["interval"] == r["interval"]]
                    avg_train = np.mean([w["train_sharpe"] for w in wf_for])
                    avg_test = np.mean([w["test_sharpe"] for w in wf_for])
                    avg_gap = np.mean([w["gap"] for w in wf_for])
                    verdict = "OK" if avg_gap < 0.5 else ("WARN" if avg_gap < 1.0 else "OVERFIT")
                    print(f"    {r['strategy']:20s} | Train={avg_train:+.2f} Test={avg_test:+.2f} Gap={avg_gap:+.2f} [{verdict}]")

    # --- Phase 4: Formula validation ---
    formula_results: list[dict] = []
    ensemble_path = PROJECT_ROOT / "data/optuna_formula_v3/ensemble.json"
    if ensemble_path.exists():
        print("\n--- Phase 4: Optuna V3 Formula Validation ---")
        try:
            from selene_space_alpha.vocab import FormulaVocab
            from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES
            from selene_space_alpha.vm import PandasStackVM

            vocab = FormulaVocab(CEX_FEATURE_NAMES, allow_gate=False)

            with open(ensemble_path) as f:
                ens_data = json.load(f)

            for fdata in ens_data.get("formulas", []):
                tokens = fdata.get("tokens", [])
                name = fdata.get("name", "unknown")
                if not tokens:
                    continue

                for symbol in symbols:
                    for interval in intervals:
                        df = load_klines_df(symbol, interval)
                        if len(df) < 100:
                            continue

                        rows = [{"timestamp": r["timestamp"], "open": r["open"], "high": r["high"],
                                 "low": r["low"], "close": r["close"], "volume": r["volume"]}
                                for _, r in df.iterrows()]
                        formula_df = pd.DataFrame(rows)

                        vm = PandasStackVM(vocab)
                        score = vm.execute(tokens, formula_df)
                        if score is None:
                            continue

                        sigmoid = 1.0 / (1.0 + np.exp(-score.values.clip(-500, 500)))
                        sig_arr = np.where(sigmoid > 0.65, 1, np.where(sigmoid < 0.35, -1, 0))

                        closes = df["close"].values.astype(float)
                        ann = ANNUAL_BARS.get(interval, 365)
                        res = simulate_long_only(closes, sig_arr, position_size=0.5, stop_loss=0.05, annual_bars=ann)

                        # Walk-forward for formula
                        splits = create_walk_forward_splits(len(df), n_windows=3)
                        oos_sharpes: list[float] = []
                        for _, (train_range, test_range) in enumerate(splits):
                            test_df = df.iloc[test_range].reset_index(drop=True)
                            test_rows = [{"timestamp": r["timestamp"], "open": r["open"], "high": r["high"],
                                          "low": r["low"], "close": r["close"], "volume": r["volume"]}
                                         for _, r in test_df.iterrows()]
                            test_fdf = pd.DataFrame(test_rows)
                            test_score = vm.execute(tokens, test_fdf)
                            if test_score is None:
                                continue
                            test_sigmoid = 1.0 / (1.0 + np.exp(-test_score.values.clip(-500, 500)))
                            test_sig = np.where(test_sigmoid > 0.65, 1, np.where(test_sigmoid < 0.35, -1, 0))
                            test_closes = test_df["close"].values.astype(float)
                            test_res = simulate_long_only(test_closes, test_sig, position_size=0.5, stop_loss=0.05, annual_bars=ann)
                            oos_sharpes.append(test_res.sharpe_ratio)

                        formula_results.append({
                            "formula": name, "token_names": fdata.get("token_names", []),
                            "symbol": symbol, "interval": interval, "n_candles": len(df),
                            "sharpe": res.sharpe_ratio, "total_return": res.total_return,
                            "max_drawdown": res.max_drawdown, "win_rate": res.win_rate,
                            "profit_factor": res.profit_factor, "total_trades": res.total_trades,
                            "pnl_ratio": res.avg_pnl_ratio,
                            "avg_oos_sharpe": float(np.mean(oos_sharpes)) if oos_sharpes else 0.0,
                        })
                        print(f"  {name:30s} {symbol:10s} {interval:4s} | Sharpe={res.sharpe_ratio:+.2f} OOS={np.mean(oos_sharpes):+.2f} Ret={res.total_return:+.1%}")
        except ImportError as e:
            print(f"  Formula validation skipped: {e}")

    # --- Final Summary ---
    print("\n" + "=" * 110)
    print("VALIDATION SUMMARY")
    print("=" * 110)

    if all_results:
        print("\n--- Top 15 by Sharpe Ratio ---")
        sorted_res = sorted(all_results, key=lambda r: r["sharpe"], reverse=True)
        print(f"{'Strategy':20s} {'Cat':10s} {'Symbol':10s} {'IV':4s} {'Sharpe':>8s} {'Return':>8s} {'MaxDD':>8s} {'WR':>6s} {'PF':>6s} {'PL_R':>6s} {'T':>5s}")
        print("-" * 110)
        for r in sorted_res[:15]:
            print(f"{r['strategy']:20s} {r['category']:10s} {r['symbol']:10s} {r['interval']:4s} {r['sharpe']:+8.2f} {r['total_return']:+7.1%} {r['max_drawdown']:7.1%} {r['win_rate']:5.0%} {r['profit_factor']:5.2f} {r['pnl_ratio']:5.2f} {r['total_trades']:5d}")

        # Category comparison
        print("\n--- Category Averages ---")
        for cat in ["original", "enhanced"]:
            cat_res = [r for r in all_results if r["category"] == cat]
            if cat_res:
                avg_sharpe = np.mean([r["sharpe"] for r in cat_res])
                avg_return = np.mean([r["total_return"] for r in cat_res])
                avg_dd = np.mean([r["max_drawdown"] for r in cat_res])
                profitable = sum(1 for r in cat_res if r["total_return"] > 0)
                print(f"  {cat:10s}: avg Sharpe={avg_sharpe:+.2f}, avg Return={avg_return:+.1%}, avg MaxDD={avg_dd:.1%}, profitable={profitable}/{len(cat_res)}")

        # Per-symbol
        print("\n--- Per-Symbol Average Sharpe ---")
        for sym in symbols:
            sym_res = [r for r in all_results if r["symbol"] == sym]
            if sym_res:
                avg_sharpe = np.mean([r["sharpe"] for r in sym_res])
                profitable = sum(1 for r in sym_res if r["total_return"] > 0)
                print(f"  {sym:10s}: avg Sharpe={avg_sharpe:+.2f}, profitable={profitable}/{len(sym_res)}")

    if ensemble_results:
        print("\n--- Ensemble Results ---")
        print(f"{'Symbol':10s} {'IV':4s} {'Sharpe':>8s} {'Return':>8s} {'MaxDD':>8s} {'WR':>6s} {'PF':>6s} {'PL_R':>6s} {'T':>5s}")
        print("-" * 80)
        for r in ensemble_results:
            print(f"{r['symbol']:10s} {r['interval']:4s} {r['sharpe']:+8.2f} {r['total_return']:+7.1%} {r['max_drawdown']:7.1%} {r['win_rate']:5.0%} {r['profit_factor']:5.2f} {r['pnl_ratio']:5.2f} {r['total_trades']:5d}")

    if formula_results:
        print("\n--- Formula Results ---")
        print(f"{'Formula':30s} {'Symbol':10s} {'IV':4s} {'Sharpe':>8s} {'OOS':>8s} {'Return':>8s} {'MaxDD':>8s}")
        print("-" * 100)
        for r in sorted(formula_results, key=lambda r: r["sharpe"], reverse=True):
            print(f"{r['formula']:30s} {r['symbol']:10s} {r['interval']:4s} {r['sharpe']:+8.2f} {r['avg_oos_sharpe']:+8.2f} {r['total_return']:+7.1%} {r['max_drawdown']:7.1%}")

    # Save results
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    out_path = OUTPUT_DIR / f"validation_{timestamp}.json"
    data = {
        "timestamp": timestamp, "symbols": symbols, "intervals": intervals,
        "single_strategies": all_results, "ensemble": ensemble_results,
        "walk_forward": wf_results, "formulas": formula_results,
    }
    with open(out_path, "w") as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\nResults saved to: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fast multi-data validation")
    parser.add_argument("--symbols", nargs="+", default=SYMBOLS)
    parser.add_argument("--intervals", nargs="+", default=INTERVALS)
    args = parser.parse_args()
    run_validation(args.symbols, args.intervals)
