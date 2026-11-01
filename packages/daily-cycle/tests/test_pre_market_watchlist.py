"""PreMarketPhase watchlist hot-reload (offline).

Absorbed refresh_stock_list pattern from daily_stock_analysis (MIT) — see
pre_market.py and market-data PROVENANCE.md. Every execute() re-reads the
configured file so edits apply without restart.
"""

from __future__ import annotations

import logging
from datetime import date

import pytest

from selene_space_daily_cycle.phases.pre_market import PreMarketPhase
from selene_space_daily_cycle.types import (
    WorkflowConfig,
    WorkflowContext,
    WorkflowStatus,
)

_TRADING_DAY = date(2026, 10, 7)


def _cfg_with_path(path: str) -> WorkflowConfig:
    return WorkflowConfig(provider_configs={"watchlist": {"path": path}})


async def _run(phase: PreMarketPhase) -> object:
    ctx = WorkflowContext()
    return await phase.execute(_TRADING_DAY, ctx)


def _write_watchlist(tmp_path, content: str) -> str:
    path = tmp_path / "watchlist.txt"
    path.write_text(content, encoding="utf-8")
    return str(path)


@pytest.mark.asyncio
async def test_watchlist_file_read_and_exposed(tmp_path) -> None:
    path = _write_watchlist(
        tmp_path,
        "# comment line\n"
        "\n"
        "000001.SZ\n"
        "  600519.SH  \n"
        "# another comment\n"
        "300750.SZ\n",
    )
    ctx = WorkflowContext()
    outcome = await PreMarketPhase(cfg=_cfg_with_path(path)).execute(_TRADING_DAY, ctx)

    assert ctx.artifacts["watchlist"] == ["000001.SZ", "600519.SH", "300750.SZ"]
    assert outcome.metrics is not None
    assert outcome.metrics["watchlist_count"] == 3
    assert outcome.status == WorkflowStatus.SUCCESS


@pytest.mark.asyncio
async def test_watchlist_unconfigured_silent_skip() -> None:
    # No provider_configs at all
    ctx = WorkflowContext()
    outcome = await PreMarketPhase().execute(_TRADING_DAY, ctx)

    assert "watchlist" not in ctx.artifacts
    assert outcome.metrics is not None
    assert "watchlist_count" not in outcome.metrics
    assert outcome.status == WorkflowStatus.SUCCESS


@pytest.mark.asyncio
async def test_watchlist_empty_config_silent_skip() -> None:
    ctx = WorkflowConfig(provider_configs={"watchlist": {}})
    outcome = await PreMarketPhase(cfg=ctx).execute(_TRADING_DAY, WorkflowContext())

    assert "watchlist" not in outcome.metrics


@pytest.mark.asyncio
async def test_watchlist_missing_file_fails_open(
    tmp_path, caplog: pytest.LogCaptureFixture
) -> None:
    missing = str(tmp_path / "nope.txt")
    ctx = WorkflowContext()
    outcome = await PreMarketPhase(cfg=_cfg_with_path(missing)).execute(
        _TRADING_DAY, ctx
    )

    assert ctx.artifacts["watchlist"] == []
    assert outcome.metrics is not None
    assert outcome.metrics["watchlist_count"] == 0
    assert outcome.status == WorkflowStatus.SUCCESS  # fail-open, no crash
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warnings  # warning recorded


@pytest.mark.asyncio
async def test_watchlist_hot_reload_picks_up_file_edits(tmp_path) -> None:
    """Re-running execute() re-reads the file — edits apply without restart."""
    path = _write_watchlist(tmp_path, "000001.SZ\n")
    ctx = WorkflowContext()
    phase = PreMarketPhase(cfg=_cfg_with_path(path))

    await phase.execute(_TRADING_DAY, ctx)
    assert ctx.artifacts["watchlist"] == ["000001.SZ"]

    path2 = _write_watchlist(tmp_path, "000001.SZ\n600519.SH\n")
    assert path2 == path  # same file, edited in place

    await phase.execute(_TRADING_DAY, ctx)  # second cycle, same ctx
    assert ctx.artifacts["watchlist"] == ["000001.SZ", "600519.SH"]


@pytest.mark.asyncio
async def test_watchlist_failure_does_not_break_macro_quality() -> None:
    """Missing watchlist file must not perturb macro data_quality."""
    ctx = WorkflowContext()
    cfg = WorkflowConfig(provider_configs={"watchlist": {"path": "<local-path-redacted>"}})
    outcome = await PreMarketPhase(cfg=cfg).execute(_TRADING_DAY, ctx)

    # No provider -> macro indicators empty -> quality missing (unchanged rule)
    assert outcome.metrics is not None
    assert outcome.metrics["data_quality"] == "missing"
    assert outcome.metrics["watchlist_count"] == 0
