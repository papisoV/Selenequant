# Ported from AlphaPurify (MIT)
"""Parallel epoch x horizon grid runner for the weight-drift backtest and IC panel.

A joblib-loky port of AlphaPurify ``FactorAnalyzer.run_stats_parallel``
(``FactorAnalyzer.py`` L2018-2118).

Architecture (what this module actually does — no promised-but-unimplemented
behaviour):

- The parent merges the ``(factor, ret)`` panel once and dumps it to a file
  inside a :class:`tempfile.TemporaryDirectory` (``ignore_cleanup_errors=True``,
  because reused loky workers on win32 hold the memmapped file open past the
  context manager, so a plain ``rmtree`` would raise ``PermissionError``).
- loky workers read the panel once with ``joblib.load(path, mmap_mode="r")``
  and cache it in the module-global ``_worker_cache`` keyed by the temp file
  path (a second task with the same path reuses the cached frame). Each task
  clones a writable ``(factor_df, ret_df)`` split off the shared (read-only)
  panel before handing it to the backtest / IC computation.
- The task grid submits all backtests first (``epochs`` order), then all IC
  cells (``horizons`` order). :func:`joblib.Parallel` returns results in
  submission order, so the result dicts' key order equals the submission order.
- Every task is a module-level function (``_worker_task``) — the win32
  ``spawn`` hard requirement — and returns a *structured* dataclass
  (``WeightDriftResult`` / ``ICPanelResult``) tagged with ``(kind, variant)``.
  Unlike AlphaPurify, no 14-/7-field positional tuple is ever produced or
  unpacked (bug4 fix: the source's ``run_stats_parallel`` packs and consumes
  positional tuples, which is brittle to any field reorder).

Differences from the source, all deliberate:

- No Arrow IPC: AlphaPurify exports the panel to an Arrow file (pyarrow).
  Here the merged panel is ``joblib.dump``-ed; pyarrow stays out of the
  runtime (joblib ships with scikit-learn, already a runtime dependency).
- No positional unpacking (bug4): workers return dataclasses.
- No ``period`` axis: the grid is ``epoch`` x ``horizon``.
"""
from __future__ import annotations

import dataclasses
import os
import tempfile
from dataclasses import dataclass
from typing import Any

import joblib
import pandas as pd
from joblib import Parallel, delayed

from .ic_panel import ICPanel, ICPanelResult
from .weight_drift_backtest import WeightDriftBacktest, WeightDriftConfig
from .weight_drift_result import WeightDriftResult

__all__ = ["ParallelBacktestRunner", "ParallelGridResult"]

# Module-global per-process cache of the shared panel, keyed by the temp file
# path. loky workers are reused across ``Parallel`` calls (and across ``run()``
# calls), so a fresh temp path always means a fresh load — no stale panel leak.
_worker_cache: dict[str, pd.DataFrame] = {}


