#!/usr/bin/env python
"""Shared backtest utilities for SeleneQuant strategy experiments.

Provides:
  - load_4h_1d_deriv(): Unified data loading via derivatives_loader
  - backtest_signal_timeout(): Signal-timeout backtest (proven best from v12)
  - Alpha formula mining: parse_formula, precompute, compute_sigmoid_score,
    backtest_parametric, init_vm, print_summary (from research)
"""

from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd

# Mock torch for alpha-core imports (avoids hard torch dependency)
_torch_mock = types.ModuleType("torch")
for _attr in ("abs", "add", "clamp", "cos", "div", "exp", "log", "matmul",
              "max", "mean", "min", "mul", "neg", "pow", "reciprocal", "relu",
              "sigmoid", "sign", "sin", "sqrt", "stack", "sub", "sum", "tanh",
              "where", "zeros", "ones", "cat", "randn", "tensor", "no_grad",
              "float32", "float64", "int32", "int64"):
    setattr(_torch_mock, _attr, lambda *a, _a=_attr, **kw: None)
_torch_mock.jit = types.ModuleType("torch.jit")
_torch_mock.jit.script = lambda f: f
_torch_mock.nn = types.ModuleType("torch.nn")
_torch_mock.nn.Module = type("Module", (), {"__init__": lambda s, *a, **kw: None})
_torch_mock.Tensor = type("Tensor", (), {})
_torch_mock.no_grad = lambda: type("ctx", (), {"__enter__": lambda s: None, "__exit__": lambda s, *a: None})()
sys.modules.setdefault("torch", _torch_mock)
sys.modules.setdefault("torch.jit", _torch_mock.jit)
sys.modules.setdefault("torch.nn", _torch_mock.nn)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "alpha-core"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "market-data"))

logger = logging.getLogger(__name__)

ANNUAL_BARS_4H = 2190


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _symbol_for_db(symbol: str) -> str:
    """Convert 'BTC/USDT' to 'BTCUSDT' for Binance DB queries."""
    return symbol.replace("/", "")


def load_4h_1d_deriv(
    klines_db: str | Path = "services/local-api/data/klines.db",
    deriv_db: str | Path = "data/derivatives.db",
    orderflow_db: str | Path = "data/orderflow.db",
    symbol: str = "BTC/USDT",
    exchange: str = "binance",
    market_type: str = "futures",
) -> tuple[pd.DataFrame, int]:
    """Load 4h klines with 1d aux + derivatives + orderflow data.

    Delegates to the unified derivatives_loader in market-data package.
    Returns (merged_df, n_bars).
    """
    from selene_space_market.derivatives_loader import load_4h_with_derivatives

    df, _coverage = load_4h_with_derivatives(
        symbol,
        klines_db=klines_db,
        derivatives_db=deriv_db,
        orderflow_db=orderflow_db,
        base_interval="4h",
        aux_interval="1d",
        exchange=exchange,
        market_type=market_type,
    )
    return df, len(df)


# ---------------------------------------------------------------------------
# Signal-timeout backtest (proven best from v12 experiments)
# ---------------------------------------------------------------------------

