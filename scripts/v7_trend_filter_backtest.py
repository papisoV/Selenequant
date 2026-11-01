"""V7 Trend Filter Backtest -- 8h/1d trend direction filtering on 15m signals.

Core idea: Only execute 15m signals that align with the higher-timeframe
trend direction. Counter-trend signals are suppressed, which should
increase win rate and reduce drawdown at the cost of fewer trades.

Three filter modes compared:
  - none:  No trend filter (baseline V5.2)
  - 8h:    Filter against 8h EMA trend
  - 1d:    Filter against 1d EMA trend

Usage:
    python scripts/v7_trend_filter_backtest.py
    python scripts/v7_trend_filter_backtest.py --filter 8h
    python scripts/v7_trend_filter_backtest.py --symbol BTC/USDT
    python scripts/v7_trend_filter_backtest.py --ema-period 50
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

from optuna_formula_search_v4 import (
    load_multi_tf_df,
    precompute_features,
)

DB_PATH = PROJECT_ROOT / "services" / "local-api" / "data" / "klines.db"
DERIVATIVES_DB_PATH = PROJECT_ROOT / "data" / "derivatives.db"

INTERVAL_HOURS = {
    "1m": 1/60, "5m": 5/60, "15m": 15/60,
    "1h": 1.0, "4h": 4.0, "8h": 8.0, "1d": 24.0,
}

# V5.2 strategy config
STRATEGY_CONFIG = {
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
}

ADAPTIVE_WEIGHTS = {
    "trending": {"long_weight": 0.11, "short_weight": 0.76},
    "ranging": {"long_weight": 0.07, "short_weight": 0.62},
    "volatile": {"long_weight": 0.11, "short_weight": 0.73,
                 "require_agreement": False, "pos_reduction": 0.20},
}


# ---------------------------------------------------------------------------
# Higher-Timeframe Trend Computation
# ---------------------------------------------------------------------------

def load_htf_df(
    db_path: Path,
    symbol: str,
    exchange: str,
    interval: str,
    market_type: str = "futures",
) -> pd.DataFrame:
    """Load higher-timeframe OHLCV data."""
    conn = sqlite3.connect(str(db_path))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? AND market_type=? "
        "ORDER BY timestamp ASC",
        conn, params=(symbol, exchange, interval, market_type),
    )
    conn.close()
    if df.empty:
        return df
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def compute_ema_trend(
    htf_df: pd.DataFrame,
    ema_period: int = 21,
    base_timestamps: pd.Series | None = None,
) -> pd.Series:
    """Compute EMA-based trend direction from higher-timeframe data.

    Returns +1 (uptrend: price above EMA) / -1 (downtrend: price below EMA).
    Forward-filled and aligned to base_timestamps.
    """
    if htf_df.empty:
        if base_timestamps is not None:
            return pd.Series(0, index=base_timestamps.index)
        return pd.Series([], dtype=int)

    ema = htf_df["close"].ewm(span=ema_period, adjust=False).mean()
    direction = np.where(htf_df["close"] > ema, 1, -1)

    if base_timestamps is not None:
        src = pd.DataFrame({
            "timestamp": htf_df["timestamp"],
            "trend": direction,
        }).set_index("timestamp").sort_index()
        aligned = src.reindex(base_timestamps, method="ffill").fillna(0)
        return aligned["trend"].astype(int).reset_index(drop=True)

    return pd.Series(direction, name="htf_trend")


def compute_sma_trend(
    htf_df: pd.DataFrame,
    sma_period: int = 50,
    base_timestamps: pd.Series | None = None,
) -> pd.Series:
    """Compute SMA-based trend direction from higher-timeframe data.

    Returns +1 (uptrend: price above SMA) / -1 (downtrend: price below SMA).
    """
    if htf_df.empty:
        if base_timestamps is not None:
            return pd.Series(0, index=base_timestamps.index)
        return pd.Series([], dtype=int)

    sma = htf_df["close"].rolling(sma_period, min_periods=sma_period // 2).mean()
    direction = np.where(htf_df["close"] > sma, 1, -1)

    if base_timestamps is not None:
        src = pd.DataFrame({
            "timestamp": htf_df["timestamp"],
            "trend": direction,
        }).set_index("timestamp").sort_index()
        aligned = src.reindex(base_timestamps, method="ffill").fillna(0)
        return aligned["trend"].astype(int).reset_index(drop=True)

    return pd.Series(direction, name="htf_trend")


def compute_dual_ema_trend(
    htf_df: pd.DataFrame,
    fast_period: int = 21,
    slow_period: int = 50,
    base_timestamps: pd.Series | None = None,
) -> pd.Series:
    """Compute dual-EMA trend: fast EMA > slow EMA = uptrend.

    More robust than single EMA — filters out whipsaws.
    """
    if htf_df.empty:
        if base_timestamps is not None:
            return pd.Series(0, index=base_timestamps.index)
        return pd.Series([], dtype=int)

    fast_ema = htf_df["close"].ewm(span=fast_period, adjust=False).mean()
    slow_ema = htf_df["close"].ewm(span=slow_period, adjust=False).mean()
    direction = np.where(fast_ema > slow_ema, 1, -1)

    if base_timestamps is not None:
        src = pd.DataFrame({
            "timestamp": htf_df["timestamp"],
            "trend": direction,
        }).set_index("timestamp").sort_index()
        aligned = src.reindex(base_timestamps, method="ffill").fillna(0)
        return aligned["trend"].astype(int).reset_index(drop=True)

    return pd.Series(direction, name="htf_trend")


# ---------------------------------------------------------------------------
# Funding Rate Loading
# ---------------------------------------------------------------------------

def load_funding_rate_series(
    symbol: str,
    exchange: str = "binance",
    base_timestamps: pd.Series | None = None,
) -> pd.Series:
    """Load funding rate series aligned to base timestamps."""
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
# Vectorized Backtest with HTF Trend Filter
# ---------------------------------------------------------------------------

def backtest_with_trend_filter(
    df: pd.DataFrame,
    signals: np.ndarray,
    config: dict,
    initial_capital: float = 10000.0,
    regime_series: pd.Series | None = None,
    trend_dir_series: pd.Series | None = None,
    htf_trend_series: pd.Series | None = None,
    funding_rate_series: pd.Series | None = None,
    trend_mode: str = "hard_filter",
    counter_trend_pos_mult: float = 0.5,
    counter_trend_sl_mult: float = 0.5,
    counter_trend_hold_mult: float = 0.5,
) -> tuple[list[float], list[dict]]:
    """Run backtest with higher-timeframe trend filter.

    trend_mode controls how counter-trend signals are handled:
      - "none":         No HTF filter (baseline)
      - "hard_filter":  Counter-trend signals suppressed entirely
      - "reduce_pos":   Counter-trend signals kept but position reduced
      - "trend_only":   Only trade WITH trend (no counter-trend at all)
      - "tight_exit":   Counter-trend: tighter SL + faster exit + reduced pos
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
    position_is_counter_trend = False  # Track if current position is counter-trend

    for i in range(1, n):
        sig = int(signals[i - 1])
        price = closes[i]
        low = lows[i]
        high = highs[i]

        if not np.isfinite(capital) or capital <= 0:
            equity_curve.append(equity_curve[-1])
            continue

        # Determine if this signal is counter-trend
        is_counter_trend = False
        if htf_trend_series is not None and i < len(htf_trend_series):
            htf_trend = int(htf_trend_series.iloc[i])
            if htf_trend != 0 and sig != 0 and np.sign(sig) != np.sign(htf_trend):
                is_counter_trend = True

        # Apply HTF trend filter based on mode
        if is_counter_trend:
            if trend_mode == "hard_filter" or trend_mode == "trend_only":
                sig = 0  # Suppress counter-trend signal

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

        # Reduce position for counter-trend trades
        if is_counter_trend and sig != 0:
            if trend_mode == "reduce_pos":
                pos_mult *= counter_trend_pos_mult
            elif trend_mode == "tight_exit":
                pos_mult *= counter_trend_pos_mult

        # Determine SL and hold for counter-trend positions (tight_exit mode)
        effective_sl = stop_loss
        effective_hold = hold_bars
        if position_is_counter_trend and trend_mode == "tight_exit":
            effective_sl = stop_loss * counter_trend_sl_mult
            effective_hold = max(1, int(hold_bars * counter_trend_hold_mult))

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
                position_is_counter_trend = is_counter_trend and sig == 1
                trades.append({
                    "entry_ts": timestamps[i], "entry_price": entry_price,
                    "side": "long", "notional": position_notional,
                    "counter_trend": position_is_counter_trend,
                })
            elif sig == -1 and bars_since_exit >= cooldown_bars:
                position = -1
                entry_price = price * (1.0 - slippage_frac)
                position_notional = current_notional
                capital -= position_notional * fee_rate
                bars_in_position = 0
                bars_since_exit = 0
                position_is_counter_trend = is_counter_trend and sig == -1
                trades.append({
                    "entry_ts": timestamps[i], "entry_price": entry_price,
                    "side": "short", "notional": position_notional,
                    "counter_trend": position_is_counter_trend,
                })

        elif position == 1:
            bars_in_position += 1
            stopped = False

            if effective_sl > 0:
                stop_price = entry_price * (1.0 - effective_sl)
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
                    position_is_counter_trend = False
                    stopped = True

            if not stopped and bars_in_position >= effective_hold and sig == -1:
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
                position_is_counter_trend = False

        elif position == -1:
            bars_in_position += 1
            stopped = False

            if effective_sl > 0:
                stop_price = entry_price * (1.0 + effective_sl)
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
                    position_is_counter_trend = False
                    stopped = True

            if not stopped and bars_in_position >= effective_hold and sig == 1:
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
                position_is_counter_trend = False
                trades[-1]["exit_price"] = exit_price
                trades[-1]["pnl"] = net_pnl
                trades[-1]["stop"] = False
                position = 0
                bars_since_exit = 0

        # Funding rate cost/benefit
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

    src = pd.DataFrame({
        "timestamp": regime_df["timestamp"],
        "regime": regime_series.values,
        "trend_dir": trend_dir.values,
    }).set_index("timestamp").sort_index()

    aligned = src.reindex(base_timestamps, method="ffill").fillna({"regime": "volatile", "trend_dir": 0})
    return aligned["regime"].reset_index(drop=True), aligned["trend_dir"].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Metrics Computation
