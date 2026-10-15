"""Weekly P&L Report for V5.2 Strategy.

Backtests the TF_TREND TF_PRESSURE ADD formula on the most recent 4 weeks
of data, splitting into weekly periods to show per-week performance.

Usage:
    python scripts/weekly_pnl_report.py
    python scripts/weekly_pnl_report.py --weeks 8
    python scripts/weekly_pnl_report.py --symbol BTC/USDT
    python scripts/weekly_pnl_report.py --interval 1h
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

from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS
from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES_V4
from selene_space_alpha.vm import PandasStackVM
from selene_space_alpha.regime import (
    RegimeConfig,
    classify_regime_series,
    compute_trend_direction,
)

# Reuse V4 data loading and feature computation
from optuna_formula_search_v4 import (
    load_multi_tf_df,
    precompute_features,
)

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"
DERIVATIVES_DB_PATH = PROJECT_ROOT / "data" / "derivatives.db"

# Bar duration in hours by interval
INTERVAL_HOURS = {
    "1m": 1/60, "5m": 5/60, "15m": 15/60,
    "1h": 1.0, "4h": 4.0, "8h": 8.0, "1d": 24.0,
}


# ---------------------------------------------------------------------------
# V5.2 Strategy Config
# ---------------------------------------------------------------------------

STRATEGY_CONFIGS = {
    "15m": {
        "tokens": [21, 24, 27],  # TF_TREND TF_PRESSURE ADD
        "base_interval": "15m",
        "aux_interval": "1h",
        "buy_th": 0.57,
        "sell_th": 0.41,
        "stop_loss": 0.04,
        "position_size": 0.40,
        "hold_bars": 2,
        "cooldown_bars": 4,
        "fee_mode": "maker",
    },
    "1h": {
        "tokens": [21, 24, 27],  # Same formula
        "base_interval": "1h",
        "aux_interval": "4h",
        "buy_th": 0.57,
        "sell_th": 0.41,
        "stop_loss": 0.04,
        "position_size": 0.50,
        "hold_bars": 2,
        "cooldown_bars": 4,
        "fee_mode": "maker",
    },
    "15m_taker": {
        "tokens": [21, 24, 27],
        "base_interval": "15m",
        "aux_interval": "1h",
        "buy_th": 0.57,
        "sell_th": 0.41,
        "stop_loss": 0.04,
        "position_size": 0.40,
        "hold_bars": 2,
        "cooldown_bars": 4,
        "fee_mode": "taker",
    },
}

ADAPTIVE_WEIGHTS = {
    "trending": {"long_weight": 0.11, "short_weight": 0.76},
    "ranging": {"long_weight": 0.07, "short_weight": 0.62},
    "volatile": {"long_weight": 0.11, "short_weight": 0.73,
                 "require_agreement": False, "pos_reduction": 0.20},
}


# ---------------------------------------------------------------------------
# Funding Rate Loading
# ---------------------------------------------------------------------------

def load_funding_rate_series(
    symbol: str,
    exchange: str = "binance",
    base_timestamps: pd.Series | None = None,
) -> pd.Series:
    """Load funding rate series aligned to base timestamps.

    FR is 8h-rate, expressed as fraction (0.0001 = 0.01%).
    Aligned via forward-fill to base timestamps.
    Returns a Series of per-bar funding rate fractions (8h rate, not prorated).
    """
    if not DERIVATIVES_DB_PATH.exists():
        if base_timestamps is not None:
            return pd.Series(0.0, index=base_timestamps.index)
        return pd.Series([], dtype=float)

    conn = sqlite3.connect(str(DERIVATIVES_DB_PATH))
    fr_df = pd.read_sql_query(
        "SELECT timestamp, funding_rate FROM funding_rates "
        "WHERE symbol=? AND exchange=? ORDER BY timestamp ASC",
        conn, params=(symbol, exchange),
    )
    conn.close()

    if fr_df.empty or base_timestamps is None:
        if base_timestamps is not None:
            return pd.Series(0.0, index=base_timestamps.index)
        return pd.Series([], dtype=float)

    fr_df["funding_rate"] = pd.to_numeric(fr_df["funding_rate"], errors="coerce").fillna(0.0)

    # Forward-fill align to base timestamps
    src = fr_df.set_index("timestamp").sort_index()
    aligned = src.reindex(base_timestamps, method="ffill").fillna(0.0)
    return aligned["funding_rate"].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Signal Computation
# ---------------------------------------------------------------------------

def compute_signals(
    tokens: list[int],
    vocab: FormulaVocab,
    df: pd.DataFrame,
    features: dict[int, pd.Series],
    buy_th: float,
    sell_th: float,
) -> np.ndarray:
    """Compute discrete signals from formula."""
    vm = PandasStackVM(vocab)
    score_series = vm.execute(tokens, df, precomputed_features=features)
    if score_series is None:
        return np.zeros(len(df), dtype=int)

    score_pd = pd.Series(score_series.values.astype(float))
    rolling_mean = score_pd.rolling(60, min_periods=10).mean()
    rolling_std = score_pd.rolling(60, min_periods=10).std() + 1e-8
    score_norm = ((score_pd - rolling_mean) / rolling_std).fillna(0.0).clip(-10, 10)
    sigmoid = 1.0 / (1.0 + np.exp(-score_norm))

    signals = np.where(sigmoid > buy_th, 1, np.where(sigmoid < sell_th, -1, 0))
    return signals


# ---------------------------------------------------------------------------
# Vectorized Backtest with Equity Curve
# ---------------------------------------------------------------------------

def backtest_with_equity(
    df: pd.DataFrame,
    signals: np.ndarray,
    config: dict,
    initial_capital: float = 10000.0,
    regime_series: pd.Series | None = None,
    trend_dir_series: pd.Series | None = None,
    funding_rate_series: pd.Series | None = None,
) -> tuple[list[float], list[dict]]:
    """Run backtest returning full equity curve and trade list.

    If funding_rate_series is provided, applies funding rate cost/benefit
    to positions. FR is the 8h rate; per-bar cost = FR * (bar_hours / 8).
    Long position pays FR if positive, earns if negative.
    Short position earns FR if positive, pays if negative.
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

        # Adaptive position sizing based on regime
        pos_mult = 1.0
        if regime_series is not None and i < len(regime_series):
            regime = str(regime_series.iloc[i])
            if regime == "volatile":
                pos_mult = 1.0 - ADAPTIVE_WEIGHTS["volatile"]["pos_reduction"]
            # Trend filter: in trending, only trade with trend
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

        # Track equity
        # Apply funding rate cost/benefit for held positions
        # FR is 8h rate; prorate to bar duration
        if position != 0 and funding_rate_series is not None and i < len(funding_rate_series):
            fr_8h = float(funding_rate_series.iloc[i])
            bar_hours = INTERVAL_HOURS.get(config["base_interval"], 0.25)
            fr_bar = fr_8h * (bar_hours / 8.0)
            # Long pays FR, short earns FR (if FR > 0)
            # Long earns -FR, short pays -FR (if FR < 0)
            if position == 1:
                capital -= position_notional * fr_bar  # long pays
            elif position == -1:
                capital += position_notional * fr_bar  # short earns

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
# Regime Computation
# ---------------------------------------------------------------------------