def backtest_signal_timeout(
    signals: pd.Series,
    df: pd.DataFrame,
    buy_threshold: float = 0.65,
    sell_threshold: float = 0.35,
    fee_rate: float = 0.0005,
    stop_loss: float = 0.05,
    position_size: float = 0.5,
    timeout_bars: int = 25,
    min_hold: int = 3,
    cooldown_bars: int = 2,
) -> dict:
    """Backtest with signal_timeout hold mode.

    Position exits when: opposite signal, stop_loss hit, or timeout_bars elapsed.
    Minimum hold period enforced before any exit (except stop_loss).
    """
    n = len(df)
    equity = 1.0
    equity_curve = [equity]
    position = 0  # 0=flat, 1=long, -1=short
    entry_price = 0.0
    hold_bars = 0
    cooldown = 0
    total_trades = 0
    winning_trades = 0
    max_equity = equity
    max_drawdown = 0.0

    close = df["close"].values
    high = df["high"].values
    low = df["low"].values

    for i in range(1, n):
        pnl = 0.0

        if position != 0:
            hold_bars += 1
            if position == 1:
                pnl = position_size * (close[i] - close[i - 1]) / close[i - 1]
                if low[i] < entry_price * (1 - stop_loss):
                    stop_pnl = position_size * (entry_price * (1 - stop_loss) - close[i - 1]) / close[i - 1]
                    pnl = stop_pnl - fee_rate * position_size
                    position = 0
                    hold_bars = 0
                    cooldown = cooldown_bars
                    total_trades += 1
                    equity += pnl
                    if pnl > 0:
                        winning_trades += 1
                    equity_curve.append(equity)
                    max_equity = max(max_equity, equity)
                    max_drawdown = min(max_drawdown, (equity - max_equity) / max_equity)
                    continue
            else:
                pnl = position_size * (close[i - 1] - close[i]) / close[i - 1]
                if high[i] > entry_price * (1 + stop_loss):
                    stop_pnl = position_size * (close[i - 1] - entry_price * (1 + stop_loss)) / close[i - 1]
                    pnl = stop_pnl - fee_rate * position_size
                    position = 0
                    hold_bars = 0
                    cooldown = cooldown_bars
                    total_trades += 1
                    equity += pnl
                    if pnl > 0:
                        winning_trades += 1
                    equity_curve.append(equity)
                    max_equity = max(max_equity, equity)
                    max_drawdown = min(max_drawdown, (equity - max_equity) / max_equity)
                    continue

            # Timeout exit
            if hold_bars >= timeout_bars:
                pnl -= fee_rate * position_size
                position = 0
                hold_bars = 0
                cooldown = cooldown_bars
                total_trades += 1
                equity += pnl
                if pnl > 0:
                    winning_trades += 1
                equity_curve.append(equity)
                max_equity = max(max_equity, equity)
                max_drawdown = min(max_drawdown, (equity - max_equity) / max_equity)
                continue

            # Opposite signal exit (after min_hold)
            sig = signals.iloc[i] if i < len(signals) else 0.0
            if hold_bars >= min_hold:
                if position == 1 and sig < sell_threshold:
                    pnl -= fee_rate * position_size
                    position = 0
                    hold_bars = 0
                    cooldown = cooldown_bars
                    total_trades += 1
                    equity += pnl
                    if pnl > 0:
                        winning_trades += 1
                    equity_curve.append(equity)
                    max_equity = max(max_equity, equity)
                    max_drawdown = min(max_drawdown, (equity - max_equity) / max_equity)
                    continue
                elif position == -1 and sig > buy_threshold:
                    pnl -= fee_rate * position_size
                    position = 0
                    hold_bars = 0
                    cooldown = cooldown_bars
                    total_trades += 1
                    equity += pnl
                    if pnl > 0:
                        winning_trades += 1
                    equity_curve.append(equity)
                    max_equity = max(max_equity, equity)
                    max_drawdown = min(max_drawdown, (equity - max_equity) / max_equity)
                    continue

            equity += pnl
            equity_curve.append(equity)
            max_equity = max(max_equity, equity)
            max_drawdown = min(max_drawdown, (equity - max_equity) / max_equity)
        else:
            # No position — check for entry
            if cooldown > 0:
                cooldown -= 1
            else:
                sig = signals.iloc[i] if i < len(signals) else 0.0
                if sig > buy_threshold:
                    position = 1
                    entry_price = close[i]
                    hold_bars = 0
                    equity -= fee_rate * position_size
                elif sig < sell_threshold:
                    position = -1
                    entry_price = close[i]
                    hold_bars = 0
                    equity -= fee_rate * position_size
            equity_curve.append(equity)

    total_return = equity_curve[-1] - 1.0 if equity_curve else 0.0
    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0

    # Sharpe from equity curve
    eq_arr = np.array(equity_curve)
    returns = np.diff(eq_arr) / eq_arr[:-1] if len(eq_arr) > 1 else np.array([0.0])
    sharpe = float(np.mean(returns) / (np.std(returns) + 1e-9) * np.sqrt(2190)) if len(returns) > 1 else 0.0

    return {
        "sharpe": sharpe,
        "total_return": total_return,
        "max_drawdown": max_drawdown,
        "total_trades": total_trades,
        "win_rate": win_rate,
        "equity_curve": equity_curve,
    }


# ---------------------------------------------------------------------------
# Alpha formula mining (from research)
# ---------------------------------------------------------------------------

def parse_formula(s: str, vocab) -> list[int]:
    """Parse a formula string into token indices using a FormulaVocab."""
    tokens: list[int] = []
    for p in s.split():
        for i, n in enumerate(vocab.feature_names):
            if n == p:
                tokens.append(i)
                break
        else:
            for i, n in enumerate(vocab.operator_names):
                if n == p:
                    tokens.append(vocab.operator_offset + i)
                    break
    return tokens


