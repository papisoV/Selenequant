# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: time-budgeted
# context-block orchestration over the existing ashare fetchers, with
# fail-open semantics and per-block coverage reporting.
"""A-share context block orchestration for LLM consumption.

Fetchers (data_fetcher / ths_data) return raw DataFrames; this module wires
them into named "context blocks" executed under a wall-clock budget:

- Every block runs in its own try/except; a failing block never propagates.
- Empty results (None / empty DataFrame) are reported as ``empty``, not errors.
- Once the accumulated elapsed time exceeds the budget, remaining blocks are
  marked ``skipped`` and their runners are never invoked. ``budget_seconds
  <= 0`` means unlimited.

The output is designed to be attached to a prompt: check ``coverage`` first,
then read ``blocks[name].data`` only for blocks whose status is ``ok``.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from selene_space_ashare.data_fetcher import (
    get_fund_flow,
    get_lhb,
    get_margin_data,
    get_northbound_flow,
    get_stock_pe_pb,
)
from selene_space_ashare.ths_data import fetch_limit_up_pool_full

logger = logging.getLogger(__name__)

# Block lifecycle states.
BLOCK_OK = "ok"
BLOCK_EMPTY = "empty"
BLOCK_FAILED = "failed"
BLOCK_SKIPPED = "skipped"

_FUND_FLOW_RECENT_N = 5
_FUND_FLOW_SUM_COLS = ("主力净流入-净额", "超大单净流入-净额", "大单净流入-净额")


@dataclass(frozen=True)
class BlockResult:
    """Outcome of one context block execution."""

    name: str
    status: str
    data: Any
    elapsed_ms: float
    error: str | None = None


@dataclass
class BlockSpec:
    """A named unit of work; ``runner`` returns the raw block payload."""

    name: str
    runner: Callable[[], Any]
    metadata: dict[str, Any] = field(default_factory=dict)


def _is_empty(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, pd.DataFrame):
        return bool(value.empty)
    return False


def run_blocks(
    specs: list[BlockSpec],
    *,
    budget_seconds: float,
    now_fn: Callable[[], float] = time.monotonic,
) -> dict[str, BlockResult]:
    """Run blocks sequentially under a wall-clock budget; never raises.

    Blocks execute in input order. Once accumulated elapsed time exceeds
    ``budget_seconds``, remaining blocks are reported as ``skipped`` without
    invoking their runners. ``budget_seconds <= 0`` means unlimited.
    """
    results: dict[str, BlockResult] = {}
    unlimited = budget_seconds <= 0
    consumed = 0.0

    for spec in specs:
        if not unlimited and consumed >= budget_seconds:
            results[spec.name] = BlockResult(
                name=spec.name,
                status=BLOCK_SKIPPED,
                data=None,
                elapsed_ms=0.0,
                error=None,
            )
            continue

        started = now_fn()
        try:
            data = spec.runner()
            elapsed_ms = (now_fn() - started) * 1000.0
            consumed += elapsed_ms / 1000.0
            status = BLOCK_EMPTY if _is_empty(data) else BLOCK_OK
            results[spec.name] = BlockResult(
                name=spec.name,
                status=status,
                data=data,
                elapsed_ms=elapsed_ms,
                error=None,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open by contract
            elapsed_ms = (now_fn() - started) * 1000.0
            consumed += elapsed_ms / 1000.0
            logger.warning("context block %r failed: %s", spec.name, exc, exc_info=True)
            results[spec.name] = BlockResult(
                name=spec.name,
                status=BLOCK_FAILED,
                data=None,
                elapsed_ms=elapsed_ms,
                error=str(exc),
            )

    return results


def coverage_of(results: dict[str, BlockResult]) -> dict[str, str]:
    """Summarize block results as ``{name: status}`` in input order."""
    return {name: result.status for name, result in results.items()}


def _sum_recent(df: pd.DataFrame, col: str, n: int) -> float | None:
    """Sum the last ``n`` numeric values of ``col``; None when unavailable."""
    if col not in df.columns:
        return None
    series = pd.to_numeric(df[col], errors="coerce").dropna().tail(n)
    if series.empty:
        return None
    return float(series.sum())


def capital_flow_block(symbol: str) -> BlockSpec:
    """Recent main/super/large-order net inflow, aggregated over the last N days."""

    def runner() -> Any:
        df = get_fund_flow(symbol)
        if df.empty:
            return None
        summary: dict[str, Any] = {"days": min(_FUND_FLOW_RECENT_N, len(df))}
        for col in _FUND_FLOW_SUM_COLS:
            summary[col] = _sum_recent(df, col, _FUND_FLOW_RECENT_N)
        return summary

    return BlockSpec(name="capital_flow", runner=runner)


def dragon_tiger_block(date: str = "") -> BlockSpec:
    """龙虎榜 (Dragon-Tiger list) for a trading day; empty date = latest."""
    return BlockSpec(name="dragon_tiger", runner=lambda: get_lhb(date))


def limit_up_pool_block(date: str = "") -> BlockSpec:
    """Full 涨停池 (limit-up pool) for a date, all pages."""
    return BlockSpec(
        name="limit_up_pool", runner=lambda: fetch_limit_up_pool_full(date)
    )


def valuation_block(symbol: str) -> BlockSpec:
    """Daily PE/PB history for a single stock."""
    return BlockSpec(name="valuation", runner=lambda: get_stock_pe_pb(symbol))


def northbound_block() -> BlockSpec:
    """Northbound (北向资金) daily flow history."""
    return BlockSpec(name="northbound", runner=get_northbound_flow)


def margin_block(symbol: str) -> BlockSpec:
    """Margin trading (融资融券) data for a stock."""
    return BlockSpec(name="margin", runner=lambda: get_margin_data(symbol))


def default_blocks(symbol: str, date: str = "") -> list[BlockSpec]:
    """The standard A-share context block set for one symbol."""
    return [
        capital_flow_block(symbol),
        dragon_tiger_block(date),
        limit_up_pool_block(date),
        valuation_block(symbol),
        northbound_block(),
        margin_block(symbol),
    ]


def build_astock_context(
    symbol: str,
    *,
    date: str = "",
    specs: list[BlockSpec] | None = None,
    budget_seconds: float = 15.0,
) -> dict[str, Any]:
    """Build the LLM-consumable context payload for one A-share symbol.

    Returns ``{"symbol", "blocks", "coverage", "budget_seconds"}``; never
    raises — fetcher failures surface as per-block ``failed`` statuses.
    """
    block_specs = specs if specs is not None else default_blocks(symbol, date)
    results = run_blocks(block_specs, budget_seconds=budget_seconds)
    return {
        "symbol": symbol,
        "blocks": results,
        "coverage": coverage_of(results),
        "budget_seconds": budget_seconds,
    }
