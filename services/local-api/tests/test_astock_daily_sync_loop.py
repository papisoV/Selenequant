"""Offline coverage for the A-share daily refresh loop (方案 B).

The loop exists to keep the live ("astock","spot","1d") partition current so the
anomaly detector's N-day/volume baseline is not read from the frozen qfq import.
It must: skip cleanly with no manager / no symbols, drive sync_incremental once
per symbol at the expected partition+interval, and survive a manager that
raises (backing off instead of dying).
"""

from __future__ import annotations

import asyncio

import pytest

from app.main import _astock_daily_sync_loop


class _RecordingManager:
    """Stub with the one surface the loop touches."""

    def __init__(self, *, fail: bool = False, count: int = 1) -> None:
        self.calls: list[tuple[str, str, str, str, int]] = []
        self._fail = fail
        self._count = count
        self.entered = asyncio.Event()

    async def sync_incremental(
        self, exchange: str, market_type: str, symbol: str, interval: str, days: int
    ) -> int:
        self.calls.append((exchange, market_type, symbol, interval, days))
        if self._count >= len(self.calls):
            self.entered.set()
        if self._fail:
            raise RuntimeError("boom")
        return 1


@pytest.mark.asyncio
async def test_missing_manager_returns_without_raising() -> None:
    # No manager in state → the loop logs and returns; it must not raise or spin.
    await asyncio.wait_for(_astock_daily_sync_loop({}, ["600519.SH"]), timeout=1.0)


@pytest.mark.asyncio
async def test_empty_symbols_returns_without_raising() -> None:
    await asyncio.wait_for(
        _astock_daily_sync_loop({"kline_manager": _RecordingManager()}, []),
        timeout=1.0,
    )


@pytest.mark.asyncio
async def test_syncs_each_symbol_on_spot_1d(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = _RecordingManager(count=2)
    monkeypatch.setattr("app.main._ASTOCK_DAILY_SYNC_INTERVAL", 0.01)
    task = asyncio.create_task(
        _astock_daily_sync_loop({"kline_manager": manager}, ["600519.SH", "000001.SZ"])
    )
    try:
        await asyncio.wait_for(manager.entered.wait(), timeout=2.0)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert manager.calls[:2] == [
        ("astock", "spot", "600519.SH", "1d", 365),
        ("astock", "spot", "000001.SZ", "1d", 365),
    ]


@pytest.mark.asyncio
async def test_manager_failure_backs_off_and_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A raising manager must not kill the loop: it records the failure and keeps
    # ticking (backoff), so a recovered manager would still be driven later.
    manager = _RecordingManager(fail=True, count=1)
    monkeypatch.setattr("app.main._ASTOCK_DAILY_SYNC_INTERVAL", 0.01)
    task = asyncio.create_task(
        _astock_daily_sync_loop({"kline_manager": manager}, ["x"])
    )
    try:
        await asyncio.wait_for(manager.entered.wait(), timeout=2.0)
        # Give the backoff tick a moment to fire a *second* attempt.
        for _ in range(50):
            if len(manager.calls) >= 2:
                break
            await asyncio.sleep(0.02)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert len(manager.calls) >= 2