def precompute(df: pd.DataFrame, vocab, vm) -> dict[int, pd.Series]:
    """Precompute all features for a DataFrame using the PandasStackVM."""
    features: dict[int, pd.Series] = {}
    for idx, (feat_name, fn) in enumerate(vm._factor_fns):
        if fn is not None:
            try:
                series = fn(df).fillna(0.0).replace([float("inf")], 1e6).replace(
                    [float("-inf")], -1e6).clip(-1e6, 1e6)
                features[idx] = series
            except Exception:
                features[idx] = pd.Series(0.0, index=df.index)
        else:
            cn = feat_name.lower()
            features[idx] = df[cn].fillna(0.0) if cn in df.columns else pd.Series(0.0, index=df.index)
    return features


def compute_sigmoid_score(
    tokens: list[int], df: pd.DataFrame, features: dict[int, pd.Series],
    vm, train_end: int, test_start: int, test_end: int,
) -> np.ndarray | None:
    """Compute sigmoid-normalized score for a formula on test window."""
    train_df = df.iloc[:train_end].reset_index(drop=True)
    test_df = df.iloc[test_start:test_end].reset_index(drop=True)
    train_feat = {k: v.iloc[:train_end].reset_index(drop=True) for k, v in features.items()}
    test_feat = {k: v.iloc[test_start:test_end].reset_index(drop=True) for k, v in features.items()}

    train_score = vm.execute(tokens, train_df, precomputed_features=train_feat)
    test_score = vm.execute(tokens, test_df, precomputed_features=test_feat)
    if train_score is None or test_score is None:
        return None

    t_vals = train_score.values.astype(float)
    e_vals = test_score.values.astype(float)
    t_mean = float(np.nanmean(t_vals))
    t_std = float(np.nanstd(t_vals)) + 1e-8
    e_norm = ((e_vals - t_mean) / t_std).clip(-10, 10)
    return 1.0 / (1.0 + np.exp(-e_norm))


