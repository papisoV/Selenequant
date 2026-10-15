"""A/B comparison of two preprocessing configs on the same factor.

Tests :func:`~selene_space_factor_research.preprocessing.ab.run_ab` — two arms
run the same factor through different pipelines, scored by IC panel +
weight-drift backtest, with reproducible provenance per arm.
"""
from __future__ import annotations

import ast
import pathlib

import numpy as np
import pandas as pd
import pytest

from selene_space_factor_research.preprocessing.ab import (
    ABResult,
    ALL_METRICS,
    _preferred,
    run_ab,
)
from selene_space_factor_research.mining.weight_drift_backtest import WeightDriftConfig

_MODULE = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "factor-research" / "selene_space_factor_research" / "preprocessing" / "ab.py"
)

N_DATES = 10
N_SYMBOLS = 10


def _data(seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range("2024-01-01", periods=N_DATES)
    n = N_DATES * N_SYMBOLS
    factor = rng.normal(0.0, 1.0, n)
    factor[np.arange(0, n, 7)] += 12.0  # outliers for winsorize to clamp
    ret = 0.3 * factor + 0.1 * rng.normal(0.0, 1.0, n)
    factor_df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, N_SYMBOLS),
            "symbol": np.tile([f"S{i}" for i in range(N_SYMBOLS)], N_DATES),
            "factor": factor,
            "size": rng.lognormal(0.0, 1.0, n),
        }
    )
    ret_df = pd.DataFrame(
        {
            "trade_date": np.repeat(dates, N_SYMBOLS),
            "symbol": np.tile([f"S{i}" for i in range(N_SYMBOLS)], N_DATES),
            "fut_ret_1": ret,
        }
    )
    return factor_df, ret_df


WIN_STEP = {"method": "winsorize_mad", "params": {"trade_date_col": "trade_date", "factor_col": "factor", "n": 3.0}}
ZSCORE_STEP = {"method": "standardize_zscore", "params": {"trade_date": "trade_date", "factor_col": "factor"}}

_CFG = WeightDriftConfig(epoch=3, n_bins=3)


def _dict_close(a: dict[str, float], b: dict[str, float]) -> None:
    """Assert two metric dicts equal, treating NaN as equal (backtest stats may be NaN)."""
    assert set(a) == set(b)
    for k in a:
        if pd.isna(a[k]) and pd.isna(b[k]):
            continue
        assert a[k] == pytest.approx(b[k])


def test_run_ab_returns_abresult() -> None:
    factor_df, ret_df = _data()
    res = run_ab(factor_df, ret_df, [], [WIN_STEP], backtest_config=_CFG)
    assert isinstance(res, ABResult)
    assert set(res.arm_a) == set(ALL_METRICS)
    assert set(res.arm_b) == set(ALL_METRICS)
    assert set(res.diff) == set(ALL_METRICS)
    assert res.preferred in ("a", "b", "tie")


def test_same_config_is_tie() -> None:
    factor_df, ret_df = _data(seed=1)
    res = run_ab(factor_df, ret_df, [WIN_STEP], [WIN_STEP], backtest_config=_CFG)
    assert res.preferred == "tie"
    _dict_close(res.arm_a, res.arm_b)
    assert all(v == 0.0 for v in res.diff.values())


def test_reproducible_metrics_and_trace() -> None:
    factor_df, ret_df = _data(seed=2)
    r1 = run_ab(factor_df, ret_df, [WIN_STEP, ZSCORE_STEP], [WIN_STEP], backtest_config=_CFG)
    r2 = run_ab(factor_df, ret_df, [WIN_STEP, ZSCORE_STEP], [WIN_STEP], backtest_config=_CFG)
    _dict_close(r1.arm_a, r2.arm_a)
    _dict_close(r1.arm_b, r2.arm_b)
    assert r1.pipeline_a.trace_hash == r2.pipeline_a.trace_hash
    assert r1.pipeline_b.trace_hash == r2.pipeline_b.trace_hash


