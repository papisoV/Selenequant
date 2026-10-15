"""V10 Macro Event Guard Validation — verify EventGuard multipliers with V5.2 backtest data.

Runs V5.2 strategy (momentum exit + per-symbol thresholds) on full data,
tags each trade with macro event proximity, then compares performance
inside vs outside event windows to validate the current multiplier schedule.

Current EventGuard multipliers (to validate):
  ±2h  → 0.0 (RED, no new positions)
  ±24h → 0.3 (ORANGE, 30% position)
  ±48h → 0.6 (YELLOW, 60% position)
  else → 1.0 (GREEN, full position)

Usage:
    python scripts/v10_macro_guard_validation.py
    python scripts/v10_macro_guard_validation.py --split 0.5
    python scripts/v10_macro_guard_validation.py --no-momentum
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES_V8
from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS
from selene_space_alpha.vm import PandasStackVM
from optuna_formula_search_v4 import load_multi_tf_df, precompute_features
from v7_trend_filter_backtest import (
    compute_signals,
    compute_regime,
    load_funding_rate_series,
)
from v9_exit_opt import backtest_flexible_exit, compute_sigmoid_scores

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"
MACRO_DB_PATH = PROJECT_ROOT / "data" / "macro_events.db"
V52_FORMULA_NAMES = ["TF_TREND", "TF_PRESSURE", "ADD"]

# Per-symbol thresholds (V9 validated)
PERSYM_THRESHOLDS = {
    "BTC/USDT": (0.65, 0.35),
    "ETH/USDT": (0.63, 0.37),
    "SOL/USDT": (0.70, 0.30),
}

# EventGuard window definitions (hours from event)
# These match event_guard/macro_source.py _MACRO_WINDOWS
GUARD_WINDOWS = [
    ("RED",    -2,   2,  0.0),
    ("ORANGE", -24,  24, 0.3),
    ("YELLOW", -48,  48, 0.6),
    ("GREEN",  None, None, 1.0),  # no event nearby
]

# Finer-grained windows for detailed analysis
FINE_WINDOWS = [
    ("pre_-48h_to_-24h", -48, -24),
    ("pre_-24h_to_-2h",  -24, -2),
    ("pre_-2h_to_0",     -2,  0),
    ("event_0_to_+2h",   0,   2),
    ("post_+2h_to_+24h", 2,   24),
    ("post_+24h_to_+48h", 24,  48),
]


def load_macro_events(macro_db_path: Path) -> pd.DataFrame:
    """Load all macro events from macro_events.db."""
    if not macro_db_path.exists():
        print(f"WARNING: {macro_db_path} not found, no macro tagging")
        return pd.DataFrame()
    conn = sqlite3.connect(str(macro_db_path))
    df = pd.read_sql_query(
        "SELECT event_type, date, timestamp_utc FROM macro_events ORDER BY timestamp_utc ASC",
        conn,
    )
    conn.close()
    if not df.empty:
        df["timestamp_utc"] = pd.to_numeric(df["timestamp_utc"], errors="coerce")
        df = df.dropna(subset=["timestamp_utc"])
    return df


def tag_trade_with_event(
    entry_ts: int,
    exit_ts: int,
    events_df: pd.DataFrame,
) -> dict:
    """Tag a single trade with the most severe macro event proximity.

    A trade is "in" an event window if ANY bar of the trade falls within
    that window. We check the entry time (when the decision to open was made)
    as the primary tag, since that's when EventGuard would have acted.

    Returns dict with:
      - guard_level: RED/ORANGE/YELLOW/GREEN
      - guard_multiplier: 0.0/0.3/0.6/1.0
      - nearest_event_type: str
      - nearest_event_date: str
      - hours_to_nearest: float (signed, negative = before event)
      - fine_window: str (finer-grained window label)
    """
    if events_df.empty:
        return {
            "guard_level": "GREEN",
            "guard_multiplier": 1.0,
            "nearest_event_type": "",
            "nearest_event_date": "",
            "hours_to_nearest": np.nan,
            "fine_window": "normal",
        }

    event_ts = events_df["timestamp_utc"].values.astype(np.int64)
    event_types = events_df["event_type"].values
    event_dates = events_df["date"].values

    # Find nearest event to entry time (when position decision was made)
    idx = np.searchsorted(event_ts, entry_ts, side="left")
    candidates = []
    if idx < len(event_ts):
        dist = (entry_ts - event_ts[idx]) / 3_600_000.0
        candidates.append((abs(dist), dist, idx))
    if idx > 0:
        dist = (entry_ts - event_ts[idx - 1]) / 3_600_000.0
        candidates.append((abs(dist), dist, idx - 1))

    if not candidates:
        return {
            "guard_level": "GREEN",
            "guard_multiplier": 1.0,
            "nearest_event_type": "",
            "nearest_event_date": "",
            "hours_to_nearest": np.nan,
            "fine_window": "normal",
        }

    candidates.sort(key=lambda x: x[0])
    _, signed_dist, best_idx = candidates[0]
    nearest_type = str(event_types[best_idx])
    nearest_date = str(event_dates[best_idx])

    # Determine guard level (tightest window that covers entry time)
    guard_level = "GREEN"
    guard_multiplier = 1.0
    for level, h_before, h_after, mult in GUARD_WINDOWS:
        if h_before is None:
            continue
        if h_before <= signed_dist <= h_after:
            guard_level = level
            guard_multiplier = mult
            break  # GUARD_WINDOWS ordered tightest first

    # Fine-grained window
    fine_window = "normal"
    for fw_name, lo, hi in FINE_WINDOWS:
        if lo <= signed_dist < hi:
            fine_window = fw_name
            break

    return {
        "guard_level": guard_level,
        "guard_multiplier": guard_multiplier,
        "nearest_event_type": nearest_type,
        "nearest_event_date": nearest_date,
        "hours_to_nearest": signed_dist,
        "fine_window": fine_window,
    }


def run_backtest_with_macro_tags(
    symbols: list[str],
    split_frac: float,
    use_momentum: bool,
    position_size: float,
    cooldown_bars: int,
) -> list[dict]:
    """Run V5.2 backtest and tag each trade with macro event info."""
    v4_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=CEX_FEATURE_NAMES_V8, operator_names=v4_op_names)
    v52_tokens = [vocab.token_names.index(n) for n in V52_FORMULA_NAMES]
    vm = PandasStackVM(vocab)

    events_df = load_macro_events(MACRO_DB_PATH)
    if events_df.empty:
        print("ERROR: No macro events loaded. Run macro_calendar_sync.py first.")
        return []

    print(f"Loaded {len(events_df)} macro events from {MACRO_DB_PATH}")
    event_type_counts = events_df["event_type"].value_counts().to_dict()
    for et, cnt in event_type_counts.items():
        print(f"  {et}: {cnt} events")

    all_tagged_trades: list[dict] = []

    for symbol in symbols:
        buy_th, sell_th = PERSYM_THRESHOLDS.get(symbol, (0.57, 0.41))
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
            print(f"  {symbol}: NO DATA")
            continue

        features = precompute_features(df, vocab, vm)
        signals = compute_signals(v52_tokens, vocab, df, features, buy_th, sell_th)
        sigmoid = compute_sigmoid_scores(v52_tokens, vocab, df, features)
        regime_series, trend_dir_series = compute_regime(DB_PATH, symbol, "binance", df["timestamp"])
        fr_series = load_funding_rate_series(symbol, "binance", df["timestamp"])

        split_idx = int(n * split_frac)
        df_oos = df.iloc[split_idx:].reset_index(drop=True)
        sig_oos = signals[split_idx:]
        score_oos = sigmoid[split_idx:]
        regime_oos = regime_series.iloc[split_idx:].reset_index(drop=True)
        trend_oos = trend_dir_series.iloc[split_idx:].reset_index(drop=True)
        fr_oos = fr_series.iloc[split_idx:].reset_index(drop=True)

        eq, trades = backtest_flexible_exit(
            df_oos, sig_oos, config,
            regime_series=regime_oos, trend_dir_series=trend_oos,
            funding_rate_series=fr_oos, pos_size_mult=1.0 / len(symbols),
            momentum_exit=use_momentum, signal_scores=score_oos,
        )

        closed = [t for t in trades if "pnl" in t]
        print(f"  {symbol}: {len(closed)} closed trades (OOS from bar {split_idx})")

        for t in closed:
            entry_ts = t.get("entry_ts", 0)
            exit_ts = t.get("exit_ts", 0)
            tag = tag_trade_with_event(entry_ts, exit_ts, events_df)
            t.update(tag)
            t["symbol"] = symbol
            all_tagged_trades.append(t)

    return all_tagged_trades


def analyze_tagged_trades(trades: list[dict]) -> None:
    """Print comprehensive analysis of macro-tagged trades."""
    if not trades:
        print("No trades to analyze.")
        return

    closed = [t for t in trades if "pnl" in t]
    n_total = len(closed)

    print(f"\n{'='*90}")
    print(f"  MACRO EVENT GUARD VALIDATION — {n_total} trades")
    print(f"{'='*90}")

    # --- 1. Overall stats ---
    all_pnls = np.array([t["pnl"] for t in closed])
    overall_wr = np.mean(all_pnls > 0)
    overall_avg = np.mean(all_pnls)
    print(f"\n  Overall: {n_total} trades, WR={overall_wr:.1%}, Avg PnL={overall_avg:+.4f}")

    # --- 2. By guard level ---
    print("\n--- By EventGuard Level ---")
    print(f"  {'Level':>8} {'Mult':>5} {'Trades':>7} {'WR':>6} {'Avg PnL':>9} {'Med PnL':>9} {'Worst':>9} {'Best':>9}")
    print(f"  {'------':>8} {'----':>5} {'------':>7} {'------':>6} {'--------':>9} {'--------':>9} {'-----':>9} {'----':>9}")

    for level, mult in [("GREEN", 1.0), ("YELLOW", 0.6), ("ORANGE", 0.3), ("RED", 0.0)]:
        level_trades = [t for t in closed if t.get("guard_level") == level]
        if not level_trades:
            print(f"  {level:>8} {mult:>5.1f} {'0':>7} {'—':>6} {'—':>9} {'—':>9} {'—':>9} {'—':>9}")
            continue
        pnls = np.array([t["pnl"] for t in level_trades])
        wr = np.mean(pnls > 0)
        print(f"  {level:>8} {mult:>5.1f} {len(level_trades):>7} {wr:>5.1%} {np.mean(pnls):>+8.4f} "
              f"{np.median(pnls):>+8.4f} {np.min(pnls):>+8.4f} {np.max(pnls):>+8.4f}")

    # --- 3. By fine-grained window ---
    print("\n--- By Fine-Grained Event Window ---")
    print(f"  {'Window':>22} {'Trades':>7} {'WR':>6} {'Avg PnL':>9} {'Med PnL':>9} {'% of Total':>10}")
    print(f"  {'------':>22} {'------':>7} {'------':>6} {'--------':>9} {'--------':>9} {'----------':>10}")

    for fw_name, lo, hi in FINE_WINDOWS:
        fw_trades = [t for t in closed if t.get("fine_window") == fw_name]
        if not fw_trades:
            print(f"  {fw_name:>22} {'0':>7} {'—':>6} {'—':>9} {'—':>9} {'—':>10}")
            continue
        pnls = np.array([t["pnl"] for t in fw_trades])
        wr = np.mean(pnls > 0)
        pct = len(fw_trades) / n_total * 100
        print(f"  {fw_name:>22} {len(fw_trades):>7} {wr:>5.1%} {np.mean(pnls):>+8.4f} "
              f"{np.median(pnls):>+8.4f} {pct:>9.1f}%")

    normal_trades = [t for t in closed if t.get("fine_window") == "normal"]
    if normal_trades:
        pnls = np.array([t["pnl"] for t in normal_trades])
        wr = np.mean(pnls > 0)
        pct = len(normal_trades) / n_total * 100
        print(f"  {'normal':>22} {len(normal_trades):>7} {wr:>5.1%} {np.mean(pnls):>+8.4f} "
              f"{np.median(pnls):>+8.4f} {pct:>9.1f}%")

    # --- 4. By event type ---
    print("\n--- By Event Type (entry within ±48h) ---")
    print(f"  {'Event':>8} {'Trades':>7} {'WR':>6} {'Avg PnL':>9} {'Guard WR':>9} {'Normal WR':>10}")
    print(f"  {'-----':>8} {'------':>7} {'------':>6} {'--------':>9} {'--------':>9} {'----------':>10}")

    for etype in ["FOMC", "CPI", "NFP"]:
        etype_trades = [t for t in closed if t.get("nearest_event_type") == etype and t.get("guard_level") != "GREEN"]
        if not etype_trades:
            print(f"  {etype:>8} {'0':>7} {'—':>6} {'—':>9} {'—':>9} {'—':>10}")
            continue
        pnls = np.array([t["pnl"] for t in etype_trades])
        wr = np.mean(pnls > 0)
        # Compare with normal trades
        normal_pnls = np.array([t["pnl"] for t in normal_trades]) if normal_trades else np.array([])
        normal_wr = np.mean(normal_pnls > 0) if len(normal_pnls) > 0 else 0
        print(f"  {etype:>8} {len(etype_trades):>7} {wr:>5.1%} {np.mean(pnls):>+8.4f} "
              f"{wr:>8.1%} {normal_wr:>9.1%}")

    # --- 5. Per-symbol breakdown ---
    print("\n--- Per-Symbol: Event vs Normal ---")
    print(f"  {'Symbol':>10} {'Event WR':>9} {'Normal WR':>10} {'Event Avg':>9} {'Normal Avg':>10} {'Event N':>8} {'Normal N':>9}")
    print(f"  {'------':>10} {'--------':>9} {'----------':>10} {'--------':>9} {'----------':>10} {'--------':>8} {'---------':>9}")

    for symbol in sorted(set(t.get("symbol", "") for t in closed)):
        sym_trades = [t for t in closed if t.get("symbol") == symbol]
        event_trades = [t for t in sym_trades if t.get("guard_level") != "GREEN"]
        norm_trades = [t for t in sym_trades if t.get("guard_level") == "GREEN"]

        if event_trades:
            ev_pnls = np.array([t["pnl"] for t in event_trades])
            ev_wr = np.mean(ev_pnls > 0)
            ev_avg = np.mean(ev_pnls)
        else:
            ev_wr = 0
            ev_avg = 0

        if norm_trades:
            nm_pnls = np.array([t["pnl"] for t in norm_trades])
            nm_wr = np.mean(nm_pnls > 0)
            nm_avg = np.mean(nm_pnls)
        else:
            nm_wr = 0
            nm_avg = 0

        print(f"  {symbol:>10} {ev_wr:>8.1%} {nm_wr:>9.1%} {ev_avg:>+8.4f} {nm_avg:>+9.4f} "
              f"{len(event_trades):>8} {len(norm_trades):>9}")

    # --- 6. Multiplier simulation ---
    print("\n--- Multiplier Simulation: What if we applied EventGuard? ---")
    print("  Simulating different multiplier schedules on the SAME trades.")
    print("  PnL = sum(trade_pnl * multiplier_at_entry)")
    print()

    # Baseline: no guard (all trades at mult=1.0)
    baseline_pnl = np.sum(all_pnls)
    baseline_wr = overall_wr

    # Current guard schedule
    current_pnl = np.sum([t["pnl"] * t.get("guard_multiplier", 1.0) for t in closed])
    current_blocked = sum(1 for t in closed if t.get("guard_multiplier", 1.0) < 0.01)

    # Alternative schedules
    alt_schedules = [
        ("Current (±2h=0, ±24h=0.3, ±48h=0.6)", lambda lvl: {
            "GREEN": 1.0, "YELLOW": 0.6, "ORANGE": 0.3, "RED": 0.0
        }.get(lvl, 1.0)),
        ("Aggressive (±2h=0, ±24h=0.1, ±48h=0.3)", lambda lvl: {
            "GREEN": 1.0, "YELLOW": 0.3, "ORANGE": 0.1, "RED": 0.0
        }.get(lvl, 1.0)),
        ("Conservative (±2h=0.3, ±24h=0.6, ±48h=0.8)", lambda lvl: {
            "GREEN": 1.0, "YELLOW": 0.8, "ORANGE": 0.6, "RED": 0.3
        }.get(lvl, 1.0)),
        ("Block RED only (±2h=0, else=1.0)", lambda lvl: {
            "GREEN": 1.0, "YELLOW": 1.0, "ORANGE": 1.0, "RED": 0.0
        }.get(lvl, 1.0)),
        ("Block RED+ORANGE (±24h=0, else=1.0)", lambda lvl: {
            "GREEN": 1.0, "YELLOW": 1.0, "ORANGE": 0.0, "RED": 0.0
        }.get(lvl, 1.0)),
        ("No guard (all=1.0)", lambda lvl: 1.0),
    ]

    print(f"  {'Schedule':>45} {'Total PnL':>10} {'vs No Guard':>11} {'Blocked':>8} {'WR (unblocked)':>14}")
    print(f"  {'--------':>45} {'--------':>10} {'----------':>11} {'-------':>8} {'--------------':>14}")

    for label, mult_fn in alt_schedules:
        adj_pnls = [t["pnl"] * mult_fn(t.get("guard_level", "GREEN")) for t in closed]
        total = np.sum(adj_pnls)
        blocked = sum(1 for t in closed if mult_fn(t.get("guard_level", "GREEN")) < 0.01)
        unblocked = [t for t in closed if mult_fn(t.get("guard_level", "GREEN")) >= 0.01]
        ub_wr = np.mean([t["pnl"] for t in unblocked] > np.zeros(len(unblocked))) if unblocked else 0
        diff = total - baseline_pnl
        print(f"  {label:>45} {total:>+9.2f} {diff:>+10.2f} {blocked:>8} {ub_wr:>13.1%}")

    # --- 7. Verdict ---
    print(f"\n{'='*90}")
    print("  VERDICT")
    print(f"{'='*90}")

    # Check if event-window trades are worse than normal
    event_trades = [t for t in closed if t.get("guard_level") != "GREEN"]
    if event_trades:
        ev_pnls = np.array([t["pnl"] for t in event_trades])
        nm_pnls = np.array([t["pnl"] for t in normal_trades]) if normal_trades else np.array([])

        ev_wr = np.mean(ev_pnls > 0)
        nm_wr = np.mean(nm_pnls > 0) if len(nm_pnls) > 0 else 0
        ev_avg = np.mean(ev_pnls)
        nm_avg = np.mean(nm_pnls) if len(nm_pnls) > 0 else 0

        print(f"\n  Event-window trades: WR={ev_wr:.1%}, Avg PnL={ev_avg:+.4f} (n={nm_wr:.1%}, Avg PnL={nm_avg:+.4f}")

        if ev_wr < nm_wr - 0.05 and ev_avg < nm_avg:
            print(f"  → Event windows HURT performance (WR {ev_wr:.1%} vs {nm_wr:.1%}, PnL {ev_avg:+.4f} vs {nm_avg:+.4f})")
            print("  → Current EventGuard multipliers are JUSTIFIED")
        elif ev_wr < nm_wr - 0.03:
            print(f"  → Event windows slightly worse (WR gap {nm_wr - ev_wr:.1%})")
            print("  → Guard provides marginal benefit, current schedule reasonable")
        elif ev_wr >= nm_wr:
            print(f"  → Event windows NOT worse than normal (WR {ev_wr:.1%} >= {nm_wr:.1%})")
            print("  → EventGuard may be OVER-CAUTIOUS — consider relaxing multipliers")
        else:
            print("  → Mixed results, need more data or per-event-type analysis")

        # Per-level verdict
        print("\n  Per-level assessment:")
        for level, mult in [("RED", 0.0), ("ORANGE", 0.3), ("YELLOW", 0.6)]:
            lt = [t for t in closed if t.get("guard_level") == level]
            if not lt:
                print(f"    {level} (mult={mult}): No trades in this window")
                continue
            lpnls = np.array([t["pnl"] for t in lt])
            lwr = np.mean(lpnls > 0)
            lavg = np.mean(lpnls)
            if lwr < nm_wr - 0.05 and lavg < 0:
                verdict = "HARMFUL → blocking/reducing is correct"
            elif lwr < nm_wr - 0.03:
                verdict = "SLIGHTLY HARMFUL → reducing is reasonable"
            elif lwr >= nm_wr:
                verdict = "NOT HARMFUL → consider relaxing multiplier"
            else:
                verdict = "NEUTRAL → current multiplier acceptable"
            print(f"    {level} (mult={mult}): WR={lwr:.1%}, Avg={lavg:+.4f}, N={len(lt)} → {verdict}")
    else:
        print("  No trades fell within macro event windows — cannot validate.")


def main() -> None:
    parser = argparse.ArgumentParser(description="V10 Macro Event Guard Validation")
    parser.add_argument("--symbols", nargs="+", default=["BTC/USDT", "ETH/USDT", "SOL/USDT"])
    parser.add_argument("--split", type=float, default=0.5, help="OOS start fraction (default 0.5)")
    parser.add_argument("--no-momentum", action="store_true", help="Disable momentum exit")
    parser.add_argument("--position-size", type=float, default=0.60, help="Position size (default 0.60)")
    parser.add_argument("--cooldown", type=int, default=4, help="Cooldown bars (default 4)")
    parser.add_argument("--output", default=str(PROJECT_ROOT / "data" / "v10_macro_guard_report.json"))
    args = parser.parse_args()

    print("=" * 90)
    print("  V10 Macro Event Guard Validation")
    print("=" * 90)
    print(f"  Symbols: {args.symbols}")
    print(f"  OOS split: {args.split:.0%}")
    print(f"  Momentum exit: {not args.no_momentum}")
    print(f"  Position size: {args.position_size:.0%}, Cooldown: {args.cooldown}")
    print()

    trades = run_backtest_with_macro_tags(
        symbols=args.symbols,
        split_frac=args.split,
        use_momentum=not args.no_momentum,
        position_size=args.position_size,
        cooldown_bars=args.cooldown,
    )

    if not trades:
        print("No trades generated. Check data availability.")
        return

    analyze_tagged_trades(trades)

    # Save detailed trade data
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(trades, f, indent=2, default=str)
    print(f"\n  Detailed trade data saved to: {output_path}")


if __name__ == "__main__":
    main()
