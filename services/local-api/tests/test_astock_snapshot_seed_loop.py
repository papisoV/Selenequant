"""Offline coverage for the whole-market snapshot universe seed loop (阶段 0).

The snapshot source registers with an *empty* universe — it cannot import the
ashare package that owns the code list, so the composition layer seeds it after
startup. Without this loop the source polls nothing and the cross-section stays
empty: a registered-but-inert source, which is the failure mode this file
exists to prevent.

Pins: absent source returns cleanly, a successful seed sets the universe and
exits, bad/empty universes are rejected rather than seeded, and a failing fetch
retries instead of dying — so a cold cache cannot leave the source idle forever.
"""

from __future__ import annotations

import asyncio

import pandas as pd
import pytest

from app.main import _astock_snapshot_seed_loop
from selene_space_market.sources.astock_market_snapshot_source import (
    AstockMarketSnapshotSource,
)


def _universe(*codes: str) -> pd.DataFrame:
    return pd.DataFrame({"code": list(codes), "name": ["x"] * len(codes)})


@pytest.mark.asyncio
async def test_missing_source_returns_without_raising() -> None:
    # No source registered (opt-out env) → nothing to seed; must not raise/spin.
    await asyncio.wait_for(_astock_snapshot_seed_loop({}), timeout=1.0)


@pytest.mark.asyncio
async def test_successful_seed_sets_universe_and_returns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = AstockMarketSnapshotSource()
    assert src.get_symbols() == []  # starts inert

    import selene_space_ashare.data_fetcher as fetcher

    monkeypatch.setattr(
        fetcher, "get_a_share_list", lambda: _universe("600519", "000001", "920819")
    )

    # Returns (does not loop) once seeded.
    await asyncio.wait_for(
        _astock_snapshot_seed_loop({"astock_snapshot_source": src}), timeout=2.0
    )
    assert sorted(src.get_symbols()) == ["000001.SZ", "600519.SH", "920819.BJ"]


@pytest.mark.asyncio
async def test_non_numeric_and_short_codes_are_filtered(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = AstockMarketSnapshotSource()
    import selene_space_ashare.data_fetcher as fetcher

    # The endpoint's list carries names/odd entries; only 6-digit codes are
    # pollable, and _sina_prefix would raise on anything else.
    monkeypatch.setattr(
        fetcher,
        "get_a_share_list",
        lambda: _universe("600519", "BADCODE", "12345", "000001"),
    )
    await asyncio.wait_for(
        _astock_snapshot_seed_loop({"astock_snapshot_source": src}), timeout=2.0
    )
    assert sorted(src.get_symbols()) == ["000001.SZ", "600519.SH"]


@pytest.mark.asyncio
async def test_empty_universe_retries_instead_of_seeding_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # An empty fetch must NOT be accepted as a successful seed — that would
    # leave the source permanently polling nothing while looking healthy.
    src = AstockMarketSnapshotSource()
    import selene_space_ashare.data_fetcher as fetcher

    calls = {"n": 0}

    def flaky() -> pd.DataFrame:
        calls["n"] += 1
        if calls["n"] == 1:
            return _universe()  # empty → rejected
        return _universe("600519")

    monkeypatch.setattr(fetcher, "get_a_share_list", flaky)
    monkeypatch.setattr("app.main._ASTOCK_SNAPSHOT_SEED_BACKOFF_SEC", 0.01)

    await asyncio.wait_for(
        _astock_snapshot_seed_loop({"astock_snapshot_source": src}), timeout=2.0
    )
    assert calls["n"] >= 2  # retried past the empty result
    assert sorted(src.get_symbols()) == ["600519.SH"]


@pytest.mark.asyncio
async def test_fetch_failure_retries_and_survives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    src = AstockMarketSnapshotSource()
    import selene_space_ashare.data_fetcher as fetcher

    calls = {"n": 0}

    def boom() -> pd.DataFrame:
        calls["n"] += 1
        if calls["n"] < 2:
            raise RuntimeError("akshare down")
        return _universe("600519")

    monkeypatch.setattr(fetcher, "get_a_share_list", boom)
    monkeypatch.setattr("app.main._ASTOCK_SNAPSHOT_SEED_BACKOFF_SEC", 0.01)

    await asyncio.wait_for(
        _astock_snapshot_seed_loop({"astock_snapshot_source": src}), timeout=2.0
    )
    assert calls["n"] >= 2
    assert sorted(src.get_symbols()) == ["600519.SH"]


@pytest.mark.asyncio
async def test_cancellation_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    # Shutdown cancels this task; the loop must not swallow CancelledError.
    # A permanently-failing fetch keeps it inside the retry sleep, where the
    # cancel lands.
    src = AstockMarketSnapshotSource()
    import selene_space_ashare.data_fetcher as fetcher

    monkeypatch.setattr(
        fetcher, "get_a_share_list", lambda: (_ for _ in ()).throw(RuntimeError("x"))
    )
    monkeypatch.setattr("app.main._ASTOCK_SNAPSHOT_SEED_BACKOFF_SEC", 3600.0)

    task = asyncio.create_task(
        _astock_snapshot_seed_loop({"astock_snapshot_source": src})
    )
    await asyncio.sleep(0.05)  # let it reach the backoff sleep
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
