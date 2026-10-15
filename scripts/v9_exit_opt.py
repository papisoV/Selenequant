"""V9 Exit Optimization — improve the exit logic of V5.2.

Current V5.2: hold 2 bars, then exit only on opposite signal.
Problem: some trades stay open too long waiting for opposite signal.

Ideas:
  1. Time-based exit: force exit after N bars regardless of signal
  2. Take-profit: exit early when unrealized profit exceeds threshold
  3. Trailing stop: lock in profits after they reach a threshold
  4. Momentum exit: exit when momentum reverses (formula score drops)

Usage:
    python scripts/v9_exit_opt.py
"""

from __future__ import annotations

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
    ADAPTIVE_WEIGHTS,
)
from v9_vol_shock_filter_backtest import compute_metrics

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"
V52_FORMULA_NAMES = ["TF_TREND", "TF_PRESSURE", "ADD"]


def backtest_flexible_exit(
    df: pd.DataFrame,
    signals: np.ndarray,
    config: dict,
    initial_capital: float = 10000.0,
    regime_series: pd.Series | None = None,
    trend_dir_series: pd.Series | None = None,
    funding_rate_series: pd.Series | None = None,
    pos_size_mult: float = 1.0,
    # Exit parameters
    max_hold_bars: int = 0,        # 0 = unlimited, force exit after N bars
    take_profit_pct: float = 0.0,  # 0 = disabled, e.g. 0.02 = 2% profit
    trailing_start_pct: float = 0.0,  # start trailing after this % profit
    trailing_drop_pct: float = 0.0,   # exit if price drops this % from peak
    momentum_exit: bool = False,   # exit when signal weakens
    signal_scores: np.ndarray | None = None,  # for momentum exit
) -> tuple[list[float], list[dict]]:
    """Backtest with flexible exit conditions."""
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
    position_peak_price = 0.0  # for trailing stop

    for i in range(1, n):
        sig = int(signals[i - 1])
        price = closes[i]
        low = lows[i]
        high = highs[i]

        if not np.isfinite(capital) or capital <= 0:
            equity_curve.append(equity_curve[-1])
            continue

        pos_mult = 1.0
        if regime_series is not None and i < len(regime_series):
            regime = str(regime_series.iloc[i])
            if regime == "volatile":
                pos_mult *= (1.0 - ADAPTIVE_WEIGHTS["volatile"]["pos_reduction"])
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
                position_peak_price = entry_price
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
                position_peak_price = entry_price
                trades.append({
                    "entry_ts": timestamps[i], "entry_price": entry_price,
                    "side": "short", "notional": position_notional,
                })

        elif position == 1:
            bars_in_position += 1
            stopped = False
            exit_reason = ""

            # Track peak for trailing stop
            if price > position_peak_price:
                position_peak_price = price

            # Fixed SL
            if stop_loss > 0:
                stop_price = entry_price * (1.0 - stop_loss)
                if low <= stop_price:
                    exit_price = stop_price * (1.0 - slippage_frac)
                    pnl_pct = (exit_price - entry_price) / entry_price
                    net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                    capital += net_pnl
                    trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="sl")
                    position = 0
                    bars_since_exit = 0
                    stopped = True

            # Take profit
            if not stopped and take_profit_pct > 0:
                pnl_pct_unrealized = (price - entry_price) / entry_price
                if pnl_pct_unrealized >= take_profit_pct:
                    exit_price = price * (1.0 - slippage_frac)
                    pnl_pct = (exit_price - entry_price) / entry_price
                    net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                    capital += net_pnl
                    trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="tp")
                    position = 0
                    bars_since_exit = 0
                    stopped = True

            # Trailing stop
            if not stopped and trailing_start_pct > 0 and trailing_drop_pct > 0:
                unrealized_pct = (position_peak_price - entry_price) / entry_price
                if unrealized_pct >= trailing_start_pct:
                    trail_stop = position_peak_price * (1.0 - trailing_drop_pct)
                    if low <= trail_stop:
                        exit_price = trail_stop * (1.0 - slippage_frac)
                        pnl_pct = (exit_price - entry_price) / entry_price
                        net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                        capital += net_pnl
                        trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="trail")
                        position = 0
                        bars_since_exit = 0
                        stopped = True

            # Max hold bars (force exit)
            if not stopped and max_hold_bars > 0 and bars_in_position >= max_hold_bars:
                exit_price = price * (1.0 - slippage_frac)
                pnl_pct = (exit_price - entry_price) / entry_price
                net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                capital += net_pnl
                trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="maxhold")
                position = 0
                bars_since_exit = 0
                stopped = True

            # Momentum exit: exit when formula score drops below neutral
            if not stopped and momentum_exit and signal_scores is not None and (i - 1) < len(signal_scores):
                score = signal_scores[i - 1]
                if score < 0.45:  # score dropped significantly
                    exit_price = price * (1.0 - slippage_frac)
                    pnl_pct = (exit_price - entry_price) / entry_price
                    net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                    capital += net_pnl
                    trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="momentum")
                    position = 0
                    bars_since_exit = 0
                    stopped = True

            # Standard exit: hold_bars elapsed + opposite signal
            if not stopped and bars_in_position >= hold_bars and sig == -1:
                exit_price = price * (1.0 - slippage_frac)
                pnl_pct = (exit_price - entry_price) / entry_price
                net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                capital += net_pnl
                trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="signal")
                position = 0
                bars_since_exit = 0

        elif position == -1:
            bars_in_position += 1
            stopped = False

            if price < position_peak_price:
                position_peak_price = price  # for short, peak is lowest price seen

            # Fixed SL
            if stop_loss > 0:
                stop_price = entry_price * (1.0 + stop_loss)
                if high >= stop_price:
                    exit_price = stop_price * (1.0 + slippage_frac)
                    pnl_pct = (entry_price - exit_price) / entry_price
                    net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                    capital += net_pnl
                    trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="sl")
                    position = 0
                    bars_since_exit = 0
                    stopped = True

            # Take profit
            if not stopped and take_profit_pct > 0:
                pnl_pct_unrealized = (entry_price - price) / entry_price
                if pnl_pct_unrealized >= take_profit_pct:
                    exit_price = price * (1.0 + slippage_frac)
                    pnl_pct = (entry_price - exit_price) / entry_price
                    net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                    capital += net_pnl
                    trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="tp")
                    position = 0
                    bars_since_exit = 0
                    stopped = True

            # Trailing stop (for shorts: peak is the lowest price, trail up)
            if not stopped and trailing_start_pct > 0 and trailing_drop_pct > 0:
                unrealized_pct = (entry_price - position_peak_price) / entry_price
                if unrealized_pct >= trailing_start_pct:
                    trail_stop = position_peak_price * (1.0 + trailing_drop_pct)
                    if high >= trail_stop:
                        exit_price = trail_stop * (1.0 + slippage_frac)
                        pnl_pct = (entry_price - exit_price) / entry_price
                        net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                        capital += net_pnl
                        trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="trail")
                        position = 0
                        bars_since_exit = 0
                        stopped = True

            # Max hold bars
            if not stopped and max_hold_bars > 0 and bars_in_position >= max_hold_bars:
                exit_price = price * (1.0 + slippage_frac)
                pnl_pct = (entry_price - exit_price) / entry_price
                net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                capital += net_pnl
                trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="maxhold")
                position = 0
                bars_since_exit = 0
                stopped = True

            # Momentum exit
            if not stopped and momentum_exit and signal_scores is not None and (i - 1) < len(signal_scores):
                score = signal_scores[i - 1]
                if score > 0.55:  # for shorts, score rising means momentum against
                    exit_price = price * (1.0 + slippage_frac)
                    pnl_pct = (entry_price - exit_price) / entry_price
                    net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                    capital += net_pnl
                    trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="momentum")
                    position = 0
                    bars_since_exit = 0
                    stopped = True

            # Standard exit
            if not stopped and bars_in_position >= hold_bars and sig == 1:
                exit_price = price * (1.0 + slippage_frac)
                pnl_pct = (entry_price - exit_price) / entry_price
                net_pnl = position_notional * pnl_pct - position_notional * abs(1.0 + pnl_pct) * fee_rate
                capital += net_pnl
                trades[-1].update(exit_ts=timestamps[i], exit_price=exit_price, pnl=net_pnl, exit_reason="signal")
                position = 0
                bars_since_exit = 0

        # FR
        if position != 0 and funding_rate_series is not None and i < len(funding_rate_series):
            fr_8h = float(funding_rate_series.iloc[i])
            fr_bar = fr_8h * (0.25 / 8.0)
            if position == 1:
                capital -= position_notional * fr_bar
            elif position == -1:
                capital += position_notional * fr_bar

        # Equity
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


