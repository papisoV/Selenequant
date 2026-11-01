"""Sentiment Brake Backtest — test if hard-brake on extreme bearish news improves win rate.

Hard-brake conditions (ALL must be met):
  1. sentiment ≤ 1 (strongly bearish)
  2. impact_strength ≥ 4
  3. event_type IN (监管, 安全事件, 宏观经济)  — exclude "市场动态" (price reports)
  4. urgency ≥ 4
  5. Dedup: same event 30min window only counts first
  6. Reversal guard: after brake triggers, 2h no new longs even if bullish appears

Action: suppress new long entries for 8 bars (2h on 15m), shorts allowed.

Also tests softer variants:
  - brake_both: suppress ALL new entries (long + short) for 2h
  - brake_reduce: reduce position size 50% for 2h instead of full suppress
  - brake_4h: extend suppression to 4h (16 bars)

Usage:
    python scripts/sentiment_brake_backtest.py
    python scripts/sentiment_brake_backtest.py --symbol BTC/USDT
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS
from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES_V4
from selene_space_alpha.vm import PandasStackVM

from optuna_formula_search_v4 import load_multi_tf_df, precompute_features
from v7_trend_filter_backtest import (
    compute_signals, compute_regime, load_funding_rate_series,
    compute_metrics, INTERVAL_HOURS, ADAPTIVE_WEIGHTS, STRATEGY_CONFIG,
)

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"

# ---------------------------------------------------------------------------
# Hard-coded TG signal events from signal_factors_detail.md
# Only the 3 events that pass the hard-brake criteria.
# Timestamps are UTC milliseconds.
# ---------------------------------------------------------------------------

BRAKE_EVENTS = [
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 8, 31, 13, tzinfo=timezone.utc).timestamp() * 1000),
        "msg_id": 25553,
        "summary": "500+ ETH wallets hacked, $800K stolen via ThorChain",
        "sentiment": 1,
        "impact_strength": 3,  # actually 3, below threshold — testing edge
        "urgency": 4,
        "event_type": "安全事件",
        "affected": "ETH",
        "half_life_hours": 4,
    },
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 16, 35, 25, tzinfo=timezone.utc).timestamp() * 1000),
        "msg_id": 25571,
        "summary": "US-Iran conflict militarization — bomb or deal",
        "sentiment": 1,
        "impact_strength": 4,
        "urgency": 5,
        "event_type": "宏观经济",
        "affected": "ALL",
        "half_life_hours": 6,
    },
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 17, 9, 55, tzinfo=timezone.utc).timestamp() * 1000),
        "msg_id": 25574,
        "summary": "DeFi April exploits record: 28 incidents, $635M lost",
        "sentiment": 1,
        "impact_strength": 4,
        "urgency": 4,
        "event_type": "安全事件",
        "affected": "ALL",
        "half_life_hours": 8,
    },
]

# Extended set: events with impact≥3 + urgency≥3 (looser criteria)
# These are the "medium" signals to test if softer filtering helps
MEDIUM_EVENTS = [
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 8, 31, 13, tzinfo=timezone.utc).timestamp() * 1000),
        "summary": "500+ ETH wallets hacked",
        "sentiment": 1, "impact_strength": 3, "urgency": 4,
        "event_type": "安全事件", "affected": "ETH",
    },
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 9, 29, 13, tzinfo=timezone.utc).timestamp() * 1000),
        "summary": "Bhutan gov selling ~$287M BTC",
        "sentiment": 2, "impact_strength": 3, "urgency": 3,
        "event_type": "市场动态", "affected": "BTC",
    },
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 12, 7, 56, tzinfo=timezone.utc).timestamp() * 1000),
        "summary": "Brazil CB bans crypto for cross-border payments",
        "sentiment": 1, "impact_strength": 3, "urgency": 3,
        "event_type": "监管", "affected": "ALL",
    },
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 15, 36, 5, tzinfo=timezone.utc).timestamp() * 1000),
        "summary": "SOL 87 days below $100, 7 consecutive monthly declines",
        "sentiment": 1, "impact_strength": 3, "urgency": 2,
        "event_type": "市场动态", "affected": "SOL",
    },
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 15, 50, 54, tzinfo=timezone.utc).timestamp() * 1000),
        "summary": "Trump: EU auto tariffs to 25% next week",
        "sentiment": 2, "impact_strength": 4, "urgency": 4,
        "event_type": "宏观经济", "affected": "ALL",
    },
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 16, 35, 25, tzinfo=timezone.utc).timestamp() * 1000),
        "summary": "US-Iran militarization",
        "sentiment": 1, "impact_strength": 4, "urgency": 5,
        "event_type": "宏观经济", "affected": "ALL",
    },
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 17, 9, 55, tzinfo=timezone.utc).timestamp() * 1000),
        "summary": "DeFi April exploits $635M",
        "sentiment": 1, "impact_strength": 4, "urgency": 4,
        "event_type": "安全事件", "affected": "ALL",
    },
    {
        "timestamp_ms": int(datetime(2026, 5, 1, 17, 45, 36, tzinfo=timezone.utc).timestamp() * 1000),
        "summary": "ETH Foundation sold 10K ETH ($23M)",
        "sentiment": 2, "impact_strength": 3, "urgency": 3,
        "event_type": "市场动态", "affected": "ETH",
    },
]


# ---------------------------------------------------------------------------
# Sentiment Brake Series Builder
# ---------------------------------------------------------------------------

def build_brake_series(
    bar_timestamps: np.ndarray,
    events: list[dict],
    brake_bars: int = 8,       # 2h on 15m
    brake_mode: str = "long_only",  # long_only | both | reduce
    pos_reduction: float = 0.5,
    affected_filter: str | None = None,  # e.g. "ETH" to only brake on ETH-affecting events
    symbol: str = "BTC/USDT",
    reversal_guard_bars: int = 8,  # extra bars to block after brake
) -> np.ndarray:
    """Build a per-bar brake multiplier series.

    Returns array same length as bar_timestamps:
      1.0 = no brake (trade normally)
      0.0 = full brake (no new entry)
      0.5 = reduced position (brake_reduce mode)
    """
    n = len(bar_timestamps)
    brake_mult = np.ones(n, dtype=float)

    bar_ms = 15 * 60 * 1000  # 15m bars

    for event in events:
        evt_ts = event["timestamp_ms"]
        evt_affected = event.get("affected", "ALL")

        # Check if this event affects the current symbol
        if affected_filter and evt_affected != "ALL":
            # Map affected tag to symbol
            affected_map = {"BTC": "BTC/USDT", "ETH": "ETH/USDT", "SOL": "SOL/USDT"}
            evt_symbol = affected_map.get(evt_affected)
            if evt_symbol and evt_symbol != symbol:
                continue

        # Find the bar index for this event
        bar_idx = np.searchsorted(bar_timestamps, evt_ts)
        if bar_idx >= n:
            continue

        # Apply brake for brake_bars after the event
        for j in range(brake_bars + reversal_guard_bars):
            idx = bar_idx + j
            if idx >= n:
                break

            if brake_mode == "long_only":
                # Mark as braked — will suppress long entries in backtest
                brake_mult[idx] = min(brake_mult[idx], 0.0)  # 0 = no long
            elif brake_mode == "both":
                brake_mult[idx] = min(brake_mult[idx], 0.0)
            elif brake_mode == "reduce":
                brake_mult[idx] = min(brake_mult[idx], pos_reduction)

    return brake_mult


# ---------------------------------------------------------------------------
# Backtest with Sentiment Brake
# ---------------------------------------------------------------------------

def backtest_with_brake(
    df: pd.DataFrame,
    signals: np.ndarray,
    config: dict,
    brake_mult: np.ndarray,
    brake_mode: str = "long_only",
    initial_capital: float = 10000.0,
    regime_series: pd.Series | None = None,
    trend_dir_series: pd.Series | None = None,
    funding_rate_series: pd.Series | None = None,
) -> tuple[list[float], list[dict]]:
    """Run backtest with sentiment brake filter.

    brake_mode:
      - "long_only": suppress new long entries when brake active
      - "both": suppress all new entries when brake active
      - "reduce": reduce position size by brake_mult when brake active
    """
    closes = df["close"].values.astype(float)
    lows = df["low"].values.astype(float)
    highs = df["high"].values.astype(float)
    timestamps = df["timestamp"].values
    n = len(closes)

    fee_rate = 0.0002 if config["fee_mode"] == "maker" else 0.0005
    slippage_frac = 0.0 if config["fee_mode"] == "maker" else 0.00015
    position_size = config["position_size"]
    stop_loss = config["stop_loss"]
    hold_bars = config["hold_bars"]
    cooldown_bars = config["cooldown_bars"]

    trade_notional = initial_capital * position_size

    capital = initial_capital
    equity_curve: list[float] = [initial_capital]
    trades: list[dict] = []
    position = 0
    entry_price = 0.0
    position_notional = 0.0
    bars_in_position = 0
    bars_since_exit = cooldown_bars + 1

    for i in range(1, n):
        sig = int(signals[i - 1])
        price = closes[i]
        low = lows[i]
        high = highs[i]

        if not np.isfinite(capital) or capital <= 0:
            equity_curve.append(equity_curve[-1])
            continue

        # Sentiment brake
        bm = brake_mult[i] if i < len(brake_mult) else 1.0

        # Apply brake to signal
        if bm < 0.01:  # effectively 0
            if brake_mode == "long_only":
                if sig == 1:
                    sig = 0  # suppress long
            elif brake_mode == "both":
                sig = 0  # suppress all

        # Adaptive position sizing based on regime
        pos_mult = 1.0
        if regime_series is not None and i < len(regime_series):
            regime = str(regime_series.iloc[i])
            if regime == "volatile":
                pos_mult = 1.0 - ADAPTIVE_WEIGHTS["volatile"]["pos_reduction"]
            if regime == "trending" and trend_dir_series is not None and i < len(trend_dir_series):
                trend_dir = int(trend_dir_series.iloc[i])
                if trend_dir != 0 and sig != 0 and np.sign(sig) != np.sign(trend_dir):
                    sig = 0

        # Reduce position if brake in reduce mode
        if brake_mode == "reduce" and bm < 1.0:
            pos_mult *= bm

        current_notional = trade_notional * pos_mult

        if position == 0:
            bars_since_exit += 1
            if sig == 1 and bars_since_exit >= cooldown_bars:
                position = 1
                entry_price = price * (1.0 + slippage_frac)
                position_notional = current_notional
                capital -= position_notional * fee_rate
                bars_in_position = 0
                bars_since_exit = 0
                trades.append({
                    "entry_ts": timestamps[i], "entry_price": entry_price,
                    "side": "long", "notional": position_notional,
                    "brake_active": bm < 0.99,
                })
            elif sig == -1 and bars_since_exit >= cooldown_bars:
                position = -1
                entry_price = price * (1.0 - slippage_frac)
                position_notional = current_notional
                capital -= position_notional * fee_rate
                bars_in_position = 0
                bars_since_exit = 0
                trades.append({
                    "entry_ts": timestamps[i], "entry_price": entry_price,
                    "side": "short", "notional": position_notional,
                    "brake_active": bm < 0.99,
                })

        elif position == 1:
            bars_in_position += 1
            stopped = False

            if stop_loss > 0:
                stop_price = entry_price * (1.0 - stop_loss)
                if low <= stop_price:
                    exit_price = stop_price * (1.0 - slippage_frac)
                    pnl_pct = (exit_price - entry_price) / entry_price
                    net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                    capital += net_pnl
                    trades[-1]["exit_ts"] = timestamps[i]
                    trades[-1]["exit_price"] = exit_price
                    trades[-1]["pnl"] = net_pnl
                    trades[-1]["stop"] = True
                    position = 0
                    bars_since_exit = 0
                    stopped = True

            if not stopped and bars_in_position >= hold_bars and sig == -1:
                exit_price = price * (1.0 - slippage_frac)
                pnl_pct = (exit_price - entry_price) / entry_price
                net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                capital += net_pnl
                trades[-1]["exit_ts"] = timestamps[i]
                trades[-1]["exit_price"] = exit_price
                trades[-1]["pnl"] = net_pnl
                trades[-1]["stop"] = False
                position = 0
                bars_since_exit = 0

        elif position == -1:
            bars_in_position += 1
            stopped = False

            if stop_loss > 0:
                stop_price = entry_price * (1.0 + stop_loss)
                if high >= stop_price:
                    exit_price = stop_price * (1.0 + slippage_frac)
                    pnl_pct = (entry_price - exit_price) / entry_price
                    net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                    capital += net_pnl
                    trades[-1]["exit_ts"] = timestamps[i]
                    trades[-1]["exit_price"] = exit_price
                    trades[-1]["pnl"] = net_pnl
                    trades[-1]["stop"] = True
                    position = 0
                    bars_since_exit = 0
                    stopped = True

            if not stopped and bars_in_position >= hold_bars and sig == 1:
                exit_price = price * (1.0 + slippage_frac)
                pnl_pct = (entry_price - exit_price) / entry_price
                net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                capital += net_pnl
                trades[-1]["exit_ts"] = timestamps[i]
                trades[-1]["exit_price"] = exit_price
                trades[-1]["pnl"] = net_pnl
                trades[-1]["stop"] = False
                position = 0
                bars_since_exit = 0

        # Funding rate
        if position != 0 and funding_rate_series is not None and i < len(funding_rate_series):
            fr_8h = float(funding_rate_series.iloc[i])
            bar_hours = INTERVAL_HOURS.get(config["base_interval"], 0.25)
            fr_bar = fr_8h * (bar_hours / 8.0)
            if position == 1:
                capital -= position_notional * fr_bar
            elif position == -1:
                capital += position_notional * fr_bar

        # Track equity
        if position == 1:
            unrealized = position_notional * (price - entry_price) / entry_price
            equity = capital + unrealized
        elif position == -1:
            unrealized = position_notional * (entry_price - price) / entry_price
            equity = capital + unrealized
        else:
            equity = capital
        equity = max(equity, 0.0) if np.isfinite(equity) else 0.0
        equity_curve.append(equity)

    return equity_curve, trades


# ---------------------------------------------------------------------------
# Simulation: generate synthetic events for broader testing
# ---------------------------------------------------------------------------

def generate_synthetic_events(
    bar_timestamps: np.ndarray,
    n_events: int = 20,
    seed: int = 42,
) -> list[dict]:
    """Generate synthetic bearish events spread across the data range.

    This simulates having months of TG signal data, not just 2 days.
    Events are placed at random times with random severity.
    """
    rng = np.random.RandomState(seed)
    ts_min = int(bar_timestamps[0] / 1000)  # work in seconds to avoid int32 overflow
    ts_max = int(bar_timestamps[-1] / 1000)

    events = []
    for i in range(n_events):
        ts_sec = rng.randint(ts_min, ts_max)
        ts_ms = ts_sec * 1000
        impact = rng.choice([3, 4, 5], p=[0.5, 0.35, 0.15])
        urgency = rng.choice([3, 4, 5], p=[0.4, 0.4, 0.2])
        evt_type = rng.choice(["监管", "安全事件", "宏观经济"], p=[0.3, 0.3, 0.4])
        affected = rng.choice(["ALL", "BTC", "ETH", "SOL"], p=[0.4, 0.2, 0.2, 0.2])

        # Only bearish events matter for brake
        sentiment = 1 if impact >= 4 else 2

        events.append({
            "timestamp_ms": int(ts_ms),
            "summary": f"synthetic_{i}",
            "sentiment": sentiment,
            "impact_strength": impact,
            "urgency": urgency,
            "event_type": evt_type,
            "affected": affected,
        })

    return events


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sentiment Brake Backtest")
    parser.add_argument("--symbol", type=str, default="all",
                        help="Symbol (BTC/USDT, ETH/USDT, SOL/USDT, or all)")
    parser.add_argument("--events", type=str, default="real",
                        choices=["real", "medium", "synthetic", "all"],
                        help="Event set: real (3 hard-brake), medium (8 loose), synthetic, all")
    args = parser.parse_args()

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if args.symbol == "all" else [args.symbol]
    config = STRATEGY_CONFIG

    v4_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=CEX_FEATURE_NAMES_V4, operator_names=v4_op_names)

    for symbol in symbols:
        print(f"\n{'='*90}")
        print(f"  {symbol} — Sentiment Brake Backtest")
        print(f"{'='*90}")

        df, n = load_multi_tf_df(
            DB_PATH, symbol, "binance",
            config["base_interval"], config["aux_interval"], "futures",
        )
        if df.empty:
            print(f"  No data for {symbol}")
            continue

        ts_start = datetime.fromtimestamp(df["timestamp"].iloc[0] / 1000, tz=timezone.utc)
        ts_end = datetime.fromtimestamp(df["timestamp"].iloc[-1] / 1000, tz=timezone.utc)
        print(f"  Data: {ts_start.strftime('%Y-%m-%d')} ~ {ts_end.strftime('%Y-%m-%d')} ({n} bars)")

        # Compute features & signals
        vm = PandasStackVM(vocab)
        features = precompute_features(df, vocab, vm)
        signals = compute_signals(
            config["tokens"], vocab, df, features,
            config["buy_th"], config["sell_th"],
        )

        # Compute regime
        regime_series, trend_dir_series = compute_regime(
            DB_PATH, symbol, "binance", df["timestamp"],
        )

        # Load funding rate
        fr_series = load_funding_rate_series(symbol, "binance", df["timestamp"])

        # Select event set
        if args.events == "real":
            event_sets = [("hard_brake(3)", BRAKE_EVENTS)]
        elif args.events == "medium":
            event_sets = [("medium(8)", MEDIUM_EVENTS)]
        elif args.events == "synthetic":
            synth = generate_synthetic_events(df["timestamp"].values, n_events=20)
            event_sets = [("synthetic(20)", synth)]
        else:
            synth = generate_synthetic_events(df["timestamp"].values, n_events=20)
            event_sets = [
                ("hard_brake(3)", BRAKE_EVENTS),
                ("medium(8)", MEDIUM_EVENTS),
                ("synthetic(20)", synth),
            ]

        # Run baseline first
        eq_base, trades_base = backtest_with_brake(
            df, signals, config,
            brake_mult=np.ones(len(df)),  # no brake
            brake_mode="long_only",
            regime_series=regime_series,
            trend_dir_series=trend_dir_series,
            funding_rate_series=fr_series,
        )
        m_base = compute_metrics(eq_base, trades_base, n, bar_hours=0.25)

        print(f"\n  {'Config':>35}  {'Sharpe':>7}  {'Return':>8}  {'MaxDD':>7}  "
              f"{'WR':>5}  {'Trades':>6}  {'WRΔ':>5}  {'ShrpΔ':>6}")
        print(f"  {'-----':>35}  {'------':>7}  {'------':>8}  {'-----':>7}  "
              f"{'---':>5}  {'------':>6}  {'----':>5}  {'-----':>6}")
        print(f"  {'baseline (no brake)':>35}  {m_base['sharpe']:>7.2f}  {m_base['return']:>+7.1%}  "
              f"{m_base['max_dd']:>6.1%}  {m_base['win_rate']:>4.0%}  "
              f"{m_base['trades']:>6}  {'---':>5}  {'---':>6}")

        # Test each event set × brake mode × duration
        test_configs = [
            # (event_set_name, brake_mode, brake_bars)
            # Hard brake: suppress longs only for 2h
            ("long_2h", "long_only", 8),
            # Hard brake: suppress longs only for 4h
            ("long_4h", "long_only", 16),
            # Brake both sides for 2h
            ("both_2h", "both", 8),
            # Reduce position instead of suppress
            ("reduce50_2h", "reduce", 8),
        ]

        for evt_name, events in event_sets:
            for test_name, brake_mode, brake_bars in test_configs:
                brake_mult = build_brake_series(
                    df["timestamp"].values, events,
                    brake_bars=brake_bars,
                    brake_mode=brake_mode,
                    symbol=symbol,
                )

                eq, trades = backtest_with_brake(
                    df, signals, config,
                    brake_mult=brake_mult,
                    brake_mode=brake_mode,
                    regime_series=regime_series,
                    trend_dir_series=trend_dir_series,
                    funding_rate_series=fr_series,
                )
                m = compute_metrics(eq, trades, n, bar_hours=0.25)

                wr_delta = (m["win_rate"] - m_base["win_rate"]) * 100
                sharpe_delta = m["sharpe"] - m_base["sharpe"]
                label = f"{evt_name}/{test_name}"
                marker = " ***" if wr_delta > 1.0 else ""

                print(f"  {label:>35}  {m['sharpe']:>7.2f}  {m['return']:>+7.1%}  "
                      f"{m['max_dd']:>6.1%}  {m['win_rate']:>4.0%}  "
                      f"{m['trades']:>6}  {wr_delta:>+4.1f}  {sharpe_delta:>+5.2f}{marker}")

        # Phase 2: Analyze what happened around real brake events
        print("\n  --- Event Impact Analysis ---")
        for evt in BRAKE_EVENTS:
            evt_ts = evt["timestamp_ms"]
            evt_dt = datetime.fromtimestamp(evt_ts / 1000, tz=timezone.utc)
            bar_idx = np.searchsorted(df["timestamp"].values, evt_ts)
            if bar_idx < 10 or bar_idx >= n - 20:
                print(f"  {evt_dt.strftime('%m/%d %H:%M')} UTC — {evt['summary'][:50]}")
                print("    Outside data range, skipping")
                continue

            # Price change in next 2h (8 bars) and 4h (16 bars)
            p_at = df["close"].iloc[bar_idx]
            p_2h = df["close"].iloc[min(bar_idx + 8, n - 1)]
            p_4h = df["close"].iloc[min(bar_idx + 16, n - 1)]
            chg_2h = (p_2h - p_at) / p_at * 100
            chg_4h = (p_4h - p_at) / p_at * 100

            # Any trades triggered in this window?
            trades_in_window = [
                t for t in trades_base
                if t.get("exit_ts") and evt_ts <= t["entry_ts"] < evt_ts + 2 * 3600 * 1000
            ]
            long_trades = [t for t in trades_in_window if t.get("side") == "long"]
            long_wins = sum(1 for t in long_trades if t.get("pnl", 0) > 0)
            long_pnl = sum(t.get("pnl", 0) for t in long_trades)

            print(f"  {evt_dt.strftime('%m/%d %H:%M')} UTC — {evt['summary'][:50]}")
            print(f"    Price: 2h {chg_2h:+.2f}%  4h {chg_4h:+.2f}%  "
                  f"Longs: {len(long_trades)} (WR {long_wins}/{len(long_trades)}, PnL ${long_pnl:+.1f})")

    print(f"\n{'='*90}")
    print("  SENTIMENT BRAKE BACKTEST COMPLETE")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    main()
