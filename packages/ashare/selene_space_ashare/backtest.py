"""
Simple strategy backtest engine for A-share factor-based strategies.

Supports:
- Long-only top-N portfolio based on factor ranking
- Long-short (top N - bottom N) portfolio
- Composite factor backtest
- Regime-conditional backtest
- Full strategy backtest (composite + regime + position sizing)
- Performance metrics: cumulative return, Sharpe, max DD, win rate, Calmar, Sortino
"""

import numpy as np
import pandas as pd

from selene_space_ashare.backtest_gates import (
    defer_limit_down_exits,
    mask_limit_up_entries,
)
from selene_space_ashare.composite import CompositeConfig, calc_composite_score
from selene_space_ashare.regime import get_regime_on_date, should_trade
from selene_space_ashare.strategy import (
    StrategyConfigAshare,
    strategy_performance_metrics,
)


def construct_portfolio(
    cross_section: pd.DataFrame,
    factor_name: str,
    direction: int = 1,
    n_long: int = 20,
    n_short: int = 0,
    forward_col: str = "fwd_5d",
    cost_bps: float = 0.0,
    *,
    limit_gates: bool = True,
) -> pd.DataFrame:
    """Construct daily long/short portfolio based on factor ranking.

    Args:
        cross_section: [date, symbol, factor_name, forward_col]
        factor_name: factor to rank on
        direction: 1 = higher factor = bullish, -1 = higher factor = bearish
        n_long: number of stocks to hold long
        n_short: number of stocks to hold short (0 = long only)
        forward_col: forward return column to evaluate
        cost_bps: one-way transaction cost in basis points, charged on
            position changes (turnover = changed stocks / n_long)
        limit_gates: drop rows the live book could not have bought (a
            limit-up-sealed signal close — see backtest_gates). Needs the
            ``limit_up_sealed`` column from :func:`attach_limit_flags`;
            without it the gate is inert, never approximated.

    Returns:
        DataFrame with [date, portfolio_return, long_count, short_count, turnover]
    """
    if limit_gates:
        cross_section = mask_limit_up_entries(cross_section)
    results = []
    prev_long: set | None = None
    prev_short: set | None = None
    for date, group in cross_section.groupby("date"):
        mask = group[factor_name].notna() & group[forward_col].notna()
        valid = group[mask]
        if len(valid) < n_long + n_short:
            continue

        ranked = valid.sort_values(factor_name, ascending=(direction == -1))

        long_stocks = ranked.head(n_long)
        long_ret = long_stocks[forward_col].mean()

        short_ret = 0.0
        short_stocks = ranked.tail(0)
        if n_short > 0:
            short_stocks = ranked.tail(n_short)
            short_ret = -short_stocks[forward_col].mean()

        cur_long = set(long_stocks["symbol"])
        cur_short = set(short_stocks["symbol"])
        turnover = 0.0
        if cost_bps > 0:
            changed = 0
            if prev_long is not None:
                changed += len(cur_long - prev_long) + len(prev_long - cur_long)
            if prev_short is not None:
                changed += len(cur_short - prev_short) + len(prev_short - cur_short)
            denom = max(1, n_long + n_short)
            turnover = changed / denom
        # Portfolio-level deduction: turnover already spans both legs, so the
        # cost is charged exactly once (per-leg adjustments double-count, and a
        # + on the short leg cancels the long-leg cost entirely).
        cost = cost_bps / 10000.0 * turnover
        prev_long = cur_long
        prev_short = cur_short

        results.append(
            {
                "date": date,
                "portfolio_return": long_ret + short_ret - cost,
                "long_return": long_ret,
                "short_return": short_ret,
                "long_count": len(long_stocks),
                "short_count": n_short,
                "turnover": turnover,
            }
        )

    return pd.DataFrame(results)


def calc_performance_metrics(returns: pd.Series, freq: int = 252) -> dict:
    """Calculate performance metrics from daily return series."""
    if returns.empty or returns.isna().all():
        return {
            "cumulative_return": np.nan,
            "annual_return": np.nan,
            "sharpe": np.nan,
            "max_drawdown": np.nan,
            "win_rate": np.nan,
            "volatility": np.nan,
        }

    cum_ret = (1 + returns).prod() - 1
    n_periods = len(returns)
    annual_ret = (1 + cum_ret) ** (freq / n_periods) - 1 if n_periods > 0 else np.nan
    vol = returns.std() * np.sqrt(freq)
    sharpe = (
        (returns.mean() / returns.std()) * np.sqrt(freq)
        if returns.std() > 0
        else np.nan
    )

    cum_series = (1 + returns).cumprod()
    running_max = cum_series.cummax()
    drawdown = cum_series / running_max - 1
    max_dd = drawdown.min()

    win_rate = (returns > 0).mean()

    return {
        "cumulative_return": cum_ret,
        "annual_return": annual_ret,
        "sharpe": sharpe,
        "max_drawdown": max_dd,
        "win_rate": win_rate,
        "volatility": vol,
    }


def backtest_factor(
    cross_section: pd.DataFrame,
    factor_name: str,
    direction: int = 1,
    n_long: int = 20,
    n_short: int = 0,
    forward_col: str = "fwd_5d",
    cost_bps: float = 0.0,
) -> dict:
    """Run a full backtest for a single factor."""
    portfolio = construct_portfolio(
        cross_section, factor_name, direction, n_long, n_short, forward_col, cost_bps
    )
    if portfolio.empty:
        return {
            "metrics": calc_performance_metrics(pd.Series(dtype=float)),
            "daily_returns": portfolio,
        }

    metrics = calc_performance_metrics(portfolio["portfolio_return"])
    return {"metrics": metrics, "daily_returns": portfolio}