def compute_sigmoid_scores(
    tokens: list[int], vocab: FormulaVocab,
    df: pd.DataFrame, features: dict[int, pd.Series],
) -> np.ndarray:
    vm = PandasStackVM(vocab)
    score_series = vm.execute(tokens, df, precomputed_features=features)
    if score_series is None:
        return np.full(len(df), 0.5)
    scores = score_series.values.astype(float)
    rolling_mean = pd.Series(scores).rolling(60, min_periods=10).mean().values
    rolling_std = pd.Series(scores).rolling(60, min_periods=10).std().values + 1e-8
    score_norm = np.nan_to_num((scores - rolling_mean) / rolling_std, 0.0).clip(-10, 10)
    return 1.0 / (1.0 + np.exp(-score_norm))


def main():
    v4_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=CEX_FEATURE_NAMES_V8, operator_names=v4_op_names)
    v52_tokens = [vocab.token_names.index(n) for n in V52_FORMULA_NAMES]
    vm = PandasStackVM(vocab)

    base_config = {
        "base_interval": "15m", "aux_interval": "1h",
        "buy_th": 0.57, "sell_th": 0.41,
        "stop_loss": 0.04, "position_size": 0.40,
        "hold_bars": 2, "cooldown_bars": 4, "fee_mode": "maker",
    }

    # Exit strategy configurations
    # (label, max_hold, tp_pct, trail_start, trail_drop, momentum_exit)
    exit_configs = [
        ("V5.2 baseline (hold2+signal)", 0, 0.0, 0.0, 0.0, False),
        ("Max hold 4 bars", 4, 0.0, 0.0, 0.0, False),
        ("Max hold 6 bars", 6, 0.0, 0.0, 0.0, False),
        ("TP 1.5%", 0, 0.015, 0.0, 0.0, False),
        ("TP 2.0%", 0, 0.020, 0.0, 0.0, False),
        ("TP 3.0%", 0, 0.030, 0.0, 0.0, False),
        ("Trail 1% start / 0.5% drop", 0, 0.0, 0.01, 0.005, False),
        ("Trail 1.5% start / 0.5% drop", 0, 0.0, 0.015, 0.005, False),
        ("Trail 2% start / 1% drop", 0, 0.0, 0.02, 0.01, False),
        ("Momentum exit", 0, 0.0, 0.0, 0.0, True),
        ("TP 1.5% + Momentum", 0, 0.015, 0.0, 0.0, True),
        ("Max hold 4 + Momentum", 4, 0.0, 0.0, 0.0, True),
        ("Trail 1%/0.5% + Momentum", 0, 0.0, 0.01, 0.005, True),
    ]

    print(f"{'Config':>40}  {'Sharpe':>7} {'Return':>8} {'MaxDD':>7} {'WR':>5} {'Trades':>6}")
    print(f"{'------':>40}  {'------':>7} {'------':>8} {'-----':>7} {'---':>5} {'------':>6}")

    for label, max_hold, tp, trail_start, trail_drop, mom_exit in exit_configs:
        portfolio_eq = None
        total_trades = 0
        total_wins = 0

        for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
            df, n = load_multi_tf_df(DB_PATH, symbol, "binance", "15m", "1h", "futures")
            if df.empty:
                continue

            features = precompute_features(df, vocab, vm)
            signals = compute_signals(v52_tokens, vocab, df, features, base_config["buy_th"], base_config["sell_th"])
            sigmoid = compute_sigmoid_scores(v52_tokens, vocab, df, features)

            regime_series, trend_dir_series = compute_regime(DB_PATH, symbol, "binance", df["timestamp"])
            fr_series = load_funding_rate_series(symbol, "binance", df["timestamp"])
            split_idx = n // 2

            df_oos = df.iloc[split_idx:].reset_index(drop=True)
            sig_oos = signals[split_idx:]
            score_oos = sigmoid[split_idx:]
            regime_oos = regime_series.iloc[split_idx:].reset_index(drop=True)
            trend_oos = trend_dir_series.iloc[split_idx:].reset_index(drop=True)
            fr_oos = fr_series.iloc[split_idx:].reset_index(drop=True)

            eq, trades = backtest_flexible_exit(
                df_oos, sig_oos, base_config,
                regime_series=regime_oos, trend_dir_series=trend_oos,
                funding_rate_series=fr_oos, pos_size_mult=1.0 / 3,
                max_hold_bars=max_hold, take_profit_pct=tp,
                trailing_start_pct=trail_start, trailing_drop_pct=trail_drop,
                momentum_exit=mom_exit, signal_scores=score_oos,
            )

            closed = [t for t in trades if "pnl" in t]
            total_trades += len(closed)
            total_wins += sum(1 for t in closed if t["pnl"] > 0)

            if portfolio_eq is None:
                portfolio_eq = np.array(eq)
            else:
                ml = min(len(portfolio_eq), len(eq))
                portfolio_eq = portfolio_eq[:ml] + np.array(eq[:ml])

        pm = compute_metrics(portfolio_eq.tolist(), [], len(portfolio_eq), bar_hours=0.25)
        pwr = total_wins / total_trades if total_trades else 0

        marker = ""
        if pwr > 0.68 and pm["sharpe"] > 16:
            marker = " ***"
        elif pwr > 0.67:
            marker = " +WR"

        print(f"{label:>40}  {pm['sharpe']:>7.2f} {pm['return']:>+7.1%} {pm['max_dd']:>6.1%} "
              f"{pwr:>4.0%} {total_trades:>6}{marker}")

    # Per-symbol breakdown for best configs
    print("\n=== Per-symbol detail ===")
    best_labels = ["V5.2 baseline (hold2+signal)", "TP 1.5%", "Momentum exit", "TP 1.5% + Momentum"]
    for blabel, max_hold, tp, trail_start, trail_drop, mom_exit in exit_configs:
        if blabel not in best_labels:
            continue
        print(f"\n  {blabel}")
        for symbol in ["BTC/USDT", "ETH/USDT", "SOL/USDT"]:
            df, n = load_multi_tf_df(DB_PATH, symbol, "binance", "15m", "1h", "futures")
            if df.empty:
                continue
            features = precompute_features(df, vocab, vm)
            signals = compute_signals(v52_tokens, vocab, df, features, base_config["buy_th"], base_config["sell_th"])
            sigmoid = compute_sigmoid_scores(v52_tokens, vocab, df, features)
            regime_series, trend_dir_series = compute_regime(DB_PATH, symbol, "binance", df["timestamp"])
            fr_series = load_funding_rate_series(symbol, "binance", df["timestamp"])
            split_idx = n // 2
            df_oos = df.iloc[split_idx:].reset_index(drop=True)
            sig_oos = signals[split_idx:]
            score_oos = sigmoid[split_idx:]
            regime_oos = regime_series.iloc[split_idx:].reset_index(drop=True)
            trend_oos = trend_dir_series.iloc[split_idx:].reset_index(drop=True)
            fr_oos = fr_series.iloc[split_idx:].reset_index(drop=True)
            eq, trades = backtest_flexible_exit(
                df_oos, sig_oos, base_config,
                regime_series=regime_oos, trend_dir_series=trend_oos,
                funding_rate_series=fr_oos,
                max_hold_bars=max_hold, take_profit_pct=tp,
                trailing_start_pct=trail_start, trailing_drop_pct=trail_drop,
                momentum_exit=mom_exit, signal_scores=score_oos,
            )
            m = compute_metrics(eq, trades, len(df_oos), bar_hours=0.25)
            # Exit reason breakdown
            closed = [t for t in trades if "pnl" in t]
            by_reason = {}
            for t in closed:
                r = t.get("exit_reason", "unknown")
                by_reason.setdefault(r, []).append(t)
            reason_str = "  ".join(f"{r}:{len(ts)}" for r, ts in sorted(by_reason.items()))
            print(f"  {symbol:>10}  Sharpe={m['sharpe']:.2f} WR={m['win_rate']:.0%} DD={m['max_dd']:.1%}  [{reason_str}]")


if __name__ == "__main__":
    main()
