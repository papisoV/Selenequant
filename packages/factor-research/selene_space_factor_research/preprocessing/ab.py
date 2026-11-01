"""A/B comparison of two preprocessing pipelines on the same factor.

AlphaPurify has no way to compare two preprocessing configs on the same factor
side by side — the hand-written if/elif chain makes experiments irreproducible.
This module runs the same factor through two :class:`PreprocessingPipeline`
configs and scores each arm with the IC diagnostic panel (:class:`ICPanel`) and
the weight-drift backtest (:class:`WeightDriftBacktest`), returning a compact
comparison table plus a reproducibility hash for each arm.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from ..mining.ic_panel import ICPanel
from ..mining.weight_drift_backtest import WeightDriftBacktest, WeightDriftConfig
from .pipeline import PipelineResult, PreprocessingPipeline

__all__ = ["ABResult", "run_ab"]

IC_METRICS: tuple[str, ...] = ("Mean IC", "Std", "t-stat", "IR")
WD_METRICS: tuple[str, ...] = ("Ann. Return", "Ann. Sharpe", "Max Drawdown", "Mean Turnover")
ALL_METRICS: tuple[str, ...] = IC_METRICS + WD_METRICS


@dataclass(frozen=True)
class ABResult:
    """Comparison of two preprocessing arms on the same factor.

    Attributes:
        arm_a / arm_b: Per-arm metric tables (IC + weight-drift scalars).
        diff: ``metric -> arm_b[metric] - arm_a[metric]``.
        preferred: Which arm wins on ``primary_metric`` (``"a"`` / ``"b"`` /
            ``"tie"``).
        pipeline_a / pipeline_b: Full provenance records for reproducibility.
    """

    arm_a: dict[str, float]
    arm_b: dict[str, float]
    diff: dict[str, float]
    preferred: str
    pipeline_a: PipelineResult
    pipeline_b: PipelineResult


def _core_columns(cfg: WeightDriftConfig) -> list[str]:
    return [cfg.trade_date_col, cfg.symbol_col, cfg.factor_col]


def _preferred(va: float, vb: float) -> str:
    """Which arm wins on a metric, NaN-aware (backtest stats may be NaN).

    ``NaN == NaN`` is ``False``, so the naive comparison mislabels both-NaN as
    arm "a". Here both-NaN is a tie, and a finite score always beats NaN.
    """
    if pd.isna(va) and pd.isna(vb):
        return "tie"
    if pd.isna(va):
        return "b"
    if pd.isna(vb):
        return "a"
    return "tie" if va == vb else ("b" if vb > va else "a")


def _evaluate(
    cleaned: pd.DataFrame,
    ret_df: pd.DataFrame,
    cfg: WeightDriftConfig,
    horizon: int,
    rolling_window: int,
) -> dict[str, float]:
    """Score one arm: IC panel + weight-drift backtest → scalar metrics."""
    core = cleaned[_core_columns(cfg)]
    ic = ICPanel().compute(
        core,
        ret_df,
        horizon=horizon,
        rolling_window=rolling_window,
        date_col=cfg.trade_date_col,
        symbol_col=cfg.symbol_col,
        factor_col=cfg.factor_col,
        ret_col=cfg.ret_col,
    )
    wd = WeightDriftBacktest().run(core, ret_df, cfg)
    metrics: dict[str, float] = {k: float(ic.ic_distribution[k]) for k in IC_METRICS}
    for k in WD_METRICS:
        metrics[k] = float(wd.stats_ls[k])
    return metrics


def run_ab(
    factor_df: pd.DataFrame,
    ret_df: pd.DataFrame,
    config_a: Any,
    config_b: Any,
    *,
    primary_metric: str = "Mean IC",
    horizon: int = 1,
    rolling_window: int = 60,
    backtest_config: WeightDriftConfig | None = None,
) -> ABResult:
    """Run two preprocessing configs on ``factor_df`` and compare the results.

    Args:
        factor_df: Factor panel with the backtest config's date/symbol/factor
            columns (plus any neutralizer inputs such as ``size``).
        ret_df: Forward-return panel with the config's date/symbol/return
            columns.
        config_a / config_b: Anything :meth:`PreprocessingPipeline.from_config`
            accepts — a step list, a dict, or a YAML/JSON file path.
        primary_metric: Metric that decides ``ABResult.preferred``; must be one
            of the IC or weight-drift metrics.
        horizon: IC horizon in days.
        rolling_window: Rolling window for IR.
        backtest_config: :class:`WeightDriftConfig`; defaults to the standard
            defaults. Also supplies the date/symbol/factor/return column names.

    Returns:
        An :class:`ABResult` with per-arm metrics, their diff, the preferred
        arm, and both arms' provenance for reproducibility.
    """
    if primary_metric not in ALL_METRICS:
        raise ValueError(
            f"primary_metric must be one of {ALL_METRICS!r}, got {primary_metric!r}"
        )
    cfg = backtest_config or WeightDriftConfig()
    pipe_a = PreprocessingPipeline.from_config(config_a)
    pipe_b = PreprocessingPipeline.from_config(config_b)
    res_a = pipe_a.run(factor_df)
    res_b = pipe_b.run(factor_df)
    arm_a = _evaluate(res_a.df, ret_df, cfg, horizon, rolling_window)
    arm_b = _evaluate(res_b.df, ret_df, cfg, horizon, rolling_window)
    diff = {k: arm_b[k] - arm_a[k] for k in ALL_METRICS}
    preferred = _preferred(arm_a[primary_metric], arm_b[primary_metric])
    return ABResult(
        arm_a=arm_a,
        arm_b=arm_b,
        diff=diff,
        preferred=preferred,
        pipeline_a=res_a,
        pipeline_b=res_b,
    )
