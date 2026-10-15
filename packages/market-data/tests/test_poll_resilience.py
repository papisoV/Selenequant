"""PollBackoff + poll-source backoff/staleness integration.

Pattern absorbed from marketingdashboard (MIT) — see poll_backoff.py docstring
and PROVENANCE.md. All tests are offline: fetches are mocked.
"""

from __future__ import annotations

import asyncio
import logging
import os as _os
from unittest.mock import patch

import pytest

from selene_space_market.poll_backoff import PollBackoff
from selene_space_market.sources.astock_source import (
    _POLL_INTERVAL_SEC as _ASTOCK_BASE,
)
from selene_space_market.sources.astock_source import AstockSource, _parse_rows
from selene_space_market.sources.us_stock_source import USStockSource


# ---------------------------------------------------------------------------
# Helpers


async def _run_passes(src, module: str, *, passes: int = 1) -> None:
    """Drive *src*._poll_loop for exactly *passes* iterations, then stop.

    Patches the module's asyncio.sleep with a counter that flips
    ``src._running`` off after the target pass, so no real waiting happens.
    """
    src._running = True
    done = {"n": 0}
    real_sleep = asyncio.sleep

    async def _sleep(_sec):
        done["n"] += 1
        if done["n"] >= passes:
            src._running = False
        await real_sleep(0)

    with patch(f"{module}.asyncio.sleep", side_effect=_sleep):
        task = asyncio.create_task(src._poll_loop())
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.TimeoutError:
            task.cancel()
            raise
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# PollBackoff unit behavior


def test_backoff_seconds_doubles_and_caps() -> None:
    pb = PollBackoff(60, cap_sec=600, stale_after_sec=300)
    assert pb.backoff_seconds(0) == 60
    assert pb.backoff_seconds(1) == 120
    assert pb.backoff_seconds(2) == 240
    assert pb.backoff_seconds(10) == 600  # capped


def test_blocked_within_window_only() -> None:
    pb = PollBackoff(60, cap_sec=600, stale_after_sec=300)
    assert not pb.blocked("s")  # no failures yet
    pb.record_attempt("s", now=1000.0)
    pb.record_failure("s")
    assert pb.blocked("s", now=1000.0)
    assert pb.blocked("s", now=1119.0)
    assert not pb.blocked("s", now=1120.0)  # window expired (fail#1 → 120s)


def test_success_resets_backoff_and_stale_flag() -> None:
    pb = PollBackoff(60, cap_sec=600, stale_after_sec=300)
    pb.record_attempt("s", now=0.0)
    pb.record_failure("s")
    pb.record_failure("s")
    pb.record_success("s", now=1.0)
    assert pb.fail_count("s") == 0
    assert not pb.blocked("s", now=2.0)
    assert pb.staleness_seconds("s", now=10.0) == 9
    assert "s" not in pb._stale_warned  # episode cleared


def test_staleness_none_before_first_success() -> None:
    pb = PollBackoff(60, cap_sec=600, stale_after_sec=300)
    assert pb.staleness_seconds("s") is None


def test_warn_if_stale_warns_once_per_episode(caplog: pytest.LogCaptureFixture) -> None:
    pb = PollBackoff(60, cap_sec=600, stale_after_sec=300)
    log = logging.getLogger("test.pb")
    pb.record_success("s")  # real monotonic now
    with caplog.at_level(logging.WARNING, logger="test.pb"):
        pb.warn_if_stale("s", log, "L")  # fresh — silent
    assert not caplog.records
    # simulate age: rewind last success past the threshold
    pb._last_success_at["s"] -= pb._stale_after + 5
    with caplog.at_level(logging.WARNING, logger="test.pb"):
        pb.warn_if_stale("s", log, "L")  # warns
        pb.warn_if_stale("s", log, "L")  # suppressed (same episode)
    stale_msgs = [r for r in caplog.records if "stale" in r.getMessage()]
    assert len(stale_msgs) == 1
    # success clears the episode
    pb.record_success("s")
    assert "s" not in pb._stale_warned


def test_invalid_intervals_rejected() -> None:
    with pytest.raises(ValueError):
        PollBackoff(0, cap_sec=600, stale_after_sec=300)
    with pytest.raises(ValueError):
        PollBackoff(60, cap_sec=-1, stale_after_sec=300)


# ---------------------------------------------------------------------------
# Source integration (offline)


def _sina_rows(ts_day: str = "2026-10-02") -> list[dict[str, str]]:
    return [
        {
            "day": ts_day,
            "open": "10.0",
            "high": "11.0",
            "low": "9.5",
            "close": "10.5",
            "volume": "12345",
        }
    ]


@pytest.mark.asyncio
async def test_astock_backoff_skips_failed_symbol() -> None:
    src = AstockSource()
    await src.subscribe("000001.SZ")
    calls: list[str] = []

    def _fail(symbol: str, datalen: int):
        calls.append(symbol)
        raise ConnectionError("down")

    with patch.object(src, "_fetch_sina", side_effect=_fail):
        await _run_passes(src, "selene_space_market.sources.astock_source", passes=2)

    # pass 1 hit upstream and failed; pass 2 was skipped by backoff
    assert len(calls) == 1
    assert src._backoff.fail_count("000001.SZ") == 1
    assert src._backoff.blocked("000001.SZ")


