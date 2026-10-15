"""IC diagnostic panel: rolling IC/IR, autocorrelation, QQ, monthly heatmap, sector IC.

Tests the pandas port ``selene_space_factor_research.mining.ic_panel`` (a faithful
re-implementation of AlphaPurify's ``calc_stats_for_horizon`` IC section) against
hand-computed references, plus the Selene ``qq_stats`` extension and the
``ic_decay`` integration smoke.

Torch/polars independence: the module lives in the standalone
``selene_space_factor_research.mining`` package (no torch), but the tests still
load it **by file path** through a synthetic package, and an AST pass asserts
neither the port nor this test imports ``torch`` / ``polars`` / ``alphapurify``.
"""
from __future__ import annotations

import ast
import pathlib
import types

import numpy as np
import pandas as pd
import pytest
from scipy import stats

from _module_loader import ALPHA_MINING_DIR as _ALPHA_MINING, MINING_DIR as _MINING, load_module

# --------------------------------------------------------------------------
# Torch/polars-independent loading of the port module.
# --------------------------------------------------------------------------
# ic_decay is pre-existing alpha-core and stays there; ic_panel moved.
def _load(name: str, src_dir: pathlib.Path = _MINING) -> types.ModuleType:
    return load_module(name, src_dir, package="selene_icp")


ic_panel = _load("ic_panel")
ICPanel = ic_panel.ICPanel
ICPanelResult = ic_panel.ICPanelResult


