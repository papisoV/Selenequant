"""Gap sweep (L2) + paginated tail sync (L1) coverage.

Covers: KlineDB.find_gaps fast/slow paths, scan-watermark state, the hole
ledger's attempt->permanent flip, the sweeper's domain gate (only partitions
with a registered source), the watermark invariant (never advance past an open
hole), backwards-pagination tail catch-up, and per-partition quality isolation.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from selene_space_market import kline_manager as km_mod
from selene_space_market.kline_db import KlineDB
from selene_space_market.kline_manager import _PAGE, KlineDataManager
from selene_space_market.quality import KlineQualityChecker
from selene_space_types.market import KlineEvent

BASE_TS = 1711000000000
HOUR_MS = 3_600_000
HOUR = "1h"


def _kline(
    ts: int,
    *,
    exchange: str = "binance",
    market_type: str = "futures",
    symbol: str = "BTC/USDT",
    interval: str = HOUR,
    close: float = 67000.0,
) -> KlineEvent:
    return KlineEvent(
        exchange=exchange,
        market_type=market_type,  # type: ignore[arg-type]
        symbol=symbol,
        timestamp=ts,
        interval=interval,
        open=close - 100,
        high=close + 100,
        low=close - 200,
        close=close,
        volume=1000.0,
        closed=True,
    )


def _contiguous(
    count: int, *, start: int = BASE_TS, step: int = HOUR_MS, **kw
) -> list[KlineEvent]:
    return [_kline(start + i * step, **kw) for i in range(count)]


def _with_hole(
    count: int,
    *,
    hole_at: int,
    hole_len: int,
    start: int = BASE_TS,
    step: int = HOUR_MS,
    **kw,
) -> list[KlineEvent]:
    """Candles [0,hole_at) then [hole_at+hole_len, count) — a missing run."""
    out = [_kline(start + i * step, **kw) for i in range(hole_at)]
    out += [
        _kline(start + (i + hole_len) * step, **kw)
        for i in range(hole_at, count - hole_len)
    ]
    return out


@pytest.fixture
def db() -> KlineDB:
    store = KlineDB(":memory:")
    store.open()
    return store


@pytest.fixture
def manager(db: KlineDB) -> KlineDataManager:
    mgr = KlineDataManager(":memory:")
    mgr._db = db  # share the in-memory store
    return mgr


# -- find_gaps ---------------------------------------------------------------


class TestFindGaps:
    def test_contiguous_returns_none(self, db: KlineDB) -> None:
        db.put_klines(_contiguous(10))
        assert db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS) == []

    def test_single_row_returns_none(self, db: KlineDB) -> None:
        db.put_klines(_contiguous(1))
        assert db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS) == []

    def test_empty_partition_returns_none(self, db: KlineDB) -> None:
        assert db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS) == []

    def test_interior_hole_detected(self, db: KlineDB) -> None:
        db.put_klines(_with_hole(10, hole_at=4, hole_len=3))
        holes = db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS)
        assert len(holes) == 1
        left, right = holes[0]
        assert right - left == 4 * HOUR_MS  # 3 missing bars -> 4-step gap

    def test_leading_edge_hole(self, db: KlineDB) -> None:
        # bars start late: MIN is not the true earliest, still one interior hole
        db.put_klines(_contiguous(3) + _contiguous(3, start=BASE_TS + 10 * HOUR_MS))
        holes = db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS)
        assert len(holes) == 1

    def test_since_ts_bounds_scan(self, db: KlineDB) -> None:
        db.put_klines(_with_hole(12, hole_at=2, hole_len=2))  # hole early
        floor = BASE_TS + 5 * HOUR_MS
        assert (
            db.find_gaps(
                "binance", "futures", "BTC/USDT", HOUR, HOUR_MS, since_ts=floor
            )
            == []
        )

    def test_fast_and_slow_paths_agree(self, db: KlineDB) -> None:
        # Non-contiguous partitions must take the pairwise path; contiguous the
        # fast path. Both are exercised above; assert the count pre-check is not
        # fooled by a duplicate-free but gapped series.
        db.put_klines(_with_hole(20, hole_at=10, hole_len=5))
        holes = db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS)
        total = db.count("binance", "futures", "BTC/USDT", HOUR)
        assert total == 15  # 20 - 5 missing
        assert len(holes) == 1

    def test_rejects_nonpositive_step(self, db: KlineDB) -> None:
        with pytest.raises(ValueError):
            db.find_gaps("binance", "futures", "BTC/USDT", HOUR, 0)


# -- watermark + hole ledger -------------------------------------------------


class TestScanState:
    def test_verified_through_defaults_none(self, db: KlineDB) -> None:
        assert db.get_verified_through("binance", "futures", "BTC/USDT", HOUR) is None

    def test_set_and_get_roundtrip(self, db: KlineDB) -> None:
        db.set_verified_through("binance", "futures", "BTC/USDT", HOUR, BASE_TS)
        assert (
            db.get_verified_through("binance", "futures", "BTC/USDT", HOUR) == BASE_TS
        )

    def test_set_is_upsert(self, db: KlineDB) -> None:
        db.set_verified_through("binance", "futures", "BTC/USDT", HOUR, BASE_TS)
        db.set_verified_through(
            "binance", "futures", "BTC/USDT", HOUR, BASE_TS + HOUR_MS
        )
        assert (
            db.get_verified_through("binance", "futures", "BTC/USDT", HOUR)
            == BASE_TS + HOUR_MS
        )

    def test_attempt_accumulates_and_flips_permanent(self, db: KlineDB) -> None:
        args = ("binance", "futures", "BTC/USDT", HOUR, BASE_TS, BASE_TS + 4 * HOUR_MS)
        s1 = db.record_hole_attempt(*args, now_ms=1)
        assert s1.attempts == 1 and not s1.permanent
        s2 = db.record_hole_attempt(*args, now_ms=2)
        assert s2.attempts == 2 and not s2.permanent
        s3 = db.record_hole_attempt(*args, now_ms=3)
        assert s3.attempts == 3 and s3.permanent  # >=3 flips

    def test_list_holes_only_open_excludes_permanent(self, db: KlineDB) -> None:
        args = ("binance", "futures", "BTC/USDT", HOUR, BASE_TS, BASE_TS + 4 * HOUR_MS)
        for i in range(3):
            db.record_hole_attempt(*args, now_ms=i)
        assert (
            db.list_holes("binance", "futures", "BTC/USDT", HOUR, only_open=True) == []
        )
        assert (
            len(db.list_holes("binance", "futures", "BTC/USDT", HOUR, only_open=False))
            == 1
        )

    def test_clear_hole_removes_row(self, db: KlineDB) -> None:
        args = ("binance", "futures", "BTC/USDT", HOUR, BASE_TS, BASE_TS + 4 * HOUR_MS)
        db.record_hole_attempt(*args, now_ms=1)
        db.clear_hole(*args)
        assert (
            db.list_holes("binance", "futures", "BTC/USDT", HOUR, only_open=False) == []
        )

    def test_list_partitions(self, db: KlineDB) -> None:
        db.put_klines(_contiguous(2))
        db.put_klines(_contiguous(2, interval="5m"))
        parts = set(db.list_partitions())
        assert ("binance", "futures", "BTC/USDT", HOUR) in parts
        assert ("binance", "futures", "BTC/USDT", "5m") in parts


# -- sweep_once --------------------------------------------------------------


def _service(candles: list[KlineEvent]) -> MagicMock:
    svc = MagicMock()
    svc.fetch_klines_rest = AsyncMock(return_value=candles)
    return svc


def _paging_service(candles: list[KlineEvent]) -> MagicMock:
    """A service that honours ``limit`` and ``end_time`` like a real exchange.

    Returns up to ``limit`` bars strictly below ``end_time`` (or the newest
    ``limit`` bars when ``end_time`` is None), so a single-page caller genuinely
    cannot reach a deep tail.
    """
    ordered = sorted(candles, key=lambda k: k.timestamp)

    async def _fetch(
        *, symbol, exchange, market_type, interval, limit=_PAGE, end_time=None, **kw
    ) -> list[KlineEvent]:
        pool = [k for k in ordered if end_time is None or k.timestamp < end_time]
        return pool[-limit:] if limit else pool

    svc = MagicMock()
    svc.fetch_klines_rest = AsyncMock(side_effect=_fetch)
    return svc


class TestSweepOnce:
    @pytest.mark.asyncio
    async def test_domain_gate_excludes_unregistered(
        self, manager: KlineDataManager
    ) -> None:
        # Two partitions: one crypto (has a source), one astock (no source).
        manager._db.put_klines(_contiguous(5))
        manager._db.put_klines(
            _contiguous(5, exchange="astock", market_type="astock", symbol="600000")
        )
        manager.set_service(_service([]))
        report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.partitions_scanned == 1  # astock never entered the domain

    @pytest.mark.asyncio
    async def test_fills_hole_and_advances_watermark(
        self, manager: KlineDataManager
    ) -> None:
        holed = _with_hole(10, hole_at=4, hole_len=3)
        manager._db.put_klines(holed)
        # REST returns the missing interior bars.
        interior = _contiguous(10)[4:7]
        manager.set_service(_service(interior))
        report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.holes_found == 1
        assert report.holes_filled == 1
        assert report.partitions_advanced == 1
        assert (
            manager._db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS) == []
        )
        assert (
            manager._db.list_holes(
                "binance", "futures", "BTC/USDT", HOUR, only_open=False
            )
            == []
        )
        assert (
            manager._db.get_verified_through("binance", "futures", "BTC/USDT", HOUR)
            is not None
        )

    @pytest.mark.asyncio
    async def test_unfillable_hole_freezes_watermark(
        self, manager: KlineDataManager
    ) -> None:
        manager._db.put_klines(_with_hole(10, hole_at=4, hole_len=3))
        manager.set_service(_service([]))  # source has nothing to give
        report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.holes_found == 1
        assert report.holes_filled == 0
        # watermark must NOT advance past an open hole
        assert (
            manager._db.get_verified_through("binance", "futures", "BTC/USDT", HOUR)
            is None
        )
        assert report.partitions_advanced == 0

    @pytest.mark.asyncio
    async def test_permanent_hole_allows_watermark_to_pass(
        self, manager: KlineDataManager
    ) -> None:
        manager._db.put_klines(_with_hole(10, hole_at=4, hole_len=3))
        manager.set_service(_service([]))
        # three sweeps -> hole goes permanent; watermark then advances over it
        for _ in range(3):
            await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert (
            manager._db.list_holes(
                "binance", "futures", "BTC/USDT", HOUR, only_open=True
            )
            == []
        )
        await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert (
            manager._db.get_verified_through("binance", "futures", "BTC/USDT", HOUR)
            is not None
        )

    @pytest.mark.asyncio
    async def test_permanent_hole_stops_consuming_rest_budget(
        self, manager: KlineDataManager
    ) -> None:
        # PM3: once a hole flips permanent (>=3 failed fills), later rounds must
        # not re-issue REST for it. find_gaps() still reports the gap forever,
        # so the sweeper has to consult the ledger to skip it.
        # A stale partition missing its exchange's newer bars. Once the hole
        # goes permanent (round 3) the tail is still stale and must resume —
        # otherwise a single unrepairable gap freezes the partition forever.
        manager._db.put_klines(_with_hole(10, hole_at=4, hole_len=3))
        svc = _service([])
        manager.set_service(svc)
        for _ in range(4):  # rounds 1-3 build attempts, round 4 is post-permanent
            await manager.sweep_once(allowed_sources={("binance", "futures")})
        # The tail is stale and its now-permanent hole no longer blocks the
        # advance, so round 4 is allowed exactly one *tail* fetch. From here on
        # the ledger must stay REST-silent: no more hole retries, and the tail
        # is as current as this fixture allows.
        calls_at_permanent = svc.fetch_klines_rest.await_count
        await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert svc.fetch_klines_rest.await_count == calls_at_permanent  # zero new REST
        # The hole is still visible (permanent holes stay in health accounting).
        report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.holes_permanent == 1

    @pytest.mark.asyncio
    async def test_transport_failure_does_not_consume_hole_attempts(
        self, manager: KlineDataManager
    ) -> None:
        # A fetch that raised says nothing about whether the exchange holds the
        # bar — only that the question could not be asked. Counting it as a
        # failed fill is how a network outage permanently blacklists real holes:
        # after three rounds the ledger flips them to permanent and the repair
        # path never retries. Measured on the live store during a 13h outage:
        # all 82 open holes sat at attempts=2 (one failed round from permanent).
        manager._db.put_klines(_with_hole(10, hole_at=4, hole_len=3))
        svc = MagicMock()
        svc.fetch_klines_rest = AsyncMock(side_effect=ConnectionError("network down"))
        manager.set_service(svc)
        for _ in range(5):
            report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.holes_found == 1
        assert manager._db.list_holes(
            "binance", "futures", "BTC/USDT", HOUR
        ) == [] or all(
            not h.permanent
            for h in manager._db.list_holes(
                "binance", "futures", "BTC/USDT", HOUR, only_open=False
            )
        ), "an unreachable exchange must never make a hole permanent"
        assert report.partitions_advanced == 0, "watermark must not pass a real hole"

    @pytest.mark.asyncio
    async def test_transport_failure_does_not_memo_tail_as_delisted(
        self, manager: KlineDataManager
    ) -> None:
        # ``_advance_tail`` memos "the exchange has nothing past X" for an hour.
        # Writing that memo for a failed fetch teaches the next round to stay
        # REST-silent: the outage ends, the partition still does not advance,
        # and nothing retries until the TTL lapses.
        manager._db.put_klines(_contiguous(10))
        svc = MagicMock()
        svc.fetch_klines_rest = AsyncMock(side_effect=ConnectionError("network down"))
        manager.set_service(svc)
        with _now_fixed(BASE_TS + 30 * HOUR_MS):
            report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.partitions_advanced == 1  # no holes; watermark still moves
        cursor, probed_at = manager._db.get_tail_probe(
            "binance", "futures", "BTC/USDT", HOUR
        )
        assert (
            cursor is None and probed_at is None
        ), "a failed fetch must not be recorded as 'source has nothing newer'"

    @pytest.mark.asyncio
    async def test_tail_transport_failure_still_lets_watermark_advance(
        self, manager: KlineDataManager
    ) -> None:
        # ``verified_through`` vouches for the *interior*. A failed forward fetch
        # leaves the tail stale, which freshness reports, but says nothing about
        # the bars already stored — blocking the watermark would stall every
        # partition that happens to have a newer bar than this round's budget.
        manager._db.put_klines(_contiguous(10))
        svc = MagicMock()
        svc.fetch_klines_rest = AsyncMock(side_effect=ConnectionError("network down"))
        manager.set_service(svc)
        with _now_fixed(BASE_TS + 30 * HOUR_MS):
            report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.transport_errors >= 1, "the outage must be visible in the report"
        assert report.hole_transport_errors == 0, "no holes means no hole failures"
        assert report.partitions_advanced == 1
        assert (
            manager._db.get_verified_through("binance", "futures", "BTC/USDT", HOUR)
            is not None
        )

    @pytest.mark.asyncio
    async def test_max_partitions_truncates(self, manager: KlineDataManager) -> None:
        for i in range(5):
            manager._db.put_klines(_contiguous(3, symbol=f"S{i}/USDT"))
        manager.set_service(_service([]))
        report = await manager.sweep_once(
            allowed_sources={("binance", "futures")}, max_partitions=2
        )
        assert report.partitions_scanned == 2
        assert report.warnings

    @pytest.mark.asyncio
    async def test_default_scan_covers_every_in_domain_partition(
        self, manager: KlineDataManager
    ) -> None:
        # The production store holds ~900 in-domain partitions and the old
        # default (50) truncated the scan before it ever reached BTC/ETH/SOL.
        # With no explicit budget, every in-domain partition must be scanned.
        for i in range(120):
            manager._db.put_klines(_contiguous(3, symbol=f"S{i}/USDT"))
        manager.set_service(_service([]))
        report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.partitions_scanned == 120
        assert not report.warnings

    @pytest.mark.asyncio
    async def test_quality_checker_scored_per_partition(
        self, manager: KlineDataManager
    ) -> None:
        manager._db.put_klines(_contiguous(30))
        manager._db.put_klines(_contiguous(30, interval="5m"))
        manager.set_service(_service([]))
        report = await manager.sweep_once(
            allowed_sources={("binance", "futures")},
            quality_checker=KlineQualityChecker(),
        )
        assert len(report.quality) == 2
        assert all(0.0 <= v <= 1.0 for v in report.quality.values())


# -- V4: non-24/7 partitions must not manufacture false holes -----------------

DAY_MS = 86_400_000


def _weekday_only(count: int, *, start: int = BASE_TS) -> list[KlineEvent]:
    """Daily bars skipping Sat/Sun — a legitimate 2-day jump every weekend.

    A non-24/7 market (astock/yahoo) is full of these. Scanning such a
    partition as if it were 24/7 flags every weekend as a hole, so the domain
    gate must keep it out of the sweep entirely.
    """
    out: list[KlineEvent] = []
    ts = start
    while len(out) < count:
        # 1970-01-01 was a Thursday; day index % 7 == 2,3 are Sat,Sun.
        if ((ts // DAY_MS) + 4) % 7 not in (2, 3):
            out.append(
                _kline(
                    ts,
                    exchange="astock",
                    market_type="astock",
                    symbol="600000",
                    interval="1d",
                )
            )
        ts += DAY_MS
    return out


class TestDomainGateNoFalseHoles:
    @pytest.mark.asyncio
    async def test_weekend_gap_partition_out_of_domain_yields_no_holes(
        self, manager: KlineDataManager
    ) -> None:
        # A crypto partition (contiguous, in domain) alongside an astock
        # partition whose weekend jumps look like holes. Only the crypto one is
        # scannable, so the false weekend holes are never even considered.
        manager._db.put_klines(_contiguous(5))
        manager._db.put_klines(_weekday_only(10))
        manager.set_service(_service([]))
        report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.partitions_scanned == 1  # astock excluded
        assert report.holes_found == 0  # no weekend false positives
        # Nothing was recorded against the astock partition's ledger either.
        assert (
            manager._db.list_holes("astock", "astock", "600000", "1d", only_open=False)
            == []
        )

    @pytest.mark.asyncio
    async def test_same_partition_in_domain_would_flag_weekend(
        self, manager: KlineDataManager
    ) -> None:
        # Negative control: if the domain gate were bypassed and the astock
        # partition treated as 24/7, its weekend jumps DO surface as holes —
        # proving the gate is what suppresses the false positives above.
        manager._db.put_klines(_weekday_only(10))
        manager.set_service(_service([]))
        report = await manager.sweep_once(allowed_sources={("astock", "astock")})
        assert report.partitions_scanned == 1
        assert report.holes_found > 0  # weekends read as holes when not gated out


# -- L3 acceptance: a bad bar must drag the partition's quality score below 1 -


class TestQualityScoreSurfacesBadBars:
    @pytest.mark.asyncio
    async def test_injected_ohlc_bad_bar_lowers_quality(
        self, manager: KlineDataManager
    ) -> None:
        # Acceptance for L3: a partition carrying an OHLC-inconsistent bar must
        # report quality_score < 1 through the sweeper's snapshot, not silently
        # score 1.0 because the series looked contiguous.
        bad = _kline(BASE_TS + 2 * HOUR_MS, close=67000.0)
        bad = bad.model_copy(update={"high": 1.0})  # high < low/open/close
        series = _contiguous(3)
        series[2] = bad
        manager._db.put_klines(series)
        manager.set_service(_service([]))
        checker = KlineQualityChecker()
        report = await manager.sweep_once(
            allowed_sources={("binance", "futures")},
            quality_checker=checker,
        )
        assert report.quality
        assert min(report.quality.values()) < 1.0


# -- tail-sync pagination ----------------------------------------------------


class TestTailSyncPagination:
    @pytest.mark.asyncio
    async def test_multi_page_catches_up_beyond_one_page(
        self, manager: KlineDataManager
    ) -> None:
        # Local holds one old bar; the exchange has 3,000 newer bars — more than
        # a single 1,500-bar page. The paginated tail sync must walk back across
        # pages; a single-page fetch would strand the first ~1,500 as a hole.
        manager._db.put_klines(_contiguous(1))
        latest = manager._db.get_latest_timestamp(
            "binance", "futures", "BTC/USDT", HOUR
        )
        assert latest is not None
        fresh = _contiguous(3000, start=latest + HOUR_MS)
        manager.set_service(_paging_service(fresh))
        written = await manager.sync_incremental("binance", "futures", "BTC/USDT", HOUR)
        assert written >= 3000
        # no interior hole between the old bar and the new tail
        assert (
            manager._db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS) == []
        )

    @pytest.mark.asyncio
    async def test_returns_int_contract(self, manager: KlineDataManager) -> None:
        manager._db.put_klines(_contiguous(1))
        manager.set_service(_paging_service(_contiguous(5, start=BASE_TS + HOUR_MS)))
        result = await manager.sync_incremental("binance", "futures", "BTC/USDT", HOUR)
        assert isinstance(result, int)


# -- quality isolation -------------------------------------------------------


class TestQualityIsolation:
    def test_reused_checker_false_gaps_across_intervals(self) -> None:
        # A single checker keys state by symbol only; feeding a 5m series after
        # a 1h series (same symbol, next partition) fabricates a gap. This is
        # why the sweeper resets per partition — the test documents the hazard
        # the reset defends against.
        checker = KlineQualityChecker()
        checker.validate_series(_contiguous(5, step=HOUR_MS), HOUR_MS)
        five_min = _contiguous(5, start=BASE_TS + 5 * HOUR_MS, step=300_000)
        report = checker.validate_series(five_min, 300_000)
        gap_issues = [i for i in report.warnings if i.issue_type == "gap"]
        assert gap_issues  # false gap: stale 1h watermark vs 5m expectation

    def test_reset_clears_state(self) -> None:
        checker = KlineQualityChecker()
        checker.validate_series(_contiguous(5, step=HOUR_MS), HOUR_MS)
        checker.reset()
        five_min = _contiguous(5, start=BASE_TS + 5 * HOUR_MS, step=300_000)
        report = checker.validate_series(five_min, 300_000)
        gap_issues = [i for i in report.warnings if i.issue_type == "gap"]
        assert not gap_issues  # reset removes the stale symbol watermark


# -- F4 regression: rolling-window blindness ---------------------------------


class TestF4WindowBlindness:
    def test_hole_older_than_rolling_window_still_found(
        self, manager: KlineDataManager
    ) -> None:
        # A hole near the very start of the partition is far outside any
        # now-window_days scan, yet find_gaps(since_ts=None) still sees it.
        manager._db.put_klines(_with_hole(50, hole_at=3, hole_len=5))
        holes = manager._db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS)
        assert len(holes) == 1
        left, right = holes[0]
        assert left == BASE_TS + 2 * HOUR_MS


# -- calendar-aware gaps: closed sessions are not holes -----------------------


def _label(day: str, *, tz: str = "Asia/Shanghai") -> int:
    """UTC ms for midnight of *day* in the source's label timezone.

    A-share daily bars are labelled at Beijing midnight (stored as 16:00 UTC on
    the previous day); US bars at session open in New York.
    """
    import datetime as _dt
    from zoneinfo import ZoneInfo

    d = _dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=ZoneInfo(tz))
    return int(d.timestamp() * 1000)


def _daily_labels(days: list[str], **kw) -> list[KlineEvent]:
    tz = kw.pop("label_tz", "Asia/Shanghai")
    return [_kline(_label(d, tz=tz), **kw) for d in days]


class TestCalendarAwareDailyGaps:
    """Daily equity bars must be judged against the session calendar.

    The fixed-step rule is wrong in both directions here: every Fri→Mon jump
    reads as a hole, and a genuinely missing Wednesday is invisible (the
    observed delta is still two days).
    """

    ASTOCK = {
        "exchange": "astock",
        "market_type": "astock",
        "symbol": "600000.SH",
        "interval": "1d",
    }
    DAY = 86_400_000

    def test_weekend_jump_is_not_a_hole(self, db: KlineDB) -> None:
        days = ["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"]
        db.put_klines(_daily_labels(days, **self.ASTOCK))
        from selene_space_market.kline_calendar import gap_predicate

        pred = gap_predicate(
            exchange=self.ASTOCK["exchange"],
            market_type=self.ASTOCK["market_type"],
            symbol=self.ASTOCK["symbol"],
            interval="1d",
            step_ms=self.DAY,
            stamps=[k.timestamp for k in _daily_labels(days, **self.ASTOCK)],
        )
        assert (
            db.find_gaps(
                self.ASTOCK["exchange"],
                self.ASTOCK["market_type"],
                self.ASTOCK["symbol"],
                "1d",
                self.DAY,
                is_hole=pred,
            )
            == []
        )

    def test_missing_trading_day_is_a_hole(self, db: KlineDB) -> None:
        # XSHG closes 09-25 (mid-autumn) and 10-01..10-07 (national day);
        # 09-30 and 10-08 are sessions. Dropping 09-30 hides inside a jump
        # whose fixed-step neighbour deltas look like every other holiday.
        days = ["2026-09-28", "2026-09-29", "2026-10-08", "2026-10-09"]
        db.put_klines(_daily_labels(days, **self.ASTOCK))
        from selene_space_market.kline_calendar import gap_predicate

        kwargs = (
            self.ASTOCK["exchange"],
            self.ASTOCK["market_type"],
            self.ASTOCK["symbol"],
            "1d",
            self.DAY,
        )
        # The fixed-step rule sees only the holiday-wide jump; a silently
        # dropped session inside a normal 2-day jump is invisible to it.
        assert len(db.find_gaps(*kwargs)) == 1
        pred = gap_predicate(
            exchange=self.ASTOCK["exchange"],
            market_type=self.ASTOCK["market_type"],
            symbol=self.ASTOCK["symbol"],
            interval="1d",
            step_ms=self.DAY,
            stamps=[k.timestamp for k in _daily_labels(days, **self.ASTOCK)],
        )
        holes = db.find_gaps(*kwargs, is_hole=pred)
        # 09-29 → 10-08 crosses the closed 09-30 session; 10-08 → 10-12 is
        # consecutive sessions.
        assert holes == [(_label("2026-09-29"), _label("2026-10-08"))]

    def test_crypto_partition_has_no_predicate(self) -> None:
        from selene_space_market.kline_calendar import gap_predicate

        assert (
            gap_predicate(
                exchange="binance",
                market_type="futures",
                symbol="BTC/USDT",
                interval="1d",
                step_ms=self.DAY,
                stamps=[],
            )
            is None
        )

    def test_crypto_results_are_bit_identical(self, db: KlineDB) -> None:
        db.put_klines(
            _with_hole(10, hole_at=4, hole_len=2, interval="1d", step=self.DAY)
        )
        kwargs = ("binance", "futures", "BTC/USDT", "1d", self.DAY)
        without = db.find_gaps(*kwargs)
        from selene_space_market.kline_calendar import gap_predicate

        pred = gap_predicate(
            exchange="binance",
            market_type="futures",
            symbol="BTC/USDT",
            interval="1d",
            step_ms=self.DAY,
            stamps=[],
        )
        with_pred = db.find_gaps(*kwargs, is_hole=pred)
        assert (
            without == with_pred == [(BASE_TS + 3 * self.DAY, BASE_TS + 6 * self.DAY)]
        )

    def test_weekday_fallback_when_calendar_unavailable(self) -> None:
        from selene_space_market.kline_calendar import gap_predicate

        pred = gap_predicate(
            exchange="astock",
            market_type="astock",
            symbol="600000.SH",
            interval="1d",
            step_ms=self.DAY,
            stamps=[],
            sessions=lambda s, e: (_ for _ in ()).throw(RuntimeError("no calendar")),
        )
        assert pred is not None
        # Fri 09-25 → Mon 09-28 spans only weekdays in the approximation.
        assert pred(_label("2026-09-25"), _label("2026-09-28")) is False
        # Thu 09-24 → Mon 09-28 loses a Friday under the approximation.
        assert pred(_label("2026-09-24"), _label("2026-09-28")) is True

    def test_sweeper_does_not_log_permanent_weekend_holes(
        self, manager: KlineDataManager
    ) -> None:
        """End-to-end: the false-hole chain that turned /api/v1/health degraded."""
        days = [
            "2026-09-21",
            "2026-09-22",
            "2026-09-23",
            "2026-09-24",
            "2026-09-28",
            "2026-09-29",
            "2026-10-08",
            "2026-10-09",
            "2026-10-12",
        ]
        manager._db.put_klines(_daily_labels(days, **self.ASTOCK))
        manager.set_service(_service([]))
        import asyncio as _asyncio

        report = _asyncio.run(
            manager.sweep_once(allowed_sources={("astock", "astock")})
        )
        # Only the 09-30 skip is a hole; the National Day closure and every
        # weekend are legitimate. No REST fill → it becomes open, not permanent.
        assert report.holes_found == 1
        assert report.holes_permanent == 0

    def test_quality_checker_ignores_weekends_but_reports_real_gap(self) -> None:
        checker = KlineQualityChecker(check_volume=False)
        days = ["2026-09-24", "2026-09-25", "2026-09-28", "2026-09-29"]
        report = checker.validate_series(_daily_labels(days, **self.ASTOCK), self.DAY)
        assert not [i for i in report.warnings if i.issue_type == "gap"]

        checker = KlineQualityChecker(check_volume=False)
        gap_days = ["2026-09-28", "2026-10-08", "2026-10-09"]  # 09-30 skipped
        report = checker.validate_series(
            _daily_labels(gap_days, **self.ASTOCK), self.DAY
        )
        gaps = [i for i in report.warnings if i.issue_type == "gap"]
        assert len(gaps) == 1


class TestPartitionBudgetRotation:
    @pytest.mark.asyncio
    async def test_every_partition_is_reached_across_rounds(
        self, manager: KlineDataManager
    ) -> None:
        # A stable prefix slice would starve partitions past the budget forever
        # (852 of 902 in-domain partitions in the production store). Each round
        # must advance the window.
        for i in range(7):
            manager._db.put_klines(_contiguous(3, symbol=f"S{i:02d}/USDT"))
        manager.set_service(_service([]))
        swept: list[str] = []
        original = manager._sweep_partition

        async def _record(*args, **kwargs):
            swept.append(str(kwargs["symbol"]))
            return await original(*args, **kwargs)

        manager._sweep_partition = _record  # type: ignore[method-assign]
        rounds = 4
        for _ in range(rounds):
            report = await manager.sweep_once(
                allowed_sources={("binance", "futures")}, max_partitions=2
            )
            assert report.partitions_scanned == 2

        assert len(swept) == rounds * 2
        assert len(set(swept)) == 7  # every partition reached within one cycle
        # ...and the window keeps advancing rather than re-sweeping the prefix.
        assert len(set(swept[:2])) == 2 and len(set(swept[2:4])) == 2


class TestStaleLedgerPruning:
    @pytest.mark.asyncio
    async def test_weekend_permanent_rows_are_pruned(
        self, manager: KlineDataManager
    ) -> None:
        # Ledger rows written under the fixed-step rule (weekends as holes) must
        # not keep the partition degraded once detection is session-aware.
        days = ["2026-09-24", "2026-09-28", "2026-09-29"]
        manager._db.put_klines(_daily_labels(days, **TestCalendarAwareDailyGaps.ASTOCK))
        left = _label("2026-09-24")
        right = _label("2026-09-28")
        for _ in range(3):
            manager._db.record_hole_attempt(
                "astock", "astock", "600000.SH", "1d", left, right, now_ms=BASE_TS
            )
        assert manager._db.list_holes(
            "astock", "astock", "600000.SH", "1d", only_open=False
        )
        manager.set_service(_service([]))
        report = await manager.sweep_once(allowed_sources={("astock", "astock")})
        assert report.holes_pruned == 1
        assert report.holes_permanent == 0
        assert (
            manager._db.list_holes(
                "astock", "astock", "600000.SH", "1d", only_open=False
            )
            == []
        )

    @pytest.mark.asyncio
    async def test_live_permanent_rows_are_kept(
        self, manager: KlineDataManager
    ) -> None:
        # A genuinely missing session stays a hole, so its permanent row (and
        # the no-auto-retry guarantee) must survive the prune pass.
        days = ["2026-09-28", "2026-09-29", "2026-10-08", "2026-10-09"]
        manager._db.put_klines(_daily_labels(days, **TestCalendarAwareDailyGaps.ASTOCK))
        left = _label("2026-09-29")
        right = _label("2026-10-08")
        for _ in range(3):
            manager._db.record_hole_attempt(
                "astock", "astock", "600000.SH", "1d", left, right, now_ms=BASE_TS
            )
        manager.set_service(_service([]))
        report = await manager.sweep_once(allowed_sources={("astock", "astock")})
        assert report.holes_pruned == 0
        assert report.holes_permanent == 1


# -- intraday slots: overnight/weekend/half-day closures are not holes ---------

US_1H: dict[str, Any] = {
    "exchange": "yahoo",
    "market_type": "us_stock",
    "symbol": "AAPL",
    "interval": "1h",
}


def _us_hourly(
    days: list[str], bars: list[str], *, skip: set[tuple[str, str]] | None = None
):
    """AAPL-style 1h labels: session 09:30–16:00 New York, 7 bars a day."""
    from datetime import datetime as _dt
    from zoneinfo import ZoneInfo

    tz = ZoneInfo("America/New_York")
    out = []
    for d in days:
        for hhmm in bars:
            if skip and (d, hhmm) in skip:
                continue
            h, m = map(int, hhmm.split(":"))
            day = _dt.strptime(d, "%Y-%m-%d").replace(hour=h, minute=m, tzinfo=tz)
            out.append(_kline(int(day.timestamp() * 1000), **US_1H))
    return out


US_BARS = ["09:30", "10:30", "11:30", "12:30", "13:30", "14:30", "15:30"]


class TestIntradaySessionGaps:
    def _pred(self, stamps):
        from selene_space_market.kline_calendar import gap_predicate

        return gap_predicate(
            exchange=US_1H["exchange"],
            market_type=US_1H["market_type"],
            symbol=US_1H["symbol"],
            interval="1h",
            step_ms=HOUR_MS,
            stamps=stamps,
        )

    def test_overnight_and_weekend_are_not_holes(self, db: KlineDB) -> None:
        # Fri 2026-10-02 → Mon 2026-10-05: 66 h of closed market.
        bars = _us_hourly(
            ["2026-09-30", "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06"],
            US_BARS,
        )
        db.put_klines(bars)
        stamps = [b.timestamp for b in bars]
        # Fixed step: four closures (two overnights + the weekend + the
        # Sunday-night gap into Monday's open).
        assert len(db.find_gaps(**{**_q(US_1H), "step_ms": HOUR_MS})) == 4
        aware = db.find_gaps(**_q(US_1H), step_ms=HOUR_MS, is_hole=self._pred(stamps))
        assert aware == []

    def test_missing_session_bar_is_a_hole(self, db: KlineDB) -> None:
        # 12:30 removed from Monday. The fixed-step rule conflates that 2 h
        # jump with the legitimate overnight/weekend closures around it; the
        # slot grid reports exactly the missing bar.
        bars = _us_hourly(
            ["2026-10-05", "2026-10-06"], US_BARS, skip={("2026-10-05", "12:30")}
        )
        db.put_klines(bars)
        stamps = [b.timestamp for b in bars]
        fixed = db.find_gaps(**_q(US_1H), step_ms=HOUR_MS)
        # (A single-bar skip is invisible to the > 2×step rule entirely.)
        assert (2 * HOUR_MS) not in {r - l for l, r in fixed}
        aware = db.find_gaps(**_q(US_1H), step_ms=HOUR_MS, is_hole=self._pred(stamps))
        assert len(aware) == 1
        left, right = aware[0]
        assert (right - left) == 2 * HOUR_MS

    def test_half_day_early_close_is_not_a_hole(self, db: KlineDB) -> None:
        # 2025-12-24 closes at 13:00: only four 1h slots exist that day.
        bars = (
            _us_hourly(["2025-12-23"], US_BARS)
            + _us_hourly(["2025-12-24"], ["09:30", "10:30", "11:30", "12:30"])
            + _us_hourly(["2025-12-26"], US_BARS)
        )
        db.put_klines(bars)
        stamps = [b.timestamp for b in bars]
        aware = db.find_gaps(**_q(US_1H), step_ms=HOUR_MS, is_hole=self._pred(stamps))
        assert aware == []

    def test_sub_bar_stub_is_not_a_hole(self, db: KlineDB) -> None:
        # Yahoo emits a live 15:30 stub at :48 plus the :00 bar; neither the
        # 18-minute nor the 12-minute neighbour jump is a missing bar.
        from datetime import datetime as _dt, timedelta
        from zoneinfo import ZoneInfo

        tz = ZoneInfo("America/New_York")
        base = _dt(2026, 10, 16, 15, 0, tzinfo=tz)
        bars = [
            _kline(int((base + timedelta(minutes=m)).timestamp() * 1000), **US_1H)
            for m in (0, 18, 30)
        ]
        db.put_klines(bars)
        stamps = [b.timestamp for b in bars]
        aware = db.find_gaps(**_q(US_1H), step_ms=HOUR_MS, is_hole=self._pred(stamps))
        assert aware == []


def _q(part: dict) -> dict:
    return {k: v for k, v in part.items()}


# -- tail advance (L1b): the sweeper must extend the tail, not only heal holes -


def _now_fixed(ts: int):
    """Pin ``_utcnow_ms`` so a 'stale tail' is expressible without waiting."""
    return patch.object(km_mod, "_utcnow_ms", return_value=ts)


def _forward_service(candles: list[KlineEvent], *, page: int) -> MagicMock:
    """A service honouring ``start_time``/``limit`` like a real exchange."""

    async def _fetch(
        *, symbol, exchange, market_type, interval, limit=_PAGE, start_time=None, **kw
    ):
        # Binance's startTime is inclusive on the bar's open time. Tail advance
        # passes cursor+1 (no bar there), the seal passes the bar itself.
        pool = [k for k in sorted(candles, key=lambda k: k.timestamp)]
        if start_time is not None:
            pool = [k for k in pool if k.timestamp >= start_time]
        return pool[: min(limit, page)]

    svc = MagicMock()
    svc.fetch_klines_rest = AsyncMock(side_effect=_fetch)
    return svc


class TestTailAdvance:
    @pytest.mark.asyncio
    async def test_sweep_extends_a_stale_tail(self, manager: KlineDataManager) -> None:
        # Local ends at BASE_TS+2h; exchange has bars through BASE_TS+9h and
        # 'now' is BASE_TS+10h. The sweeper's job is to close that gap: the
        # old implementation only healed interior holes and left the tail
        # frozen forever (901 partitions, only 45 fresh in production).
        manager._db.put_klines(_contiguous(3))
        now = BASE_TS + 10 * HOUR_MS
        exchange = _contiguous(10)
        manager.set_service(_service(exchange))
        with _now_fixed(now):
            report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.partitions_advanced == 1
        latest = manager._db.get_latest_timestamp(
            "binance", "futures", "BTC/USDT", HOUR
        )
        assert latest == BASE_TS + 9 * HOUR_MS
        assert (
            manager._db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS) == []
        )

    @pytest.mark.asyncio
    async def test_tail_advance_walks_multiple_pages(
        self, manager: KlineDataManager
    ) -> None:
        # A long outage must not be filled with a single page. The exchange
        # caps this test's page at 50 while 300 bars are missing.
        manager._db.put_klines(_contiguous(1))
        now = BASE_TS + 400 * HOUR_MS
        exchange = _contiguous(400)
        manager.set_service(_forward_service(exchange, page=50))
        with _now_fixed(now):
            await manager.sweep_once(allowed_sources={("binance", "futures")})
        latest = manager._db.get_latest_timestamp(
            "binance", "futures", "BTC/USDT", HOUR
        )
        assert latest == BASE_TS + 399 * HOUR_MS

    @pytest.mark.asyncio
    async def test_fresh_tail_costs_no_rest(self, manager: KlineDataManager) -> None:
        # A partition already current must not issue a fetch at all — the
        # periodic sweep runs every 30 min over ~900 partitions.
        manager._db.put_klines(_contiguous(5))
        svc = _service([])
        manager.set_service(svc)
        now = BASE_TS + 4 * HOUR_MS
        with _now_fixed(now):
            await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert svc.fetch_klines_rest.await_count == 0

    @pytest.mark.asyncio
    async def test_tail_advance_respects_page_budget(
        self, manager: KlineDataManager
    ) -> None:
        # The budget bounds REST work per partition per round; a partition
        # further behind than the budget picks the rest up next round instead
        # of stalling the whole sweep.
        manager._db.put_klines(_contiguous(1))
        now = BASE_TS + 1000 * HOUR_MS
        manager.set_service(_forward_service(_contiguous(1000), page=10))
        with _now_fixed(now):
            report = await manager.sweep_once(
                allowed_sources={("binance", "futures")},
                page_budget=3,
                tail_page_budget=3,
            )
        latest = manager._db.get_latest_timestamp(
            "binance", "futures", "BTC/USDT", HOUR
        )
        assert latest is not None and latest < BASE_TS + 1000 * HOUR_MS
        assert report.pages_fetched == 3

    @pytest.mark.asyncio
    async def test_tail_advance_skips_while_hole_open(
        self, manager: KlineDataManager
    ) -> None:
        # Filling a tail above an open hole would extend the series across a
        # known gap; the invariant is that the watermark — and therefore the
        # advance — waits for the hole. The hole here is unfillable.
        manager._db.put_klines(_with_hole(10, hole_at=4, hole_len=3))
        now = BASE_TS + 10 * HOUR_MS
        manager.set_service(_service([]))
        with _now_fixed(now):
            report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.partitions_advanced == 0
        assert (
            manager._db.get_latest_timestamp("binance", "futures", "BTC/USDT", HOUR)
            == BASE_TS + 9 * HOUR_MS
        )

    @pytest.mark.asyncio
    async def test_empty_tail_probe_is_memoized(
        self, manager: KlineDataManager
    ) -> None:
        # A source that answers "nothing newer" (delisted symbol, lagging feed)
        # must not be re-asked every 30 minutes forever. The first probe is
        # recorded; later rounds inside the TTL stay REST-silent.
        manager._db.put_klines(_contiguous(3))
        svc = _service([])
        manager.set_service(svc)
        now = BASE_TS + 10 * HOUR_MS
        with _now_fixed(now):
            await manager.sweep_once(allowed_sources={("binance", "futures")})
            assert svc.fetch_klines_rest.await_count == 1
            for _ in range(5):
                await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert svc.fetch_klines_rest.await_count == 1  # memo suppressed the rest
        cursor, probed_at = manager._db.get_tail_probe(
            "binance", "futures", "BTC/USDT", HOUR
        )
        assert cursor == BASE_TS + 2 * HOUR_MS
        assert probed_at == now

    @pytest.mark.asyncio
    async def test_tail_probe_expires_after_ttl(
        self, manager: KlineDataManager
    ) -> None:
        # The memo is a cache, not a verdict: a delisted symbol that relists
        # (or a feed that catches up) must be probed again once the TTL lapses.
        manager._db.put_klines(_contiguous(3))
        svc = _service([])
        manager.set_service(svc)
        with _now_fixed(BASE_TS + 10 * HOUR_MS):
            await manager.sweep_once(allowed_sources={("binance", "futures")})
        with _now_fixed(BASE_TS + 10 * HOUR_MS + km_mod._TAIL_PROBE_TTL_MS + 1):
            await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert svc.fetch_klines_rest.await_count == 2

    @pytest.mark.asyncio
    async def test_memo_for_older_cursor_does_not_block(
        self, manager: KlineDataManager
    ) -> None:
        # The memo records *where* the source stopped. Once local data moves
        # past that cursor (another sync path wrote bars), the memo is stale
        # evidence and the next round probes again rather than waiting the TTL.
        manager._db.put_klines(_contiguous(3))
        svc = _service([])
        manager.set_service(svc)
        with _now_fixed(BASE_TS + 10 * HOUR_MS):
            await manager.sweep_once(allowed_sources={("binance", "futures")})
            assert manager._db.get_tail_probe(
                "binance", "futures", "BTC/USDT", HOUR
            ) == (BASE_TS + 2 * HOUR_MS, BASE_TS + 10 * HOUR_MS)
            manager._db.put_klines(_contiguous(6))  # local moves 2h -> 5h
            await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert svc.fetch_klines_rest.await_count == 2
        assert manager._db.get_tail_probe("binance", "futures", "BTC/USDT", HOUR) == (
            BASE_TS + 5 * HOUR_MS,
            BASE_TS + 10 * HOUR_MS,
        )

    @pytest.mark.asyncio
    async def test_new_empty_probe_overwrites_memo(
        self, manager: KlineDataManager
    ) -> None:
        # A successful advance followed by a fresh empty probe must record the
        # new cursor; otherwise a second, later outage would be masked by the
        # first outage's memo.
        manager._db.put_klines(_contiguous(3))
        manager.set_service(_service([]))
        with _now_fixed(BASE_TS + 10 * HOUR_MS):
            await manager.sweep_once(allowed_sources={("binance", "futures")})
            assert (
                manager._db.get_tail_probe("binance", "futures", "BTC/USDT", HOUR)[0]
                == BASE_TS + 2 * HOUR_MS
            )
        # New bars arrive through another path (e.g. the WS sync).
        manager._db.put_klines(_contiguous(6))
        with _now_fixed(BASE_TS + 20 * HOUR_MS):
            await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert manager._db.get_tail_probe("binance", "futures", "BTC/USDT", HOUR) == (
            BASE_TS + 5 * HOUR_MS,
            BASE_TS + 20 * HOUR_MS,
        )

    @pytest.mark.asyncio
    async def test_successful_advance_has_no_memo(
        self, manager: KlineDataManager
    ) -> None:
        # A round that actually advanced is not evidence of exhaustion and must
        # not leave a memo behind.
        manager._db.put_klines(_contiguous(3))
        manager.set_service(_service(_contiguous(10)))
        with _now_fixed(BASE_TS + 10 * HOUR_MS):
            await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert manager._db.get_tail_probe("binance", "futures", "BTC/USDT", HOUR) == (
            None,
            None,
        )

    @pytest.mark.asyncio
    async def test_hole_filled_elsewhere_stops_blocking_tail(
        self, manager: KlineDataManager
    ) -> None:
        # The ledger is written by the sweeper but the data is not: sync,
        # backfill and the seal all write bars. A hole filled by one of those
        # leaves an ``permanent=0`` row behind, and _advance_tail skips any
        # partition with an open hole — so the tail stalls forever behind a
        # hole that no longer exists. Measured in production: BTC futures 5m
        # sat 13 bars behind on three such ghost rows (their ranges were
        # already fully filled: 36,128 expected == 36,128 present).
        candles = _with_hole(10, hole_at=4, hole_len=3)
        manager._db.put_klines(candles)
        manager._db.record_hole_attempt(
            "binance",
            "futures",
            "BTC/USDT",
            HOUR,
            BASE_TS + 3 * HOUR_MS,
            BASE_TS + 7 * HOUR_MS,
            now_ms=BASE_TS + 9 * HOUR_MS,
        )
        # Another writer fills the gap.
        manager._db.put_klines(_contiguous(10))
        assert (
            manager._db.find_gaps("binance", "futures", "BTC/USDT", HOUR, HOUR_MS) == []
        )

        manager.set_service(_service(_contiguous(12)))
        with _now_fixed(BASE_TS + 12 * HOUR_MS):
            await manager.sweep_once(allowed_sources={("binance", "futures")})

        assert (
            manager._db.list_holes("binance", "futures", "BTC/USDT", HOUR) == []
        ), "hole filled by another writer must leave the ledger"
        assert (
            manager._db.get_latest_timestamp("binance", "futures", "BTC/USDT", HOUR)
            == BASE_TS + 11 * HOUR_MS
        ), "the tail must advance once the ghost hole is gone"


class TestSealStaleOpenBars:
    """Bars stored mid-candle must be re-fetched once they close.

    Both tail paths start strictly after the newest stored stamp, so nothing in
    the manager ever re-reads a bar it already has. On the live store that left
    3,345 partial bars (close off by ~$140, volume understated up to 20×) spread
    over 897 partitions, some 63 days old.
    """

    @staticmethod
    def _partial(ts: int, *, close: float = 1.0) -> KlineEvent:
        return KlineEvent(
            exchange="binance",
            market_type="futures",
            symbol="BTC/USDT",
            timestamp=ts,
            interval=HOUR,
            open=close,
            high=close,
            low=close,
            close=close,
            volume=1.0,
            closed=False,
        )

    @pytest.mark.asyncio
    async def test_stale_partial_is_rewritten_from_rest(
        self, manager: KlineDataManager
    ) -> None:
        partial = self._partial(BASE_TS + 2 * HOUR_MS)
        manager._db.put_klines([*_contiguous(2), partial, *_contiguous(3)[3:]])
        closed = _kline(BASE_TS + 2 * HOUR_MS, close=999.0)
        manager.set_service(_service([closed]))
        with _now_fixed(BASE_TS + 3 * HOUR_MS):
            report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        frame = manager._db.get_klines(
            "binance", "futures", "BTC/USDT", HOUR, None, None
        )
        row = frame[frame.timestamp == BASE_TS + 2 * HOUR_MS].iloc[0]
        assert float(row.close) == 999.0
        assert int(row.closed) == 1
        assert report.stale_open_sealed == 1

    @pytest.mark.asyncio
    async def test_forming_bar_is_left_alone(self, manager: KlineDataManager) -> None:
        # The newest bar is legitimately still forming; re-fetching it every
        # round would rewrite a live candle from a stale response.
        manager._db.put_klines(_contiguous(2) + [self._partial(BASE_TS + 2 * HOUR_MS)])
        svc = _service([])
        manager.set_service(svc)
        with _now_fixed(BASE_TS + 2 * HOUR_MS + HOUR_MS // 2):
            report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.stale_open_sealed == 0
        frame = manager._db.get_klines(
            "binance", "futures", "BTC/USDT", HOUR, None, None
        )
        assert int(frame.iloc[-1].closed) == 0

    @pytest.mark.asyncio
    async def test_only_closed_answers_are_written_back(
        self, manager: KlineDataManager
    ) -> None:
        # A source may still report the bar as forming (exchange lag). Writing
        # that back would reintroduce the partial with a fresh timestamp.
        manager._db.put_klines(_contiguous(2) + [self._partial(BASE_TS + 2 * HOUR_MS)])
        manager.set_service(_service([self._partial(BASE_TS + 2 * HOUR_MS, close=5.0)]))
        with _now_fixed(BASE_TS + 3 * HOUR_MS):
            report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.stale_open_sealed == 0
        frame = manager._db.get_klines(
            "binance", "futures", "BTC/USDT", HOUR, None, None
        )
        row = frame[frame.timestamp == BASE_TS + 2 * HOUR_MS].iloc[0]
        assert float(row.close) == 1.0

    @pytest.mark.asyncio
    async def test_okx_seals_backwards(self, manager: KlineDataManager) -> None:
        # OKX's REST adapter honours only end_time (backwards), so the seal
        # pass must reach it that way: a start_time request is narrowed after
        # the newest page is fetched and can never return an old bar.
        okx = {"exchange": "okx", "market_type": "spot"}
        partial = KlineEvent(
            timestamp=BASE_TS + 2 * HOUR_MS,
            interval=HOUR,
            open=1.0,
            high=1.0,
            low=1.0,
            close=1.0,
            volume=1.0,
            closed=False,
            symbol="BTC/USDT",
            **okx,
        )
        manager._db.put_klines(
            [KlineEvent(**{**k.model_dump(), **okx}) for k in _contiguous(2)]
            + [partial]
        )
        closed = KlineEvent(
            timestamp=BASE_TS + 2 * HOUR_MS,
            interval=HOUR,
            open=1.0,
            high=1.0,
            low=1.0,
            close=77.0,
            volume=9.0,
            closed=True,
            symbol="BTC/USDT",
            **okx,
        )
        svc = MagicMock()
        svc.fetch_klines_rest = AsyncMock(return_value=[closed])
        manager.set_service(svc)
        with _now_fixed(BASE_TS + 3 * HOUR_MS):
            report = await manager.sweep_once(allowed_sources={("okx", "spot")})
        assert report.stale_open_sealed == 1
        calls = svc.fetch_klines_rest.await_args_list
        assert calls and all("end_time" in c.kwargs for c in calls)
        assert all("start_time" not in c.kwargs for c in calls)

    @pytest.mark.asyncio
    async def test_seal_walks_past_a_spot_sized_page(
        self, manager: KlineDataManager
    ) -> None:
        # Spot pages cap at 1,000 rows while _PAGE is 1,500. Progress must be
        # measured from the page actually returned, not the limit requested,
        # or the next cursor skips the stamps in between.
        stamps = [BASE_TS + i * HOUR_MS for i in (0, 1, 1100, 1200)]
        manager._db.put_klines([self._partial(ts) for ts in stamps])
        healed = [_kline(ts, close=42000.0) for ts in stamps]
        manager.set_service(_forward_service(healed, page=1000))
        with _now_fixed(stamps[-1] + 2 * HOUR_MS):
            report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.stale_open_sealed == len(stamps)
        frame = manager._db.get_klines(
            "binance", "futures", "BTC/USDT", HOUR, None, None
        )
        assert (frame.closed.astype(int) == 1).all()
