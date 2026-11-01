"""The daily bar is restated, not appended — regression coverage.

Two bugs hang off one fact: a daily bar for *today* is published from the
opening bell with the price so far, and after the close the same session date
comes back finalised. Both the poll loop and the incremental sync used to dedupe
on the timestamp alone, so the post-close restatement — the one update that
matters — was discarded every time, and the intraday snapshot became the
permanent record for that session.

Every test names the mutation it is meant to catch.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import patch

import pytest

from selene_space_core.session_clock import CST
from selene_space_market.kline_manager import KlineDataManager
from selene_space_market.sources.astock_source import AstockSource, _parse_rows
from selene_space_types.market import KlineEvent

_PAST = "2026-09-30"  # a settled session: the flag must be True for it at any clock


def _sina_rows(day: str = _PAST, *, close: str = "10.5", volume: str = "12345"):
    return [
        {
            "day": day,
            "open": "10.0",
            "high": "11.0",
            "low": "9.5",
            "close": close,
            "volume": volume,
        }
    ]


async def _run_passes(src, *, passes: int = 1) -> None:
    """Drive ``src._poll_loop`` for exactly ``passes`` iterations, then stop."""
    src._running = True
    done = {"n": 0}
    real_sleep = asyncio.sleep

    async def _sleep(_sec):
        done["n"] += 1
        if done["n"] >= passes:
            src._running = False
        await real_sleep(0)

    with patch(
        "selene_space_market.sources.astock_source.asyncio.sleep", side_effect=_sleep
    ):
        task = asyncio.create_task(src._poll_loop())
        try:
            await asyncio.wait_for(task, timeout=5)
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# _parse_rows: the flag the whole mechanism hangs on


class TestParseRowsClosedVerdict:
    def test_a_settled_session_is_closed(self) -> None:
        events = _parse_rows(_sina_rows(_PAST), "600519.SH")
        assert events and events[0].closed

    def test_todays_row_before_the_close_is_a_snapshot(self) -> None:
        """Catches: the old unconditional ``closed=True``, which let strategy
        runtimes act on a half-formed bar."""
        events = _parse_rows(
            _sina_rows("2026-10-22"),
            "600519.SH",
            as_of=datetime(2026, 10, 22, 10, 0, tzinfo=CST),
        )
        assert events and not events[0].closed

    def test_todays_row_after_the_close_is_final(self) -> None:
        """The flip itself: same session date, same parser, different verdict."""
        events = _parse_rows(
            _sina_rows("2026-10-22"),
            "600519.SH",
            as_of=datetime(2026, 10, 22, 15, 30, tzinfo=CST),
        )
        assert events and events[0].closed

    def test_a_mixed_tail_judges_each_row_separately(self) -> None:
        """Catches: hoisting the verdict out of the loop and applying one flag
        to the whole response — which is what makes the snapshot final again."""
        rows = _sina_rows("2026-10-21") + _sina_rows("2026-10-22")
        events = _parse_rows(
            rows, "600519.SH", as_of=datetime(2026, 10, 21, 15, 30, tzinfo=CST)
        )
        by_day = {
            datetime.fromtimestamp(e.timestamp / 1000, tz=CST).date(): e.closed
            for e in events
        }
        assert by_day[datetime(2026, 10, 21).date()] is True
        assert by_day[datetime(2026, 10, 22).date()] is False


# ---------------------------------------------------------------------------
# Poll loop: the restatement must reach subscribers


@pytest.mark.asyncio
async def test_poll_republishes_the_restated_bar() -> None:
    """Catches: deduping on ``timestamp <= last_ts`` — the exact guard that
    swallowed the post-close row, leaving the snapshot as the last word."""
    src = AstockSource()
    received: list[KlineEvent] = []
    src.on_kline(received.append)
    await src.subscribe("600519.SH")

    snapshot = _parse_rows(
        _sina_rows("2026-10-22", close="10.2"),
        "600519.SH",
        as_of=datetime(2026, 10, 22, 10, 0, tzinfo=CST),
    )
    final = _parse_rows(
        _sina_rows("2026-10-22", close="10.9", volume="99999"),
        "600519.SH",
        as_of=datetime(2026, 10, 22, 15, 30, tzinfo=CST),
    )
    assert snapshot[0].timestamp == final[0].timestamp  # same session key

    with patch.object(src, "_fetch_sina", side_effect=[snapshot, final]):
        await _run_passes(src, passes=2)

    assert [e.close for e in received] == [10.2, 10.9]
    assert [e.closed for e in received] == [False, True]
    # The cache/history must hold the finalised bar, not the snapshot.
    assert src.get_kline("600519.SH").close == 10.9


@pytest.mark.asyncio
async def test_poll_does_not_republish_an_unchanged_bar() -> None:
    """Catches: dropping the payload comparison and publishing every tick."""
    src = AstockSource()
    received: list[KlineEvent] = []
    src.on_kline(received.append)
    await src.subscribe("600519.SH")

    same = _parse_rows(_sina_rows(), "600519.SH")
    with patch.object(src, "_fetch_sina", return_value=same):
        await _run_passes(src, passes=3)

    assert len(received) == 1


@pytest.mark.asyncio
async def test_poll_history_replaces_rather_than_stacks() -> None:
    """Catches: ``history.append`` on a restatement, which puts two versions of
    one session into the windowed read and double-counts it."""
    src = AstockSource()
    await src.subscribe("600519.SH")

    first = _parse_rows(_sina_rows(close="10.2"), "600519.SH")
    second = _parse_rows(_sina_rows(close="10.9"), "600519.SH")

    with patch.object(src, "_fetch_sina", side_effect=[first, second]):
        await _run_passes(src, passes=2)

    history = src.get_klines("600519.SH")
    assert len(history) == 1
    assert history[0].close == 10.9


# ---------------------------------------------------------------------------
# Incremental sync: the store path must accept the restatement too


def _bar(ts: int, *, close: float, closed: bool = True) -> KlineEvent:
    return KlineEvent(
        exchange="astock",
        market_type="spot",  # type: ignore[arg-type]
        symbol="600519.SH",  # type: ignore[arg-type]
        timestamp=ts,
        interval="1d",
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=1000.0,
        closed=closed,
    )


class TestIncrementalSyncRestatement:
    @pytest.fixture
    def manager(self) -> KlineDataManager:
        return KlineDataManager(":memory:")

    @staticmethod
    def _session_ts(day: int) -> int:
        return int(datetime(2026, 10, day, tzinfo=CST).timestamp() * 1000)

    @pytest.mark.asyncio
    async def test_same_timestamp_with_new_close_is_written(
        self, manager: KlineDataManager
    ) -> None:
        """Catches: the original ``k.timestamp > latest_ts`` filter, which made
        the snapshot permanent by rejecting its own restatement."""
        ts = self._session_ts(20)
        manager._db.put_klines([_bar(ts, close=10.2, closed=False)])

        restated = [_bar(ts, close=10.9, closed=True)]
        service = _service(restated)
        with patch.object(manager, "_get_service", return_value=service):
            written = await manager.sync_incremental(
                "astock", "spot", "600519.SH", "1d"
            )

        assert written == 1
        stored = manager._db.get_bar("astock", "spot", "600519.SH", "1d", ts)
        assert stored is not None
        assert stored["close"] == 10.9
        assert stored["closed"] is True

    @pytest.mark.asyncio
    async def test_same_timestamp_with_identical_payload_is_skipped(
        self, manager: KlineDataManager
    ) -> None:
        """Catches: writing the boundary bar every sync regardless of change."""
        ts = self._session_ts(20)
        manager._db.put_klines([_bar(ts, close=10.9, closed=True)])

        service = _service([_bar(ts, close=10.9, closed=True)])
        with patch.object(manager, "_get_service", return_value=service):
            written = await manager.sync_incremental(
                "astock", "spot", "600519.SH", "1d"
            )

        assert written == 0

    @pytest.mark.asyncio
    async def test_a_flip_to_closed_alone_counts_as_a_change(
        self, manager: KlineDataManager
    ) -> None:
        """The normal daily path: OHLCV identical, only the flag moves."""
        ts = self._session_ts(20)
        manager._db.put_klines([_bar(ts, close=10.9, closed=False)])

        service = _service([_bar(ts, close=10.9, closed=True)])
        with patch.object(manager, "_get_service", return_value=service):
            written = await manager.sync_incremental(
                "astock", "spot", "600519.SH", "1d"
            )

        assert written == 1
        stored = manager._db.get_bar("astock", "spot", "600519.SH", "1d", ts)
        assert stored is not None and stored["closed"] is True


def _service(candles: list[KlineEvent]):
    """A stand-in for ``MarketDataService`` that honours the pagination window.

    A mock returning the full list for every call makes ``sync_incremental``'s
    deep-history phase look like it re-fetched the boundary bar, which buries
    the property under test. What the real endpoint does is return bars
    *strictly below* ``end_time``; mirroring that keeps the two phases distinct.
    """
    from unittest.mock import AsyncMock, MagicMock

    ordered = sorted(candles, key=lambda k: k.timestamp)

    async def _fetch(*, limit: int = 200, end_time: int | None = None, **_kwargs):
        below = [k for k in ordered if end_time is None or k.timestamp < end_time]
        return below[-limit:]

    svc = MagicMock()
    svc.fetch_klines_rest = AsyncMock(side_effect=_fetch)
    return svc