def backtest_parametric(
    tokens: list[int], formula_str: str,
    df: pd.DataFrame, features: dict[int, pd.Series],
    vm,
    buy_th: float, sell_th: float,
    hold_mode: str = "fixed",
    hold_bars: int = 5,
    hold_timeout: int = 20,
    min_hold_bars: int = 2,
    confirm_mode: str = "none",
    confirm_bars: int = 2,
    score_margin: float = 0.05,
    position_mode: str = "fixed",
    min_position: float = 0.30,
    position_size: float = 0.80,
    cooldown: int = 3,
    fee_rate: float = 0.0005,
    slippage_bps: float = 1.5,
    test_bars: int = 360,
    n_windows: int = 8,
    verbose: bool = True,
    label: str = "",
) -> dict | None:
    """Run parameterized walk-forward backtest with configurable hold/confirm/position modes."""
    n = len(df)
    min_train = 500
    gap = 10
    available_start = min_train + gap
    available_for_test = n - available_start
    if available_for_test < test_bars:
        if verbose:
            print(f"  Not enough data ({n} bars)")
        return None

    step = (available_for_test - test_bars) / max(n_windows - 1, 1)
    slip_frac = slippage_bps / 10000.0

    if verbose:
        tag = f" [{label}]" if label else ""
        print(f"\n{'='*70}")
        print(f"FORMULA: {formula_str}{tag}")
        print(f"hold={hold_mode}(bars={hold_bars},timeout={hold_timeout},min={min_hold_bars}) "
              f"confirm={confirm_mode}(bars={confirm_bars},margin={score_margin}) "
              f"pos={position_mode}(size={position_size},min={min_position})")
        print(f"buy={buy_th}, sell={sell_th}, cool={cooldown}")
        print(f"{'='*70}")
        print(f"{'Win':>4} {'Trades':>7} {'WR':>6} {'Sharpe':>8} {'Return':>8} {'MaxDD':>8} {'AvgRet':>7} {'AvgBars':>7}")
        print("-" * 65)

    wf_results: list[dict] = []

    for wi in range(n_windows):
        test_start = available_start + int(step * wi)
        test_end = min(test_start + test_bars, n)
        train_end = test_start - gap

        sigmoid = compute_sigmoid_score(tokens, df, features, vm, train_end, test_start, test_end)
        if sigmoid is None:
            if verbose:
                print(f"W{wi:>2}  FAILED")
            continue

        signals = np.where(sigmoid > buy_th, 1, np.where(sigmoid < sell_th, -1, 0))

        closes = df.iloc[test_start:test_end]["close"].values.astype(float)
        cap = 10000.0
        pos = 0
        entry = 0.0
        pos_not = 0.0
        eq: list[float] = [cap]
        trades: list[dict] = []
        bars_in = 0
        bars_out = cooldown + 1
        consecutive_buy = 0
        entry_score = 0.0

        for i in range(1, len(closes)):
            sig = signals[i - 1]
            score = sigmoid[i - 1] if i - 1 < len(sigmoid) else 0.5
            price = closes[i]

            if not np.isfinite(cap) or cap <= 0:
                eq.append(eq[-1] if eq else 0.0)
                continue

            if pos == 0:
                bars_out += 1

                should_enter = False
                if sig == 1 and bars_out >= cooldown:
                    if confirm_mode == "none":
                        should_enter = True
                    elif confirm_mode == "consecutive":
                        if sig == 1:
                            consecutive_buy += 1
                        else:
                            consecutive_buy = 0
                        if consecutive_buy >= confirm_bars:
                            should_enter = True
                            consecutive_buy = 0
                    elif confirm_mode == "score_margin":
                        if score > buy_th + score_margin:
                            should_enter = True

                if should_enter:
                    if position_mode == "score_scaled":
                        score_excess = max(0.0, min(1.0, (score - buy_th) / (1.0 - buy_th)))
                        eff_size = min_position + (position_size - min_position) * score_excess
                    else:
                        eff_size = position_size

                    pos = 1
                    entry = price * (1 + slip_frac)
                    pos_not = cap * eff_size
                    cap -= pos_not * fee_rate
                    bars_in = 0
                    bars_out = 0
                    entry_score = score

            elif pos == 1:
                bars_in += 1

                should_exit = False
                if hold_mode == "fixed":
                    if bars_in >= hold_bars and sig == -1:
                        should_exit = True
                    elif bars_in >= hold_bars * 2:
                        should_exit = True
                elif hold_mode == "signal":
                    if sig == -1 and bars_in >= 1:
                        should_exit = True
                    elif bars_in >= hold_timeout:
                        should_exit = True
                elif hold_mode == "signal_timeout":
                    if bars_in >= min_hold_bars and sig == -1:
                        should_exit = True
                    elif bars_in >= hold_timeout:
                        should_exit = True

                if should_exit:
                    pnl_pct = (price - entry) / entry
                    net = pos_not * pnl_pct - pos_not * abs(1 + pnl_pct) * fee_rate
                    cap += net
                    trades.append({"pnl": net, "pnl_pct": pnl_pct, "bars": bars_in, "entry_score": entry_score})
                    pos = 0
                    pos_not = 0.0
                    bars_out = 0

            eq.append(max(cap + (pos_not * (price - entry) / entry if pos else 0), 0.0))

        total_ret = (eq[-1] - 10000) / 10000
        peak = 10000.0
        max_dd = 0.0
        for e in eq:
            if e > peak:
                peak = e
            dd = (peak - e) / peak
            if dd > max_dd:
                max_dd = dd

        wr = sharpe = avg_ret = avg_bars = 0.0
        if trades:
            wins = [t for t in trades if t["pnl"] > 0]
            wr = len(wins) / len(trades)
            avg_ret = float(np.mean([t["pnl_pct"] for t in trades]))
            avg_bars = float(np.mean([t["bars"] for t in trades]))
            rets = pd.Series(eq).pct_change().dropna()
            if len(rets) > 1 and rets.std() > 1e-10:
                sharpe = float(rets.mean() / rets.std() * ANNUAL_BARS_4H**0.5)

        wf_results.append({
            "trades": len(trades), "wr": wr, "sharpe": sharpe,
            "ret": total_ret, "dd": -max_dd, "avg_ret": avg_ret, "avg_bars": avg_bars,
        })
        if verbose:
            print(f"W{wi:>2}  {len(trades):>5}  {wr:>5.1%}  {sharpe:>+7.2f}  {total_ret:>+7.1%}  {-max_dd:>7.1%}  {avg_ret:>+6.2%}  {avg_bars:>6.1f}")

    # Stability calculation
    valid = [r for r in wf_results if r["trades"] >= 2]
    if valid:
        sharpes = [r["sharpe"] for r in valid]
        wrs = [r["wr"] for r in valid]
        rets = [r["ret"] for r in valid]
        min_tr = min(r["trades"] for r in wf_results)
        sh_cv = float(np.std(sharpes)) / (abs(float(np.mean(sharpes))) + 1e-8)
        wr_cv = float(np.std(wrs)) / (float(np.mean(wrs)) + 1e-8)
        sh_stab = max(0, 1 - sh_cv)
        wr_stab = max(0, 1 - wr_cv)
        consist = 1.0 if all(r["ret"] > 0 for r in valid) else 0.5
        trade_ad = min(1.0, min_tr / 5.0)
        stability = 0.35 * sh_stab + 0.25 * wr_stab + 0.20 * consist + 0.20 * trade_ad
        min_wr = min(wrs)
        avg_ret_all = float(np.mean([r["avg_ret"] for r in valid]))
        avg_bars_all = float(np.mean([r["avg_bars"] for r in valid]))

        if stability >= 0.75 and min_wr >= 0.60:
            verdict = "ROBUST"
        elif stability >= 0.55 and min_wr >= 0.50:
            verdict = "ACCEPTABLE"
        elif stability >= 0.35:
            verdict = "FRAGILE"
        else:
            verdict = "UNSTABLE"

        if verbose:
            print(f"  STABILITY: {stability:.3f} -> {verdict}")
            print(f"  Avg WR={np.mean(wrs):.1%} (min={min_wr:.1%}), Avg Sharpe={np.mean(sharpes):+.2f}")
            print(f"  Avg Return={np.mean(rets):+.1%}, Avg Trade Ret={avg_ret_all:+.2%}, Avg Bars={avg_bars_all:.1f}, Min Trades={min_tr}")

        return {
            "formula": formula_str, "label": label,
            "hold_mode": hold_mode, "hold_bars": hold_bars, "hold_timeout": hold_timeout,
            "confirm_mode": confirm_mode, "position_mode": position_mode,
            "stability": stability, "verdict": verdict,
            "avg_wr": float(np.mean(wrs)), "min_wr": min_wr,
            "avg_sharpe": float(np.mean(sharpes)), "avg_ret": float(np.mean(rets)),
            "avg_trade_ret": avg_ret_all, "avg_bars": avg_bars_all,
            "min_trades": min_tr, "max_dd": float(np.mean([r["dd"] for r in valid])),
        }
    else:
        if verbose:
            print("  NO VALID WINDOWS")
        return None


