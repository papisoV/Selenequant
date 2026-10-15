"""Offline coverage for the live ladder warm-up loop.

The first ``/ashare/phase`` request pays the whole 10jqka endpoint walk —
measured 2026-10-15 at 18.5s on a cold process with a warm disk (66.3s when the
on-disk caches are bypassed). The frontend aborts at 12s, so without a
background warm the board is empty on the first load after every restart.

Pins: no snapshot source → no warm (nothing consumes the live panel), a
successful warm calls the *same memo key* the endpoint uses and returns instead
of looping, a failing warm retries rather than dying, and cancellation
propagates so the teardown can stop it.
"""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from app.main import _astock_ladder_warmup_loop


def _source() -> object:
    # Any non-None sentinel: the loop only checks presence, it never calls it.
    return object()


@pytest.mark.asyncio
async def test_no_snapshot_source_returns_without_warming() -> None:
    # Opt-out env → no live panel consumer; must not raise or call the walk.
    await asyncio.wait_for(_astock_ladder_warmup_loop({}), timeout=1.0)
    await asyncio.wait_for(
        _astock_ladder_warmup_loop({"astock_snapshot_source": None}), timeout=1.0
    )


@pytest.mark.asyncio
async def test_successful_warm_calls_default_window_and_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The warm must populate the memo the endpoint reads.

    Both endpoints call ``live_ladder_history()`` with no arguments, and the
    memo is keyed by ``days_back`` — so a warm at a different window would be
    useless. Assert both the call shape and that it returns (does not loop).
    """
    import selene_space_ashare.ladder_live as ladder_live

    calls: list[tuple] = []

    def _warm(*args, **kwargs):
        calls.append((args, kwargs))
        return None

    monkeypatch.setattr(ladder_live, "live_ladder_history", _warm)

    await asyncio.wait_for(
        _astock_ladder_warmup_loop({"astock_snapshot_source": _source()}),
        timeout=2.0,
    )
    assert len(calls) == 1  # warmed once, then returned
    # No days_back override, so it hits the default memo key (20) the endpoints use.
    assert calls[0] == ((), {})


@pytest.mark.asyncio
async def test_warm_failure_retries_and_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import selene_space_ashare.ladder_live as ladder_live

    calls = {"n": 0}

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("10jqka down")
        return None

    monkeypatch.setattr(ladder_live, "live_ladder_history", flaky)
    monkeypatch.setattr("app.main._ASTOCK_LADDER_WARMUP_BACKOFF_SEC", 0.01)

    await asyncio.wait_for(
        _astock_ladder_warmup_loop({"astock_snapshot_source": _source()}),
        timeout=2.0,
    )
    assert calls["n"] >= 2  # retried past the failure


@pytest.mark.asyncio
async def test_upstream_outage_is_a_retryable_failure_not_a_successful_warm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unreachable endpoint must not read as a completed warm.

    The regression this pins: ``live_ladder_history`` used to return an empty
    frame on a total outage, so this loop logged "warmed", returned, and never
    retried — leaving the board empty until the next process restart. It now
    raises ``LimitUpDataUnavailable``, which the loop's broad ``except`` treats
    like any other failure and retries with backoff.

    This drives the *real* ``live_ladder_history`` (only its fetchers are
    stubbed) — stubbing ``live_ladder_history`` itself would make the test pass
    under the old code too, since a raise was always retried. The bug was
    specifically the empty-frame path, so the stub has to reproduce it: a
    transport failure raises for a strict caller and returns an empty frame
    otherwise, exactly as the real fetchers do.
    """
    import selene_space_ashare.ladder_live as ladder_live
    from selene_space_ashare.ths_data import LimitUpDataUnavailable

    real = ladder_live.live_ladder_history
    walks = {"n": 0}

    def _counted(*args, **kwargs):
        # Count loop iterations, not fetcher calls: the negative cache makes a
        # retry cheap (it re-raises from the memo without the walk), so the
        # count that proves "kept retrying" is of this function.
        walks["n"] += 1
        return real(*args, **kwargs)

    def _outage_fetcher(date="", force=False, strict=False):
        if strict:
            raise LimitUpDataUnavailable("10jqka answered nothing")
        return pd.DataFrame()

    monkeypatch.setattr(ladder_live, "live_ladder_history", _counted)
    monkeypatch.setattr(ladder_live, "fetch_limit_up_pool_full", _outage_fetcher)
    monkeypatch.setattr(ladder_live, "fetch_continuous_limit_up", _outage_fetcher)
    monkeypatch.setattr(ladder_live, "_closed_sessions", lambda n, today: ["20241001"])
    monkeypatch.setattr(ladder_live, "_failures", {})
    monkeypatch.setattr("app.main._ASTOCK_LADDER_WARMUP_BACKOFF_SEC", 0.01)

    with pytest.raises(asyncio.TimeoutError):
        # It must keep retrying, so it never returns within the window — the old
        # empty-frame behavior returned after a single call.
        await asyncio.wait_for(
            _astock_ladder_warmup_loop({"astock_snapshot_source": _source()}),
            timeout=0.5,
        )
    assert walks["n"] >= 2  # the outage did not end the warm-up


@pytest.mark.asyncio
async def test_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    # Teardown cancels this task; the loop must not swallow CancelledError.
    import selene_space_ashare.ladder_live as ladder_live

    monkeypatch.setattr(
        ladder_live,
        "live_ladder_history",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")),
    )
    monkeypatch.setattr("app.main._ASTOCK_LADDER_WARMUP_BACKOFF_SEC", 3600.0)

    task = asyncio.create_task(
        _astock_ladder_warmup_loop({"astock_snapshot_source": _source()})
    )
    await asyncio.sleep(0.05)  # let it reach the backoff sleep
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
