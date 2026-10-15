"""Step-5 coverage: the detector is mounted on the shared TickerEvent bus.

Two offline checks from the S3a plan — the wiring registers a TickerEvent
handler on a stub service, and a synthetic limit-up tick round-trips through
handler → process → republish as an ``AnomalyEvent``. Plus the two edge
guards the shared bus makes load-bearing: non-A-share tickers are dropped
before the synchronous SQLite read, and the session gate is calendar-aware.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, cast

import pytest
from selene_space_market.event_bus import EventBus
from selene_space_market.kline_db import KlineDB
from selene_space_types.market import AnomalyEvent, KlineEvent, TickerEvent

from app.astock_anomaly_wiring import (
    astock_session_open,
    wire_astock_anomaly_detector,
)

if TYPE_CHECKING:
    from selene_space_market.service import MarketDataService

_CST = timezone(timedelta(hours=8))
# Mid-session, relative to the real clock: _settled_bars derives its cutoff
# from datetime.now (no injectable clock on this path), so a hardcoded date
# would tie the suite to the day it runs on.
SESSION_DAY = datetime.now(_CST).replace(hour=10, minute=0, second=0, microsecond=0)


class _StubService:
    """Just the surface ``wire_astock_anomaly_detector`` touches.

    A real MarketDataService would drag in every source's construction; the
    wiring only reads ``event_bus``, so a stand-in keeps the mount test offline.
    """

    event_bus: EventBus

    def __init__(self) -> None:
        self.event_bus = EventBus()

    def as_service(self) -> "MarketDataService":
        """Hand the stub to code annotated against the real service type."""
        return cast("MarketDataService", self)


def _daily_bar(day: datetime, close: float, *, high: float | None = None) -> KlineEvent:
    return KlineEvent(
        exchange="astock",
        # Production convention: klines.db holds A-share bars under "astock".
        market_type="astock",
        symbol="600519.SH",
        timestamp=int(day.timestamp() * 1000),
        interval="1d",
        open=close - 0.5,
        high=close + 0.5 if high is None else high,
        low=close - 1.0,
        close=close,
        volume=1_000_000.0,
        closed=True,
    )


def _ticker(
    *,
    symbol: str = "600519.SH",
    exchange: str = "astock",
    last_price: float,
    when: datetime = SESSION_DAY,
    name: str = "贵州茅台",
    volume: float = 1_000.0,
    prev_close: float | None = None,
) -> TickerEvent:
    return TickerEvent(
        exchange=exchange,
        symbol=symbol,
        timestamp=int(when.timestamp() * 1000),
        last_price=last_price,
        bid=last_price - 0.01,
        ask=last_price + 0.01,
        volume_24h=volume,
        change_24h_pct=0.0,
        name=name,
        prev_close=prev_close,
    )


@pytest.fixture
def kline_db(tmp_path) -> KlineDB:
    db = KlineDB(str(tmp_path / "klines.db"))
    db.open()
    # prev_close 10.00 → limit-up 11.00 (main board +10%). The prior high sits
    # at the limit so an 11.00 tick fires limit_up alone, not n_day_high too.
    # Stamped at CST MIDNIGHT, as production does (astock_source._parse_rows):
    # a bar sitting at 15:00 would dodge the settlement cutoff's exact boundary
    # and could not catch a cutoff that wrongly includes today's forming bar.
    # Dated relative to the real clock because _settled_bars reads datetime.now,
    # not an injected value — a hardcoded date would couple this test to the
    # calendar date the suite happens to run on.
    yesterday = datetime.now(_CST) - timedelta(days=1)
    bar = _daily_bar(
        yesterday.replace(hour=0, minute=0, second=0, microsecond=0), 10.0, high=11.0
    )
    assert db.put_klines([bar]) == 1
    return db


class TestMount:
    def test_registers_ticker_handler(self, kline_db) -> None:
        svc = _StubService()
        wire_astock_anomaly_detector(
            svc.as_service(),
            kline_db,
            is_session_open=lambda: True,
            astock_symbols=["600519.SH"],
        )
        assert svc.event_bus.handler_count(TickerEvent) > 0

    def test_limit_up_round_trips_as_anomaly_event(self, kline_db) -> None:
        svc = _StubService()
        seen: list[AnomalyEvent] = []
        svc.event_bus.subscribe(AnomalyEvent, seen.append)
        wire_astock_anomaly_detector(
            svc.as_service(),
            kline_db,
            is_session_open=lambda: True,
            astock_symbols=["600519.SH"],
        )

        svc.event_bus.publish(_ticker(last_price=11.0))

        assert [e.kind for e in seen] == ["limit_up"]
        assert seen[0].symbol == "600519.SH"
        assert seen[0].prev_close == 10.0

    def test_empty_reachable_set_still_wires(self, kline_db) -> None:
        # Starvation (no A-share in defaults ∪ favorites) must not crash
        # startup — it is a logged condition, not a wiring failure.
        svc = _StubService()
        wire_astock_anomaly_detector(
            svc.as_service(), kline_db, is_session_open=lambda: True, astock_symbols=[]
        )
        assert svc.event_bus.handler_count(TickerEvent) > 0

    def test_ticker_prev_close_overrides_stale_kline_db_basis(self, kline_db) -> None:
        # The DB bar says prev_close 10.00 (limit-up 11.00) but the venue's own
        # field says 12.00 (limit-up 13.20). The ticker basis must win: an 11.00
        # tick is NOT a limit-up, while 13.20 is — proving the mount path takes
        # the raw venue close over the (stale qfq) stored bar.
        svc = _StubService()
        seen: list[AnomalyEvent] = []
        svc.event_bus.subscribe(AnomalyEvent, seen.append)
        wire_astock_anomaly_detector(
            svc.as_service(),
            kline_db,
            is_session_open=lambda: True,
            astock_symbols=["600519.SH"],
        )

        svc.event_bus.publish(_ticker(last_price=11.0, prev_close=12.0))
        assert seen == []  # under the DB basis this would have fired limit_up
        svc.event_bus.publish(_ticker(last_price=13.2, prev_close=12.0))
        assert [e.kind for e in seen] == ["limit_up"]
        assert seen[0].prev_close == 12.0


class TestExchangeGuard:
    def test_non_astock_ticker_is_dropped(self, kline_db) -> None:
        svc = _StubService()
        seen: list[AnomalyEvent] = []
        svc.event_bus.subscribe(AnomalyEvent, seen.append)
        wire_astock_anomaly_detector(
            svc.as_service(),
            kline_db,
            is_session_open=lambda: True,
            astock_symbols=["600519.SH"],
        )

        # Same price, wrong venue: must not be mistaken for an A-share limit-up.
        svc.event_bus.publish(
            _ticker(exchange="binance", symbol="BTC/USDT", last_price=11.0)
        )
        assert seen == []


class TestSessionGate:
    @pytest.mark.parametrize(
        ("hour", "minute", "expected"),
        [
            (9, 25, False),  # AUCTION — excluded
            (9, 30, True),  # open
            (11, 29, True),
            (11, 30, False),  # lunch break
            (13, 0, True),
            (14, 57, False),  # closing auction
            (15, 0, False),
        ],
    )
    def test_gate_tracks_calendar_phases(self, hour, minute, expected) -> None:
        now = datetime(2026, 10, 13, hour, minute, tzinfo=_CST)
        assert astock_session_open(now) is expected

    def test_gate_is_false_on_weekend(self) -> None:
        saturday = datetime(2026, 10, 10, 10, 0, tzinfo=_CST)
        assert astock_session_open(saturday) is False
