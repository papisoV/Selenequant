"""Parallel epoch x horizon grid runner: parity gate, determinism, worker cache.

Tests ``selene_space_factor_research.mining.parallel_runner``
(:class:`ParallelBacktestRunner`), the bug4-parity gate of the P3 AlphaPurify
absorption: every ``n_jobs`` value must reproduce the single-process serial loop
(``WeightDriftBacktest`` per epoch + ``ICPanel`` per horizon) to 1e-8 across all
result fields, pinned as a persistent CI assertion.

Torch/polars independence:
- The module lives in the standalone ``selene_space_factor_research.mining``
  package (no torch), and an AST pass asserts neither the port nor this test
  imports ``torch`` / ``polars`` / ``alphapurify``.

Synthetic-vs-real loading (deliberate deviation from the sibling
``test_ic_panel.py`` convention — see P3-P-02 spec):
- Parallel execution MUST use the real package import. The ``joblib``-loky
  backend ``spawn``s a fresh interpreter per worker and re-imports the task
  module by name; a synthetic-path module (registered under an in-memory
  package) is not importable by name in the child, so any parallel ``run()`` on
  the synthetic module raises ``BrokenProcessPool`` (probe-confirmed).
- The synthetic ``_module_loader`` load is still performed to satisfy the PRD
  convention and coverage attribution: it proves ``parallel_runner`` is loadable
  by the shared path loader (relative imports resolve through the synthetic
  package's ``__path__``), and the AST assertions read the file on disk directly
  (never through the synthetic module).
"""
from __future__ import annotations

import ast
import dataclasses
import os
import pathlib
import types
from typing import Any

import joblib
import numpy as np
import pandas as pd
import pytest

from _module_loader import MINING_DIR as _MINING, load_module

# Real-package import (execution path — loky spawn needs a name-importable
# module; see the module docstring). The deps come from the same package so the
# serial reference and the parallel cells share identical classes.
from selene_space_factor_research.mining import parallel_runner as _pr_module
from selene_space_factor_research.mining.ic_panel import ICPanel, ICPanelResult
from selene_space_factor_research.mining.parallel_runner import (
    ParallelBacktestRunner,
    ParallelGridResult,
)
from selene_space_factor_research.mining.weight_drift_backtest import (
    WeightDriftBacktest,
    WeightDriftConfig,
)
from selene_space_factor_research.mining.weight_drift_result import WeightDriftResult


# Synthetic loader (convention / AST / coverage attribution only — never the
# execution path).
def _load(name: str, src_dir: pathlib.Path = _MINING) -> types.ModuleType:
    return load_module(name, src_dir, package="selene_parallel")


parallel_runner = _load("parallel_runner")