@pytest.mark.asyncio
async def test_astock_success_resets_and_publishes() -> None:
    src = AstockSource()
    received: list = []
    src.on_kline(received.append)
    await src.subscribe("000001.SZ")

    events = _parse_rows(_sina_rows(), "000001.SZ")
    with patch.object(src, "_fetch_sina", return_value=events):
        await _run_passes(src, "selene_space_market.sources.astock_source")

    assert received and received[0].close == 10.5
    assert src._backoff.fail_count("000001.SZ") == 0
    assert src.staleness_seconds("000001.SZ") is not None
    assert src.get_kline("000001.SZ") is not None


@pytest.mark.asyncio
async def test_astock_recovery_after_failures() -> None:
    src = AstockSource()
    await src.subscribe("600519.SH")
    calls: list[str] = []

    def _fail(symbol: str, datalen: int):
        calls.append(symbol)
        raise ConnectionError("down")

    with patch.object(src, "_fetch_sina", side_effect=_fail):
        await _run_passes(src, "selene_space_market.sources.astock_source")

    assert src._backoff.fail_count("600519.SH") == 1
    # expired backoff window → next pass retries and succeeds
    src._backoff._last_attempt_at["600519.SH"] -= src._backoff.backoff_seconds(1) + 1
    events = _parse_rows(_sina_rows(), "600519.SH")
    with patch.object(src, "_fetch_sina", return_value=events):
        await _run_passes(src, "selene_space_market.sources.astock_source")
    assert src._backoff.fail_count("600519.SH") == 0
    assert src.get_kline("600519.SH") is not None


@pytest.mark.asyncio
async def test_astock_staleness_exposed_after_success() -> None:
    src = AstockSource()
    await src.subscribe("600519.SH")
    assert src.staleness_seconds("600519.SH") is None  # never fetched
    events = _parse_rows(_sina_rows(), "600519.SH")
    with patch.object(src, "_fetch_sina", return_value=events):
        await _run_passes(src, "selene_space_market.sources.astock_source")
    age = src.staleness_seconds("600519.SH")
    assert age is not None and age >= 0


@pytest.mark.asyncio
async def test_us_stock_backoff_and_success() -> None:
    src = USStockSource()
    await src.subscribe("AAPL", kline_interval="1d")
    key = "AAPL:1d"

    async def _fail(*a, **kw):
        raise ConnectionError("yahoo down")

    with patch.object(src, "fetch_klines_rest", side_effect=_fail):
        await _run_passes(src, "selene_space_market.sources.us_stock_source")

    assert src._backoff.fail_count(key) == 1
    assert src._backoff.blocked(key)

    # expired backoff window → retry succeeds
    src._backoff._last_attempt_at[key] -= src._backoff.backoff_seconds(1) + 1

    # recovery
    from selene_space_types.market import KlineEvent

    good = [
        KlineEvent(
            exchange="yahoo",
            market_type="us_stock",
            symbol="AAPL",
            timestamp=1_711_000_000_000,
            interval="1d",
            open=1,
            high=2,
            low=0.5,
            close=1.5,
            volume=10,
            closed=True,
        )
    ]
    with patch.object(src, "fetch_klines_rest", return_value=good):
        await _run_passes(src, "selene_space_market.sources.us_stock_source")
    assert src._backoff.fail_count(key) == 0
    assert not src._backoff.blocked(key)
    assert src.get_kline("AAPL") is not None
    assert src.staleness_seconds("AAPL") is not None


def test_backoff_constants_sane() -> None:
    assert _ASTOCK_BASE == 600


def test_parse_rows_stamps_bars_at_cst_midnight() -> None:
    """A daily bar's epoch must be Beijing midnight, not host-local midnight.

    The anomaly detector's settled-bar cutoff keys on the session date derived
    from these timestamps; stamping off local wall time instead would shift
    every bar by the host's UTC offset and slide bars across that boundary.
    """
    from datetime import datetime, timedelta, timezone

    cst = timezone(timedelta(hours=8))
    expected = int(datetime(2026, 10, 2, tzinfo=cst).timestamp() * 1000)
    events = _parse_rows(_sina_rows(), "600519.SH")
    assert len(events) == 1
    assert events[0].timestamp == expected
    # The epoch itself must resolve back to 2026-10-02 00:00 in Beijing.
    back = datetime.fromtimestamp(events[0].timestamp / 1000, tz=cst)
    assert (back.year, back.month, back.day, back.hour, back.minute) == (
        2026,
        10,
        2,
        0,
        0,
    )


def test_parse_rows_is_host_tz_independent() -> None:
    """The stamped epoch must not move with the host timezone.

    On a UTC+8 dev box ``datetime.strptime(...).timestamp()`` (naive → local)
    happens to equal Beijing midnight, so the assertion above passes even with
    the bug present. Forcing the child process into UTC makes the two disagree:
    a host-local stamp becomes UTC midnight (offset by 8h) and this fails.
    """
    import subprocess
    import sys

    snippet = (
        "from selene_space_market.sources.astock_source import _parse_rows;"
        "print(_parse_rows([{'day':'2026-10-02','open':'10.0','high':'11.0',"
        "'low':'9.5','close':'10.5','volume':'12345'}], '600519.SH')[0].timestamp)"
    )
    out = subprocess.run(
        [sys.executable, "-c", snippet],
        env={**_os.environ, "TZ": "UTC"},
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    # 2026-10-02 00:00 Beijing = 2026-10-01 16:00 UTC = 1790870400000 ms.
    assert out.stdout.strip() == "1790870400000"