# ---------------------------------------------------------------------------
# Summary table printer
# ---------------------------------------------------------------------------

def print_summary(results: list[dict], title: str = "EXPERIMENT RESULTS") -> None:
    """Print a formatted summary table of experiment results."""
    print(f"\n{'='*120}")
    print(f"{title}")
    print(f"{'='*120}")
    print(f"{'Label':>30} {'Hold':>14} {'Confirm':>12} {'Pos':>8} {'Stab':>6} {'Verdict':>10} "
          f"{'WR':>6} {'Sharpe':>7} {'Ret':>7} {'TrRet':>6} {'Bars':>5} {'MinTr':>5} {'MaxDD':>6}")
    print("-" * 120)
    for r in results:
        hold_str = f"{r['hold_mode'][:3]}({r.get('hold_bars','')}{r.get('hold_timeout','')})"
        print(f"{r.get('label',''):>30} {hold_str:>14} {r['confirm_mode']:>12} {r['position_mode']:>8} "
              f"{r['stability']:>6.3f} {r['verdict']:>10} "
              f"{r['avg_wr']:>5.1%} {r['avg_sharpe']:>+6.2f} {r['avg_ret']:>+6.1%} "
              f"{r['avg_trade_ret']:>+5.2%} {r['avg_bars']:>5.1f} {r['min_trades']:>5} {r['max_dd']:>6.1%}")


# ---------------------------------------------------------------------------
# Convenience: init VM
# ---------------------------------------------------------------------------

def init_vm():
    """Initialize the formula VM with V4 features."""
    from selene_space_alpha.vocab import FormulaVocab
    from selene_space_alpha.ops.ops_pandas import OPS_CONFIG_PANDAS
    from selene_space_alpha.real_data_loader import CEX_FEATURE_NAMES_V4
    from selene_space_alpha.vm import PandasStackVM

    v4op = tuple(cfg[0] for cfg in OPS_CONFIG_PANDAS if cfg[0] != "GATE")
    vocab = FormulaVocab(feature_names=CEX_FEATURE_NAMES_V4, operator_names=v4op)
    vm = PandasStackVM(vocab)
    return vocab, vm