def compute_regime(
    db_path: Path,
    symbol: str,
    exchange: str,
    base_timestamps: pd.Series,
    market_type: str = "futures",
) -> tuple[pd.Series, pd.Series]:
    """Compute regime classification and trend direction, aligned to base."""
    conn = sqlite3.connect(str(db_path))
    regime_df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval='4h' AND market_type=? "
        "ORDER BY timestamp ASC",
        conn, params=(symbol, exchange, market_type),
    )
    conn.close()

    if regime_df.empty:
        n = len(base_timestamps)
        return pd.Series(["volatile"] * n), pd.Series([0] * n)

    for col in ["open", "high", "low", "close", "volume"]:
        regime_df[col] = pd.to_numeric(regime_df[col], errors="coerce")

    regime_cfg = RegimeConfig()
    regime_series = classify_regime_series(regime_df, regime_cfg)
    trend_dir = compute_trend_direction(regime_df)

    # Forward-fill align to base timestamps
    src = pd.DataFrame({
        "timestamp": regime_df["timestamp"],
        "regime": regime_series.values,
        "trend_dir": trend_dir.values,
    }).set_index("timestamp").sort_index()

    aligned = src.reindex(base_timestamps, method="ffill").fillna({"regime": "volatile", "trend_dir": 0})
    return aligned["regime"].reset_index(drop=True), aligned["trend_dir"].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Weekly Split & Report
