"""Gap sweep (L2) + paginated tail sync (L1) coverage.

Covers: KlineDB.find_gaps fast/slow paths, scan-watermark state, the hole
ledger's attempt->permanent flip, the sweeper's domain gate (only partitions
with a registered source), the watermark invariant (never advance past an open
hole), backwards-pagination tail catch-up, and per-partition quality isolation.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

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
        manager._db.put_klines(_with_hole(10, hole_at=4, hole_len=3))
        svc = _service([])
        manager.set_service(svc)
        for _ in range(4):  # rounds 1-3 build attempts, round 4 is post-permanent
            await manager.sweep_once(allowed_sources={("binance", "futures")})
        calls_at_permanent = svc.fetch_klines_rest.await_count
        await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert svc.fetch_klines_rest.await_count == calls_at_permanent  # zero new REST
        # The hole is still visible (permanent holes stay in health accounting).
        report = await manager.sweep_once(allowed_sources={("binance", "futures")})
        assert report.holes_permanent == 1

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
