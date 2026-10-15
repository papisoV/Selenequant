"""Sentiment Signal Deep Analysis — event window + 4h/1d brake backtest.

Analyzes:
  1. Price impact around each TG signal event (pre/post windows)
  2. Whether LLM labels predict price direction at 4h/1d scale
  3. 4h strategy with sentiment brake
  4. Signal half-life estimation from actual price data

Usage:
    python scripts/sentiment_signal_analysis.py
    python scripts/sentiment_signal_analysis.py --symbol BTC/USDT
"""

from __future__ import annotations

import argparse
import sqlite3
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
    compute_metrics, INTERVAL_HOURS,
)

DB_PATH = PROJECT_ROOT / "services/local-api/data/klines.db"

# ---------------------------------------------------------------------------
# All 42 TG signals with timestamps and full LLM analysis
# ---------------------------------------------------------------------------

ALL_SIGNALS = [
    {"id": 1,  "ts": "2026-05-01T03:13:57Z", "msg": "China court: AI can't replace employees for cost savings", "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 2,  "ts": "2026-05-01T04:29:18Z", "msg": "BTC breaks $77,000",                                        "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 3,  "ts": "2026-05-01T07:23:27Z", "msg": "Musk: most crypto are scams",                "sentiment": 2, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 4,  "ts": "2026-05-01T07:29:52Z", "msg": "Crypto is coldest topic on X",               "sentiment": 2, "type": "市场动态",  "strength": 2, "urgency": 1, "scope": "宏观"},
    {"id": 5,  "ts": "2026-05-01T08:31:13Z", "msg": "500+ ETH wallets hacked $800K via ThorChain","sentiment": 1, "type": "安全事件",  "strength": 3, "urgency": 4, "scope": "微观"},
    {"id": 6,  "ts": "2026-05-01T09:29:13Z", "msg": "Bhutan gov selling ~$287M BTC",              "sentiment": 2, "type": "市场动态",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 7,  "ts": "2026-05-01T12:07:56Z", "msg": "Brazil CB bans crypto cross-border payments","sentiment": 1, "type": "监管",      "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 8,  "ts": "2026-05-01T12:34:18Z", "msg": "BTC breaks $78,000",                                        "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 9,  "ts": "2026-05-01T13:30:47Z", "msg": "S&P500 NASDAQ open +0.4%",                   "sentiment": 4, "type": "宏观经济",  "strength": 1, "urgency": 2, "scope": "宏观"},
    {"id": 10, "ts": "2026-05-01T14:00:17Z", "msg": "US ISM manufacturing misses expectations",    "sentiment": 2, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 11, "ts": "2026-05-01T14:34:16Z", "msg": "Apple +5% adds $205B market cap",             "sentiment": 4, "type": "宏观经济",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 12, "ts": "2026-05-01T14:57:43Z", "msg": "Tether Q1 profit $1.04B, surplus $8.23B",    "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 1, "scope": "微观"},
    {"id": 13, "ts": "2026-05-01T14:59:27Z", "msg": "Spirit Air deal collapses",                   "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 14, "ts": "2026-05-01T15:02:22Z", "msg": "Spirit Air going bankrupt",                   "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 15, "ts": "2026-05-01T15:36:05Z", "msg": "SOL 87 days below $100, 7 month decline record", "sentiment": 1, "type": "市场动态", "strength": 3, "urgency": 2, "scope": "微观"},
    {"id": 16, "ts": "2026-05-01T15:50:54Z", "msg": "Trump: EU auto tariffs to 25%",                "sentiment": 2, "type": "宏观经济",  "strength": 4, "urgency": 4, "scope": "宏观"},
    {"id": 17, "ts": "2026-05-01T15:53:54Z", "msg": "Trump: EU auto tariffs to 25% (repeat)",       "sentiment": 2, "type": "宏观经济",  "strength": 4, "urgency": 4, "scope": "宏观"},
    {"id": 18, "ts": "2026-05-01T16:25:39Z", "msg": "Trump: negotiating via phone, uncertain deal", "sentiment": 2, "type": "宏观经济",  "strength": 2, "urgency": 3, "scope": "宏观"},
    {"id": 19, "ts": "2026-05-01T16:29:55Z", "msg": "Trump on Iran: not happy, just talked",        "sentiment": 2, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 20, "ts": "2026-05-01T16:31:09Z", "msg": "Trump on Spirit Air final proposal",           "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 21, "ts": "2026-05-01T16:35:25Z", "msg": "Trump on Iran: bomb them or make a deal",      "sentiment": 1, "type": "宏观经济",  "strength": 4, "urgency": 5, "scope": "宏观"},
    {"id": 22, "ts": "2026-05-01T16:46:41Z", "msg": "Trump net worth tripled to $6.5B via crypto",  "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 1, "scope": "宏观"},
    {"id": 23, "ts": "2026-05-01T17:09:55Z", "msg": "DeFi April exploits record: 28 incidents, $635M", "sentiment": 1, "type": "安全事件", "strength": 4, "urgency": 4, "scope": "微观"},
    {"id": 24, "ts": "2026-05-01T17:10:20Z", "msg": "Iran proposes new peace talk with US - CBS",   "sentiment": 4, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 25, "ts": "2026-05-01T17:11:34Z", "msg": "US gov Intel stake +$40B",                     "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 26, "ts": "2026-05-01T17:11:41Z", "msg": "Tether becomes #17 US Treasury holder",        "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 1, "scope": "微观"},
    {"id": 27, "ts": "2026-05-01T17:45:36Z", "msg": "ETH Foundation sells 10K ETH ($23M)",           "sentiment": 2, "type": "市场动态",  "strength": 3, "urgency": 3, "scope": "微观"},
    {"id": 28, "ts": "2026-05-01T17:57:01Z", "msg": "ETH Foundation OTC 10K ETH at $2292.15",       "sentiment": 2, "type": "市场动态",  "strength": 3, "urgency": 3, "scope": "微观"},
    {"id": 29, "ts": "2026-05-01T18:00:31Z", "msg": "Trump Media plans Truth Social spinoff",        "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 30, "ts": "2026-05-01T18:13:32Z", "msg": "S&P500 accelerates giant IPO entry",           "sentiment": 4, "type": "宏观经济",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 31, "ts": "2026-05-01T18:32:37Z", "msg": "Jane Street paid $9.4B to employees",          "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 32, "ts": "2026-05-01T18:40:02Z", "msg": "Trump: Iran hostilities terminated",            "sentiment": 3, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 33, "ts": "2026-05-01T20:00:19Z", "msg": "S&P500 close +0.3%, NASDAQ +0.9%",             "sentiment": 4, "type": "宏观经济",  "strength": 1, "urgency": 2, "scope": "宏观"},
    {"id": 34, "ts": "2026-05-01T21:52:19Z", "msg": "GameStop preparing to bid for eBay",            "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 35, "ts": "2026-05-02T00:00:14Z", "msg": "Fear & Greed Index: 39 (Fear), BTC $78210",    "sentiment": 2, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 36, "ts": "2026-05-02T00:16:13Z", "msg": "Trump: will take over Cuba almost immediately", "sentiment": 2, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 37, "ts": "2026-05-02T00:29:43Z", "msg": "Trump on Iran: maybe no deal is better",       "sentiment": 2, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 38, "ts": "2026-05-02T03:50:46Z", "msg": "Trump: take over Cuba immediately (repeat)",    "sentiment": 2, "type": "宏观经济",  "strength": 3, "urgency": 3, "scope": "宏观"},
    {"id": 39, "ts": "2026-05-02T06:27:24Z", "msg": "Spirit Air officially bankrupt",               "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 40, "ts": "2026-05-02T15:18:09Z", "msg": "Pelosi stock trading controversy",             "sentiment": 3, "type": "其他",      "strength": 1, "urgency": 1, "scope": "微观"},
    {"id": 41, "ts": "2026-05-02T21:38:23Z", "msg": "BTC breaks $79,000",                                        "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
    {"id": 42, "ts": "2026-05-02T21:40:34Z", "msg": "BTC back above $79,000",                      "sentiment": 4, "type": "市场动态",  "strength": 2, "urgency": 2, "scope": "宏观"},
]


def parse_ts(ts_str: str) -> int:
    """Parse ISO timestamp to milliseconds."""
    dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
    return int(dt.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Part 1: Price Impact Window Analysis
# ---------------------------------------------------------------------------

def analyze_price_impact(symbol: str, interval: str = "15m") -> None:
    """Analyze price changes around each signal event at multiple time scales."""
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange='binance' AND interval=? AND market_type='futures' "
        "ORDER BY timestamp ASC",
        conn, params=(symbol, interval),
    )
    conn.close()
    if df.empty:
        print(f"  No {interval} data for {symbol}")
        return

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    timestamps = df["timestamp"].values
    closes = df["close"].values

    bar_ms = int(INTERVAL_HOURS[interval] * 3600 * 1000)

    print(f"\n  {'#':>2}  {'Time(UTC)':>14}  {'Sent':>4}  {'Type':>6}  {'Str':>3}  "
          f"{'1bar':>6}  {'4bar':>6}  {'8bar':>6}  {'16bar':>6}  {'1d':>6}  {'Msg':>40}")
    print(f"  {'--':>2}  {'----------':>14}  {'----':>4}  {'------':>6}  {'---':>3}  "
          f"{'-----':>6}  {'-----':>6}  {'-----':>6}  {'------':>6}  {'---':>6}  {'---':>40}")

    # Track sentiment vs actual direction accuracy
    predictions = []

    for sig in ALL_SIGNALS:
        evt_ts = parse_ts(sig["ts"])
        bar_idx = np.searchsorted(timestamps, evt_ts)
        if bar_idx < 2 or bar_idx >= len(closes) - 32:
            continue

        p_at = closes[bar_idx]
        windows = [1, 4, 8, 16, 32]  # bars after event
        changes = []
        for w in windows:
            idx = min(bar_idx + w, len(closes) - 1)
            chg = (closes[idx] - p_at) / p_at * 100
            changes.append(chg)

        # Did sentiment predict direction?
        # Bearish (1-2) → expect negative, Bullish (4-5) → expect positive
        for w_idx, w in enumerate(windows):
            actual_dir = 1 if changes[w_idx] > 0 else -1
            if sig["sentiment"] <= 2:
                predicted = -1
            elif sig["sentiment"] >= 4:
                predicted = 1
            else:
                predicted = 0  # neutral, skip
            if predicted != 0:
                predictions.append({
                    "window": w,
                    "window_label": ["1bar", "4bar", "8bar", "16bar", "1d"][w_idx],
                    "correct": predicted == actual_dir,
                    "sentiment": sig["sentiment"],
                    "strength": sig["strength"],
                    "type": sig["type"],
                    "chg": changes[w_idx],
                })

        dt_str = datetime.fromtimestamp(evt_ts / 1000, tz=timezone.utc).strftime("%m/%d %H:%M")
        sent_marker = "▼" if sig["sentiment"] <= 2 else ("▲" if sig["sentiment"] >= 4 else "─")
        print(f"  {sig['id']:>2}  {dt_str:>14}  {sig['sentiment']}{sent_marker}  {sig['type']:>6}  {sig['strength']:>3}  "
              f"{changes[0]:>+5.2f}%  {changes[1]:>+5.2f}%  {changes[2]:>+5.2f}%  {changes[3]:>+5.2f}%  "
              f"{changes[4]:>+5.2f}%  {sig['msg'][:40]}")

    # Accuracy summary
    print(f"\n  === Sentiment Prediction Accuracy ({symbol} {interval}) ===")
    print(f"  {'Window':>8}  {'Total':>6}  {'Correct':>7}  {'Accuracy':>8}  "
          f"{'BearAcc':>7}  {'BullAcc':>7}  {'StrongAcc':>9}")
    print(f"  {'------':>8}  {'-----':>6}  {'-------':>7}  {'--------':>8}  "
          f"{'-------':>7}  {'-------':>7}  {'---------':>9}")

    for w_label in ["1bar", "4bar", "8bar", "16bar", "1d"]:
        wp = [p for p in predictions if p["window_label"] == w_label]
        if not wp:
            continue
        total = len(wp)
        correct = sum(1 for p in wp if p["correct"])
        acc = correct / total * 100

        bear = [p for p in wp if p["sentiment"] <= 2]
        bull = [p for p in wp if p["sentiment"] >= 4]
        bear_acc = sum(1 for p in bear if p["correct"]) / len(bear) * 100 if bear else 0
        bull_acc = sum(1 for p in bull if p["correct"]) / len(bull) * 100 if bull else 0

        strong = [p for p in wp if p["strength"] >= 3]
        strong_acc = sum(1 for p in strong if p["correct"]) / len(strong) * 100 if strong else 0

        print(f"  {w_label:>8}  {total:>6}  {correct:>7}  {acc:>7.0f}%  "
              f"{bear_acc:>6.0f}%  {bull_acc:>6.0f}%  {strong_acc:>8.0f}%")

    # Half-life estimation: at what window does accuracy drop below 55%?
    print("\n  === Half-life Estimate ===")
    for category, filter_fn in [
        ("all signals", lambda p: True),
        ("bearish only", lambda p: p["sentiment"] <= 2),
        ("strong signals", lambda p: p["strength"] >= 3),
        ("strong bearish", lambda p: p["sentiment"] <= 2 and p["strength"] >= 3),
    ]:
        for w_label in ["1bar", "4bar", "8bar", "16bar", "1d"]:
            wp = [p for p in predictions if p["window_label"] == w_label and filter_fn(p)]
            if not wp:
                continue
            acc = sum(1 for p in wp if p["correct"]) / len(wp) * 100
            if acc < 55:
                bar_hours = INTERVAL_HOURS[interval]
                prev_labels = ["1bar", "4bar", "8bar", "16bar", "1d"]
                prev_idx = prev_labels.index(w_label)
                half_life = prev_idx * 4 * bar_hours if prev_idx > 0 else bar_hours
                print(f"  {category:>16}: accuracy drops below 55% at {w_label} (~{half_life:.0f}h)")
                break
        else:
            print(f"  {category:>16}: accuracy stays above 55% through 1d")


# ---------------------------------------------------------------------------
# Part 2: 4h Strategy with Sentiment Brake
# ---------------------------------------------------------------------------

def backtest_4h_with_brake(symbol: str) -> None:
    """Run 4h strategy with sentiment brake on longer timeframes."""
    from sentiment_brake_backtest import build_brake_series, backtest_with_brake, BRAKE_EVENTS, MEDIUM_EVENTS

    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange='binance' AND interval='4h' AND market_type='futures' "
        "ORDER BY timestamp ASC",
        conn, params=(symbol,),
    )
    conn.close()
    if df.empty:
        print(f"  No 4h data for {symbol}")
        return

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    n = len(df)
    ts_start = datetime.fromtimestamp(df["timestamp"].iloc[0] / 1000, tz=timezone.utc)
    ts_end = datetime.fromtimestamp(df["timestamp"].iloc[-1] / 1000, tz=timezone.utc)
    print(f"  4h data: {ts_start.strftime('%Y-%m-%d')} ~ {ts_end.strftime('%Y-%m-%d')} ({n} bars)")

    # Use V5.2 formula on 4h data directly
    config_4h = {
        "tokens": [21, 24, 27],
        "base_interval": "4h",
        "aux_interval": "1d",
        "buy_th": 0.57,
        "sell_th": 0.41,
        "stop_loss": 0.04,
        "position_size": 0.50,
        "hold_bars": 2,
        "cooldown_bars": 4,
        "fee_mode": "maker",
    }

    # Load 1d aux data
    df_1d, n_1d = load_multi_tf_df(DB_PATH, symbol, "binance", "4h", "1d", "futures")
    if df_1d.empty:
        print("  Cannot load 4h+1d data")
        return

    v4_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=CEX_FEATURE_NAMES_V4, operator_names=v4_op_names)
    vm = PandasStackVM(vocab)
    features = precompute_features(df_1d, vocab, vm)
    signals = compute_signals(config_4h["tokens"], vocab, df_1d, features,
                              config_4h["buy_th"], config_4h["sell_th"])

    regime_series, trend_dir_series = compute_regime(DB_PATH, symbol, "binance", df_1d["timestamp"])
    fr_series = load_funding_rate_series(symbol, "binance", df_1d["timestamp"])

    # Baseline 4h
    eq_base, trades_base = backtest_with_brake(
        df_1d, signals, config_4h,
        brake_mult=np.ones(len(df_1d)),
        brake_mode="long_only",
        regime_series=regime_series,
        trend_dir_series=trend_dir_series,
        funding_rate_series=fr_series,
    )
    m_base = compute_metrics(eq_base, trades_base, n_1d, bar_hours=4.0)

    print(f"\n  {'Config':>35}  {'Sharpe':>7}  {'Return':>8}  {'MaxDD':>7}  "
          f"{'WR':>5}  {'Trades':>6}  {'WRΔ':>5}  {'ShrpΔ':>6}")
    print(f"  {'-----':>35}  {'------':>7}  {'------':>8}  {'-----':>7}  "
          f"{'---':>5}  {'------':>6}  {'----':>5}  {'-----':>6}")
    print(f"  {'baseline 4h (no brake)':>35}  {m_base['sharpe']:>7.2f}  {m_base['return']:>+7.1%}  "
          f"{m_base['max_dd']:>6.1%}  {m_base['win_rate']:>4.0%}  "
          f"{m_base['trades']:>6}  {'---':>5}  {'---':>6}")

    # Test brake on 4h: 1 bar = 4h, so 2 bars = 8h, 4 bars = 16h
    for evt_name, events in [("hard(3)", BRAKE_EVENTS), ("medium(8)", MEDIUM_EVENTS)]:
        for brake_bars, label in [(1, "4h"), (2, "8h"), (4, "16h")]:
            brake_mult = build_brake_series(
                df_1d["timestamp"].values, events,
                brake_bars=brake_bars,
                brake_mode="long_only",
                symbol=symbol,
                reversal_guard_bars=0,
            )
            eq, trades = backtest_with_brake(
                df_1d, signals, config_4h,
                brake_mult=brake_mult,
                brake_mode="long_only",
                regime_series=regime_series,
                trend_dir_series=trend_dir_series,
                funding_rate_series=fr_series,
            )
            m = compute_metrics(eq, trades, n_1d, bar_hours=4.0)
            wr_delta = (m["win_rate"] - m_base["win_rate"]) * 100
            sharpe_delta = m["sharpe"] - m_base["sharpe"]
            config_label = f"{evt_name}/long_{label}"
            marker = " ***" if wr_delta > 1.0 else ""
            print(f"  {config_label:>35}  {m['sharpe']:>7.2f}  {m['return']:>+7.1%}  "
                  f"{m['max_dd']:>6.1%}  {m['win_rate']:>4.0%}  "
                  f"{m['trades']:>6}  {wr_delta:>+4.1f}  {sharpe_delta:>+5.2f}{marker}")


# ---------------------------------------------------------------------------
# Part 3: Pre-event drift — does price move BEFORE the signal?
# ---------------------------------------------------------------------------

def analyze_pre_event_drift(symbol: str) -> None:
    """Check if price was already moving before the signal was published.

    If price dropped before a bearish signal, the signal is lagging not leading.
    """
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close FROM klines "
        "WHERE symbol=? AND exchange='binance' AND interval='15m' AND market_type='futures' "
        "ORDER BY timestamp ASC",
        conn, params=(symbol,),
    )
    conn.close()
    if df.empty:
        return

    timestamps = df["timestamp"].values
    closes = df["close"].values

    # Only check strong signals (strength >= 3)
    strong_signals = [s for s in ALL_SIGNALS if s["strength"] >= 3]

    print(f"\n  {'#':>2}  {'Time':>14}  {'Sent':>4}  {'Type':>6}  "
          f"{'-4h':>6}  {'-2h':>6}  {'-1h':>6}  {'@evt':>6}  {'+1h':>6}  {'+2h':>6}  {'+4h':>6}  {'Msg':>35}")
    print(f"  {'--':>2}  {'----':>14}  {'----':>4}  {'------':>6}  "
          f"{'-----':>6}  {'-----':>6}  {'-----':>6}  {'-----':>6}  {'-----':>6}  {'-----':>6}  {'-----':>6}  {'---':>35}")

    for sig in strong_signals:
        evt_ts = parse_ts(sig["ts"])
        bar_idx = np.searchsorted(timestamps, evt_ts)
        if bar_idx < 16 or bar_idx >= len(closes) - 16:
            continue

        p_at = closes[bar_idx]
        offsets = [-16, -8, -4, 0, 4, 8, 16]  # in 15m bars
        labels = ["-4h", "-2h", "-1h", "@evt", "+1h", "+2h", "+4h"]
        changes = []
        for off in offsets:
            idx = bar_idx + off
            chg = (closes[idx] - p_at) / p_at * 100
            changes.append(chg)

        dt_str = datetime.fromtimestamp(evt_ts / 1000, tz=timezone.utc).strftime("%m/%d %H:%M")
        sent_marker = "▼" if sig["sentiment"] <= 2 else ("▲" if sig["sentiment"] >= 4 else "─")
        print(f"  {sig['id']:>2}  {dt_str:>14}  {sig['sentiment']}{sent_marker}  {sig['type']:>6}  "
              f"{changes[0]:>+5.2f}%  {changes[1]:>+5.2f}%  {changes[2]:>+5.2f}%  "
              f"{changes[3]:>+5.2f}%  {changes[4]:>+5.2f}%  {changes[5]:>+5.2f}%  "
              f"{changes[6]:>+5.2f}%  {sig['msg'][:35]}")

    # Summary: pre-event drift indicates signal is lagging
    pre_drifts = []
    post_drifts = []
    for sig in strong_signals:
        evt_ts = parse_ts(sig["ts"])
        bar_idx = np.searchsorted(timestamps, evt_ts)
        if bar_idx < 16 or bar_idx >= len(closes) - 16:
            continue
        p_at = closes[bar_idx]
        pre_4h = (closes[bar_idx] - closes[bar_idx - 16]) / closes[bar_idx - 16] * 100
        post_4h = (closes[bar_idx + 16] - closes[bar_idx]) / closes[bar_idx] * 100

        if sig["sentiment"] <= 2:  # bearish
            pre_drifts.append(pre_4h)
            post_drifts.append(post_4h)

    if pre_drifts:
        print("\n  Bearish signals (strength>=3):")
        print(f"    Pre-event 4h drift: {np.mean(pre_drifts):>+.2f}% (negative = price already falling before signal)")
        print(f"    Post-event 4h drift: {np.mean(post_drifts):>+.2f}% (negative = signal was right)")
        if np.mean(pre_drifts) < -0.1:
            print("    >>> Signal is LAGGING: price already dropped before news arrived")
        elif abs(np.mean(pre_drifts)) < 0.1 and np.mean(post_drifts) < -0.1:
            print("    >>> Signal is LEADING: price drops after news, not before")
        else:
            print("    >>> Signal has no clear predictive value at this time scale")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sentiment Signal Deep Analysis")
    parser.add_argument("--symbol", type=str, default="all",
                        help="Symbol (BTC/USDT, ETH/USDT, SOL/USDT, or all)")
    args = parser.parse_args()

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if args.symbol == "all" else [args.symbol]

    # Need to import sentiment_brake_backtest from scripts dir
    sys.path.insert(0, str(PROJECT_ROOT / "scripts"))

    for symbol in symbols:
        print(f"\n{'='*110}")
        print(f"  {symbol} — Sentiment Signal Deep Analysis")
        print(f"{'='*110}")

        # Part 1: Price impact windows at 15m resolution
        print("\n  ── Part 1: Price Impact Windows (15m bars) ──")
        analyze_price_impact(symbol, "15m")

        # Part 2: Pre-event drift analysis
        print("\n  ── Part 2: Pre-Event Drift (Is Signal Leading or Lagging?) ──")
        analyze_pre_event_drift(symbol)

        # Part 3: 4h strategy with brake
        print("\n  ── Part 3: 4h Strategy with Sentiment Brake ──")
        backtest_4h_with_brake(symbol)

    print(f"\n{'='*110}")
    print("  ANALYSIS COMPLETE")
    print(f"{'='*110}\n")


if __name__ == "__main__":
    main()
