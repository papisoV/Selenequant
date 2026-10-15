"""IC diagnostic sheet (plotly render layer): figure structure, degradation, AST independence.

Tests ``selene_space_factor_research.mining.ic_panel_viz`` — the plotly render
layer over :class:`ICPanelResult`. Loaded by file path through a synthetic
package so the suite runs without installing the factor-research package.

Torch/polars independence: an AST pass asserts neither the port nor this test
imports ``torch`` / ``polars`` / ``alphapurify``. plotly is the only new
dependency of the render layer.
"""
from __future__ import annotations

import ast
import pathlib
import types

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import pytest

from _module_loader import MINING_DIR as _MINING, load_module

# --------------------------------------------------------------------------
# Torch/polars-independent loading of the port modules.
# --------------------------------------------------------------------------
def _load(name: str, src_dir: pathlib.Path = _MINING) -> types.ModuleType:
    return load_module(name, src_dir, package="selene_icp_viz")


ic_panel = _load("ic_panel")
ic_panel_viz = _load("ic_panel_viz")
ICPanel = ic_panel.ICPanel
ICPanelResult = ic_panel.ICPanelResult
create_ic_sheet = ic_panel_viz.create_ic_sheet


# --------------------------------------------------------------------------
# Synthetic panel helper (mirrors test_ic_panel.py).
# --------------------------------------------------------------------------
def _syn(
    n_dates: int = 40,
    n_symbols: int = 20,
    seed: int = 0,
    ic_strength: float = 0.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2023-01-02", periods=n_dates)
    n = n_dates * n_symbols
    df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, n_symbols),
            "symbol": np.tile([f"S{i}" for i in range(n_symbols)], n_dates),
            "daily": rng.normal(0.001, 0.01, n),
        }
    )
    df["fut_ret_1"] = df.groupby("symbol")["daily"].shift(-1)  # forward return
    noise = rng.normal(0.0, 0.02, n)
    df["factor"] = ic_strength * df["fut_ret_1"].fillna(0.0) + noise
    return (
        df[["trade_date", "symbol", "factor"]],
        df[["trade_date", "symbol", "fut_ret_1"]],
    )


def _compute(by_group: str | None = None) -> ICPanelResult:
    factor_df, ret_df = _syn()
    if by_group is not None:
        n_symbols = factor_df["symbol"].nunique()
        sector = (["A"] * 8 + ["B"] * 6 + ["C"] * 6)  # n_symbols == 20
        factor_df = factor_df.copy()
        factor_df[by_group] = np.tile(sector, len(factor_df) // n_symbols)
    return ICPanel().compute(factor_df, ret_df, horizon=1, rolling_window=5, by_group=by_group)


# --------------------------------------------------------------------------
# Independence guarantees.
# --------------------------------------------------------------------------
def test_port_imports_no_torch_or_polars() -> None:
    tree = ast.parse((_MINING / "ic_panel_viz.py").read_text(encoding="utf-8"))
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
def test_create_ic_sheet_returns_figure() -> None:
    res = _compute()
    fig = create_ic_sheet(res)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) >= 4
    names = {t.name for t in fig.data}
    assert {"IC", "Cumulative IC", "Self-Correlation"} <= names


def test_ic_sheet_has_qq_and_stats_heatmaps() -> None:
    res = _compute()
    fig = create_ic_sheet(res)
    names = {t.name for t in fig.data}
    assert "Base Line" in names  # QQ row present (>= 8 finite IC points)
    assert any(isinstance(t, go.Heatmap) for t in fig.data)  # monthly + stats rows


def test_sector_bar_only_with_by_group() -> None:
    plain = create_ic_sheet(_compute(by_group=None))
    assert not any(isinstance(t, go.Bar) for t in plain.data)

    grouped = create_ic_sheet(_compute(by_group="sector"))
    bars = [t for t in grouped.data if isinstance(t, go.Bar)]
    assert len(bars) == 1
    assert len(bars[0].y) == 3  # A / B / C groups


def test_return_fig_false_returns_none() -> None:
    res = _compute()
    assert create_ic_sheet(res, return_fig=False) is None


def test_to_html_serializable() -> None:
    res = _compute()
    html = create_ic_sheet(res).to_html()
    assert isinstance(html, str) and len(html) > 100


# --------------------------------------------------------------------------
# Degradation.
# --------------------------------------------------------------------------
def test_empty_panel_does_not_raise() -> None:
    empty = ICPanelResult(
        rolling_ic=pd.Series(dtype=float),
        rolling_ir=pd.Series(dtype=float),
        autocorr=pd.Series(dtype=float),
        ic_distribution={},
        qq_stats={},
        monthly_heatmap=pd.DataFrame(),
        sector_ic=pd.DataFrame(columns=["group", "contrib"]),
    )
    fig = create_ic_sheet(empty)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0


@pytest.mark.filterwarnings("ignore:.*Degrees of freedom.*")
def test_single_observation_panel_does_not_raise() -> None:
    # A one-row panel yields a NaN IC (no cross-section); the sheet must degrade.
    factor_df, ret_df = _syn(2, 3, seed=2)
    res = ICPanel().compute(factor_df, ret_df, horizon=1, rolling_window=2)
    fig = create_ic_sheet(res)
    assert isinstance(fig, go.Figure)
