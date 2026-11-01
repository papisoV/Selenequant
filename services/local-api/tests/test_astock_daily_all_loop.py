"""Offline coverage for the whole-market daily-base loop (B1/B2).

The loop advances ``("astock","daily_all","1d")`` once per trading day after the
close. Its two failure modes are the ones worth pinning, because both are
*silent*:

* **Running when not due.** The pass walks 5574 symbols serially — 10–20
  minutes. A scheduler that refetches the whole market on every restart, or
  before the bar is published, is expensive and produces nothing.
* **Not running when due.** A process that starts after the close must catch
  up immediately. A naive "sleep until tomorrow's 15:05" skips a session
  entirely, and the partition is then permanently one day short until someone
  notices.

None of this touches the network: ``is_due`` / ``sync_eod`` are stubbed, so the
test pins the loop's *decisions*, not the fetch.
"""

from __future__ import annotations

import asyncio

import pytest

from app.main import _astock_daily_all_loop


class _Manager:
    """Stands in for KlineDataManager; the loop only reads ``_db`` off it."""

    def __init__(self, db: object) -> None:
        self._db = db


@pytest.mark.asyncio
async def test_no_kline_manager_returns_without_looping() -> None:
    # Nothing to write to; a retry loop would just log forever.
    await asyncio.wait_for(_astock_daily_all_loop({}), timeout=1.0)
    await asyncio.wait_for(_astock_daily_all_loop({"kline_manager": None}), timeout=1.0)


@pytest.mark.asyncio
async def test_manager_without_db_returns() -> None:
    await asyncio.wait_for(
        _astock_daily_all_loop({"kline_manager": object()}), timeout=1.0
    )


@pytest.mark.asyncio
async def test_not_due_sleeps_instead_of_fetching(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An up-to-date partition must not trigger a 10–20 minute market walk."""
    import selene_space_ashare.daily_all as daily_all

    syncs: list[object] = []
    monkeypatch.setattr(daily_all, "is_due", lambda db, now: False)
    monkeypatch.setattr(daily_all, "sync_eod", lambda db: syncs.append(db))

    task = asyncio.create_task(
        _astock_daily_all_loop({"kline_manager": _Manager(object())})
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert syncs == []  # slept on the "next EOD" path, never fetched


@pytest.mark.asyncio
async def test_due_runs_the_pass_and_logs_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Due → one pass, with the report surfaced rather than swallowed."""
    import selene_space_ashare.daily_all as daily_all

    reports: list[object] = []

    class _Report:
        stale = False

        def summary(self) -> str:
            return "stub summary"

    def _sync(db, **kwargs):
        reports.append(db)
        return _Report()

    calls = {"due": 0}

    def _is_due(db, now):
        calls["due"] += 1
        # Due once, then current — so the second iteration takes the sleep path
        # and the loop parks instead of hammering the market.
        return calls["due"] == 1

    monkeypatch.setattr(daily_all, "is_due", _is_due)
    monkeypatch.setattr(daily_all, "sync_eod", _sync)

    task = asyncio.create_task(
        _astock_daily_all_loop({"kline_manager": _Manager(object())})
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(reports) == 1


@pytest.mark.asyncio
async def test_failure_is_retried_not_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed pass must back off and retry, not end the loop."""
    import selene_space_ashare.daily_all as daily_all

    attempts = {"n": 0}

    def _sync(db, **kwargs):
        attempts["n"] += 1
        raise RuntimeError("sina down")

    monkeypatch.setattr(daily_all, "is_due", lambda db, now: True)
    monkeypatch.setattr(daily_all, "sync_eod", _sync)
    monkeypatch.setattr("app.main._ASTOCK_DAILY_ALL_BACKOFF_SEC", 0.01)

    task = asyncio.create_task(
        _astock_daily_all_loop({"kline_manager": _Manager(object())})
    )
    await asyncio.sleep(0.2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert attempts["n"] >= 2  # kept retrying


@pytest.mark.asyncio
async def test_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Teardown cancels this task; the loop must not swallow CancelledError."""
    import selene_space_ashare.daily_all as daily_all

    def _sync(db, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(daily_all, "is_due", lambda db, now: True)
    monkeypatch.setattr(daily_all, "sync_eod", _sync)
    monkeypatch.setattr("app.main._ASTOCK_DAILY_ALL_BACKOFF_SEC", 3600.0)

    task = asyncio.create_task(
        _astock_daily_all_loop({"kline_manager": _Manager(object())})
    )
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
