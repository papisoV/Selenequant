"""Long-short return sheet (plotly render layer): figure structure, degradation, AST independence.

Tests ``selene_space_factor_research.mining.return_report_viz`` — the plotly
render layer over :class:`WeightDriftResult`. Loaded by file path through a
synthetic package so the suite runs without installing the factor-research
package.

Torch/polars independence: an AST pass asserts neither the port nor this test
imports ``torch`` / ``polars`` / ``alphapurify``.
"""
from __future__ import annotations

import ast
import pathlib
import types

import numpy as np
import pandas as pd
import plotly.graph_objects as go

from _module_loader import MINING_DIR as _MINING, load_module

# --------------------------------------------------------------------------
# Torch/polars-independent loading of the port modules.
# --------------------------------------------------------------------------
def _load(name: str, src_dir: pathlib.Path = _MINING) -> types.ModuleType:
    return load_module(name, src_dir, package="selene_wd_viz")


weight_drift_result = _load("weight_drift_result")
wd = _load("weight_drift_backtest")
return_report_viz = _load("return_report_viz")
WeightDriftConfig = wd.WeightDriftConfig
WeightDriftBacktest = wd.WeightDriftBacktest
WeightDriftResult = weight_drift_result.WeightDriftResult
create_long_short_sheet = return_report_viz.create_long_short_sheet


# --------------------------------------------------------------------------
# Synthetic panel helper (mirrors test_weight_drift_backtest.py).
# --------------------------------------------------------------------------
def _syn(
    n_dates: int,
    n_symbols: int,
    seed: int,
    ret_sigma: float = 0.01,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2024-01-01", periods=n_dates)
    n = n_dates * n_symbols
    df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, n_symbols),
            "symbol": np.tile([f"S{i}" for i in range(n_symbols)], n_dates),
            "factor": rng.normal(0.0, 1.0, n),
            "fut_ret_1": rng.normal(0.0, ret_sigma, n),
        }
    )
    return (
        df[["trade_date", "symbol", "factor"]],
        df[["trade_date", "symbol", "fut_ret_1"]],
    )


def _group_by(n_symbols: int) -> dict:
    return {f"S{i}": ("A" if i < 6 else "B" if i < 11 else "C") for i in range(n_symbols)}


def _run(n_dates: int = 30, n_symbols: int = 15, group_by: dict | None = None) -> WeightDriftResult:
    factor_df, ret_df = _syn(n_dates, n_symbols, seed=1)
    cfg = WeightDriftConfig(epoch=10, n_bins=5, group_by=group_by)
    return WeightDriftBacktest().run(factor_df, ret_df, cfg)


# --------------------------------------------------------------------------
# Independence guarantees.
# --------------------------------------------------------------------------
def test_port_imports_no_torch_or_polars() -> None:
    tree = ast.parse((_MINING / "return_report_viz.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in ("torch", "polars"), f"banned import {alias.name}"


def test_testfile_imports_no_polars_torch_alphapurify() -> None:
    tree = ast.parse(pathlib.Path(__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in ("polars", "torch", "alphapurify"), f"banned import {alias.name}"


# --------------------------------------------------------------------------
# Figure structure.
# --------------------------------------------------------------------------
def test_create_long_short_sheet_returns_figure() -> None:
    res = _run(group_by=_group_by(15))
    fig = create_long_short_sheet(res)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 5
    names = {t.name for t in fig.data}
    assert {"Return Before Cost", "Cumulative LS Return", "LS Portfolio Turnover"} <= names


def test_industry_bar_and_stats_heatmap_present_with_group_by() -> None:
    res = _run(group_by=_group_by(15))
    fig = create_long_short_sheet(res)
    assert res.df_indus is not None
    bars = [t for t in fig.data if isinstance(t, go.Bar)]
    assert len(bars) == 1
    assert len(bars[0].y) == 3  # A / B / C industries
    assert any(isinstance(t, go.Heatmap) for t in fig.data)  # stats row


def test_no_industry_when_group_by_none() -> None:
    res = _run(group_by=None)
    assert res.df_indus is None
    fig = create_long_short_sheet(res)
    assert not any(isinstance(t, go.Bar) for t in fig.data)
    assert any(isinstance(t, go.Heatmap) for t in fig.data)  # stats row still present


def test_return_fig_false_returns_none() -> None:
    res = _run()
    assert create_long_short_sheet(res, return_fig=False) is None


def test_to_html_serializable() -> None:
    res = _run()
    html = create_long_short_sheet(res).to_html()
    assert isinstance(html, str) and len(html) > 100


# --------------------------------------------------------------------------
# Degradation.
# --------------------------------------------------------------------------
def test_empty_result_does_not_raise() -> None:
    empty = WeightDriftResult(
        df_result=pd.DataFrame(),
        df_agg=pd.DataFrame(),
        stats_ls={},
        mean_ret=0.0,
        avg_turnover_ls=0.0,
        ret_ls=pd.Series(dtype=float),
        cum_ret_ls=pd.Series(dtype=float),
        cum_ret_net_ls=pd.Series(dtype=float),
        config=None,
    )
    fig = create_long_short_sheet(empty)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0
