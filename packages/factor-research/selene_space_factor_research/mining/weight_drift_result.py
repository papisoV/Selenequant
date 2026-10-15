# Ported from AlphaPurify (MIT)
"""Result container for :class:`~selene_space_factor_research.mining.weight_drift_backtest.WeightDriftBacktest`.

Faithful to the meaningful outputs of AlphaPurify ``FactorAnalyzer.calc_stats_for_period``
(L529-1325), which returns a 15-tuple ``(period, stats_ls, df_result, mean_ret,
monthly_ret_ls, monthly_ret_l, stats_l, monthly_ret_s, stats_s, df_indus,
avg_turnover_ls, df_agg, df_heatmap_calendar_l, df_heatmap_calendar_s)``. The two
calendar heatmaps are plotly visualization tables (not a data-port surface) and are
intentionally not ported.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from .weight_drift_backtest import WeightDriftConfig


@dataclass
class WeightDriftResult:
    """Weight-drift quantile backtest result.

    Attributes:
        df_result: Per-date quantile return table (index = row position, with the
            date column shifted -1 exactly as AlphaPurify does). Columns include
            ``ret_<q>`` / ``ret_s_<q>`` / ``ret_net_<q>`` / ``cum_ret_net_<q>``,
            the long-short ``ret_ls`` / ``cum_ret_ls`` / ``cum_ret_net_ls``, and
            (for the top/bottom quantiles) ``cum_ret_<bins>`` / ``cum_ret_s_1``.
        df_agg: Per-date turnover / cost aggregates: ``buy_<q>``, ``sell_<q>``,
            ``turnover_<q>``, ``cost_<q>`` plus ``<x>_ls`` halves.
        stats_ls: Long-short performance stats (Ann Return/Std/Sharpe/Sortino/
            Calmar/Mean Turnover/Max Drawdown/WinRate/P-L/PnL).
        mean_ret: Mean of the long-short net return series.
        avg_turnover_ls: Average long-short turnover across rebalance dates.
        ret_ls: Long-short return series indexed by date.
        cum_ret_ls: Cumulative long-short return series indexed by date.
        cum_ret_net_ls: Cumulative long-short *net* (cost-adjusted) return series
            indexed by date.
        stats_l: Long-quantile (top) stats, mirroring AlphaPurify.
        stats_s: Short-quantile (bottom) stats, mirroring AlphaPurify.
        df_indus: Industry attribution table, or ``None`` when ``group_by`` unset.
        monthly_ret_ls: Monthly long-short aggregation when ``agg_freq`` is set,
            else ``None``.
        monthly_ret_l: Monthly long-quantile aggregation (same condition).
        monthly_ret_s: Monthly short-quantile aggregation (same condition).
        config: The config used to run the backtest (echoed back).
    """

    df_result: pd.DataFrame
    df_agg: pd.DataFrame
    stats_ls: dict
    mean_ret: float
    avg_turnover_ls: float
    ret_ls: pd.Series
    cum_ret_ls: pd.Series
    cum_ret_net_ls: pd.Series
    stats_l: dict | None = None
    stats_s: dict | None = None
    df_indus: pd.DataFrame | None = None
    monthly_ret_ls: pd.DataFrame | None = None
    monthly_ret_l: pd.DataFrame | None = None
    monthly_ret_s: pd.DataFrame | None = None
    config: "WeightDriftConfig | None" = None