# ---------------------------------------------------------------------------

def split_into_weeks(
    df: pd.DataFrame,
    equity_curve: list[float],
    trades: list[dict],
    n_warmup: int = 200,
) -> list[dict]:
    """Split results into weekly periods."""
    if df.empty or len(equity_curve) < n_warmup:
        return []

    timestamps = df["timestamp"].values
    ms_per_week = 7 * 24 * 3600 * 1000

    # Start from warmup period
    start_ts = timestamps[n_warmup]
    end_ts = timestamps[-1]

    weeks = []
    week_start = start_ts
    week_idx = 0

    while week_start < end_ts:
        week_end = week_start + ms_per_week
        week_idx += 1

        # Find bar indices for this week
        mask = (timestamps >= week_start) & (timestamps < week_end)
        bar_indices = np.where(mask)[0]

        if len(bar_indices) == 0:
            week_start = week_end
            continue

        # Equity at start/end
        first_bar = bar_indices[0]
        last_bar = bar_indices[-1]
        eq_start = equity_curve[first_bar] if first_bar < len(equity_curve) else equity_curve[-1]
        eq_end = equity_curve[min(last_bar + 1, len(equity_curve) - 1)]

        # Weekly return
        weekly_return = (eq_end - eq_start) / eq_start if eq_start > 0 else 0.0

        # Max drawdown within week
        week_equity = equity_curve[first_bar:last_bar + 2]
        peak = eq_start
        max_dd = 0.0
        for eq in week_equity:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd

        # Trades in this week
        week_trades = [
            t for t in trades
            if t.get("exit_ts") is not None
            and week_start <= t["exit_ts"] < week_end
        ]
        n_trades = len(week_trades)
        n_wins = sum(1 for t in week_trades if t.get("pnl", 0) > 0)
        total_pnl = sum(t.get("pnl", 0) for t in week_trades)
        fee_cost = sum(
            t.get("notional", 0) * 0.0002 * 2  # entry + exit maker fee
            for t in week_trades
        )

        # Date labels
        dt_start = datetime.fromtimestamp(week_start / 1000, tz=timezone.utc)
        dt_end = datetime.fromtimestamp(min(week_end - 1, end_ts) / 1000, tz=timezone.utc)

        weeks.append({
            "week": week_idx,
            "start": dt_start.strftime("%m/%d"),
            "end": dt_end.strftime("%m/%d"),
            "return": weekly_return,
            "max_dd": -max_dd,
            "trades": n_trades,
            "wins": n_wins,
            "win_rate": n_wins / n_trades if n_trades > 0 else 0.0,
            "pnl": total_pnl,
            "fees": fee_cost,
        })

        week_start = week_end

    return weeks


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Weekly P&L Report for V5.2 Strategy")
    parser.add_argument("--weeks", type=int, default=4, help="Number of recent weeks to report")
    parser.add_argument("--symbol", type=str, default="all", help="Symbol (BTC/USDT, ETH/USDT, SOL/USDT, or all)")
    parser.add_argument("--interval", type=str, default="15m", help="Base interval (15m or 1h)")
    parser.add_argument("--exchange", type=str, default="binance")
    parser.add_argument("--market-type", type=str, default="futures")
    args = parser.parse_args()

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if args.symbol == "all" else [args.symbol]
    config = STRATEGY_CONFIGS.get(args.interval)
    if config is None:
        print(f"Unknown interval: {args.interval}. Use 15m or 1h.")
        return

    v4_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=CEX_FEATURE_NAMES_V4, operator_names=v4_op_names)

    print(f"\n{'='*70}")
    print("  V5.2 Strategy Weekly P&L Report")
    print("  Formula: TF_TREND TF_PRESSURE ADD")
    print(f"  Interval: {config['base_interval']} (aux: {config['aux_interval']})")
    print(f"  Fee mode: {config['fee_mode']} ({'0.02%' if config['fee_mode'] == 'maker' else '0.05%'})")
    print(f"  Pos: {config['position_size']:.0%}  SL: {config['stop_loss']:.0%}  "
          f"Hold: {config['hold_bars']}  Cool: {config['cooldown_bars']}")
    print(f"{'='*70}\n")

    for symbol in symbols:
        print(f"--- {symbol} ({args.interval}) ---")

        # Load data
        df, n = load_multi_tf_df(
            DB_PATH, symbol, args.exchange,
            config["base_interval"], config["aux_interval"],
            args.market_type,
        )
        if df.empty:
            print(f"  No data for {symbol}")
            continue

        # Data range
        ts_start = datetime.fromtimestamp(df["timestamp"].iloc[0] / 1000, tz=timezone.utc)
        ts_end = datetime.fromtimestamp(df["timestamp"].iloc[-1] / 1000, tz=timezone.utc)
        print(f"  Data: {ts_start.strftime('%Y-%m-%d')} ~ {ts_end.strftime('%Y-%m-%d')} ({n} bars)")

        # Compute features
        vm = PandasStackVM(vocab)
        features = precompute_features(df, vocab, vm)

        # Compute signals
        signals = compute_signals(
            config["tokens"], vocab, df, features,
            config["buy_th"], config["sell_th"],
        )

        # Compute regime
        regime_series, trend_dir_series = compute_regime(
            DB_PATH, symbol, args.exchange,
            df["timestamp"], args.market_type,
        )

        # Load funding rate for cost calculation
        fr_series = load_funding_rate_series(
            symbol, args.exchange, df["timestamp"],
        )

        # Run backtest (with funding rate cost)
        equity_curve, trades = backtest_with_equity(
            df, signals, config,
            regime_series=regime_series,
            trend_dir_series=trend_dir_series,
            funding_rate_series=fr_series,
        )

        # Overall metrics
        total_return = (equity_curve[-1] - equity_curve[0]) / equity_curve[0]
        peak = equity_curve[0]
        max_dd = 0.0
        for eq in equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak
            if dd > max_dd:
                max_dd = dd

        n_total = len(trades)
        n_win = sum(1 for t in trades if t.get("pnl", 0) > 0)
        total_pnl = sum(t.get("pnl", 0) for t in trades)

        print(f"  Overall: Return {total_return:+.1%}  MaxDD {-max_dd:.1%}  "
              f"Trades {n_total}  WinRate {n_win/n_total:.0%}" if n_total > 0 else
              f"  Overall: Return {total_return:+.1%}  MaxDD {-max_dd:.1%}  No trades")

        # Weekly breakdown
        n_warmup = min(200, n // 2)
        weeks = split_into_weeks(df, equity_curve, trades, n_warmup)

        # Only show the most recent N weeks
        weeks = weeks[-args.weeks:] if len(weeks) > args.weeks else weeks

        if not weeks:
            print("  Not enough data for weekly breakdown\n")
            continue

        print(f"\n  {'Week':>4}  {'Date Range':>11}  {'Return':>8}  {'MaxDD':>7}  "
              f"{'Trades':>6}  {'WinRate':>7}  {'PnL':>9}  {'Fees':>7}")
        print(f"  {'----':>4}  {'-----------':>11}  {'--------':>8}  {'-------':>7}  "
              f"{'------':>6}  {'-------':>7}  {'---------':>9}  {'-------':>7}")

        for w in weeks:
            print(f"  {w['week']:>4}  {w['start']}~{w['end']:>5}  "
                  f"{w['return']:>+7.2%}  {w['max_dd']:>6.2%}  "
                  f"{w['trades']:>6}  {w['win_rate']:>6.0%}  "
                  f"${w['pnl']:>+8.1f}  ${w['fees']:>6.1f}")

        # Weekly summary
        avg_weekly = np.mean([w["return"] for w in weeks])
        pos_weeks = sum(1 for w in weeks if w["return"] > 0)
        print(f"\n  Avg weekly: {avg_weekly:+.2%}  "
              f"Positive weeks: {pos_weeks}/{len(weeks)}  "
              f"Total PnL: ${sum(w['pnl'] for w in weeks):+.1f}\n")


if __name__ == "__main__":
    main()
