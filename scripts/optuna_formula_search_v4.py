"""Optuna V4 Formula Search -- Dual-Profile Alpha Mining.

Two independent search profiles:
  short: 15m base + 1h aux (high-frequency, short hold)
  long:  4h base + 1d aux  (low-frequency, long hold)

Key improvements over V3:
1. 15m/1h/4h/1d intervals with 3k-100k+ bars of data
2. 27 features (11 original + 16 new: microstructure, rolling stats, multi-TF)
3. 20 operators (12 original + 8 new time-series ops, GATE excluded)
4. Realistic fee/slippage model (taker fee + 1.5bps slippage per side)
5. Hold period + cooldown to prevent whipsaw
6. market_type filter (fix spot/futures mixing bug)
7. Multi-timeframe data merge (base + aux interval)
8. Turnover penalty + net return floor in fitness
9. Optuna MedianPruner for early stopping
10. Dual-profile: short (15m+1h) and long (4h+1d) run independently

Usage:
    python scripts/optuna_formula_search_v4.py --profile both
    python scripts/optuna_formula_search_v4.py --profile short --trials 300
    python scripts/optuna_formula_search_v4.py --profile long --trials 200
    python scripts/optuna_formula_search_v4.py --slippage-stress 2
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import optuna

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))

from selene_space_alpha.vocab import FormulaVocab
from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS
from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES_V4
from selene_space_alpha.vm import PandasStackVM
from selene_space_strategy.template import BacktestResult

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

INTERVAL_TO_ANNUAL_BARS: dict[str, int] = {
    "1m": 525600, "5m": 105120, "15m": 35040,
    "1h": 8760, "4h": 2190, "8h": 1095, "1d": 365, "1w": 52,
}

# Bars per day for turnover calculation
BARS_PER_DAY: dict[str, int] = {
    "1m": 1440, "5m": 288, "15m": 96,
    "1h": 24, "4h": 6, "8h": 3, "1d": 1,
}


# ---------------------------------------------------------------------------
# Trading Costs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class TradingCosts:
    maker_fee: float = 0.0002       # 0.02% Binance Futures maker
    taker_fee: float = 0.0005       # 0.05% Binance Futures taker
    slippage_bps: float = 1.5       # 1.5 bps = 0.015% per side
    assume_taker: bool = True       # Conservative: assume taker fills


# ---------------------------------------------------------------------------
# Data Loading (V4: with market_type filter + multi-TF merge)
# ---------------------------------------------------------------------------

def load_events_v4(
    db_path: str | Path,
    symbol: str,
    exchange: str,
    interval: str,
    market_type: str = "futures",
) -> tuple[list, int]:
    """Load kline events with market_type filter."""
    from selene_space_types.market import KlineEvent

    conn = sqlite3.connect(str(db_path))
    df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? AND market_type=? "
        "ORDER BY timestamp ASC",
        conn, params=(symbol, exchange, interval, market_type),
    )
    conn.close()
    if df.empty:
        return [], 0

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    events = [
        KlineEvent(
            exchange=exchange, symbol=symbol, interval=interval,
            timestamp=int(r["timestamp"]), open=float(r["open"]),
            high=float(r["high"]), low=float(r["low"]),
            close=float(r["close"]), volume=float(r["volume"]),
            closed=True,
        )
        for _, r in df.iterrows()
    ]
    return events, len(df)


def load_multi_tf_df(
    db_path: str | Path,
    symbol: str,
    exchange: str,
    base_interval: str = "5m",
    aux_interval: str = "15m",
    market_type: str = "futures",
) -> tuple[pd.DataFrame, int]:
    """Load base interval OHLCV and merge auxiliary timeframe data.

    Returns a DataFrame with base OHLCV columns plus TF15_ prefixed
    columns from the auxiliary timeframe, forward-filled to avoid lookahead.
    """
    conn = sqlite3.connect(str(db_path))

    # Load base interval
    base_df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? AND market_type=? "
        "ORDER BY timestamp ASC",
        conn, params=(symbol, exchange, base_interval, market_type),
    )
    if base_df.empty:
        conn.close()
        return pd.DataFrame(), 0

    # Load auxiliary interval
    aux_df = pd.read_sql_query(
        "SELECT timestamp, open, high, low, close, volume FROM klines "
        "WHERE symbol=? AND exchange=? AND interval=? AND market_type=? "
        "ORDER BY timestamp ASC",
        conn, params=(symbol, exchange, aux_interval, market_type),
    )
    conn.close()

    for col in ["open", "high", "low", "close", "volume"]:
        base_df[col] = pd.to_numeric(base_df[col], errors="coerce")
        if not aux_df.empty:
            aux_df[col] = pd.to_numeric(aux_df[col], errors="coerce")

    if aux_df.empty:
        logger.warning("No aux interval data for %s %s", symbol, aux_interval)
        return base_df, len(base_df)

    # Forward-fill merge: align aux data to base timestamps
    aux_df = aux_df.set_index("timestamp")
    base_ts = base_df["timestamp"]

    # For each base timestamp, find the latest aux bar that closed before it
    prefix = aux_interval.replace("m", "M") if "m" in aux_interval else aux_interval
    prefix = f"TF{prefix}_"

    # Reindex aux to base timestamps with forward fill (no lookahead)
    aux_aligned = aux_df.reindex(base_ts, method="ffill")
    aux_aligned.columns = [f"{prefix}{c}" for c in aux_aligned.columns]
    aux_aligned = aux_aligned.reset_index(drop=True)

    merged = pd.concat([base_df, aux_aligned], axis=1)
    return merged, len(merged)


def events_to_df(events: list) -> pd.DataFrame:
    """Convert KlineEvent list to DataFrame."""
    rows = [{
        "timestamp": e.timestamp, "open": e.open, "high": e.high,
        "low": e.low, "close": e.close, "volume": e.volume,
    } for e in events if e.closed]
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Feature Pre-computation
# ---------------------------------------------------------------------------

def precompute_features(
    df: pd.DataFrame,
    vocab: FormulaVocab,
    vm: PandasStackVM,
) -> dict[int, pd.Series]:
    """Pre-compute all features once for a DataFrame."""
    features: dict[int, pd.Series] = {}
    for idx, (feat_name, fn) in enumerate(vm._factor_fns):
        if fn is not None:
            try:
                series = fn(df).fillna(0.0)
                series = series.replace([float("inf")], 1e6).replace(
                    [float("-inf")], -1e6
                ).clip(-1e6, 1e6)
                features[idx] = series
            except Exception:
                features[idx] = pd.Series(0.0, index=df.index)
        else:
            col_name = feat_name.lower()
            if col_name in df.columns:
                features[idx] = df[col_name].fillna(0.0)
            else:
                features[idx] = pd.Series(0.0, index=df.index)
    return features


# ---------------------------------------------------------------------------
# Walk-Forward Windows
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WalkForwardWindow:
    train_df: pd.DataFrame
    test_df: pd.DataFrame
    train_features: dict[int, pd.Series]
    test_features: dict[int, pd.Series]
    window_idx: int


def create_walk_forward_windows(
    df: pd.DataFrame,
    features: dict[int, pd.Series],
    n_windows: int = 4,
    train_ratio: float = 0.50,
    gap_ratio: float = 0.05,
    test_ratio: float = 0.20,
) -> list[WalkForwardWindow]:
    """Create expanding-window walk-forward splits."""
    n = len(df)
    if n < 100:
        return [WalkForwardWindow(df, df, features, features, 0)]

    total_window = train_ratio + gap_ratio + test_ratio
    step = (1.0 - total_window) / max(n_windows - 1, 1) if n_windows > 1 else 0.0

    windows: list[WalkForwardWindow] = []
    for i in range(n_windows):
        offset = step * i
        train_end = int((offset + train_ratio) * n)
        gap_end = int((offset + train_ratio + gap_ratio) * n)
        test_end = int((offset + train_ratio + gap_ratio + test_ratio) * n)

        train_df = df.iloc[:train_end].reset_index(drop=True)
        test_df = df.iloc[gap_end:test_end].reset_index(drop=True)

        # Slice features to match
        train_feat = {k: v.iloc[:train_end].reset_index(drop=True) for k, v in features.items()}
        test_feat = {k: v.iloc[gap_end:test_end].reset_index(drop=True) for k, v in features.items()}

        windows.append(WalkForwardWindow(
            train_df=train_df,
            test_df=test_df,
            train_features=train_feat,
            test_features=test_feat,
            window_idx=i,
        ))
    return windows


# ---------------------------------------------------------------------------
# Stack-Aware Tokenizer (from V3, adapted for V4 vocab)
# ---------------------------------------------------------------------------

class V4Tokenizer:
    """Generates valid RPN formula token sequences for Optuna."""

    def __init__(self, vocab: FormulaVocab):
        self.vocab = vocab
        self.delta_map: dict[int, int] = {}
        self.arity_map: dict[int, int] = {}

        for i in range(vocab.feature_count):
            self.delta_map[i] = 1
            self.arity_map[i] = 0

        ops_offset = vocab.operator_offset
        ops_arity: dict[str, int] = {cfg[0]: cfg[2] for cfg in OPS_CONFIG_PANDAS}
        for i, op_name in enumerate(vocab.operator_names):
            token_id = ops_offset + i
            arity = ops_arity.get(op_name, 1)
            self.delta_map[token_id] = 1 - arity
            self.arity_map[token_id] = arity

        self._by_delta: dict[int, list[int]] = {}
        for tid, delta in self.delta_map.items():
            self._by_delta.setdefault(delta, []).append(tid)

    def get_allowed_tokens(self, stack_depth: int, remaining: int) -> list[int]:
        allowed: list[int] = []
        r_after = remaining - 1
        for token_id, delta in self.delta_map.items():
            d_new = stack_depth + delta
            if d_new < 0:
                continue
            if r_after == 0:
                if d_new == 1:
                    allowed.append(token_id)
                continue
            if d_new > r_after + 1:
                continue
            target = 1 - d_new
            if target < -2 * r_after or target > 1 * r_after:
                continue
            allowed.append(token_id)
        return allowed

    def _map_to_valid(self, raw_token: int, allowed: list[int]) -> int:
        if not allowed:
            raise ValueError("No allowed tokens")
        if raw_token in allowed:
            return raw_token
        raw_delta = self.delta_map.get(raw_token, 1)
        best = allowed[0]
        best_dist = abs(self.delta_map.get(best, 0) - raw_delta) * 100 + abs(best - raw_token)
        for t in allowed[1:]:
            dist = abs(self.delta_map.get(t, 0) - raw_delta) * 100 + abs(t - raw_token)
            if dist < best_dist:
                best = t
                best_dist = dist
        return best

    def suggest_formula(
        self, trial: optuna.Trial, min_len: int = 4, max_len: int = 8,
    ) -> list[int]:
        formula_len = trial.suggest_int("formula_len", min_len, max_len)
        all_tokens = list(range(self.vocab.size))
        tokens: list[int] = []
        stack_depth = 0

        for i in range(formula_len):
            remaining = formula_len - i
            allowed = self.get_allowed_tokens(stack_depth, remaining)
            if not allowed:
                raise ValueError(f"No valid tokens at pos {i}, depth={stack_depth}")
            raw = trial.suggest_categorical(f"t{i}", all_tokens)
            mapped = self._map_to_valid(raw, allowed)
            stack_depth += self.delta_map[mapped]
            tokens.append(mapped)
        return tokens

    def reconstruct_tokens(self, params: dict, formula_len: int) -> list[int]:
        tokens: list[int] = []
        stack_depth = 0
        for i in range(formula_len):
            remaining = formula_len - i
            allowed = self.get_allowed_tokens(stack_depth, remaining)
            raw = params[f"t{i}"]
            mapped = self._map_to_valid(raw, allowed)
            stack_depth += self.delta_map[mapped]
            tokens.append(mapped)
        return tokens


# ---------------------------------------------------------------------------
# V4 Backtest Engine (slippage + hold + cooldown)
# ---------------------------------------------------------------------------

def backtest_formula_v4(
    tokens: list[int],
    vocab: FormulaVocab,
    df: pd.DataFrame,
    precomputed_features: dict[int, pd.Series],
    buy_th: float = 0.65,
    sell_th: float = 0.35,
    initial_capital: float = 10000.0,
    costs: TradingCosts = TradingCosts(),
    stop_loss: float = 0.05,
    position_size: float = 0.70,
    hold_bars: int = 4,
    cooldown_bars: int = 2,
) -> BacktestResult:
    """Vectorized backtest with slippage, hold period, and cooldown."""
    if len(df) < 10:
        return BacktestResult()

    vm = PandasStackVM(vocab)
    score_series = vm.execute(tokens, df, precomputed_features=precomputed_features)
    if score_series is None:
        return BacktestResult()

    # Rolling z-score normalize (60-bar window ≈ 5hr on 5m) to avoid lookahead bias
    score_vals = score_series.values.astype(float)
    score_pd = pd.Series(score_vals)
    rolling_mean = score_pd.rolling(60, min_periods=10).mean()
    rolling_std = score_pd.rolling(60, min_periods=10).std() + 1e-8
    score_norm = ((score_pd - rolling_mean) / rolling_std).fillna(0.0).values
    score_norm = score_norm.clip(-10, 10)

    sigmoid = 1.0 / (1.0 + np.exp(-score_norm))
    signals = np.where(sigmoid > buy_th, 1, np.where(sigmoid < sell_th, -1, 0))

    closes = df["close"].values.astype(float)
    lows = df["low"].values.astype(float)
    highs = df["high"].values.astype(float)

    fee_rate = costs.taker_fee if costs.assume_taker else costs.maker_fee
    slippage_frac = costs.slippage_bps / 10_000.0

    # Fixed notional per trade (no compounding) to prevent equity explosion
    trade_notional = initial_capital * position_size

    position = 0  # 0=flat, 1=long, -1=short
    entry_price = 0.0
    position_notional = 0.0
    capital = initial_capital
    equity_curve: list[float] = [initial_capital]
    trades: list[dict] = []
    bars_in_position = 0
    bars_since_exit = cooldown_bars + 1  # Start able to trade

    for i in range(1, len(closes)):
        sig = signals[i - 1]
        price = closes[i]
        low = lows[i]
        high = highs[i]

        # Bail out on NaN/inf capital
        if not np.isfinite(capital) or capital <= 0:
            equity_curve.append(equity_curve[-1] if equity_curve else 0.0)
            continue

        if position == 0:
            bars_since_exit += 1
            # Long entry
            if sig == 1 and bars_since_exit >= cooldown_bars:
                position = 1
                entry_price = price * (1.0 + slippage_frac)
                position_notional = trade_notional
                entry_fee = position_notional * fee_rate
                capital -= entry_fee
                bars_in_position = 0
                bars_since_exit = 0
            # Short entry
            elif sig == -1 and bars_since_exit >= cooldown_bars:
                position = -1
                entry_price = price * (1.0 - slippage_frac)
                position_notional = trade_notional
                entry_fee = position_notional * fee_rate
                capital -= entry_fee
                bars_in_position = 0
                bars_since_exit = 0

        elif position == 1:
            bars_in_position += 1

            # Long stop-loss: price drops below entry
            if stop_loss > 0:
                stop_price = entry_price * (1.0 - stop_loss)
                if low <= stop_price:
                    exit_price = stop_price * (1.0 - slippage_frac)
                    pnl_pct = (exit_price - entry_price) / entry_price
                    net_pnl = position_notional * pnl_pct
                    exit_fee = position_notional * abs(1.0 + pnl_pct) * fee_rate
                    net_pnl -= exit_fee
                    capital += net_pnl
                    trades.append({"pnl": net_pnl, "stop": True})
                    position = 0
                    entry_price = 0.0
                    position_notional = 0.0
                    bars_in_position = 0
                    bars_since_exit = 0
                    equity_curve.append(max(capital, 0.0))
                    continue

            # Long exit on sell signal (after hold period)
            if bars_in_position >= hold_bars and sig == -1:
                exit_price = price * (1.0 - slippage_frac)
                pnl_pct = (exit_price - entry_price) / entry_price
                net_pnl = position_notional * pnl_pct
                exit_fee = position_notional * abs(1.0 + pnl_pct) * fee_rate
                net_pnl -= exit_fee
                capital += net_pnl
                trades.append({"pnl": net_pnl, "stop": False})
                position = 0
                entry_price = 0.0
                position_notional = 0.0
                bars_in_position = 0
                bars_since_exit = 0

        elif position == -1:
            bars_in_position += 1

            # Short stop-loss: price rises above entry
            if stop_loss > 0:
                stop_price = entry_price * (1.0 + stop_loss)
                if high >= stop_price:
                    exit_price = stop_price * (1.0 + slippage_frac)
                    pnl_pct = (entry_price - exit_price) / entry_price
                    net_pnl = position_notional * pnl_pct
                    exit_fee = position_notional * abs(1.0 + pnl_pct) * fee_rate
                    net_pnl -= exit_fee
                    capital += net_pnl
                    trades.append({"pnl": net_pnl, "stop": True})
                    position = 0
                    entry_price = 0.0
                    position_notional = 0.0
                    bars_in_position = 0
                    bars_since_exit = 0
                    equity_curve.append(max(capital, 0.0))
                    continue

            # Short exit on buy signal (after hold period)
            if bars_in_position >= hold_bars and sig == 1:
                exit_price = price * (1.0 + slippage_frac)
                pnl_pct = (entry_price - exit_price) / entry_price
                net_pnl = position_notional * pnl_pct
                exit_fee = position_notional * abs(1.0 + pnl_pct) * fee_rate
                net_pnl -= exit_fee
                capital += net_pnl
                trades.append({"pnl": net_pnl, "stop": False})
                position = 0
                entry_price = 0.0
                position_notional = 0.0
                bars_in_position = 0
                bars_since_exit = 0

        # Equity tracking
        if position == 1:
            unrealized_pnl = position_notional * (price - entry_price) / entry_price
            equity = capital + unrealized_pnl
        elif position == -1:
            unrealized_pnl = position_notional * (entry_price - price) / entry_price
            equity = capital + unrealized_pnl
        else:
            equity = capital
        equity = max(equity, 0.0) if np.isfinite(equity) else 0.0
        equity_curve.append(equity)

    result = BacktestResult(equity_curve=equity_curve, total_trades=len(trades))
    if len(equity_curve) < 2:
        return result

    result.total_return = (equity_curve[-1] - initial_capital) / initial_capital

    peak = initial_capital
    max_dd = 0.0
    for eq in equity_curve:
        if eq > peak:
            peak = eq
        dd = (peak - eq) / peak
        if dd > max_dd:
            max_dd = dd
    result.max_drawdown = -max_dd

    if trades:
        wins = [t for t in trades if t["pnl"] > 0]
        losses = [t for t in trades if t["pnl"] <= 0]
        result.win_rate = len(wins) / len(trades)
        gross_profit = sum(t["pnl"] for t in wins)
        gross_loss = abs(sum(t["pnl"] for t in losses))
        if gross_loss > 0:
            result.profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            result.profit_factor = float("inf")

    return result


def compute_sharpe_from_equity(
    equity_curve: list[float], annual_bars: int,
) -> float:
    if len(equity_curve) < 2:
        return 0.0
    returns = pd.Series(equity_curve).pct_change().dropna()
    if returns.std() < 1e-10:
        return 0.0
    return float(returns.mean() / returns.std() * (annual_bars ** 0.5))


# ---------------------------------------------------------------------------
# V4 Fitness
# ---------------------------------------------------------------------------

@dataclass
class WindowResult:
    key: str
    window_idx: int
    train_sharpe: float
    test_sharpe: float
    test_equity_curve: list[float]
    test_trades: int
    test_max_dd: float
    test_n_bars: int
    test_return: float = 0.0
    interval: str = "5m"


def compute_v4_fitness(
    window_results: list[WindowResult],
    formula_len: int,
    min_formula_len: int = 4,
    complexity_weight: float = 0.05,
    degradation_weight: float = 0.3,
    turnover_weight: float = 0.5,
    min_trades_per_day: float = 0.5,
) -> float:
    """OOS-first fitness with turnover control and cost realism."""
    if not window_results:
        return -10.0

    valid = [wr for wr in window_results if wr.test_trades >= 3]
    if not valid:
        return -10.0

    oos_sharpes = [wr.test_sharpe for wr in valid]
    train_sharpes = [wr.train_sharpe for wr in valid]
    oos_returns = [wr.test_return for wr in valid]

    oos_sharpe = float(np.nanmean(oos_sharpes))
    train_sharpe = float(np.nanmean(train_sharpes)) if train_sharpes else 0.0
    avg_return = float(np.nanmean(oos_returns))

    # Hard floor: negative OOS return after costs
    if avg_return < 0:
        return -10.0

    # Degradation penalty
    degradation = max(0.0, train_sharpe - oos_sharpe)
    degradation_penalty = degradation * degradation_weight

    # Complexity penalty
    complexity_penalty = (formula_len - min_formula_len) * complexity_weight

    # Turnover penalty
    turnover_penalties = []
    for wr in valid:
        bpd = BARS_PER_DAY.get(wr.interval, 288)
        test_days = wr.test_n_bars / bpd if bpd > 0 else 1
        tpd = wr.test_trades / max(test_days, 0.1)
        if tpd > 15:
            turnover_penalties.append((tpd - 15) * turnover_weight)
        elif tpd < min_trades_per_day:
            turnover_penalties.append((min_trades_per_day - tpd) * 10)
    turnover_penalty = float(np.mean(turnover_penalties)) if turnover_penalties else 0.0

    # Drawdown disqualification
    for wr in valid:
        if wr.test_max_dd < -0.50:
            return -10.0

    dd_penalty = 0.0
    avg_dd = float(np.nanmean([wr.test_max_dd for wr in valid]))
    if avg_dd < -0.40:
        dd_penalty = abs(avg_dd + 0.40) * 10

    fitness = (
        oos_sharpe
        - degradation_penalty
        - complexity_penalty
        - turnover_penalty
        - dd_penalty
    )
    return fitness


# ---------------------------------------------------------------------------
# Ensemble Selection
# ---------------------------------------------------------------------------

@dataclass
class ScoredFormula:
    tokens: list[int]
    readable: str
    buy_th: float
    sell_th: float
    stop_loss: float
    position_size: float
    hold_bars: int
    cooldown_bars: int
    oos_sharpe: float
    train_sharpe: float
    oos_equity_curves: dict[str, list[float]] = field(default_factory=dict)
    formula_len: int = 0


def compute_equity_correlation(
    curves_a: dict[str, list[float]], curves_b: dict[str, list[float]],
) -> float:
    correlations: list[float] = []
    for key in curves_a:
        if key not in curves_b:
            continue
        eq_a = pd.Series(curves_a[key]).pct_change().dropna()
        eq_b = pd.Series(curves_b[key]).pct_change().dropna()
        min_len = min(len(eq_a), len(eq_b))
        if min_len < 10:
            continue
        corr = eq_a.iloc[:min_len].corr(eq_b.iloc[:min_len])
        if not np.isnan(corr):
            correlations.append(corr)
    return float(np.mean(correlations)) if correlations else 0.0


def select_ensemble(
    candidates: list[ScoredFormula],
    max_formulas: int = 5,
    corr_threshold: float = 0.7,
) -> list[ScoredFormula]:
    if not candidates:
        return []

    seen_formulas: set[str] = set()
    unique_candidates: list[ScoredFormula] = []
    for c in sorted(candidates, key=lambda f: f.oos_sharpe, reverse=True):
        if c.readable not in seen_formulas:
            seen_formulas.add(c.readable)
            unique_candidates.append(c)

    selected: list[ScoredFormula] = [unique_candidates[0]]

    for candidate in unique_candidates[1:]:
        if len(selected) >= max_formulas:
            break
        too_correlated = False
        for existing in selected:
            corr = compute_equity_correlation(
                candidate.oos_equity_curves, existing.oos_equity_curves,
            )
            if corr > corr_threshold:
                too_correlated = True
                break
        if not too_correlated:
            selected.append(candidate)

    return selected


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Optuna Formula Search V4 (Short-Interval Alpha Mining)"
    )
    # Data
    parser.add_argument("--db", default="services/local-api/data/klines.db")
    parser.add_argument(
        "--symbols", nargs="+",
        default=["BTC/USDT", "ETH/USDT", "SOL/USDT"],
    )
    parser.add_argument("--interval", default="5m")
    parser.add_argument("--aux-interval", default="15m",
                        help="Auxiliary timeframe for multi-TF features")
    parser.add_argument("--market-type", default="futures",
                        help="Market type filter (fixes spot/futures mixing)")
    parser.add_argument("--exchange", default="binance")
    # Search
    parser.add_argument("--trials", type=int, default=300)
    parser.add_argument("--formula-len-min", type=int, default=4)
    parser.add_argument("--formula-len-max", type=int, default=8)
    parser.add_argument("--seed", type=int, default=42)
    # Walk-forward
    parser.add_argument("--n-windows", type=int, default=4)
    parser.add_argument("--train-ratio", type=float, default=0.50)
    parser.add_argument("--gap-ratio", type=float, default=0.05)
    parser.add_argument("--test-ratio", type=float, default=0.20)
    # Trading costs
    parser.add_argument("--fee-rate", type=float, default=0.0005,
                        help="Taker fee rate (default 0.05%%)")
    parser.add_argument("--slippage-bps", type=float, default=1.5,
                        help="Per-side slippage in basis points")
    parser.add_argument("--slippage-stress", type=float, default=1.0,
                        help="Multiplier for slippage stress test (1x, 2x, 3x)")
    # Fitness
    parser.add_argument("--complexity-weight", type=float, default=0.05)
    parser.add_argument("--degradation-weight", type=float, default=0.3)
    parser.add_argument("--turnover-weight", type=float, default=0.5)
    # Strategy parameter ranges
    parser.add_argument("--hold-min", type=int, default=2,
                        help="Min hold bars (default 2)")
    parser.add_argument("--hold-max", type=int, default=12,
                        help="Max hold bars (default 12)")
    parser.add_argument("--sl-min", type=float, default=0.03,
                        help="Min stop loss (default 0.03)")
    parser.add_argument("--sl-max", type=float, default=0.10,
                        help="Max stop loss (default 0.10)")
    parser.add_argument("--pos-min", type=float, default=0.30,
                        help="Min position size (default 0.30)")
    parser.add_argument("--pos-max", type=float, default=0.80,
                        help="Max position size (default 0.80)")
    # Ensemble
    parser.add_argument("--ensemble-size", type=int, default=5)
    parser.add_argument("--corr-threshold", type=float, default=0.7)
    # Output
    parser.add_argument("--output-dir", default="data/optuna_formula_v4")
    args = parser.parse_args()

    db_path = PROJECT_ROOT / args.db
    output_dir = PROJECT_ROOT / args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Effective slippage with stress multiplier
    effective_slippage = args.slippage_bps * args.slippage_stress
    costs = TradingCosts(
        taker_fee=args.fee_rate,
        slippage_bps=effective_slippage,
    )

    # Build V4 vocab (GATE excluded by default)
    v4_op_names = tuple(
        cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE"
    )
    vocab = FormulaVocab(
        feature_names=CEX_FEATURE_NAMES_V4,
        operator_names=v4_op_names,
    )
    tokenizer = V4Tokenizer(vocab)
    vm = PandasStackVM(vocab)

    print("=" * 65)
    print("Optuna Formula Search V4 (Short-Interval Alpha Mining)")
    print("=" * 65)
    print(f"  Interval: {args.interval} (aux: {args.aux_interval})")
    print(f"  Market type: {args.market_type}")
    print(f"  Vocab: {vocab.size} tokens "
          f"({vocab.feature_count} features + {len(vocab.operator_names)} ops)")
    print(f"  Formula length: {args.formula_len_min}-{args.formula_len_max}")
    print(f"  Trials: {args.trials}")
    print(f"  Symbols: {args.symbols}")
    print(f"  Fee: {costs.taker_fee:.3%} taker, Slippage: {effective_slippage:.1f} bps"
          f" (stress {args.slippage_stress:.1f}x)")
    print(f"  Walk-forward: {args.n_windows} windows "
          f"({args.train_ratio:.0%}/{args.gap_ratio:.0%}/{args.test_ratio:.0%})")
    print()

    # Load data and pre-compute features
    print("[1/4] Loading data and pre-computing features...")
    all_windows: dict[str, list[WalkForwardWindow]] = {}
    annual_bars_map: dict[str, int] = {}

    for sym in args.symbols:
        key = f"{sym}:{args.interval}"

        # Try multi-TF merge first
        df, n = load_multi_tf_df(
            db_path, sym, args.exchange,
            args.interval, args.aux_interval, args.market_type,
        )

        if n == 0:
            # Fallback: load without multi-TF
            events, n = load_events_v4(
                db_path, sym, args.exchange, args.interval, args.market_type,
            )
            if n == 0:
                print(f"  {key}: NO DATA, skipping")
                continue
            df = events_to_df(events)

        print(f"  {key}: {n} bars loaded")
        features = precompute_features(df, vocab, vm)
        windows = create_walk_forward_windows(
            df, features, args.n_windows,
            args.train_ratio, args.gap_ratio, args.test_ratio,
        )
        all_windows[key] = windows
        annual_bars_map[key] = INTERVAL_TO_ANNUAL_BARS.get(args.interval, 252)

        for w in windows:
            print(f"    W{w.window_idx}: {len(w.train_df)} train / {len(w.test_df)} test")

    if not all_windows:
        print("No data loaded. Check --db, --market-type, --symbols.")
        return
    print()

    # Cache for top-trial equity curves
    trial_cache: dict[int, tuple[list[int], float, float, dict[str, list[float]]]] = {}

    # Optuna objective
    def objective(trial: optuna.Trial) -> float:
        tokens = tokenizer.suggest_formula(
            trial, args.formula_len_min, args.formula_len_max,
        )

        buy_th = trial.suggest_float("buy_th", 0.50, 0.80, step=0.02)
        sell_th = trial.suggest_float("sell_th", 0.20, 0.50, step=0.02)
        stop_loss = trial.suggest_float("stop_loss", args.sl_min, args.sl_max, step=0.01)
        position_size = trial.suggest_float("position_size", args.pos_min, args.pos_max, step=0.10)
        hold_bars = trial.suggest_int("hold_bars", args.hold_min, args.hold_max)
        cooldown_bars = trial.suggest_int("cooldown_bars", 1, 6)

        if buy_th <= sell_th + 0.05:
            return -10.0

        window_results: list[WindowResult] = []
        for key, windows in all_windows.items():
            annual_bars = annual_bars_map.get(key, 252)
            interval = key.split(":")[-1] if ":" in key else args.interval
            for window in windows:
                train_r = backtest_formula_v4(
                    tokens, vocab, window.train_df, window.train_features,
                    buy_th, sell_th, costs=costs,
                    stop_loss=stop_loss, position_size=position_size,
                    hold_bars=hold_bars, cooldown_bars=cooldown_bars,
                )
                test_r = backtest_formula_v4(
                    tokens, vocab, window.test_df, window.test_features,
                    buy_th, sell_th, costs=costs,
                    stop_loss=stop_loss, position_size=position_size,
                    hold_bars=hold_bars, cooldown_bars=cooldown_bars,
                )
                train_sharpe = compute_sharpe_from_equity(
                    train_r.equity_curve, annual_bars,
                )
                test_sharpe = compute_sharpe_from_equity(
                    test_r.equity_curve, annual_bars,
                )
                window_results.append(WindowResult(
                    key=key, window_idx=window.window_idx,
                    train_sharpe=train_sharpe, test_sharpe=test_sharpe,
                    test_equity_curve=test_r.equity_curve,
                    test_trades=test_r.total_trades,
                    test_max_dd=test_r.max_drawdown,
                    test_n_bars=len(window.test_df),
                    test_return=test_r.total_return,
                    interval=interval,
                ))

        fitness = compute_v4_fitness(
            window_results, len(tokens),
            args.formula_len_min, args.complexity_weight,
            args.degradation_weight, args.turnover_weight,
        )

        # Drawdown bonus
        oos_dds = [wr.test_max_dd for wr in window_results if wr.test_trades >= 3]
        if oos_dds:
            avg_dd = float(np.nanmean(oos_dds))
            if avg_dd > -0.10:
                fitness += 0.3
            elif avg_dd > -0.15:
                fitness += 0.1

        # Cache for ensemble
        if fitness > -5.0:
            oos_curves: dict[str, list[float]] = {}
            for key, windows in all_windows.items():
                if windows:
                    last_w = windows[-1]
                    last_r = backtest_formula_v4(
                        tokens, vocab, last_w.test_df, last_w.test_features,
                        buy_th, sell_th, costs=costs,
                        stop_loss=stop_loss, position_size=position_size,
                        hold_bars=hold_bars, cooldown_bars=cooldown_bars,
                    )
                    oos_curves[key] = last_r.equity_curve
            valid_oos = [wr.test_sharpe for wr in window_results if wr.test_trades >= 3]
            valid_train = [wr.train_sharpe for wr in window_results if wr.test_trades >= 3]
            trial_cache[trial.number] = (
                tokens,
                float(np.nanmean(valid_oos)) if valid_oos else -10.0,
                float(np.nanmean(valid_train)) if valid_train else 0.0,
                oos_curves,
            )

        return fitness

    # Run Optuna with pruning
    print("[2/4] Searching formulas (TPE + MedianPruner, OOS-first)...")
    sampler = optuna.samplers.TPESampler(seed=args.seed)
    pruner = optuna.pruners.MedianPruner(
        n_startup_trials=10,
        n_warmup_steps=2,
        interval_steps=1,
    )
    study = optuna.create_study(direction="maximize", sampler=sampler, pruner=pruner)
    study.optimize(objective, n_trials=args.trials, show_progress_bar=True)

    print()
    print(f"Best trial: #{study.best_trial.number}")
    print(f"Best fitness (OOS-based): {study.best_value:.4f}")
    print()

    # Reconstruct best formula
    best_params = study.best_params
    best_len = best_params["formula_len"]
    best_tokens = tokenizer.reconstruct_tokens(best_params, best_len)
    best_buy = best_params["buy_th"]
    best_sell = best_params["sell_th"]
    best_sl = best_params["stop_loss"]
    best_ps = best_params["position_size"]
    best_hold = best_params["hold_bars"]
    best_cooldown = best_params["cooldown_bars"]
    best_readable = vocab.decode(best_tokens)

    print(f"Best formula ({best_len} tokens): {best_readable}")
    print(f"Buy={best_buy:.2f} Sell={best_sell:.2f} SL={best_sl:.0%} "
          f"Pos={best_ps:.0%} Hold={best_hold} Cooldown={best_cooldown}")
    print()

    # Full evaluation
    print("[3/4] Full evaluation (Walk-Forward)...")
    print()
    header = (
        f"{'Key':<18} {'Win':<4} {'Set':<6} {'Return':>8} {'Sharpe':>7} "
        f"{'MaxDD':>8} {'Trades':>7} {'WinRate':>7} {'PF':>6}"
    )
    print(header)
    print("-" * len(header))

    all_results: dict[str, dict] = {}

    for key, windows in all_windows.items():
        annual_bars = annual_bars_map.get(key, 252)
        sym_results: dict = {"windows": {}}

        for window in windows:
            train_r = backtest_formula_v4(
                best_tokens, vocab, window.train_df, window.train_features,
                best_buy, best_sell, costs=costs,
                stop_loss=best_sl, position_size=best_ps,
                hold_bars=best_hold, cooldown_bars=best_cooldown,
            )
            test_r = backtest_formula_v4(
                best_tokens, vocab, window.test_df, window.test_features,
                best_buy, best_sell, costs=costs,
                stop_loss=best_sl, position_size=best_ps,
                hold_bars=best_hold, cooldown_bars=best_cooldown,
            )
            train_sharpe = compute_sharpe_from_equity(train_r.equity_curve, annual_bars)
            test_sharpe = compute_sharpe_from_equity(test_r.equity_curve, annual_bars)

            for label, r, sh in [("TRAIN", train_r, train_sharpe), ("TEST", test_r, test_sharpe)]:
                print(
                    f"{key:<18} W{window.window_idx:<3} {label:<6} "
                    f"{r.total_return:>7.2%} {sh:>6.3f} "
                    f"{r.max_drawdown:>7.2%} {r.total_trades:>7} "
                    f"{r.win_rate:>6.1%} {r.profit_factor:>5.2f}"
                )

            sym_results["windows"][f"window_{window.window_idx}"] = {
                "train_sharpe": train_sharpe,
                "test_sharpe": test_sharpe,
                "test_return": test_r.total_return,
                "test_trades": test_r.total_trades,
                "test_max_dd": test_r.max_drawdown,
            }
        all_results[key] = sym_results

    # Overfitting check
    print()
    oos_sharpes = []
    train_sharpes = []
    for key, windows in all_windows.items():
        annual_bars = annual_bars_map.get(key, 252)
        for window in windows:
            test_r = backtest_formula_v4(
                best_tokens, vocab, window.test_df, window.test_features,
                best_buy, best_sell, costs=costs,
                stop_loss=best_sl, position_size=best_ps,
                hold_bars=best_hold, cooldown_bars=best_cooldown,
            )
            train_r = backtest_formula_v4(
                best_tokens, vocab, window.train_df, window.train_features,
                best_buy, best_sell, costs=costs,
                stop_loss=best_sl, position_size=best_ps,
                hold_bars=best_hold, cooldown_bars=best_cooldown,
            )
            if test_r.total_trades >= 3:
                oos_sharpes.append(compute_sharpe_from_equity(test_r.equity_curve, annual_bars))
                train_sharpes.append(compute_sharpe_from_equity(train_r.equity_curve, annual_bars))

    avg_oos = float(np.nanmean(oos_sharpes)) if oos_sharpes else 0.0
    avg_train = float(np.nanmean(train_sharpes)) if train_sharpes else 0.0
    gap = avg_train - avg_oos

    print(f"Train Sharpe avg: {avg_train:.4f}")
    print(f"OOS Sharpe avg:   {avg_oos:.4f}")
    print(f"Overfitting gap:  {gap:.4f}")
    if gap > 1.0:
        print("WARNING: Significant overfitting!")
    elif gap > 0.5:
        print("CAUTION: Moderate overfitting.")
    elif avg_oos > 0:
        print("GOOD: OOS positive, low overfitting gap.")

    # Formula breakdown
    print()
    print("Formula breakdown:")
    for i, t in enumerate(best_tokens):
        kind = "FEATURE" if t < vocab.feature_count else "OPERATOR"
        name = vocab.token_names[t] if t < vocab.size else f"UNK({t})"
        print(f"  [{i}] {kind}: {name}")

    # Ensemble selection
    print()
    print("[4/4] Ensemble selection...")
    top_trials = sorted(
        study.trials, key=lambda t: t.value if t.value is not None else -999,
        reverse=True,
    )[:20]

    candidates: list[ScoredFormula] = []
    for trial in top_trials:
        if trial.number in trial_cache:
            tokens, oos_sh, train_sh, oos_curves = trial_cache[trial.number]
            candidates.append(ScoredFormula(
                tokens=tokens,
                readable=vocab.decode(tokens),
                buy_th=trial.params["buy_th"],
                sell_th=trial.params["sell_th"],
                stop_loss=trial.params["stop_loss"],
                position_size=trial.params["position_size"],
                hold_bars=trial.params["hold_bars"],
                cooldown_bars=trial.params["cooldown_bars"],
                oos_sharpe=oos_sh,
                train_sharpe=train_sh,
                oos_equity_curves=oos_curves,
                formula_len=len(tokens),
            ))

    ensemble = select_ensemble(candidates, args.ensemble_size, args.corr_threshold)

    print(f"  {len(candidates)} candidates, {len(ensemble)} selected")
    for i, f in enumerate(ensemble):
        print(f"  [{i}] {f.readable} "
              f"(OOS Sharpe: {f.oos_sharpe:.3f}, "
              f"buy={f.buy_th:.2f}, sell={f.sell_th:.2f}, "
              f"SL={f.stop_loss:.0%}, hold={f.hold_bars}, "
              f"cool={f.cooldown_bars})")

    # Save results
    result_data = {
        "version": "v4",
        "config": {
            "interval": args.interval,
            "aux_interval": args.aux_interval,
            "market_type": args.market_type,
            "fee_rate": costs.taker_fee,
            "slippage_bps": effective_slippage,
            "slippage_stress": args.slippage_stress,
            "n_windows": args.n_windows,
            "formula_len_range": [args.formula_len_min, args.formula_len_max],
            "complexity_weight": args.complexity_weight,
            "degradation_weight": args.degradation_weight,
            "turnover_weight": args.turnover_weight,
        },
        "best_formula": {
            "tokens": best_tokens,
            "readable": best_readable,
            "buy_th": best_buy,
            "sell_th": best_sell,
            "stop_loss": best_sl,
            "position_size": best_ps,
            "hold_bars": best_hold,
            "cooldown_bars": best_cooldown,
            "oos_sharpe": avg_oos,
            "train_sharpe": avg_train,
            "overfitting_gap": gap,
            "formula_len": best_len,
        },
        "ensemble": [
            {
                "tokens": f.tokens,
                "readable": f.readable,
                "buy_th": f.buy_th,
                "sell_th": f.sell_th,
                "stop_loss": f.stop_loss,
                "position_size": f.position_size,
                "hold_bars": f.hold_bars,
                "cooldown_bars": f.cooldown_bars,
                "oos_sharpe": f.oos_sharpe,
                "train_sharpe": f.train_sharpe,
                "formula_len": f.formula_len,
            }
            for f in ensemble
        ],
        "walk_forward_results": all_results,
        "vocab_features": list(vocab.feature_names),
        "vocab_operators": list(vocab.operator_names),
        "n_trials": len(study.trials),
    }
    result_path = output_dir / "result.json"
    with open(result_path, "w") as f:
        json.dump(result_data, f, indent=2, ensure_ascii=False)

    ensemble_data = {
        "formulas": [
            {
                "tokens": f.tokens,
                "readable": f.readable,
                "buy_th": f.buy_th,
                "sell_th": f.sell_th,
                "stop_loss": f.stop_loss,
                "position_size": f.position_size,
                "hold_bars": f.hold_bars,
                "cooldown_bars": f.cooldown_bars,
                "oos_sharpe": f.oos_sharpe,
            }
            for f in ensemble
        ],
        "fusion_method": "weighted_average",
        "vocab_features": list(vocab.feature_names),
        "vocab_operators": list(vocab.operator_names),
    }
    ensemble_path = output_dir / "ensemble.json"
    with open(ensemble_path, "w") as f:
        json.dump(ensemble_data, f, indent=2, ensure_ascii=False)

    print(f"\nResults saved to: {result_path}")
    print(f"Ensemble saved to: {ensemble_path}")


if __name__ == "__main__":
    main()
