"""S2 coverage: the outage ledger is wired to the detector's own store, and a
tick published after an outage still reaches the anomaly detector.

Two seams, both of which only exist in the wiring layer:

* ``wire_astock_intraday_gap_sink`` binds the intraday source's gap sink to
  the *same* :class:`KlineDB` the detector reads — a setter call, because
  ``main.py`` registers sources before it builds the kline store. The
  assertion that matters is that an outage episode provably lands in that
  store, not merely that a callable was attached.
* The source publishes onto the shared bus and the detector subscribes to it;
  an outage between the two must not sever the wire. Driving a real outage
  through the real poll loop and then ticking a limit-up proves no bookkeeping
  was inserted between publisher and subscriber.

Everything is offline: the HTTP fetch is scripted and the clock is injected.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

from selene_space_market.kline_db import KlineDB
from selene_space_market.sources.astock_intraday_source import AstockIntradaySource
from selene_space_types.market import AnomalyEvent, TickerEvent

from app.astock_anomaly_wiring import (
    wire_astock_anomaly_detector,
    wire_astock_intraday_gap_sink,
)

if TYPE_CHECKING:
    from selene_space_market.service import MarketDataService

_CST = timezone(timedelta(hours=8))

# Sina field indices the source reads: 0 name, 2 prev_close, 3 last, 6 bid,
# 7 ask, 8 volume, 30 date, 31 time.
_MIN_FIELDS = 33


def _body(*, last: float, prev_close: float = 10.0, volume: float = 1000.0) -> str:
    """One ``hq_str_sh600519`` line, valid for the source's parser.

    Defaults put the limit-up price at ``prev_close * 1.1`` (main board), so
    ``last=11.0`` is a limit-up tick and ``last=10.0`` is an ordinary one.

    The venue timestamp is stamped at the current instant: the source's
    freshness guard compares it against wall clock, so a body pinned to a fixed
    session hour would (correctly) trip a stale-upstream warning on any run
    later than that and drown the assertions in unrelated noise.
    """
    now = datetime.now(_CST)
    fields = [
        "贵州茅台",
        f"{prev_close:.3f}",
        f"{prev_close:.3f}",
        f"{last:.3f}",
        f"{last + 1:.3f}",
        f"{last - 1:.3f}",
        f"{last - 0.01:.3f}",
        f"{last + 0.01:.3f}",
        f"{volume:.3f}",
    ]
    fields += ["0.000"] * 21  # 9..29 — book depth, unused here
    fields += [
        now.strftime("%Y-%m-%d"),
        now.strftime("%H:%M:%S"),
        "00",
    ]
    assert len(fields) >= _MIN_FIELDS
    return f'var hq_str_sh600519="{",".join(fields)}";'


class _FakeResp:
    status_code = 200

    def __init__(self, text: str) -> None:
        self.text = text
        self.encoding = "ISO-8859-1"  # overwritten by the source

    def raise_for_status(self) -> None:
        pass


class _StubService:
    """The surface the wiring functions touch, plus the source registry."""

    def __init__(self, source: AstockIntradaySource | None) -> None:
        from selene_space_market.event_bus import EventBus

        self.event_bus = EventBus()
        self._source = source

    def get_source(
        self, exchange: str = "binance", market_type: str = "spot"
    ) -> AstockIntradaySource | None:
        return self._source

    def as_service(self) -> "MarketDataService":
        """Hand the stub to code annotated against the real service type."""
        return cast("MarketDataService", self)


def _drive_script(src: AstockIntradaySource, *, script: list[str | None]) -> None:
    """Run the real poll loop through a scripted fetch sequence.

    A ``None`` entry raises ``ConnectionError`` (outage); a ``str`` is a body.
    One entry == one poll pass. A failed pass blocks every symbol in its
    ``PollBackoff`` window, so a fake monotonic clock advances past the backoff
    cap on each sleep — modelling "the window expired, we retried".
    """
    import selene_space_market.poll_backoff as poll_backoff_mod
    import selene_space_market.sources.astock_intraday_source as mod

    outcomes = list(script)

    def fake_get(*a: Any, **k: Any) -> _FakeResp:
        outcome = outcomes.pop(0) if outcomes else None
        if outcome is None:
            raise ConnectionError("network down")
        return _FakeResp(outcome)

    class _Clock:
        now = 1000.0

        def monotonic(self) -> float:
            return self.now

    clock = _Clock()

    async def _drive() -> None:
        src._running = True
        done = {"n": 0}
        real_sleep = asyncio.sleep

        async def _sleep(_sec: float) -> None:
            done["n"] += 1
            clock.now += 400.0  # past _BACKOFF_CAP_SEC (300s)
            if done["n"] >= len(script):
                src._running = False
            await real_sleep(0)

        with (
            patch.object(mod.requests, "get", fake_get),
            patch.object(poll_backoff_mod, "time", clock),
            patch.object(mod.asyncio, "sleep", side_effect=_sleep),
        ):
            task = asyncio.create_task(src._poll_loop())
            try:
                await asyncio.wait_for(task, timeout=5)
            except asyncio.CancelledError:
                pass

    asyncio.run(_drive())


def _kline_db(tmp_path) -> KlineDB:
    db = KlineDB(str(tmp_path / "klines.db"))
    db.open()
    return db


class TestGapSinkWiring:
    def test_sink_writes_episode_into_the_detector_store(self, tmp_path) -> None:
        db = _kline_db(tmp_path)
        src = AstockIntradaySource()
        svc = _StubService(src)

        assert wire_astock_intraday_gap_sink(svc.as_service(), db) is True

        src._subscribed = {"600519.SH"}
        _drive_script(src, script=[None, None, _body(last=10.0)])

        gaps = db.list_l1_gaps()
        assert len(gaps) == 1
        assert gaps[0].source == "astock_intraday"
        assert gaps[0].symbols == ("600519.SH",)
        assert gaps[0].closed_at_ms is not None

    def test_absent_source_is_not_an_error(self, tmp_path) -> None:
        # SELENE_ASTOCK_INTRADAY_SOURCE=0 is a supported deployment.
        svc = _StubService(None)
        assert (
            wire_astock_intraday_gap_sink(svc.as_service(), _kline_db(tmp_path))
            is False
        )

    def test_sink_persists_the_volume_bracket(self, tmp_path) -> None:
        """The A′ bracket survives the whole path: source → wiring → DB row.

        The pre-gap tick reports 1000 traded shares and the post-gap one 1750,
        so the persisted row must bracket 750 — proving ``persist_intraday_gap``
        forwards the volume fields rather than dropping them on the floor.
        """
        db = _kline_db(tmp_path)
        src = AstockIntradaySource()
        svc = _StubService(src)

        assert wire_astock_intraday_gap_sink(svc.as_service(), db) is True

        src._subscribed = {"600519.SH"}
        _drive_script(
            src,
            script=[
                _body(last=10.0, volume=1000.0),
                None,
                None,
                _body(last=10.0, volume=1750.0),
            ],
        )

        gaps = db.list_l1_gaps()
        assert len(gaps) == 1
        assert gaps[0].volume_before == 1000.0
        assert gaps[0].volume_after == 1750.0
        assert gaps[0].volume_delta == 750.0
        assert gaps[0].volume_symbols == 1


class TestDetectorSurvivesOutage:
    def test_limit_up_after_outage_still_reaches_detector(self, tmp_path) -> None:
        """The wire from source to detector is untouched by outage bookkeeping.

        A quote that moved *during* the blackout is published on recovery (its
        dedup tuple differs from the pre-outage one) and must still be seen by
        the detector — proving the S2 path inserted nothing between publisher
        and subscriber.
        """
        db = _kline_db(tmp_path)
        src = AstockIntradaySource()
        svc = _StubService(src)

        seen: list[AnomalyEvent] = []
        svc.event_bus.subscribe(AnomalyEvent, seen.append)
        src.on_ticker(svc.event_bus.publish)
        wire_astock_anomaly_detector(
            svc.as_service(),
            db,
            is_session_open=lambda: True,
            astock_symbols=["600519.SH"],
        )
        wire_astock_intraday_gap_sink(svc.as_service(), db)

        src._subscribed = {"600519.SH"}
        # Ordinary tick, outage, then a limit-up printed while we were blind.
        _drive_script(src, script=[_body(last=10.0), None, None, _body(last=11.0)])

        assert [e.kind for e in seen] == ["limit_up"]
        assert seen[0].symbol == "600519.SH"
        assert seen[0].prev_close == 10.0
        # And the outage is on the record alongside it.
        assert len(db.list_l1_gaps()) == 1

    def test_ordinary_tick_after_outage_publishes_but_does_not_signal(
        self, tmp_path
    ) -> None:
        """Control: the ticker still crosses the wire without a limit price."""
        db = _kline_db(tmp_path)
        src = AstockIntradaySource()
        svc = _StubService(src)

        tickers: list[TickerEvent] = []
        seen: list[AnomalyEvent] = []
        svc.event_bus.subscribe(TickerEvent, tickers.append)
        svc.event_bus.subscribe(AnomalyEvent, seen.append)
        src.on_ticker(svc.event_bus.publish)
        wire_astock_anomaly_detector(
            svc.as_service(),
            db,
            is_session_open=lambda: True,
            astock_symbols=["600519.SH"],
        )
        wire_astock_intraday_gap_sink(svc.as_service(), db)

        src._subscribed = {"600519.SH"}
        _drive_script(src, script=[None, None, _body(last=10.5)])

        assert [t.last_price for t in tickers] == [10.5]
        assert seen == []


class TestRestartDoesNotEraseTheLedger:
    def test_two_outages_across_a_restart_both_persist(self, tmp_path) -> None:
        """The production path for two outages with a process restart between.

        Each "process" builds its own source (the counter would restart at 1)
        and wires it into the *same* durable store. Both outages must survive —
        the earlier row must not be overwritten by the later one.
        """
        import selene_space_market.sources.astock_intraday_source as mod

        db = _kline_db(tmp_path)

        def one_process(open_instant: float) -> None:
            src = AstockIntradaySource()
            wire_astock_intraday_gap_sink(_StubService(src).as_service(), db)
            src._subscribed = {"600519.SH"}
            with patch.object(mod, "_now_ms", lambda: int(open_instant * 1000)):
                _drive_script(src, script=[None, None, _body(last=10.0)])

        one_process(1_700_000_000.0)  # process 1
        one_process(1_700_020_000.0)  # process 2, after a restart

        rows = db.list_l1_gaps()
        assert [r.opened_at_ms for r in rows] == [
            1_700_000_000_000,
            1_700_020_000_000,
        ], "the second outage must not overwrite the first"