def test_diff_is_arm_b_minus_arm_a() -> None:
    factor_df, ret_df = _data(seed=3)
    res = run_ab(factor_df, ret_df, [WIN_STEP], [WIN_STEP, ZSCORE_STEP], backtest_config=_CFG)
    _dict_close(res.diff, {k: res.arm_b[k] - res.arm_a[k] for k in ALL_METRICS})


def test_swapping_configs_flips_preferred_and_diff() -> None:
    factor_df, ret_df = _data(seed=4)
    cfg_a, cfg_b = [WIN_STEP], [WIN_STEP, ZSCORE_STEP]
    forward = run_ab(factor_df, ret_df, cfg_a, cfg_b, backtest_config=_CFG)
    reverse = run_ab(factor_df, ret_df, cfg_b, cfg_a, backtest_config=_CFG)
    # swapped arms must mirror each other
    _dict_close(forward.arm_a, reverse.arm_b)
    _dict_close(forward.arm_b, reverse.arm_a)
    _dict_close(forward.diff, {k: -reverse.diff[k] for k in ALL_METRICS})
    assert {"a", "b", "tie"}.issuperset({forward.preferred, reverse.preferred})
    # the better arm flips when configs are swapped
    assert forward.preferred == {"a": "b", "b": "a", "tie": "tie"}[reverse.preferred]


def test_primary_metric_override() -> None:
    factor_df, ret_df = _data(seed=5)
    res = run_ab(
        factor_df,
        ret_df,
        [WIN_STEP],
        [WIN_STEP, ZSCORE_STEP],
        primary_metric="Ann. Sharpe",
        backtest_config=_CFG,
    )
    a, b = res.arm_a["Ann. Sharpe"], res.arm_b["Ann. Sharpe"]
    expected = "tie" if a == b else ("b" if b > a else "a")
    assert res.preferred == expected


def test_invalid_primary_metric_raises() -> None:
    factor_df, ret_df = _data()
    with pytest.raises(ValueError, match="primary_metric"):
        run_ab(factor_df, ret_df, [], [], primary_metric="not-a-metric", backtest_config=_CFG)


def test_preferred_handles_nan_primary() -> None:
    # Both NaN is a tie (NaN == NaN is False, so a naive compare would pick "a").
    assert _preferred(float("nan"), float("nan")) == "tie"
    # A finite score always beats NaN.
    assert _preferred(float("nan"), 1.0) == "b"
    assert _preferred(1.0, float("nan")) == "a"
    # Finite comparisons unchanged.
    assert _preferred(2.0, 1.0) == "a"
    assert _preferred(1.0, 2.0) == "b"
    assert _preferred(2.0, 2.0) == "tie"


def test_provenance_attached_per_arm() -> None:
    factor_df, ret_df = _data(seed=6)
    res = run_ab(factor_df, ret_df, [WIN_STEP, ZSCORE_STEP], [], backtest_config=_CFG)
    assert [s.method for s in res.pipeline_a.provenance] == ["winsorize_mad", "standardize_zscore"]
    assert res.pipeline_b.provenance == []
    assert len(res.pipeline_a.trace_hash) == 64


def test_lasso_extra_columns_handled() -> None:
    factor_df, ret_df = _data(seed=7)
    lasso = {
        "method": "neutralize_lasso",
        "params": {"trade_date_col": "trade_date", "factor_col": "factor", "neutralizer_cols": ["size"]},
    }
    res = run_ab(factor_df, ret_df, [lasso], [WIN_STEP], backtest_config=_CFG)
    assert res.pipeline_a.df["factor"].notna().all()
    assert isinstance(res, ABResult)


def test_distinct_configs_yield_distinct_traces() -> None:
    factor_df, ret_df = _data(seed=8)
    res = run_ab(factor_df, ret_df, [], [WIN_STEP, ZSCORE_STEP], backtest_config=_CFG)
    assert res.pipeline_a.trace_hash != res.pipeline_b.trace_hash


def test_port_module_imports_no_torch_or_polars() -> None:
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                root = alias.name.split(".")[0]
                assert root not in ("torch", "polars"), f"banned import {alias.name}"
