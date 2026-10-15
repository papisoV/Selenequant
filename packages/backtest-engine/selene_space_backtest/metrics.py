"""Performance metrics and cross-sectional IC statistics for backtests."""

from __future__ import annotations

import numpy as np
from dataclasses import dataclass
from typing import Any
from scipy.stats import spearmanr

from selene_space_types.strategy import BacktestMetrics


def _bars_table() -> dict[str, int]:
    return {
        "1m": 525_600,
        "3m": 175_200,
        "5m": 105_120,
        "15m": 35_040,
        "30m": 17_520,
        "1h": 8_760,
        "2h": 4_380,
        "4h": 2_190,
        "6h": 1_460,
        "8h": 1_095,
        "12h": 730,
        "1d": 365,
        "1w": 52,
    }


_TABLE = _bars_table()
_DAILY = 365
_ANNUAL_CAP = 10_000.0  # QD 同款封顶(runtime.py:2043-2051):年化 ≤ 1e4 倍


def infer_annualization_factor(interval: str) -> int:
    """Bars per year for an interval label; daily fallback when unknown."""
    return _TABLE.get(interval.strip().lower(), _DAILY)


def _max_drawdown(equity: np.ndarray) -> float:
    """Deepest peak-to-trough decline as a non-positive fraction."""
    if equity.size == 0:
        return 0.0
    running_peak = np.maximum.accumulate(equity)
    dips = (equity - running_peak) / running_peak
    worst = float(dips.min())
    return worst if worst < 0.0 else 0.0


def _annualized(
    total_return: float,
    n_periods: int,
    per_year: int,
) -> float:
    """年化收益率(已封顶)。需要知道"是否被封顶"时用 _annualized_capped。"""
    return _annualized_capped(total_return, n_periods, per_year)[0]


def _annualized_capped(
    total_return: float,
    n_periods: int,
    per_year: int,
) -> tuple[float, bool]:
    """年化收益率 + 是否触发封顶。

    返回 (annual_return, capped)。capped=True 表示返回值是 _ANNUAL_CAP
    截断结果而非真实年化 —— UI 必须提示,否则用户看到的是被静默改过的数字。
    """
    if n_periods <= 0 or per_year <= 0:
        return 0.0, False
    if total_return <= -1.0:
        return -1.0, False
    growth = 1.0 + total_return
    # QD 同款封顶:连续复利 min(log*系数, log(10001)) ≈ 年化封顶 1e4 倍,
    # 防止短窗口爆仓样本产出 inf 炸 JSON 序列化。
    try:
        annual = float(float(growth) ** (per_year / n_periods)) - 1.0
    except OverflowError:
        return _ANNUAL_CAP, True
    if annual > _ANNUAL_CAP:
        return _ANNUAL_CAP, True
    return annual, False


def _sharpe(
    returns: np.ndarray,
    per_year: int,
) -> float:
    if returns.size < 2:
        return 0.0
    dispersion = float(np.std(returns, ddof=1))
    if dispersion <= 1e-15:
        return 0.0
    return float(round(float(np.mean(returns)) / dispersion * np.sqrt(per_year), 4))


def _sortino(
    returns: np.ndarray,
    per_year: int,
) -> float:
    """Annualized mean over root-mean-square of losing periods."""
    if returns.size < 2:
        return 0.0
    losses = returns[returns < 0.0]
    if losses.size == 0:
        return 0.0
    downside = float(np.sqrt(np.mean(losses**2)))
    if downside == 0.0:
        return 0.0
    return float(float(np.mean(returns)) / downside * np.sqrt(per_year))


def _annualized_over_dd(
    annual_return: float,
    mdd: float,
) -> float:
    if mdd == 0.0:
        return 0.0
    return annual_return / abs(mdd)


def _returned_over_dd(
    total_return: float,
    mdd: float,
) -> float:
    if mdd == 0.0:
        return 0.0
    return total_return / abs(mdd)


def _expectancy(trades: list[dict]) -> float:
    """Per-trade expected PnL from win probability and win/loss sizes."""
    if not trades:
        return 0.0
    pnls = [float(t.get("pnl", 0.0)) for t in trades]
    winners = [v for v in pnls if v > 0.0]
    loser_magnitudes = [-v for v in pnls if v < 0.0]
    p_win = len(winners) / len(pnls)
    avg_win = sum(winners) / len(winners) if winners else 0.0
    avg_loss = (
        sum(loser_magnitudes) / len(loser_magnitudes) if loser_magnitudes else 0.0
    )
    return p_win * avg_win - (1.0 - p_win) * avg_loss


def _gross_win_loss_ratio(pnls: list[float]) -> tuple[float | None, bool]:
    """Returns (profit_factor or None-if-no-trades, clean_sweep flag).

    A sweep (trades exist, none losing) clamps the factor at 1e9 so JSON
    serialization stays finite.
    """
    gross_profit = sum(v for v in pnls if v > 0)
    gross_loss = -sum(v for v in pnls if v < 0)
    any_loser = any(v < 0 for v in pnls)
    sweep = bool(pnls) and not any_loser
    if not pnls:
        return 0.0, False
    if sweep:
        return 1e9, True
    if gross_loss > 0:
        return gross_profit / gross_loss, False
    return 0.0, False


_EMPTY_METRICS: dict[str, Any] = dict(
    total_return=0.0,
    annual_return=0.0,
    sharpe_ratio=0.0,
    max_drawdown=0.0,
    win_rate=0.0,
    profit_factor=0.0,
    funding_paid=0.0,
    borrow_fee_paid=0.0,
)


