"""V9 VOL_SHOCK Filter Backtest — V8 formula as V5.2 auxiliary filter.

Two approaches tested:
  1. V8 standalone: VOL_SHOCK CLOSE_POS DIV TS_MIN as independent strategy
  2. V5.2 + V8 filter: V8 signal as entry filter for V5.2
  3. V5.2 + V8 ensemble: both signals active

Uses walk-forward validation (50/50 split) to avoid overfitting.
Runs on BTC, ETH, SOL individually + multi-symbol portfolio.

Usage:
    python scripts/v9_vol_shock_filter_backtest.py
    python scripts/v9_vol_shock_filter_backtest.py --symbol BTC/USDT
    python scripts/v9_vol_shock_filter_backtest.py --split 0.5
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

from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES_V8
from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS
from selene_space_alpha.vm import PandasStackVM

from optuna_formula_search_v4 import (
    load_multi_tf_df,
    precompute_features,
)

from v7_trend_filter_backtest import (
    compute_signals,
    compute_regime,
    load_funding_rate_series,
    INTERVAL_HOURS,
    ADAPTIVE_WEIGHTS,
)

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"
DERIVATIVES_DB_PATH = PROJECT_ROOT / "data" / "derivatives.db"

# V5.2 strategy: TF_TREND TF_PRESSURE ADD (token IDs resolved dynamically per vocab)
V52_FORMULA_NAMES = ["TF_TREND", "TF_PRESSURE", "ADD"]
V52_CONFIG = {
    "base_interval": "15m",
    "aux_interval": "1h",
    "buy_th": 0.57,
    "sell_th": 0.41,
    "stop_loss": 0.04,
    "position_size": 0.40,
    "hold_bars": 2,
    "cooldown_bars": 4,
    "fee_mode": "maker",
}

# V8 formula: VOL_SHOCK CLOSE_POS DIV TS_MIN
# (from earlier Optuna search that selected these tokens for BTC)
V8_FORMULA_NAMES = ["VOL_SHOCK", "CLOSE_POS", "DIV", "TS_MIN"]


def find_token_ids(vocab: FormulaVocab, names: list[str]) -> list[int]:
    """Find token IDs by name in vocab."""
    tokens = []
    for name in names:
        if name in vocab.token_names:
            tokens.append(vocab.token_names.index(name))
        else:
            print(f"  WARNING: token '{name}' not found in vocab")
            tokens.append(-1)
    return tokens


def compute_vol_shock_raw(
    df: pd.DataFrame,
    features: dict[int, pd.Series],
    vm: PandasStackVM,
) -> np.ndarray:
    """Get raw VOL_SHOCK score for filter threshold testing."""
    for i, (name, _) in enumerate(vm._factor_fns):
        if name == "VOL_SHOCK":
            return features[i].fillna(0.0).values
    return np.zeros(len(df))


# ---------------------------------------------------------------------------
# Signal combination modes
# ---------------------------------------------------------------------------

def combine_signals_v52_v8(
    v52_signals: np.ndarray,
    v8_signals: np.ndarray,
    mode: str,
    vol_shock_raw: np.ndarray | None = None,
    vol_shock_filter_th: float = 1.0,
) -> np.ndarray:
    """Combine V5.2 and V8 signals.

    Modes:
      v52_only:       V5.2 alone (baseline)
      v8_only:        V8 VOL_SHOCK formula alone
      ensemble_any:   Take any non-zero signal from either
      ensemble_agree: Only trade when both agree on direction
      v52_vol_filter: V5.2 but suppress entries when VOL_SHOCK > threshold
      v52_vol_boost:  V5.2 + increase position confidence when VOL_SHOCK confirms
    """
    n = len(v52_signals)

    if mode == "v52_only":
        return v52_signals.copy()
    elif mode == "v8_only":
        return v8_signals.copy()
    elif mode == "ensemble_any":
        combined = np.zeros(n, dtype=int)
        for i in range(n):
            s52 = int(v52_signals[i])
            s8 = int(v8_signals[i])
            if s52 != 0:
                combined[i] = s52
            elif s8 != 0:
                combined[i] = s8
        return combined
    elif mode == "ensemble_agree":
        combined = np.zeros(n, dtype=int)
        for i in range(n):
            s52 = int(v52_signals[i])
            s8 = int(v8_signals[i])
            if s52 != 0 and s52 == s8:
                combined[i] = s52
        return combined
    elif mode == "v52_vol_filter":
        # Suppress V5.2 longs when VOL_SHOCK is high (market event in progress)
        filtered = v52_signals.copy()
        if vol_shock_raw is not None:
            for i in range(n):
                if vol_shock_raw[i] > vol_shock_filter_th:
                    if filtered[i] == 1:
                        filtered[i] = 0
        return filtered
    elif mode == "v52_vol_boost":
        # Only take V5.2 signals that also have V8 confirmation
        # But also take V5.2 signals when VOL_SHOCK is calm (below threshold)
        combined = np.zeros(n, dtype=int)
        for i in range(n):
            s52 = int(v52_signals[i])
            s8 = int(v8_signals[i])
            # V5.2 + V8 agreement: always take
            if s52 != 0 and s52 == s8:
                combined[i] = s52
            # V5.2 alone: take only if VOL_SHOCK is low (calm market)
            elif s52 != 0 and s8 == 0:
                vs = vol_shock_raw[i] if vol_shock_raw is not None else 0
                if vs <= vol_shock_filter_th:
                    combined[i] = s52
        return combined
    else:
        return v52_signals.copy()


# ---------------------------------------------------------------------------
# Backtest engine
# ---------------------------------------------------------------------------

def backtest_signals(
    df: pd.DataFrame,
    signals: np.ndarray,
    config: dict,
    initial_capital: float = 10000.0,
    regime_series: pd.Series | None = None,
    trend_dir_series: pd.Series | None = None,
    funding_rate_series: pd.Series | None = None,
    pos_size_mult: float = 1.0,
) -> tuple[list[float], list[dict]]:
    """Run backtest with given signal array."""
    closes = df["close"].values.astype(float)
    lows = df["low"].values.astype(float)
    highs = df["high"].values.astype(float)
    timestamps = df["timestamp"].values
    n = len(closes)

    fee_rate = 0.0002 if config["fee_mode"] == "maker" else 0.0005
    slippage_frac = 0.0 if config["fee_mode"] == "maker" else 0.00015
    position_size = config["position_size"] * pos_size_mult
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

        # Regime-adaptive
        pos_mult = 1.0
        if regime_series is not None and i < len(regime_series):
            regime = str(regime_series.iloc[i])
            if regime == "volatile":
                pos_mult = 1.0 - ADAPTIVE_WEIGHTS["volatile"]["pos_reduction"]
            if regime == "trending" and trend_dir_series is not None and i < len(trend_dir_series):
                trend_dir = int(trend_dir_series.iloc[i])
                if trend_dir != 0 and sig != 0 and np.sign(sig) != np.sign(trend_dir):
                    sig = 0

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
                    trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, stop=True)
                    position = 0
                    bars_since_exit = 0
                    stopped = True

            if not stopped and bars_in_position >= hold_bars and sig == -1:
                exit_price = price * (1.0 - slippage_frac)
                pnl_pct = (exit_price - entry_price) / entry_price
                net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                capital += net_pnl
                trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, stop=False)
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
                    trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, stop=True)
                    position = 0
                    bars_since_exit = 0
                    stopped = True

            if not stopped and bars_in_position >= hold_bars and sig == 1:
                exit_price = price * (1.0 + slippage_frac)
                pnl_pct = (entry_price - exit_price) / entry_price
                net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                capital += net_pnl
                trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, stop=False)
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


def compute_metrics(
    equity_curve: list[float],
    trades: list[dict],
    n_bars: int,
    bar_hours: float = 0.25,
    n_warmup: int = 200,
) -> dict:
    """Compute backtest metrics."""
    eq = equity_curve[n_warmup:]
    if len(eq) < 2:
        return {"sharpe": 0.0, "return": 0.0, "max_dd": 0.0,
                "win_rate": 0.0, "trades": 0, "long_wr": 0.0, "short_wr": 0.0}

    total_return = (eq[-1] - eq[0]) / eq[0] if eq[0] > 0 else 0.0

    peak = eq[0]
    max_dd = 0.0
    for e in eq:
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    eq_arr = np.array(eq)
    bar_returns = np.diff(eq_arr) / eq_arr[:-1]
    bar_returns = bar_returns[np.isfinite(bar_returns)]

    if len(bar_returns) > 1 and np.std(bar_returns) > 0:
        bars_per_year = 365 * 24 / bar_hours
        sharpe = np.mean(bar_returns) / np.std(bar_returns) * np.sqrt(bars_per_year)
    else:
        sharpe = 0.0

    closed_trades = [t for t in trades if "pnl" in t]
    n_trades = len(closed_trades)
    n_wins = sum(1 for t in closed_trades if t["pnl"] > 0)
    win_rate = n_wins / n_trades if n_trades > 0 else 0.0

    long_trades = [t for t in closed_trades if t.get("side") == "long"]
    short_trades = [t for t in closed_trades if t.get("side") == "short"]
    long_wr = sum(1 for t in long_trades if t["pnl"] > 0) / len(long_trades) if long_trades else 0.0
    short_wr = sum(1 for t in short_trades if t["pnl"] > 0) / len(short_trades) if short_trades else 0.0

    return {
        "sharpe": sharpe,
        "return": total_return,
        "max_dd": -max_dd,
        "win_rate": win_rate,
        "trades": n_trades,
        "long_wr": long_wr,
        "short_wr": short_wr,
    }


# ---------------------------------------------------------------------------
# Portfolio-level backtest (multi-symbol combined equity)
# ---------------------------------------------------------------------------

def portfolio_backtest(
    symbols: list[str],
    modes: list[tuple[str, str]],
    config: dict,
    split_frac: float = 0.5,
) -> None:
    """Run portfolio-level backtest combining multiple symbols.

    Each symbol gets equal capital allocation. Equity curves are summed.
    """
    n_symbols = len(symbols)
    per_symbol_capital = 10000.0 / n_symbols

    v4_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=CEX_FEATURE_NAMES_V8, operator_names=v4_op_names)
    vm = PandasStackVM(vocab)

    v8_tokens = find_token_ids(vocab, V8_FORMULA_NAMES)
    print(f"  V8 tokens: {V8_FORMULA_NAMES} -> {v8_tokens}")

    for mode_key, mode_label in modes:
        combined_equity: dict[str, list[float]] = {}
        total_metrics: dict[str, dict] = {}

        for symbol in symbols:
            df, n = load_multi_tf_df(
                DB_PATH, symbol, "binance",
                config["base_interval"], config["aux_interval"], "futures",
            )
            if df.empty:
                continue

            features = precompute_features(df, vocab, vm)
            v52_tokens = find_token_ids(vocab, V52_FORMULA_NAMES)
            v52_signals = compute_signals(v52_tokens, vocab, df, features, config["buy_th"], config["sell_th"])
            v8_signals = compute_signals(v8_tokens, vocab, df, features, 0.55, 0.45)
            vol_shock_raw = compute_vol_shock_raw(df, features, vm)

            regime_series, trend_dir_series = compute_regime(DB_PATH, symbol, "binance", df["timestamp"])
            fr_series = load_funding_rate_series(symbol, "binance", df["timestamp"])

            # Split: use second half as OOS
            split_idx = int(len(df) * split_frac)
            df_oos = df.iloc[split_idx:].reset_index(drop=True)
            signals_oos = combine_signals_v52_v8(
                v52_signals[split_idx:], v8_signals[split_idx:],
                mode=mode_key, vol_shock_raw=vol_shock_raw[split_idx:],
            )
            regime_oos = regime_series.iloc[split_idx:].reset_index(drop=True) if regime_series is not None else None
            trend_oos = trend_dir_series.iloc[split_idx:].reset_index(drop=True) if trend_dir_series is not None else None
            fr_oos = fr_series.iloc[split_idx:].reset_index(drop=True) if fr_series is not None else None

            eq, trades = backtest_signals(
                df_oos, signals_oos, config,
                initial_capital=per_symbol_capital,
                regime_series=regime_oos, trend_dir_series=trend_oos,
                funding_rate_series=fr_oos,
            )
            total_metrics[symbol] = compute_metrics(eq, trades, len(df_oos), bar_hours=0.25)
            combined_equity[symbol] = eq

        if not combined_equity:
            continue

        # Sum equity curves (equal weight portfolio)
        min_len = min(len(eq) for eq in combined_equity.values())
        portfolio_eq = np.zeros(min_len)
        for eq in combined_equity.values():
            portfolio_eq += np.array(eq[:min_len])

        portfolio_metrics = compute_metrics(
            portfolio_eq.tolist(), [], min_len, bar_hours=0.25,
        )

        # Per-symbol breakdown
        print(f"\n  --- Portfolio: {mode_label} (OOS) ---")
        print(f"  {'Symbol':>10}  {'Sharpe':>7}  {'Return':>8}  {'MaxDD':>7}  {'WR':>5}  {'Trades':>6}  {'LongWR':>7}  {'ShortWR':>7}")
        print(f"  {'------':>10}  {'------':>7}  {'------':>8}  {'-----':>7}  {'---':>5}  {'------':>6}  {'------':>7}  {'-------':>7}")

        for sym, m in total_metrics.items():
            print(f"  {sym:>10}  {m['sharpe']:>7.2f}  {m['return']:>+7.1%}  {m['max_dd']:>6.1%}  "
                  f"{m['win_rate']:>4.0%}  {m['trades']:>6}  {m['long_wr']:>6.0%}  {m['short_wr']:>6.0%}")

        pm = portfolio_metrics
        print(f"  {'PORTFOLIO':>10}  {pm['sharpe']:>7.2f}  {pm['return']:>+7.1%}  {pm['max_dd']:>6.1%}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="V9 VOL_SHOCK Filter Backtest")
    parser.add_argument("--symbol", type=str, default="all",
                        help="Symbol or 'all' for BTC+ETH+SOL")
    parser.add_argument("--split", type=float, default=0.5,
                        help="Train/test split fraction (default: 0.5 = use last 50%% as OOS)")
    parser.add_argument("--vol-filter-th", type=float, default=1.0,
                        help="VOL_SHOCK threshold for filter modes (default: 1.0)")
    args = parser.parse_args()

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if args.symbol == "all" else [args.symbol]
    config = V52_CONFIG
    split_frac = args.split

    # Build V8 vocab (includes VOL_SHOCK)
    v4_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=CEX_FEATURE_NAMES_V8, operator_names=v4_op_names)
    vm = PandasStackVM(vocab)

    # Find V8 token IDs
    v52_tokens = find_token_ids(vocab, V52_FORMULA_NAMES)
    v8_tokens = find_token_ids(vocab, V8_FORMULA_NAMES)
    print(f"\nV5.2 formula: {' '.join(V52_FORMULA_NAMES)} -> tokens {v52_tokens}")
    print(f"V8 formula: {' '.join(V8_FORMULA_NAMES)} -> tokens {v8_tokens}")
    print(f"Split: use last {split_frac:.0%} as OOS")

    # Modes to test
    modes = [
        ("v52_only", "V5.2 baseline"),
        ("v8_only", "V8 VOL_SHOCK standalone"),
        ("ensemble_any", "V5.2 + V8 (any signal)"),
        ("ensemble_agree", "V5.2 + V8 (both agree)"),
        ("v52_vol_filter", "V5.2 filtered by VOL_SHOCK"),
        ("v52_vol_boost", "V5.2 boosted by V8 confirmation"),
    ]

    for symbol in symbols:
        print(f"\n{'='*90}")
        print(f"  {symbol} — V9 VOL_SHOCK Filter Backtest")
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
        split_idx = int(n * split_frac)
        ts_split = datetime.fromtimestamp(df["timestamp"].iloc[split_idx] / 1000, tz=timezone.utc)
        print(f"  Data: {ts_start.strftime('%Y-%m-%d')} ~ {ts_end.strftime('%Y-%m-%d')} ({n} bars)")
        print(f"  OOS:  {ts_split.strftime('%Y-%m-%d')} ~ {ts_end.strftime('%Y-%m-%d')} ({n - split_idx} bars)")

        # Compute features
        features = precompute_features(df, vocab, vm)

        # Compute signals for full period
        v52_tokens = find_token_ids(vocab, V52_FORMULA_NAMES)
        v52_signals = compute_signals(v52_tokens, vocab, df, features, config["buy_th"], config["sell_th"])
        v8_signals = compute_signals(v8_tokens, vocab, df, features, 0.55, 0.45)
        vol_shock_raw = compute_vol_shock_raw(df, features, vm)

        # Compute regime & FR
        regime_series, trend_dir_series = compute_regime(DB_PATH, symbol, "binance", df["timestamp"])
        fr_series = load_funding_rate_series(symbol, "binance", df["timestamp"])

        # Split: IS (first half) and OOS (second half)
        df_is = df.iloc[:split_idx].reset_index(drop=True)
        df_oos = df.iloc[split_idx:].reset_index(drop=True)

        print(f"\n  {'Mode':>25}  {'Split':>5}  {'Sharpe':>7}  {'Return':>8}  {'MaxDD':>7}  {'WR':>5}  {'Trades':>6}  {'LongWR':>7}  {'ShortWR':>7}")
        print(f"  {'----':>25}  {'-----':>5}  {'------':>7}  {'------':>8}  {'-----':>7}  {'---':>5}  {'------':>6}  {'------':>7}  {'-------':>7}")

        for mode_key, mode_label in modes:
            for split_label, df_sub, sig_start, sig_end in [
                ("IS", df_is, 0, split_idx),
                ("OOS", df_oos, split_idx, n),
            ]:
                v52_sub = v52_signals[sig_start:sig_end]
                v8_sub = v8_signals[sig_start:sig_end]
                vs_sub = vol_shock_raw[sig_start:sig_end]

                combined = combine_signals_v52_v8(
                    v52_sub, v8_sub, mode=mode_key,
                    vol_shock_raw=vs_sub,
                    vol_shock_filter_th=args.vol_filter_th,
                )

                regime_sub = regime_series.iloc[sig_start:sig_end].reset_index(drop=True) if regime_series is not None else None
                trend_sub = trend_dir_series.iloc[sig_start:sig_end].reset_index(drop=True) if trend_dir_series is not None else None
                fr_sub = fr_series.iloc[sig_start:sig_end].reset_index(drop=True) if fr_series is not None else None

                eq, trades = backtest_signals(
                    df_sub, combined, config,
                    regime_series=regime_sub,
                    trend_dir_series=trend_sub,
                    funding_rate_series=fr_sub,
                )
                m = compute_metrics(eq, trades, len(df_sub), bar_hours=0.25)

                marker = ""
                if split_label == "OOS" and m["sharpe"] > 12:
                    marker = " ***"
                elif split_label == "OOS" and m["win_rate"] > 0.66:
                    marker = " +WR"

                print(f"  {mode_label:>25}  {split_label:>5}  {m['sharpe']:>7.2f}  {m['return']:>+7.1%}  "
                      f"{m['max_dd']:>6.1%}  {m['win_rate']:>4.0%}  "
                      f"{m['trades']:>6}  {m['long_wr']:>6.0%}  {m['short_wr']:>6.0%}{marker}")

            # IS/OOS gap for this mode
            print()

        # Weekly PnL for best OOS mode
        print("\n  --- Weekly PnL: V5.2 OOS ---")
        combined_oos = combine_signals_v52_v8(
            v52_signals[split_idx:], v8_signals[split_idx:],
            mode="v52_only", vol_shock_raw=vol_shock_raw[split_idx:],
        )
        regime_oos = regime_series.iloc[split_idx:].reset_index(drop=True) if regime_series is not None else None
        trend_oos = trend_dir_series.iloc[split_idx:].reset_index(drop=True) if trend_dir_series is not None else None
        fr_oos = fr_series.iloc[split_idx:].reset_index(drop=True) if fr_series is not None else None

        eq52, _ = backtest_signals(
            df_oos, combined_oos, config,
            regime_series=regime_oos, trend_dir_series=trend_oos,
            funding_rate_series=fr_oos,
        )

        bar_hours = 0.25
        bars_per_week = int(7 * 24 / bar_hours)
        n_weeks = len(eq52) // bars_per_week
        weekly_returns = []
        for w in range(n_weeks):
            si = w * bars_per_week
            ei = min((w + 1) * bars_per_week, len(eq52) - 1)
            if si >= len(eq52) or ei >= len(eq52):
                break
            wr = (eq52[ei] - eq52[si]) / eq52[si] * 100 if eq52[si] > 0 else 0
            weekly_returns.append(wr)
        if weekly_returns:
            n_pos = sum(1 for r in weekly_returns if r > 0)
            print(f"  Weeks: {len(weekly_returns)}, Positive: {n_pos} ({n_pos/len(weekly_returns)*100:.0f}%)")
            print(f"  Avg weekly: {np.mean(weekly_returns):+.2f}%, Median: {np.median(weekly_returns):+.2f}%")
            print(f"  Best: {max(weekly_returns):+.2f}%, Worst: {min(weekly_returns):+.2f}%")

    # Portfolio-level test
    if len(symbols) > 1:
        print(f"\n{'='*90}")
        print("  MULTI-SYMBOL PORTFOLIO (equal weight, OOS)")
        print(f"{'='*90}")
        portfolio_backtest(symbols, modes, config, split_frac=args.split)

    print(f"\n{'='*90}")
    print("  V9 VOL_SHOCK FILTER BACKTEST COMPLETE")
    print(f"{'='*90}\n")


if __name__ == "__main__":
    main()
