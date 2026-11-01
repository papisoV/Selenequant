"""The gap sweep's domain gate: only 24/7 venues may be judged on contiguity.

The loop used to hand ``sweep_once`` every registered source. On an equity feed
that turns every overnight/weekend/holiday break into a "repairable hole"
(measured on the live store: AAPL yahoo/us_stock/1h 391 holes / 10,950 bars).
The sweep must therefore select sources through ``continuous_market``, not
merely the source registry — and the selection must be re-derived every round,
since sources can be added at runtime.
"""

from __future__ import annotations

import asyncio

import pytest

from app.main import _kline_gap_sweep_loop


class _SweepRecorder:
    def __init__(self) -> None:
        self.allowed: list[set[tuple[str, str]]] = []
        self.entered = asyncio.Event()

    async def sweep_once(self, *, allowed_sources, quality_checker) -> object:  # type: ignore[no-untyped-def]
        self.allowed.append(set(allowed_sources))
        self.entered.set()

        class _Report:
            partitions_scanned = 0
            holes_found = 0
            holes_filled = 0
            holes_permanent = 0
            partitions_advanced = 0
            quality: dict = {}
            warnings: list = []

        return _Report()


class _MarketSvc:
    def __init__(self, sources) -> None:  # type: ignore[no-untyped-def]
        self.sources = sources


@pytest.mark.asyncio
async def test_missing_manager_returns_without_raising() -> None:
    await asyncio.wait_for(_kline_gap_sweep_loop({}), timeout=1.0)


@pytest.mark.asyncio
async def test_equity_sources_are_excluded_from_the_sweep(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _SweepRecorder()
    state = {
        "kline_manager": manager,
        "market_svc": _MarketSvc(
            [
                ("binance", "spot"),
                ("binance", "futures"),
                ("okx", "futures"),
                ("yahoo", "us_stock"),
                ("astock", "spot"),
                ("astock", "market_snapshot"),
            ]
        ),
    }
    monkeypatch.setattr("app.main._KLINE_SWEEP_INTERVAL", 0.01)
    task = asyncio.create_task(_kline_gap_sweep_loop(state))
    try:
        await asyncio.wait_for(manager.entered.wait(), timeout=2.0)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert manager.allowed[0] == {
        ("binance", "spot"),
        ("binance", "futures"),
        ("okx", "futures"),
    }