def _split_panel(
    panel: pd.DataFrame,
    date_col: str,
    symbol_col: str,
    factor_col: str,
    ret_col: str,
    by_group: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Split the merged panel back into ``(factor_df, ret_df)`` writable copies.

    The parent merges with ``suffixes=("", "_ret")``, so a ``ret_df`` non-key
    column that collided with a ``factor_df`` column is stored as ``ret_col_ret``;
    detect that collision here and rename back. The shared panel is mmap'd
    read-only, so both splits are copied to writable frames before use.
    """
    factor_cols = [date_col, symbol_col, factor_col]
    if by_group is not None:
        factor_cols.append(by_group)
    merged_ret_col = ret_col if ret_col not in factor_cols else f"{ret_col}_ret"
    factor_df = panel[factor_cols].copy()
    ret_df = panel[[date_col, symbol_col, merged_ret_col]].copy()
    if merged_ret_col != ret_col:
        ret_df = ret_df.rename(columns={merged_ret_col: ret_col})
    return factor_df, ret_df


def _load_panel(panel_path: str) -> pd.DataFrame:
    """Return the shared panel, reading ``panel_path`` at most once per process.

    The frame is mmap'd read-only (``joblib.load(..., mmap_mode="r")``); callers
    must copy before mutating, which :func:`_split_panel` does.
    """
    panel = _worker_cache.get(panel_path)
    if panel is None:
        panel = joblib.load(panel_path, mmap_mode="r")
        _worker_cache[panel_path] = panel
    return panel


def _worker_task(
    kind: str,
    variant: int,
    panel_path: str,
    base_config: dict,
    ic_kwargs: dict,
) -> tuple[str, int, WeightDriftResult | ICPanelResult]:
    """Run one grid cell in a loky worker (module-level: ``spawn`` requirement).

    Args:
        kind: ``"backtest"`` for an epoch cell, ``"ic"`` for a horizon cell.
        variant: the epoch (backtest) or horizon (IC) keying the result dict.
        panel_path: temp file holding the merged panel.
        base_config: serialized :class:`WeightDriftConfig`; a backtest cell's
            dict already carries ``epoch=variant`` and the resolved column names.
        ic_kwargs: resolved :class:`ICPanel.compute` kwargs (column names already
            resolved against ``base_config``).

    Returns:
        ``(kind, variant, result)`` with a structured dataclass result — never a
        positional field unpacking (bug4 fix).
    """
    panel = _load_panel(panel_path)

    if kind == "backtest":
        cfg = WeightDriftConfig(**base_config)
        factor_df, ret_df = _split_panel(
            panel,
            date_col=cfg.trade_date_col,
            symbol_col=cfg.symbol_col,
            factor_col=cfg.factor_col,
            ret_col=cfg.ret_col,
        )
        return ("backtest", variant, WeightDriftBacktest().run(factor_df, ret_df, config=cfg))

    if kind != "ic":
        raise ValueError(f"unknown task kind {kind!r}")

    factor_df, ret_df = _split_panel(
        panel,
        date_col=ic_kwargs["date_col"],
        symbol_col=ic_kwargs["symbol_col"],
        factor_col=ic_kwargs["factor_col"],
        ret_col=ic_kwargs["ret_col"],
        by_group=ic_kwargs.get("by_group"),
    )
    # ``ic_kwargs`` carries the resolved column names plus any ``rolling_window``
    # / ``by_group`` knobs — all valid keyword args of :meth:`ICPanel.compute`.
    return ("ic", variant, ICPanel().compute(factor_df, ret_df, horizon=variant, **ic_kwargs))


def _as_config_dict(cfg: WeightDriftConfig) -> dict[str, Any]:
    """Shallow-serialize a config so it pickles cleanly across loky.

    ``cost_model`` is an arbitrary object kept by reference (never recursed
    into), which is exactly what ``WeightDriftConfig(**dict)`` reconstructs.
    """
    return {f.name: getattr(cfg, f.name) for f in dataclasses.fields(cfg)}


def _assemble_stats_df(backtests: dict[int, WeightDriftResult], attr: str) -> pd.DataFrame:
    """One row per epoch from each backtest's ``stats_ls`` / ``stats_l`` / ``stats_s``.

    Rows keep the dict's insertion order (== submission order). Backtests whose
    leg stats are ``None`` are skipped.
    """
    rows: list[dict[str, Any]] = []
    for epoch, result in backtests.items():
        stats = getattr(result, attr)
        if stats is not None:
            rows.append({"epoch": epoch, **stats})
    return pd.DataFrame(rows)


def _assemble_ic_panel(ics: dict[int, ICPanelResult]) -> pd.DataFrame:
    """One row per horizon from each IC result's ``ic_distribution``."""
    return pd.DataFrame(
        [
            {"horizon": horizon, **result.ic_distribution}
            for horizon, result in ics.items()
        ]
    )


@dataclass
class ParallelGridResult:
    """Aggregated result of a parallel ``epoch`` x ``horizon`` grid run.

    Attributes:
        backtests: ``epoch`` -> :class:`WeightDriftResult`, keyed in the same
            order the epochs were submitted.
        ics: ``horizon`` -> :class:`ICPanelResult`, keyed in the same order the
            horizons were submitted.
        stats_df_ls: Optional long-short stats table, one row per epoch
            (``{"epoch": ..., **stats_ls}``), assembled by :meth:`run`.
            ``None`` when the dataclass is constructed directly.
        stats_df_l: Optional long-quantile stats table (``**stats_l``).
        stats_df_s: Optional short-quantile stats table (``**stats_s``).
        ic_panel: Optional IC distribution table, one row per horizon
            (``{"horizon": ..., **ic_distribution}``).
    """

    backtests: dict[int, WeightDriftResult]
    ics: dict[int, ICPanelResult]
    stats_df_ls: pd.DataFrame | None = None
    stats_df_l: pd.DataFrame | None = None
    stats_df_s: pd.DataFrame | None = None
    ic_panel: pd.DataFrame | None = None


class ParallelBacktestRunner:
    """Runs an ``epoch`` x ``horizon`` grid with joblib-loky over a shared panel.

    ``n_jobs=1`` goes through the exact same code path as ``n_jobs>1`` (no
    special-casing) and returns identical results.
    """

    def run(
        self,
        factor_df: pd.DataFrame,
        ret_df: pd.DataFrame,
        *,
        epochs: list[int],
        horizons: list[int],
        base_config: WeightDriftConfig | None = None,
        ic_kwargs: dict | None = None,
        n_jobs: int = -1,
    ) -> ParallelGridResult:
        """Run every ``epoch`` backtest and every ``horizon`` IC cell in parallel.

        Args:
            factor_df: Panel with ``[date, symbol, factor]`` columns.
            ret_df: Panel with ``[date, symbol, forward_return]`` columns.
            epochs: Rebalance epochs (trading days) — one backtest per value,
                submitted in list order.
            horizons: IC lags — one IC panel per value, submitted in list order.
            base_config: Base :class:`WeightDriftConfig`; each backtest cell
                overrides its ``epoch``. Column names default from this config
                and can be overridden per-key in ``ic_kwargs``.
            ic_kwargs: Optional kwargs forwarded to :meth:`ICPanel.compute`
                (``rolling_window``, ``by_group``, and/or ``date_col`` /
                ``symbol_col`` / ``factor_col`` / ``ret_col`` overrides).
            n_jobs: Worker count. ``-1`` (default) uses
                ``min(total_tasks, cpu_count - 1)``.

        Returns:
            A :class:`ParallelGridResult` whose dict key order matches the
            submission order (backtests first, then ICs).
        """
        if not epochs:
            raise ValueError("epochs must be non-empty")
        if not horizons:
            raise ValueError("horizons must be non-empty")
        if any(not isinstance(e, int) or e < 1 for e in epochs):
            raise ValueError("epochs must be positive ints (trading days)")
        if any(not isinstance(h, int) or h < 1 for h in horizons):
            raise ValueError("horizons must be positive ints")
        if not isinstance(factor_df, pd.DataFrame) or not isinstance(ret_df, pd.DataFrame):
            raise TypeError("factor_df and ret_df must be pandas DataFrames")

        cfg = base_config or WeightDriftConfig()
        user_ic = dict(ic_kwargs or {})
        # Panel column vocabulary: base_config names, overridden per-key by ic_kwargs.
        date_col = user_ic.pop("date_col", cfg.trade_date_col)
        symbol_col = user_ic.pop("symbol_col", cfg.symbol_col)
        factor_col = user_ic.pop("factor_col", cfg.factor_col)
        ret_col = user_ic.pop("ret_col", cfg.ret_col)
        by_group = user_ic.get("by_group")

        for col in (date_col, symbol_col, factor_col):
            if col not in factor_df.columns:
                raise ValueError(f"column {col!r} missing from factor_df")
        for col in (date_col, symbol_col, ret_col):
            if col not in ret_df.columns:
                raise ValueError(f"column {col!r} missing from ret_df")
        if by_group is not None and by_group not in factor_df.columns:
            raise ValueError(f"by_group column {by_group!r} not in factor_df")

        # The backtest config adopts the resolved names so backtest and IC cells
        # split the exact same merged panel (same column vocabulary).
        bt_cfg = dataclasses.replace(
            cfg,
            trade_date_col=date_col,
            symbol_col=symbol_col,
            factor_col=factor_col,
            ret_col=ret_col,
        )
        resolved_ic = {
            "date_col": date_col,
            "symbol_col": symbol_col,
            "factor_col": factor_col,
            "ret_col": ret_col,
            **user_ic,
        }

        # ---- shared panel (PD2): one dump, every worker maps it at most once ----
        factor_cols = [date_col, symbol_col, factor_col]
        if by_group is not None:
            factor_cols.append(by_group)
        panel = factor_df[factor_cols].merge(
            ret_df[[date_col, symbol_col, ret_col]],
            on=[date_col, symbol_col],
            how="inner",
            suffixes=("", "_ret"),
        )

        # ---- task grid (PD1): backtests in epochs order, then ICs in horizons order ----
        tasks: list[tuple[str, int, str, dict[str, Any], dict[str, Any]]] = []
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp_dir:
            panel_path = os.path.join(tmp_dir, "panel.joblib")
            joblib.dump(panel, panel_path)

            for e in epochs:
                tasks.append(
                    (
                        "backtest",
                        e,
                        panel_path,
                        _as_config_dict(dataclasses.replace(bt_cfg, epoch=e)),
                        resolved_ic,
                    )
                )
            for h in horizons:
                tasks.append(("ic", h, panel_path, _as_config_dict(bt_cfg), resolved_ic))

            total_tasks = len(tasks)
            n_cpu = os.cpu_count() or 1
            max_workers = min(total_tasks, n_cpu - 1) if n_jobs == -1 else n_jobs
            if max_workers < 1:
                # Degenerate guard: joblib rejects n_jobs=0 (single-core box or an
                # explicit n_jobs=0); fall back to one worker instead.
                max_workers = 1

            results = Parallel(n_jobs=max_workers, backend="loky", mmap_mode="r")(
                delayed(_worker_task)(*task) for task in tasks
            )

        # joblib returns results in submission order; zip keeps the dict key
        # order identical to the submission order.
        backtests: dict[int, WeightDriftResult] = {}
        ics: dict[int, ICPanelResult] = {}
        for _, (kind, variant, result) in zip(tasks, results):
            if kind == "backtest":
                backtests[variant] = result
            else:
                ics[variant] = result

        return ParallelGridResult(
            backtests=backtests,
            ics=ics,
            stats_df_ls=_assemble_stats_df(backtests, "stats_ls"),
            stats_df_l=_assemble_stats_df(backtests, "stats_l"),
            stats_df_s=_assemble_stats_df(backtests, "stats_s"),
            ic_panel=_assemble_ic_panel(ics),
        )