# --------------------------------------------------------------------------
# Synthetic panel helpers.
# --------------------------------------------------------------------------
def _syn(
    n_dates: int = 40,
    n_symbols: int = 20,
    seed: int = 0,
    ic_strength: float = 0.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Panel whose factor predicts the next bar's return (positive spearman IC)."""
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


def _merged(factor_df: pd.DataFrame, ret_df: pd.DataFrame) -> pd.DataFrame:
    return factor_df.merge(ret_df, on=["trade_date", "symbol"])


# --------------------------------------------------------------------------
# Independence guarantees.
# --------------------------------------------------------------------------
def test_port_imports_no_torch_or_polars() -> None:
    tree = ast.parse((_MINING / "ic_panel.py").read_text(encoding="utf-8"))
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
# Result shape and signature.
# --------------------------------------------------------------------------
def test_result_fields_all_present() -> None:
    factor_df, ret_df = _syn(30, 15, seed=1)
    res = ICPanel().compute(factor_df, ret_df, horizon=1, rolling_window=5)
    assert isinstance(res, ICPanelResult)
    assert isinstance(res.rolling_ic, pd.Series)
    assert isinstance(res.rolling_ir, pd.Series)
    assert isinstance(res.autocorr, pd.Series)
    assert isinstance(res.ic_distribution, dict)
    assert isinstance(res.qq_stats, dict)
    assert isinstance(res.monthly_heatmap, pd.DataFrame)
    assert isinstance(res.sector_ic, pd.DataFrame)
    assert set(res.ic_distribution) == {
        "Mean IC", "Std", "Skewness", "Kurtosis", "t-stat", "p-Value", "IR",
    }
    assert set(res.qq_stats) == {
        "slope", "intercept", "r_value", "normality_stat", "normality_p",
    }


def test_compute_default_signature_runs() -> None:
    factor_df, ret_df = _syn(30, 15, seed=2)
    res = ICPanel().compute(factor_df, ret_df)  # horizon=1, rolling_window=60
    assert len(res.rolling_ic) >= 20


# --------------------------------------------------------------------------
# Golden semantic checks (hand-computed references).
# --------------------------------------------------------------------------
def test_rolling_ic_matches_hand_computed_spearman() -> None:
    factor_df, ret_df = _syn(40, 20, seed=3)
    res = ICPanel().compute(factor_df, ret_df, horizon=1, rolling_window=5)
    m = _merged(factor_df, ret_df)
    expected: dict[str, float] = {}
    for date, g in m.groupby("trade_date"):
        g = g.dropna(subset=["factor", "fut_ret_1"])
        if len(g) >= 2:
            expected[str(date)] = stats.spearmanr(g["factor"], g["fut_ret_1"])[0]
    exp = pd.Series(expected, name="ic")
    exp.index = pd.to_datetime(exp.index)
    exp = exp.sort_index()
    got = res.rolling_ic.reindex(exp.index)
    assert np.allclose(got.to_numpy(dtype=float), exp.to_numpy(dtype=float), atol=1e-12)


def test_autocorr_matches_hand_computed_lagged_rank_pearson() -> None:
    horizon = 2
    factor_df, ret_df = _syn(40, 20, seed=4)
    res = ICPanel().compute(factor_df, ret_df, horizon=horizon, rolling_window=5)
    m = _merged(factor_df, ret_df).sort_values(["symbol", "trade_date"])
    m["rank"] = m.groupby("trade_date")["factor"].rank(method="dense")
    m["lag"] = m.groupby("symbol")["rank"].shift(horizon)
    expected: dict[str, float] = {}
    for date, g in m.groupby("trade_date"):
        g = g.dropna(subset=["rank", "lag"])
        if len(g) >= 2 and np.ptp(g["rank"]) > 0 and np.ptp(g["lag"]) > 0:
            expected[str(date)] = float(np.corrcoef(g["rank"], g["lag"])[0, 1])
    exp = pd.Series(expected, name="autocorr")
    exp.index = pd.to_datetime(exp.index)
    exp = exp.sort_index()
    # The source joins autocorr onto the NaN-dropped IC frame, so only dates
    # with a valid IC (e.g. not the last bar, which has no forward return)
    # carry an autocorr value — align the hand computation to those dates.
    exp = exp.reindex(res.rolling_ic.index)
    got = res.autocorr.reindex(exp.index)
    assert np.allclose(
        got.to_numpy(dtype=float), exp.to_numpy(dtype=float), atol=1e-12, equal_nan=True
    )


def test_ic_distribution_mean_matches() -> None:
    factor_df, ret_df = _syn(40, 20, seed=5)
    res = ICPanel().compute(factor_df, ret_df, horizon=1, rolling_window=5)
    assert np.isclose(res.ic_distribution["Mean IC"], res.rolling_ic.mean(), atol=1e-12)
    assert np.isclose(
        res.ic_distribution["IR"],
        res.ic_distribution["Mean IC"] / res.ic_distribution["Std"],
        atol=1e-12,
    )


# --------------------------------------------------------------------------
# Monthly heatmap.
# --------------------------------------------------------------------------
def test_monthly_heatmap_shape_and_sums() -> None:
    # 2023-11 through 2024-02 business days -> spans two years, four months.
    dates = pd.bdate_range("2023-11-01", periods=88)  # ~4 months of business days
    n_symbols = 10
    rng = np.random.default_rng(6)
    n = len(dates) * n_symbols
    df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, n_symbols),
            "symbol": np.tile([f"S{i}" for i in range(n_symbols)], len(dates)),
            "daily": rng.normal(0.001, 0.01, n),
        }
    )
    df["fut_ret_1"] = df.groupby("symbol")["daily"].shift(-1)
    df["factor"] = 0.5 * df["fut_ret_1"].fillna(0.0) + rng.normal(0.0, 0.02, n)
    factor_df = df[["trade_date", "symbol", "factor"]]
    ret_df = df[["trade_date", "symbol", "fut_ret_1"]]

    res = ICPanel().compute(factor_df, ret_df, horizon=1, rolling_window=5)
    heat = res.monthly_heatmap
    assert heat.index.tolist() == sorted(heat.index.tolist())
    assert list(heat.columns) == sorted(heat.columns)
    assert heat.shape[0] == len(pd.DatetimeIndex(dates).year.unique())

    # Each cell = sum of daily IC within that (year, month). The pivot is a
    # dense year x month grid, so a cell with no data in that month is NaN.
    s = res.rolling_ic
    for year in heat.index:
        for month in heat.columns:
            cell = float(heat.loc[year, month])
            mask = (s.index.year == year) & (s.index.month == month)
            if np.isnan(cell):
                assert float(s[mask].sum()) == 0.0
            else:
                assert np.isclose(cell, float(s[mask].sum()), atol=1e-12)


# --------------------------------------------------------------------------
# QQ diagnostics (Selene extension).
# --------------------------------------------------------------------------
def test_qq_stats_normal_samples_accept_normality() -> None:
    rng = np.random.default_rng(7)
    samples = np.array([rng.normal(0.0, 0.05, 200) for _ in range(5)])
    qq = ic_panel.ICPanel._qq_stats(samples[0])
    assert qq["normality_p"] > 0.05
    assert qq["r_value"] > 0.99


