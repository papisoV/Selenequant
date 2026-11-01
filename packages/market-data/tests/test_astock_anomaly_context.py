"""Coverage for MarketDataAstockContext — prev_close and prior_daily_bars
must read only settled bars STRICTLY EARLIER than the current session day,
against a real (in-memory) KlineDB."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from selene_space_market.kline_db import KlineDB
from selene_space_types.market import KlineEvent

from app.astock_anomaly_wiring import MarketDataAstockContext

_CST = timezone(timedelta(hours=8))
# Mid-session on 2026-10-13 (Tuesday) — the moment the detector actually runs.
# Deliberately NOT midnight: the cutoff is midnight, and the bug this file
# guards only shows when 'now' is after it.
SESSION_DAY = datetime(2026, 10, 13, 10, 0, tzinfo=_CST)


def _daily_bar(
    day: date,
    close: float,
    *,
    symbol: str = "600519.SH",
    exchange: str = "astock",
    market_type: str = "astock",
) -> KlineEvent:
    """One daily bar, stamped at CST midnight of ``day``.

    Production bars look exactly like this — ``astock_source._parse_rows``
    parses a bare ``%Y-%m-%d`` — which is what places today's still-forming
    bar precisely ON the settlement cutoff. Stamping these at, say, 15:00
    would hide that collision and let a leaking cutoff pass unnoticed.
    """
    ts = int(datetime(day.year, day.month, day.day, tzinfo=_CST).timestamp() * 1000)
    return KlineEvent(
        exchange=exchange,
        market_type=market_type,
        symbol=symbol,
        timestamp=ts,
        interval="1d",
        open=close - 0.5,
        high=close + 0.5,
        low=close - 1.0,
        close=close,
        volume=1_000_000.0,
        closed=True,
    )


def _seed(db: KlineDB) -> None:
    bars = [
        _daily_bar(date(2026, 10, 9), 10.0),
        _daily_bar(date(2026, 10, 12), 10.5),
        # "Today's" forming bar — same midnight stamp, so it sits exactly on
        # the cutoff. It must never be visible to the detector.
        _daily_bar(date(2026, 10, 13), 11.0),
    ]
    assert db.put_klines(bars) == 3


class TestPrevClose:
    def test_prev_close_is_last_strictly_earlier_bar(self, tmp_path) -> None:
        db = KlineDB(str(tmp_path / "klines.db"))
        db.open()
        _seed(db)
        context = MarketDataAstockContext(db)
        with _patch_today(SESSION_DAY):
            assert context.prev_close("600519.SH") == 10.5

    def test_no_earlier_bars_returns_none(self, tmp_path) -> None:
        db = KlineDB(str(tmp_path / "klines.db"))
        db.open()
        db.put_klines([_daily_bar(date(2026, 10, 13), 11.0)])
        context = MarketDataAstockContext(db)
        with _patch_today(SESSION_DAY):
            assert context.prev_close("600519.SH") is None


class TestPriorDailyBars:
    def test_today_bar_filtered_out_and_order_is_old_to_new(self, tmp_path) -> None:
        db = KlineDB(str(tmp_path / "klines.db"))
        db.open()
        _seed(db)
        context = MarketDataAstockContext(db)
        with _patch_today(SESSION_DAY):
            bars = context.prior_daily_bars("600519.SH", 60)
        assert [bar.close for bar in bars] == [10.0, 10.5]

    def test_n_limit_takes_most_recent(self, tmp_path) -> None:
        db = KlineDB(str(tmp_path / "klines.db"))
        db.open()
        _seed(db)
        context = MarketDataAstockContext(db)
        with _patch_today(SESSION_DAY):
            bars = context.prior_daily_bars("600519.SH", 1)
        assert [bar.close for bar in bars] == [10.5]


class TestPartitionResolution:
    """klines.db files A-share daily bars under two conventions that disagree:
    the bulk import writes qfq (adjusted) bars under market_type="astock" and
    freezes them at the import date, while the live AstockSource syncs raw,
    current bars under market_type="spot". The detector compares the daily
    baseline against raw intraday ticks, so it must read the live "spot"
    partition and only fall back to the adjusted import when "spot" is still
    empty — reading the wrong (or stale-adjusted) partition fires level signals
    on a baseline that no longer matches the price."""

    def test_reads_spot_partition_when_present(self, tmp_path) -> None:
        db = KlineDB(str(tmp_path / "klines.db"))
        db.open()
        db.put_klines([_daily_bar(date(2026, 10, 12), 10.5, market_type="spot")])
        context = MarketDataAstockContext(db)
        with _patch_today(SESSION_DAY):
            assert context.prev_close("600519.SH") == 10.5

    def test_falls_back_to_astock_import_when_spot_empty(self, tmp_path) -> None:
        db = KlineDB(str(tmp_path / "klines.db"))
        db.open()
        db.put_klines([_daily_bar(date(2026, 10, 12), 10.5, market_type="astock")])
        context = MarketDataAstockContext(db)
        with _patch_today(SESSION_DAY):
            assert context.prev_close("600519.SH") == 10.5

    def test_prefers_spot_partition_when_both_present(self, tmp_path) -> None:
        db = KlineDB(str(tmp_path / "klines.db"))
        db.open()
        db.put_klines([_daily_bar(date(2026, 10, 12), 10.5, market_type="spot")])
        db.put_klines([_daily_bar(date(2026, 10, 12), 99.0, market_type="astock")])
        context = MarketDataAstockContext(db)
        with _patch_today(SESSION_DAY):
            # The raw live bar, not the adjusted import that disagrees with it.
            assert context.prev_close("600519.SH") == 10.5


class _patch_today:
    """Freeze MarketDataAstockContext's view of 'now' for the block.

    The adapter derives the cutoff from ``datetime.now``; patch the datetime
    symbol it uses rather than real time so the test is day-independent.
    """

    def __init__(self, now: datetime) -> None:
        self._now = now

    def __enter__(self) -> None:
        import app.astock_anomaly_wiring as wiring

        # setattr/getattr, not attribute syntax: mypy reads ``wiring.datetime``
        # as the class symbol and rejects assigning over it.
        self._saved = getattr(wiring, "datetime")
        setattr(wiring, "datetime", _FrozenDatetime(self._now, self._saved))

    def __exit__(self, *exc: object) -> None:
        import app.astock_anomaly_wiring as wiring

        setattr(wiring, "datetime", self._saved)


class _FrozenDatetime:
    """Stands in for the datetime module inside the wiring module."""

    def __init__(self, now: datetime, real: type[datetime]) -> None:
        self._now = now
        self._real = real

    def now(self, tz: object | None = None) -> datetime:
        if tz is not None:
            return self._now.astimezone(tz)  # type: ignore[arg-type]
        return self._now.replace(tzinfo=None)