def compute_metrics(
    equity_curve: list[float],
    trades: list[dict],
    annualization_factor: int = 252,
) -> BacktestMetrics:
    """Condense an equity curve plus trade blotter into a metrics model.

    Rounding ladder: return-style figures keep 8 decimals; ratio-style
    figures keep 4.
    """
    if not equity_curve:
        payload = {**_EMPTY_METRICS, "total_trades": len(trades)}
        return BacktestMetrics(**payload)

    eq = np.asarray(equity_curve, dtype=np.float64)
    start_value = float(eq[0])
    final_value = float(eq[-1])

    total_return = (
        (final_value - start_value) / start_value if start_value != 0.0 else 0.0
    )
    mdd = _max_drawdown(eq)
    n_returns = eq.size - 1
    annual_return, annual_capped = _annualized_capped(
        total_return, n_returns, annualization_factor
    )

    period_returns = np.diff(eq) / eq[:-1]
    sharpe = _sharpe(period_returns, annualization_factor)

    pnls = [float(t["pnl"]) for t in trades]
    trade_count = len(pnls)
    wins = sum(1 for v in pnls if v > 0)
    win_rate = wins / trade_count if trade_count else 0.0
    profit_factor, sweep = _gross_win_loss_ratio(pnls)

    funding_total = sum(float(t.get("funding", 0.0)) for t in trades)
    borrow_total = sum(float(t.get("borrow_fee", 0.0)) for t in trades)

    # 混合类型(int/bool/float),须显式标注否则 mypy 收敛成 dict[str, float]
    fields: dict[str, Any] = {
        "total_return": round(total_return, 8),
        "annual_return": round(annual_return, 8),
        # 封顶标记:True 时 annual_return 非真实年化,UI 须提示
        "annual_return_capped": annual_capped,
        "sharpe_ratio": sharpe,
        "max_drawdown": round(mdd, 8),
        "win_rate": round(win_rate, 4),
        "profit_factor": (
            round(profit_factor, 4) if profit_factor is not None else 0.0
        ),
        "total_trades": trade_count,
        "had_no_losses": sweep,
        "calmar_ratio": round(_annualized_over_dd(annual_return, mdd), 4),
        "sortino_ratio": round(_sortino(period_returns, annualization_factor), 4),
        "expectancy": round(_expectancy(trades), 4),
        "recovery_factor": round(_returned_over_dd(total_return, mdd), 4),
        "funding_paid": float(funding_total),
        "borrow_fee_paid": float(borrow_total),
    }
    return BacktestMetrics(**fields)


@dataclass(frozen=True)
class ICSummary:
    """Rolled-up information-coefficient statistics across dates."""

    method: str
    ic_mean: float
    ic_std: float
    icir: float
    t_stat: float
    hit_rate: float
    n_dates: int


_MIN_WIDTH = 3


def _corr(
    factors: np.ndarray,
    returns: np.ndarray,
    method: str,
) -> float:
    if method == "pearson":
        sf, sr = float(np.std(factors)), float(np.std(returns))
        if sf == 0.0 or sr == 0.0:
            return float("nan")
        return float(np.corrcoef(factors, returns)[0, 1])
    return float(spearmanr(factors, returns).statistic)


def compute_ic(
    factor_values: np.ndarray,
    forward_returns: np.ndarray,
    method: str = "spearman",
) -> np.ndarray:
    """One cross-sectional correlation per date row of the input matrices.

    Rows with fewer than three fully-observed pairs yield NaN instead of
    a degenerate statistic.
    """
    allowed = ("spearman", "pearson")
    if method not in allowed:
        raise ValueError(
            f"unsupported IC method {method!r}; use 'spearman' or 'pearson'"
        )
    fv = np.array(factor_values, dtype=float)
    fr = np.array(forward_returns, dtype=float)
    if fv.shape != fr.shape:
        raise ValueError(
            f"factor_values and forward_returns must have the same shape: "
            f"{fv.shape} vs {fr.shape}"
        )
    if fv.ndim != 2 or fv.shape[0] == 0:
        return np.array([], dtype=float)

    series: list[float] = []
    for f_row, r_row in zip(fv, fr):
        seen = ~(np.isnan(f_row) | np.isnan(r_row))
        f_ok, r_ok = f_row[seen], r_row[seen]
        if f_ok.size < _MIN_WIDTH:
            series.append(float("nan"))
            continue
        series.append(_corr(f_ok, r_ok, method))
    return np.asarray(series, dtype=float)


def compute_ic_summary(
    factor_values: np.ndarray,
    forward_returns: np.ndarray,
    method: str = "spearman",
) -> ICSummary:
    """Aggregate a per-date IC series into mean/ICIR/t-stat/hit-rate."""
    full_series = compute_ic(factor_values, forward_returns, method=method)
    usable = full_series[~np.isnan(full_series)]
    dates_n = int(usable.size)
    if dates_n == 0:
        nan = float("nan")
        return ICSummary(
            method=method,
            ic_mean=nan,
            ic_std=nan,
            icir=nan,
            t_stat=nan,
            hit_rate=nan,
            n_dates=0,
        )

    ic_mean = float(np.mean(usable))
    ic_std = float(np.std(usable))
    icir = ic_mean / ic_std if ic_std > 0.0 else 0.0
    t_statistic = float(icir) * np.sqrt(dates_n)
    share_positive = float(np.count_nonzero(usable > 0.0)) / dates_n
    return ICSummary(
        method=method,
        ic_mean=ic_mean,
        ic_std=ic_std,
        icir=icir,
        t_stat=t_statistic,
        hit_rate=share_positive,
        n_dates=dates_n,
    )