def test_qq_stats_skewed_samples_reject_normality() -> None:
    rng = np.random.default_rng(8)
    qq = ic_panel.ICPanel._qq_stats(rng.chisquare(2.0, 200))  # strongly right-skewed
    assert qq["normality_p"] < 0.05
    assert qq["r_value"] < 0.99


# --------------------------------------------------------------------------
# Sector / group IC contribution.
# --------------------------------------------------------------------------
def test_sector_ic_weighted_contrib_sorted() -> None:
    factor_df, ret_df = _syn(30, 18, seed=9)
    sector = []
    for i in range(18):
        sector.append("SECTOR_A" if i < 8 else "SECTOR_B" if i < 14 else "SECTOR_C")
    factor_df["sector"] = np.tile(sector, 30)

    res = ICPanel().compute(factor_df, ret_df, horizon=1, rolling_window=5, by_group="sector")
    out = res.sector_ic
    assert out.columns.tolist() == ["group", "contrib"]
    assert len(out) == 3
    assert out["contrib"].is_monotonic_decreasing

    m = _merged(factor_df.drop(columns=["sector"]), ret_df)
    m["sector"] = factor_df["sector"]
    contribs: dict[str, list[float]] = {k: [] for k in ("SECTOR_A", "SECTOR_B", "SECTOR_C")}
    for (date, grp), gg in m.groupby(["trade_date", "sector"]):
        n_grp = len(gg)
        n_date = len(m[m["trade_date"] == date])
        gg = gg.dropna(subset=["factor", "fut_ret_1"])
        if len(gg) >= 2:
            ic = stats.spearmanr(gg["factor"], gg["fut_ret_1"])[0]
            contribs[grp].append(ic * (n_grp / n_date))
    for grp in contribs:
        expected = float(np.mean(contribs[grp]))
        got = float(out.loc[out["group"] == grp, "contrib"].iloc[0])
        assert np.isclose(got, expected, atol=1e-12)


def test_sector_ic_empty_without_group() -> None:
    factor_df, ret_df = _syn(20, 10, seed=10)
    res = ICPanel().compute(factor_df, ret_df, horizon=1, rolling_window=5)
    assert res.sector_ic.empty
    assert res.sector_ic.columns.tolist() == ["group", "contrib"]


# --------------------------------------------------------------------------
# Validation and integration.
# --------------------------------------------------------------------------
def test_validation_errors() -> None:
    factor_df, ret_df = _syn(20, 10, seed=11)
    panel = ICPanel()
    with pytest.raises(ValueError, match="horizon"):
        panel.compute(factor_df, ret_df, horizon=0)
    with pytest.raises(ValueError, match="rolling_window"):
        panel.compute(factor_df, ret_df, rolling_window=1)
    with pytest.raises(ValueError, match="factor"):
        panel.compute(factor_df.rename(columns={"factor": "f"}), ret_df)
    with pytest.raises(ValueError, match="by_group"):
        panel.compute(factor_df, ret_df, by_group="nope")
    with pytest.raises(TypeError, match="DataFrame"):
        panel.compute(factor_df.to_numpy(), ret_df)


def test_integration_with_ic_decay() -> None:
    ic_decay = _load("ic_decay", _ALPHA_MINING)
    factor_df, ret_df = _syn(40, 20, seed=12)
    res = ICPanel().compute(factor_df, ret_df, horizon=1, rolling_window=5)
    assert isinstance(res, ICPanelResult)

    sym = factor_df["symbol"].iloc[0]
    sig = (
        factor_df[factor_df["symbol"] == sym]
        .sort_values("trade_date")
        .set_index("trade_date")["factor"]
    )
    r = (
        ret_df[ret_df["symbol"] == sym]
        .sort_values("trade_date")
        .set_index("trade_date")["fut_ret_1"]
    )
    decay = ic_decay.compute_ic_decay(sig, r, max_lag=5)
    assert -1 <= decay.peak_ic <= 1
    assert decay.ic_by_lag.index.tolist() == [1, 2, 3, 4, 5]