# --------------------------------------------------------------------------
# Synthetic panel helpers.
# --------------------------------------------------------------------------
def _syn(
    n_dates: int = 40,
    n_symbols: int = 20,
    seed: int = 0,
    ic_strength: float = 0.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Panel whose factor predicts the next bar's return (positive spearman IC).

    ``n_dates=40`` keeps the panel span > 2 x max_epoch (epoch 10 needs >= 21
    dates) so ``epochs=[5, 10]`` and ``horizons=[1, 2]`` all stay well inside it.
    """
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


# --------------------------------------------------------------------------
# Serial reference — mirrors ParallelBacktestRunner.run()'s column resolution.
# --------------------------------------------------------------------------
def _serial_run(
    factor_df: pd.DataFrame,
    ret_df: pd.DataFrame,
    epochs: list[int],
    horizons: list[int],
    base_config: WeightDriftConfig | None = None,
    ic_kwargs: dict | None = None,
) -> tuple[dict[int, WeightDriftResult], dict[int, ICPanelResult]]:
    """Single-process loop the parallel grid must reproduce to 1e-8."""
    cfg = base_config or WeightDriftConfig()
    user_ic = dict(ic_kwargs or {})
    date_col = user_ic.pop("date_col", cfg.trade_date_col)
    symbol_col = user_ic.pop("symbol_col", cfg.symbol_col)
    factor_col = user_ic.pop("factor_col", cfg.factor_col)
    ret_col = user_ic.pop("ret_col", cfg.ret_col)
    bt_cfg = dataclasses.replace(
        cfg,
        trade_date_col=date_col,
        symbol_col=symbol_col,
        factor_col=factor_col,
        ret_col=ret_col,
    )
    backtests: dict[int, WeightDriftResult] = {}
    for e in epochs:
        backtests[e] = WeightDriftBacktest().run(
            factor_df, ret_df, config=dataclasses.replace(bt_cfg, epoch=e)
        )
    ics: dict[int, ICPanelResult] = {}
    for h in horizons:
        ics[h] = ICPanel().compute(
            factor_df,
            ret_df,
            horizon=h,
            date_col=date_col,
            symbol_col=symbol_col,
            factor_col=factor_col,
            ret_col=ret_col,
            **user_ic,
        )
    return backtests, ics


# --------------------------------------------------------------------------
# NaN-aware 1e-8 comparison helpers.
# --------------------------------------------------------------------------
def _assert_stats_close(got: dict, exp: dict, label: str) -> None:
    assert set(got) == set(exp), f"{label}: keys differ {set(got)} vs {set(exp)}"
    for k in exp:
        gv, ev = got[k], exp[k]
        both_nan = bool(pd.isna(gv)) and bool(pd.isna(ev))
        assert both_nan or abs(float(gv) - float(ev)) < 1e-8, f"{label}[{k}]: {gv} vs {ev}"


def _assert_scalar_close(got: float, exp: float, label: str) -> None:
    assert (pd.isna(got) and pd.isna(exp)) or abs(float(got) - float(exp)) < 1e-8, (
        f"{label}: {got} vs {exp}"
    )


def _assert_wd_parity(got: WeightDriftResult, exp: WeightDriftResult, label: str) -> None:
    """Full WeightDriftResult parity at 1e-8 (bug4 gate)."""
    pd.testing.assert_frame_equal(
        got.df_result, exp.df_result, rtol=0, atol=1e-8, check_dtype=False, obj=f"{label}.df_result"
    )
    pd.testing.assert_frame_equal(
        got.df_agg, exp.df_agg, rtol=0, atol=1e-8, check_dtype=False, obj=f"{label}.df_agg"
    )
    for attr in ("ret_ls", "cum_ret_ls", "cum_ret_net_ls"):
        pd.testing.assert_series_equal(
            getattr(got, attr),
            getattr(exp, attr),
            rtol=0,
            atol=1e-8,
            check_names=False,
            check_dtype=False,
            obj=f"{label}.{attr}",
        )
    for attr in ("stats_ls", "stats_l", "stats_s"):
        _assert_stats_close(getattr(got, attr), getattr(exp, attr), f"{label}.{attr}")
    for attr in ("mean_ret", "avg_turnover_ls"):
        _assert_scalar_close(getattr(got, attr), getattr(exp, attr), f"{label}.{attr}")


def _assert_ic_parity(got: ICPanelResult, exp: ICPanelResult, label: str) -> None:
    """Full ICPanelResult parity at 1e-8 (bug4 gate)."""
    for attr in ("rolling_ic", "rolling_ir", "autocorr"):
        pd.testing.assert_series_equal(
            getattr(got, attr),
            getattr(exp, attr),
            rtol=0,
            atol=1e-8,
            check_names=False,
            check_dtype=False,
            obj=f"{label}.{attr}",
        )
    for attr in ("ic_distribution", "qq_stats"):
        _assert_stats_close(getattr(got, attr), getattr(exp, attr), f"{label}.{attr}")
    for attr in ("monthly_heatmap", "sector_ic"):
        pd.testing.assert_frame_equal(
            getattr(got, attr), getattr(exp, attr), rtol=0, atol=1e-8, check_dtype=False,
            obj=f"{label}.{attr}",
        )


# --------------------------------------------------------------------------
# Independence guarantees.
# --------------------------------------------------------------------------
def test_port_imports_no_torch_or_polars() -> None:
    tree = ast.parse((_MINING / "parallel_runner.py").read_text(encoding="utf-8"))
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


def test_synthetic_loader_loads_parallel_runner() -> None:
    """The shared path loader can load the module (convention + coverage basis)."""
    assert parallel_runner.__name__ == "selene_parallel.parallel_runner"
    assert hasattr(parallel_runner, "ParallelBacktestRunner")
    assert hasattr(parallel_runner, "ParallelGridResult")
    assert isinstance(parallel_runner._worker_cache, dict)


# --------------------------------------------------------------------------
# Grid construction and validation.
# --------------------------------------------------------------------------
def test_grid_keys_and_task_count() -> None:
    f, r = _syn()
    res = ParallelBacktestRunner().run(
        f, r, epochs=[5, 10], horizons=[1, 2], ic_kwargs={"rolling_window": 10}, n_jobs=2
    )
    assert isinstance(res, ParallelGridResult)
    # Submission order preserved: backtests first (epochs order), then ICs (horizons order).
    assert list(res.backtests) == [5, 10]
    assert list(res.ics) == [1, 2]
    # Total tasks = len(epochs) + len(horizons) = 4 cells, each present exactly once.
    assert len(res.backtests) == 2
    assert len(res.ics) == 2


def test_max_workers_formula(monkeypatch: pytest.MonkeyPatch) -> None:
    """n_jobs=-1 -> min(total_tasks, cpu_count - 1), capped by the task count."""
    captured: dict[str, Any] = {}
    original = _pr_module.Parallel

    def _spy(*args: Any, **kwargs: Any) -> Any:
        captured["n_jobs"] = kwargs.get("n_jobs")
        return original(*args, **kwargs)

    monkeypatch.setattr(_pr_module, "Parallel", _spy)
    f, r = _syn()
    n_cpu = os.cpu_count() or 1
    # 2 epochs + 2 horizons = 4 tasks -> max_workers = min(4, cpu_count - 1).
    ParallelBacktestRunner().run(
        f, r, epochs=[5, 10], horizons=[1, 2], ic_kwargs={"rolling_window": 10}, n_jobs=-1
    )
    assert captured["n_jobs"] == min(4, n_cpu - 1)
    # Small grid: 1 epoch + 1 horizon = 2 tasks -> the total_tasks cap bites.
    ParallelBacktestRunner().run(
        f, r, epochs=[5], horizons=[1], ic_kwargs={"rolling_window": 10}, n_jobs=-1
    )
    assert captured["n_jobs"] == min(2, n_cpu - 1)


def test_validation_errors() -> None:
    f, r = _syn(20, 10, seed=11)
    runner = ParallelBacktestRunner()
    with pytest.raises(ValueError, match="epochs must be non-empty"):
        runner.run(f, r, epochs=[], horizons=[1])
    with pytest.raises(ValueError, match="horizons must be non-empty"):
        runner.run(f, r, epochs=[5], horizons=[])
    with pytest.raises(ValueError, match="positive ints"):
        runner.run(f, r, epochs=[0], horizons=[1])
    with pytest.raises(ValueError, match="positive ints"):
        runner.run(f, r, epochs=[5], horizons=[0])
    with pytest.raises(ValueError, match="positive ints"):
        runner.run(f, r, epochs=[5.5], horizons=[1])
    with pytest.raises(ValueError, match="positive ints"):
        runner.run(f, r, epochs=[5], horizons=[-2])
    with pytest.raises(TypeError, match="DataFrame"):
        runner.run(f.to_numpy(), r, epochs=[5], horizons=[1])
    with pytest.raises(TypeError, match="DataFrame"):
        runner.run(f, r.to_numpy(), epochs=[5], horizons=[1])


# --------------------------------------------------------------------------
# Parity gate (bug4): every n_jobs == serial loop, full fields, 1e-8.
# --------------------------------------------------------------------------
@pytest.mark.parametrize("n_jobs", [1, 2, 4])
def test_parallel_parity_with_serial(n_jobs: int) -> None:
    f, r = _syn(40, 20, seed=7)
    res = ParallelBacktestRunner().run(
        f, r, epochs=[5, 10], horizons=[1, 2], ic_kwargs={"rolling_window": 10}, n_jobs=n_jobs
    )
    bt, ic = _serial_run(f, r, [5, 10], [1, 2], ic_kwargs={"rolling_window": 10})
    for e in (5, 10):
        _assert_wd_parity(res.backtests[e], bt[e], f"n_jobs={n_jobs} epoch={e}")
    for h in (1, 2):
        _assert_ic_parity(res.ics[h], ic[h], f"n_jobs={n_jobs} horizon={h}")


# --------------------------------------------------------------------------
# Determinism.
# --------------------------------------------------------------------------
def test_two_runs_bit_identical_with_key_order() -> None:
    f, r = _syn(40, 20, seed=3)
    runner = ParallelBacktestRunner()
    res1 = runner.run(
        f, r, epochs=[5, 10], horizons=[1, 2], ic_kwargs={"rolling_window": 10}, n_jobs=2
    )
    res2 = runner.run(
        f, r, epochs=[5, 10], horizons=[1, 2], ic_kwargs={"rolling_window": 10}, n_jobs=2
    )
    assert list(res1.backtests) == list(res2.backtests) == [5, 10]
    assert list(res1.ics) == list(res2.ics) == [1, 2]
    for e in (5, 10):
        _assert_wd_parity(res1.backtests[e], res2.backtests[e], f"det epoch={e}")
    for h in (1, 2):
        _assert_ic_parity(res1.ics[h], res2.ics[h], f"det horizon={h}")


# --------------------------------------------------------------------------
# H2 — no stale-panel leak across run() calls; path-keyed worker cache.
# --------------------------------------------------------------------------
def test_second_panel_not_stale_across_runs() -> None:
    """run() on panel A then panel B must yield B == serial(B) — no stale A leak."""
    f_a, r_a = _syn(40, 20, seed=0, ic_strength=0.5)
    f_b, r_b = _syn(40, 20, seed=42, ic_strength=0.1)
    runner = ParallelBacktestRunner()
    runner.run(
        f_a, r_a, epochs=[5, 10], horizons=[1, 2], ic_kwargs={"rolling_window": 10}, n_jobs=2
    )
    res_b = runner.run(
        f_b, r_b, epochs=[5, 10], horizons=[1, 2], ic_kwargs={"rolling_window": 10}, n_jobs=2
    )
    bt_b, ic_b = _serial_run(f_b, r_b, [5, 10], [1, 2], ic_kwargs={"rolling_window": 10})
    for e in (5, 10):
        _assert_wd_parity(res_b.backtests[e], bt_b[e], f"H2 epoch={e}")
    for h in (1, 2):
        _assert_ic_parity(res_b.ics[h], ic_b[h], f"H2 horizon={h}")


def test_worker_cache_is_path_keyed(tmp_path: pathlib.Path) -> None:
    """Cache hit is keyed by panel path: distinct paths load fresh, same path reuses.

    The parent's ``_worker_cache`` is normally filled only inside workers (and by
    inline ``n_jobs=1`` runs), so this exercises :func:`_load_panel` directly in
    the parent — deterministic, no loky spawn needed. Cache is cleared first for
    hermeticity and my keys are popped after to avoid cross-test pollution.
    """
    _pr_module._worker_cache.clear()
    panel_a = _syn(20, 10, seed=0)[0]
    panel_b = _syn(20, 10, seed=1)[0]
    p1 = tmp_path / "a.joblib"
    p2 = tmp_path / "b.joblib"
    joblib.dump(panel_a, p1)
    joblib.dump(panel_b, p2)
    try:
        r1 = _pr_module._load_panel(str(p1))
        r2 = _pr_module._load_panel(str(p2))
        r1_again = _pr_module._load_panel(str(p1))
        assert len(_pr_module._worker_cache) == 2
        assert set(_pr_module._worker_cache) == {str(p1), str(p2)}
        assert r1_again is r1  # same path -> same cached object (identity)
        assert r2 is not r1
        pd.testing.assert_frame_equal(r1, panel_a)
    finally:
        for k in (str(p1), str(p2)):
            _pr_module._worker_cache.pop(k, None)


# --------------------------------------------------------------------------
# Temp directory lifecycle (M1) and integration.
# --------------------------------------------------------------------------
def test_tempdir_lifecycle_concurrent_run() -> None:
    """A concurrent grid must complete — temp-dir cleanup must not raise PermissionError."""
    f, r = _syn(40, 20, seed=13)
    res = ParallelBacktestRunner().run(
        f, r, epochs=[5, 10], horizons=[1, 2], ic_kwargs={"rolling_window": 10}, n_jobs=4
    )
    assert len(res.backtests) == 2
    assert len(res.ics) == 2


def test_integration_stats_tables_shape() -> None:
    """ParallelGridResult is directly consumable: stats tables row-per-epoch, IC panel row-per-horizon."""
    epochs, horizons = [5, 10], [1, 2]
    f, r = _syn()
    res = ParallelBacktestRunner().run(
        f, r, epochs=epochs, horizons=horizons, ic_kwargs={"rolling_window": 10}, n_jobs=2
    )
    for df in (res.stats_df_ls, res.stats_df_l, res.stats_df_s):
        assert df is not None
        assert len(df) == len(epochs)
        assert "epoch" in df.columns
        assert df["epoch"].tolist() == epochs
    assert res.ic_panel is not None
    assert len(res.ic_panel) == len(horizons)
    assert "horizon" in res.ic_panel.columns
    assert res.ic_panel["horizon"].tolist() == horizons