# ---------------------------------------------------------------------------

def compute_metrics(
    equity_curve: list[float],
    trades: list[dict],
    n_bars: int,
    bar_hours: float = 0.25,
    n_warmup: int = 200,
) -> dict:
    """Compute backtest metrics from equity curve and trade list."""
    eq = equity_curve[n_warmup:]
    if len(eq) < 2:
        return {"sharpe": 0.0, "return": 0.0, "max_dd": 0.0,
                "win_rate": 0.0, "trades": 0, "avg_pnl": 0.0}

    # Return
    total_return = (eq[-1] - eq[0]) / eq[0] if eq[0] > 0 else 0.0

    # Max drawdown
    peak = eq[0]
    max_dd = 0.0
    for e in eq:
        if e > peak:
            peak = e
        dd = (peak - e) / peak if peak > 0 else 0.0
        if dd > max_dd:
            max_dd = dd

    # Per-bar returns for Sharpe
    eq_arr = np.array(eq)
    bar_returns = np.diff(eq_arr) / eq_arr[:-1]
    bar_returns = bar_returns[np.isfinite(bar_returns)]

    if len(bar_returns) > 1 and np.std(bar_returns) > 0:
        bars_per_year = 365 * 24 / bar_hours
        sharpe = np.mean(bar_returns) / np.std(bar_returns) * np.sqrt(bars_per_year)
    else:
        sharpe = 0.0

    # Trade stats
    closed_trades = [t for t in trades if "pnl" in t]
    n_trades = len(closed_trades)
    n_wins = sum(1 for t in closed_trades if t["pnl"] > 0)
    win_rate = n_wins / n_trades if n_trades > 0 else 0.0
    avg_pnl = np.mean([t["pnl"] for t in closed_trades]) if closed_trades else 0.0

    # Long/short breakdown
    long_trades = [t for t in closed_trades if t.get("side") == "long"]
    short_trades = [t for t in closed_trades if t.get("side") == "short"]
    long_wr = sum(1 for t in long_trades if t["pnl"] > 0) / len(long_trades) if long_trades else 0.0
    short_wr = sum(1 for t in short_trades if t["pnl"] > 0) / len(short_trades) if short_trades else 0.0

    # Counter-trend vs trend-aligned breakdown
    ct_trades = [t for t in closed_trades if t.get("counter_trend")]
    ta_trades = [t for t in closed_trades if not t.get("counter_trend")]
    ct_wr = sum(1 for t in ct_trades if t["pnl"] > 0) / len(ct_trades) if ct_trades else 0.0
    ta_wr = sum(1 for t in ta_trades if t["pnl"] > 0) / len(ta_trades) if ta_trades else 0.0

    # Stop-loss stats
    stopped = sum(1 for t in closed_trades if t.get("stop"))
    stop_rate = stopped / n_trades if n_trades > 0 else 0.0

    return {
        "sharpe": sharpe,
        "return": total_return,
        "max_dd": -max_dd,
        "win_rate": win_rate,
        "trades": n_trades,
        "avg_pnl": avg_pnl,
        "long_wr": long_wr,
        "short_wr": short_wr,
        "stop_rate": stop_rate,
        "ct_wr": ct_wr,
        "ta_wr": ta_wr,
        "ct_trades": len(ct_trades),
        "ta_trades": len(ta_trades),
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="V7 Trend Filter Backtest")
    parser.add_argument("--symbol", type=str, default="all",
                        help="Symbol (BTC/USDT, ETH/USDT, SOL/USDT, or all)")
    parser.add_argument("--filter", type=str, default="compare",
                        choices=["none", "8h", "1d", "compare"],
                        help="Trend filter mode: none/8h/1d/compare(all)")
    parser.add_argument("--ema-period", type=int, default=21,
                        help="EMA period for trend (default: 21)")
    parser.add_argument("--sma-period", type=int, default=50,
                        help="SMA period for trend (default: 50)")
    parser.add_argument("--trend-method", type=str, default="dual_ema",
                        choices=["ema", "sma", "dual_ema"],
                        help="Trend computation method")
    parser.add_argument("--trend-mode", type=str, default="compare",
                        choices=["hard_filter", "reduce_pos", "trend_only", "tight_exit", "compare"],
                        help="Trend filter mode")
    parser.add_argument("--counter-pos", type=float, default=0.5,
                        help="Position multiplier for counter-trend trades")
    parser.add_argument("--counter-sl", type=float, default=0.5,
                        help="SL multiplier for counter-trend trades in tight_exit mode")
    parser.add_argument("--counter-hold", type=float, default=0.5,
                        help="Hold bars multiplier for counter-trend trades in tight_exit mode")
    parser.add_argument("--exchange", type=str, default="binance")
    parser.add_argument("--market-type", type=str, default="futures")
    args = parser.parse_args()

    symbols = ["BTC/USDT", "ETH/USDT", "SOL/USDT"] if args.symbol == "all" else [args.symbol]
    config = STRATEGY_CONFIG

    v4_op_names = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=CEX_FEATURE_NAMES_V4, operator_names=v4_op_names)

    filter_modes = ["none", "8h", "1d"] if args.filter == "compare" else [args.filter]
    trend_modes = ["hard_filter", "reduce_pos", "tight_exit"] if args.trend_mode == "compare" else [args.trend_mode]

    print(f"\n{'='*80}")
    print("  V7 Trend Filter Backtest")
    print("  Formula: TF_TREND TF_PRESSURE ADD")
    print(f"  Base: {config['base_interval']}  Aux: {config['aux_interval']}")
    print(f"  Trend method: {args.trend_method} (EMA={args.ema_period}, SMA={args.sma_period})")
    print(f"  Fee: {config['fee_mode']}  Pos: {config['position_size']:.0%}  SL: {config['stop_loss']:.0%}")
    print(f"  HTF filters: {', '.join(filter_modes)}")
    print(f"  Trend modes: {', '.join(trend_modes)}")
    if "reduce_pos" in trend_modes:
        print(f"  Counter-trend pos mult: {args.counter_pos}")
    print(f"{'='*80}\n")

    for symbol in symbols:
        print(f"{'─'*80}")
        print(f"  {symbol}")
        print(f"{'─'*80}")

        # Load base 15m + 1h aux data
        df, n = load_multi_tf_df(
            DB_PATH, symbol, args.exchange,
            config["base_interval"], config["aux_interval"],
            args.market_type,
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
            DB_PATH, symbol, args.exchange,
            df["timestamp"], args.market_type,
        )

        # Load funding rate
        fr_series = load_funding_rate_series(
            symbol, args.exchange, df["timestamp"],
        )

        # Load higher-timeframe data for trend filter
        htf_data: dict[str, pd.Series] = {}
        for interval in ["8h", "1d"]:
            htf_df = load_htf_df(
                DB_PATH, symbol, args.exchange,
                interval, args.market_type,
            )
            if htf_df.empty:
                print(f"  WARNING: No {interval} data for {symbol}")
                htf_data[interval] = pd.Series(0, index=df.index)
            else:
                if args.trend_method == "ema":
                    htf_data[interval] = compute_ema_trend(
                        htf_df, args.ema_period, df["timestamp"],
                    )
                elif args.trend_method == "sma":
                    htf_data[interval] = compute_sma_trend(
                        htf_df, args.sma_period, df["timestamp"],
                    )
                else:  # dual_ema
                    htf_data[interval] = compute_dual_ema_trend(
                        htf_df, args.ema_period, args.sma_period, df["timestamp"],
                    )
                print(f"  {interval} bars loaded: {len(htf_df)}")

        # Run backtest for each filter mode × trend mode combination
        results: dict[str, dict] = {}
        for tmode in trend_modes:
            for fmode in filter_modes:
                htf_trend = None
                if fmode == "8h":
                    htf_trend = htf_data.get("8h")
                elif fmode == "1d":
                    htf_trend = htf_data.get("1d")

                equity_curve, trades = backtest_with_trend_filter(
                    df, signals, config,
                    regime_series=regime_series,
                    trend_dir_series=trend_dir_series,
                    htf_trend_series=htf_trend,
                    funding_rate_series=fr_series,
                    trend_mode=tmode,
                    counter_trend_pos_mult=args.counter_pos,
                    counter_trend_sl_mult=args.counter_sl,
                    counter_trend_hold_mult=args.counter_hold,
                )

                metrics = compute_metrics(
                    equity_curve, trades, n,
                    bar_hours=INTERVAL_HOURS[config["base_interval"]],
                )
                key = f"{fmode}/{tmode}" if fmode != "none" else "none"
                results[key] = metrics

        # Print comparison table
        print(f"\n  {'Mode':>20}  {'Sharpe':>7}  {'Return':>8}  {'MaxDD':>7}  "
              f"{'WinRate':>7}  {'Trades':>6}  {'TA_WR':>6}  {'CT_WR':>6}  {'CT#':>4}  {'StopR':>5}")
        print(f"  {'----':>20}  {'------':>7}  {'------':>8}  {'-----':>7}  "
              f"{'-------':>7}  {'------':>6}  {'-----':>6}  {'-----':>6}  {'---':>4}  {'-----':>5}")

        # Print baseline first, then sorted by key
        sorted_keys = ["none"] + [k for k in results if k != "none"]
        for key in sorted_keys:
            m = results[key]
            print(f"  {key:>20}  {m['sharpe']:>7.2f}  {m['return']:>+7.1%}  {m['max_dd']:>6.1%}  "
                  f"{m['win_rate']:>6.0%}  {m['trades']:>6}  {m['ta_wr']:>5.0%}  "
                  f"{m['ct_wr']:>5.0%}  {m['ct_trades']:>4}  {m['stop_rate']:>4.0%}")

        # Highlight best improvement
        if "none" in results and len(results) > 1:
            baseline = results["none"]
            print("\n  --- vs Baseline (no filter) ---")
            best_wr_key = None
            best_wr_delta = -999
            for key in sorted_keys:
                if key == "none":
                    continue
                m = results[key]
                wr_delta = (m["win_rate"] - baseline["win_rate"]) * 100
                dd_delta = (m["max_dd"] - baseline["max_dd"]) * 100
                trade_delta = m["trades"] - baseline["trades"]
                sharpe_delta = m["sharpe"] - baseline["sharpe"]
                print(f"  {key:>20}: WR {wr_delta:+.1f}pp  DD {dd_delta:+.1f}pp  "
                      f"Trades {trade_delta:+d}  Sharpe {sharpe_delta:+.2f}")
                if wr_delta > best_wr_delta:
                    best_wr_delta = wr_delta
                    best_wr_key = key
            if best_wr_key:
                print(f"  >>> Best win rate improvement: {best_wr_key} ({best_wr_delta:+.1f}pp)")

        print()


if __name__ == "__main__":
    main()