def backtest_factors_batch(
    cross_section: pd.DataFrame,
    factor_names: list[str],
    factor_directions: dict[str, int] | None = None,
    n_long: int = 20,
    forward_col: str = "fwd_5d",
) -> pd.DataFrame:
    """Backtest multiple factors and return comparison table."""
    if factor_directions is None:
        factor_directions = {}

    results = []
    for name in factor_names:
        direction = factor_directions.get(name, 1)
        bt = backtest_factor(cross_section, name, direction, n_long, 0, forward_col)
        m = bt["metrics"]
        m["factor"] = name
        m["direction"] = direction
        results.append(m)

    report = pd.DataFrame(results)
    if not report.empty:
        report = report.sort_values("sharpe", ascending=False)
    return report


def backtest_composite(
    cross_section: pd.DataFrame,
    composite_config: CompositeConfig,
    n_long: int = 5,
    forward_col: str = "fwd_5d",
    cost_bps: float = 0.0,
) -> dict:
    """Backtest a composite factor strategy (no regime, no sizing)."""
    panel = cross_section.copy()
    if "composite_score" not in panel.columns:
        panel["composite_score"] = calc_composite_score(panel, composite_config)

    portfolio = construct_portfolio(
        panel.rename(columns={"composite_score": "_composite"}),
        "_composite",
        direction=1,
        n_long=n_long,
        n_short=0,
        forward_col=forward_col,
        cost_bps=cost_bps,
    )
    if portfolio.empty:
        return {
            "metrics": calc_performance_metrics(pd.Series(dtype=float)),
            "daily_returns": portfolio,
        }

    metrics = calc_performance_metrics(portfolio["portfolio_return"])
    return {"metrics": metrics, "daily_returns": portfolio}


def backtest_with_regime(
    cross_section: pd.DataFrame,
    regime_df: pd.DataFrame,
    factor_name: str,
    strategy_mode: str = "trend_follow",
    direction: int = 1,
    n_long: int = 5,
    forward_col: str = "fwd_5d",
    cost_bps: float = 0.0,
) -> dict:
    """Backtest a single factor with regime filter (no position sizing)."""
    results = []
    prev_long: set | None = None
    for date, group in cross_section.groupby("date"):
        regime = get_regime_on_date(regime_df, str(date))
        if not should_trade(regime, strategy_mode):
            prev_long = None
            results.append(
                {
                    "date": date,
                    "portfolio_return": 0.0,
                    "is_invested": False,
                    "turnover": 0.0,
                }
            )
            continue

        mask = group[factor_name].notna() & group[forward_col].notna()
        valid = group[mask]
        if len(valid) < n_long:
            prev_long = None
            results.append(
                {
                    "date": date,
                    "portfolio_return": 0.0,
                    "is_invested": False,
                    "turnover": 0.0,
                }
            )
            continue

        ranked = valid.sort_values(factor_name, ascending=(direction == -1))
        long_stocks = ranked.head(n_long)
        long_ret = long_stocks[forward_col].mean()

        turnover = 0.0
        if cost_bps > 0:
            cur_long = set(long_stocks["symbol"])
            if prev_long is not None:
                turnover = len(cur_long ^ prev_long) / max(1, n_long)
                long_ret -= cost_bps / 10000.0 * turnover
            prev_long = cur_long

        results.append(
            {
                "date": date,
                "portfolio_return": long_ret,
                "is_invested": True,
                "turnover": turnover,
            }
        )

    portfolio = pd.DataFrame(results)
    if portfolio.empty:
        return {
            "metrics": calc_performance_metrics(pd.Series(dtype=float)),
            "daily_returns": portfolio,
        }

    invested = portfolio[portfolio["is_invested"]]
    win_rate = (
        (invested["portfolio_return"] > 0).mean() if not invested.empty else np.nan
    )

    metrics = calc_performance_metrics(portfolio["portfolio_return"])
    metrics["time_in_market"] = portfolio["is_invested"].mean()
    metrics["invested_win_rate"] = win_rate
    return {"metrics": metrics, "daily_returns": portfolio}


def backtest_full_strategy(
    cross_section: pd.DataFrame,
    regime_df: pd.DataFrame,
    config: StrategyConfigAshare,
    forward_col: str = "fwd_1d",
    cost_bps: float = 0.0,
    *,
    limit_gates: bool = True,
) -> dict:
    """Full strategy backtest: composite + regime + position sizing.

    With ``limit_gates`` (default) the forward column is first rewritten so a
    limit-down-sealed horizon close defers to the first sellable close
    (:func:`backtest_gates.defer_limit_down_exits`), and entry rows sealed at
    limit-up are dropped before selection — the two fillability gates TSP's
    engine applies at the trade layer.
    """
    from selene_space_ashare.strategy import run_strategy

    panel = cross_section
    if limit_gates and not panel.empty:
        import re

        horizon_match = re.search(r"fwd_(\d+)d", forward_col)
        horizon = int(horizon_match.group(1)) if horizon_match else 0
        if horizon > 0 and {"close", "symbol", "date"}.issubset(panel.columns):
            panel = defer_limit_down_exits(panel, forward_col, horizon)

    result = run_strategy(
        panel, regime_df, config, forward_col, cost_bps, limit_gates=limit_gates
    )
    if result.empty:
        return {
            "metrics": strategy_performance_metrics(
                pd.Series(dtype=float), pd.Series(dtype=bool)
            ),
            "daily_returns": result,
        }

    metrics = strategy_performance_metrics(
        result["portfolio_return"], result["is_invested"]
    )
    return {"metrics": metrics, "daily_returns": result}
