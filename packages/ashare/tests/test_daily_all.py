"""Tests for the whole-market EOD increment (B1) and its read face (B3).

Zero network: ``fetch_daily_rows`` and ``get_a_share_list`` are monkeypatched
with rows shaped like the real Sina response, and the DB lives under
``tmp_path`` so a test never touches the user's real klines.db.

The properties worth guarding, in the order they bite in production:

* **A halted symbol is not a current symbol.** Sina answers a suspended
  session with ``0`` prices; ``parse_rows`` drops those rows. Counting the raw
  newest *row* instead of the newest *usable bar* would make a halted name look
  like it traded today — the exact confusion the ``behind`` count exists to
  surface.
* **A session that produced nothing is not silence.** The report must carry the
  counts and the failed symbol list; a pass that returns quietly after writing
  zero rows reads downstream as "the market had no data today".
* **The read face reports a stale row for a halted symbol rather than dropping
  it.** A cross-section that omits halted names turns a suspension into an
  invisible absence, indistinguishable from a delisting.
* **``prev_close`` is the symbol's own previous close**, not the prior row of
  whatever ordering the frame happens to be in — the trap this is most likely
  to die from, so the fixture gives the two symbols different price levels.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd
import pytest
from selene_space_ashare import daily_all as da
from selene_space_ashare import market_daily as md
from selene_space_market.kline_db import KlineDB
from selene_space_types.market import KlineEvent

# Distinct closes per symbol, so a `prev_close` test cannot pass by accident
# when every row happens to carry the same price.
_MOUTAI = [("2026-10-14", 100.0), ("2026-10-15", 101.0), ("2026-10-16", 102.0)]
_PINGAN = [("2026-10-14", 20.0), ("2026-10-15", 21.0)]  # halted on the 16th


def _rows(series: list[tuple[str, float]], *, volume: str = "1234"):
    """Sina-shaped rows: OHLC around the given close."""
    return [
        {
            "day": day,
            "open": str(close),
            "high": str(close + 1),
            "low": str(close - 1),
            "close": str(close),
            "volume": volume,
        }
        for day, close in series
    ]


def _halted_row(day: str):
    """A suspended session exactly as Sina returns it: prices of zero."""
    return {
        "day": day,
        "open": "0",
        "high": "0",
        "low": "0",
        "close": "0",
        "volume": "0",
    }


@pytest.fixture
def db(tmp_path):
    """A temp DB; no test ever touches the user's real klines.db."""
    handle = KlineDB(db_path=str(tmp_path / "klines.db"))
    handle.open()
    try:
        yield handle
    finally:
        handle.close()


@pytest.fixture
def script(monkeypatch):
    """Install a scripted fetch table; unknown symbols raise like a 404 would."""

    def install(rows_by_symbol):
        def fake_fetch(symbol, *, datalen=da.EOD_DATALEN, session=None, timeout=15.0):
            if symbol not in rows_by_symbol:
                raise ConnectionError(f"no scripted rows for {symbol}")
            return rows_by_symbol[symbol]

        monkeypatch.setattr(da, "fetch_daily_rows", fake_fetch)

    return install


def _ms(day: str) -> int:
    """Midnight-UTC epoch millis for a bare date (the KlineEvent convention)."""
    return int(
        datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
        * 1000
    )


# ─── B1: EOD increment ─────────────────────────────────────────────────────


class TestFetchFailures:
    def test_one_symbol_failing_does_not_abort_the_rest(self, db, monkeypatch):
        """The failure mode the binance script this mirrors was written against."""

        def fake_fetch(symbol, *, datalen=da.EOD_DATALEN, session=None, timeout=15.0):
            if symbol == "000001.SZ":
                raise ConnectionError("boom")
            return _rows(_MOUTAI)

        monkeypatch.setattr(da, "fetch_daily_rows", fake_fetch)
        report = da.sync_eod(db, symbols=["600519.SH", "000001.SZ"])

        assert report.failed == ("000001.SZ",)
        assert report.symbols_failed == 1
        assert report.symbols_updated == 1  # the healthy symbol still wrote
        assert not report.ok
        assert db.count(da.EXCHANGE, da.MARKET_TYPE, "600519.SH", "1d") == 3

    def test_failed_symbols_are_not_counted_as_behind(self, db, monkeypatch):
        """Unknown is not halted; folding them together hides a transport fault."""

        def fake_fetch(symbol, *, datalen=da.EOD_DATALEN, session=None, timeout=15.0):
            if symbol == "600519.SH":
                return _rows(_MOUTAI)
            raise OSError("network down")

        monkeypatch.setattr(da, "fetch_daily_rows", fake_fetch)
        report = da.sync_eod(db, symbols=["600519.SH", "000001.SZ"])

        assert report.symbols_behind == 0
        assert report.symbols_failed == 1


class TestHaltedSession:
    def test_zero_price_rows_are_dropped_and_counted(self, db, script):
        """A halted row is not a bar; dropping it silently reads as 'no data'."""
        script({"600519.SH": [*_rows(_MOUTAI), _halted_row("2026-10-19")]})
        report = da.sync_eod(db, symbols=["600519.SH"])

        assert report.dropped_nonpositive == 1
        assert report.symbols_updated == 1
        # Newest *usable* bar: the halted session must not set the watermark.
        assert report.latest_session == "2026-10-16"

    def test_all_rows_halted_is_empty_not_updated(self, db, script):
        script({"600519.SH": [_halted_row("2026-10-16"), _halted_row("2026-10-15")]})
        report = da.sync_eod(db, symbols=["600519.SH"])

        assert report.symbols_empty == 1
        assert report.symbols_updated == 0
        assert report.latest_session is None


class TestIdempotence:
    def test_rerun_does_not_duplicate(self, db, script):
        script({"600519.SH": _rows(_MOUTAI)})

        first = da.sync_eod(db, symbols=["600519.SH"])
        second = da.sync_eod(db, symbols=["600519.SH"])

        assert first.symbols_updated == 1
        assert second.symbols_unchanged == 1  # already current
        assert db.count(da.EXCHANGE, da.MARKET_TYPE, "600519.SH", "1d") == 3


class TestBehindCount:
    def test_symbols_older_than_the_market_are_counted(self, db, script):
        """Suspensions are real here: 158 symbols carry genuine date holes."""
        script(
            {
                "600519.SH": _rows(_MOUTAI),
                "000001.SZ": _rows(_MOUTAI),
                "920002.BJ": _rows([("2026-10-09", 5.0)]),  # halted all week
            }
        )
        report = da.sync_eod(db, symbols=["600519.SH", "000001.SZ", "920002.BJ"])

        assert report.latest_session == "2026-10-16"
        assert report.symbols_behind == 1


class TestUniverse:
    def test_beijing_codes_map(self):
        """The old SZ/SH-only map raised on every .BJ code — 349 symbols lost."""
        from selene_space_core.sina_symbols import code_to_symbol

        assert code_to_symbol("920002") == "920002.BJ"
        assert code_to_symbol("600519") == "600519.SH"
        assert code_to_symbol("000001") == "000001.SZ"

    def test_invalid_codes_are_reported_not_dropped(self, monkeypatch):
        """An unmappable universe entry is a data problem the caller must see."""
        monkeypatch.setattr(
            "selene_space_ashare.data_fetcher.get_a_share_list",
            lambda force=False: pd.DataFrame(
                [("600519", "贵州茅台"), ("920002", "万达轴承"), ("BAD", "乱码")],
                columns=["code", "name"],
            ),
        )
        symbols, invalid = da.whole_market_symbols()

        assert symbols == ["600519.SH", "920002.BJ"]
        assert invalid == ["BAD"]


class TestScheduling:
    def test_next_run_is_strictly_after(self):
        """A loop waking at the target must sleep a day, not spin on a zero delay."""
        monday = datetime(2026, 10, 19, 15, 5)  # exactly the EOD time
        target = da.next_eod_run_at(monday)

        assert target > monday
        assert (target.hour, target.minute) == (15, 5)
        assert target.date().isoformat() == "2026-10-20"

    def test_expected_session_before_close_is_previous_day(self):
        """Asking for today's bar mid-session would mark the whole market stale."""
        assert da.expected_session(datetime(2026, 10, 16, 12, 0)) == "2026-10-15"

    def test_expected_session_after_close_is_today(self):
        assert da.expected_session(datetime(2026, 10, 16, 15, 30)) == "2026-10-16"

    def test_is_due_false_on_empty_partition(self, db):
        """The backfill owns history; the increment must not try to create it."""
        assert da.is_due(db, datetime(2026, 10, 16, 16, 0)) is False

    def test_is_due_true_when_partition_lags(self, db, script):
        script({"600519.SH": _rows(_MOUTAI[:2])})  # through Thursday
        da.sync_eod(db, symbols=["600519.SH"])

        assert da.is_due(db, datetime(2026, 10, 16, 16, 0)) is True

    def test_is_due_false_when_current(self, db, script):
        script({"600519.SH": _rows(_MOUTAI)})  # through Friday
        da.sync_eod(db, symbols=["600519.SH"])

        assert da.is_due(db, datetime(2026, 10, 16, 16, 0)) is False


# ─── B3: read face ─────────────────────────────────────────────────────────


@pytest.fixture
def seeded(db, script, monkeypatch):
    """The cross-section both B3 tests and the SQL test read from."""
    script({"600519.SH": _rows(_MOUTAI), "000001.SZ": _rows(_PINGAN)})
    da.sync_eod(db, symbols=["600519.SH", "000001.SZ"])
    monkeypatch.setattr(md, "_universe_names", lambda: {"600519.SH": "贵州茅台"})
    return db


class TestMarketDailyUniverse:
    def test_columns_contract(self, seeded):
        """Sina returns no turnover, so an `amount` column would be fabricated."""
        frame = md.market_daily_universe(seeded, end_date="2026-10-16")

        assert list(frame.columns) == list(md.COLUMNS)
        assert "amount" not in frame.columns
        assert frame.attrs == {"adjust": "raw", "source": "sina"}

    def test_prev_close_is_the_symbols_own_previous_close(self, seeded):
        """Multi-symbol ordering trap: prev_close is per symbol, not per row."""
        frame = md.market_daily_universe(seeded, end_date="2026-10-16")
        by_symbol = {r["symbol"]: r for _, r in frame.iterrows()}

        assert by_symbol["600519.SH"]["prev_close"] == pytest.approx(101.0)
        assert by_symbol["000001.SZ"]["prev_close"] == pytest.approx(20.0)

    def test_close_is_the_latest_bar_at_or_before_end(self, seeded):
        frame = md.market_daily_universe(seeded, end_date="2026-10-16")
        by_symbol = {r["symbol"]: r for _, r in frame.iterrows()}

        assert by_symbol["600519.SH"]["close"] == pytest.approx(102.0)
        assert by_symbol["600519.SH"]["date"] == "2026-10-16"

    def test_halted_symbol_keeps_its_own_date_and_stays_in_the_frame(self, seeded):
        """Dropping a halted name turns a suspension into an invisible absence."""
        frame = md.market_daily_universe(seeded, end_date="2026-10-16")
        row = frame[frame["symbol"] == "000001.SZ"].iloc[0]

        assert row["date"] == "2026-10-15"  # last session it actually traded
        assert row["close"] == pytest.approx(21.0)
        assert len(frame) == 2  # not filtered out

    def test_name_blank_outside_the_universe(self, seeded):
        frame = md.market_daily_universe(seeded, end_date="2026-10-16")
        by_symbol = {r["symbol"]: r["name"] for _, r in frame.iterrows()}

        assert by_symbol["600519.SH"] == "贵州茅台"
        assert by_symbol["000001.SZ"] == ""

    def test_empty_partition_returns_typed_empty_frame(self, db, monkeypatch):
        """An empty frame must still carry the columns, not raise a KeyError."""
        monkeypatch.setattr(md, "_universe_names", lambda: {})
        frame = md.market_daily_universe(db, end_date="2026-10-16")

        assert frame.empty
        assert list(frame.columns) == list(md.COLUMNS)

    def test_adjust_other_than_raw_raises(self, db):
        """A silent fallback would hand raw prices to a caller asking for adjusted."""
        with pytest.raises(ValueError, match="raw"):
            md.market_daily_universe(db, end_date="2026-10-16", adjust="qfq")  # type: ignore[arg-type]

    def test_bad_end_date_raises(self, db):
        with pytest.raises(ValueError):
            md.market_daily_universe(db, end_date="2026-13-45")

    def test_end_date_excludes_later_bars(self, seeded):
        """Asking for an earlier date must not leak a later bar into the frame."""
        frame = md.market_daily_universe(seeded, end_date="2026-10-15")
        by_symbol = {r["symbol"]: r for _, r in frame.iterrows()}

        assert by_symbol["600519.SH"]["date"] == "2026-10-15"
        assert by_symbol["600519.SH"]["close"] == pytest.approx(101.0)


class TestCrossSectionSQL:
    """The read face is a query; guard it directly against a stub-only pass."""

    def test_returns_latest_bar_at_or_before_end_with_prior_close(self, db):
        def ev(symbol: str, day: str, close: float) -> KlineEvent:
            return KlineEvent(
                exchange="astock",
                market_type="daily_all",
                symbol=symbol,
                interval="1d",
                timestamp=_ms(day),
                open=close,
                high=close,
                low=close,
                close=close,
                volume=100.0,
                closed=True,
            )

        db.put_klines(
            [
                ev("600519.SH", "2026-10-14", 100.0),
                ev("600519.SH", "2026-10-15", 101.0),
                ev("600519.SH", "2026-10-20", 999.0),  # AFTER end_ms
            ]
        )
        frame = db.get_cross_section(
            "astock", "daily_all", "1d", end_ms=_ms("2026-10-16")
        )
        rows = frame.to_dict("records")

        assert len(rows) == 1
        assert rows[0]["close"] == pytest.approx(101.0)  # the 10-20 bar excluded
        assert rows[0]["prev_close"] == pytest.approx(100.0)

    def test_symbol_with_a_single_bar_has_null_prev_close(self, db):
        """No prior bar → NULL, not a same-day close (which would fake a 0% move)."""
        db.put_klines(
            [
                KlineEvent(
                    exchange="astock",
                    market_type="daily_all",
                    symbol="920002.BJ",
                    interval="1d",
                    timestamp=_ms("2026-10-15"),
                    open=5.0,
                    high=6.0,
                    low=4.0,
                    close=5.5,
                    volume=10.0,
                    closed=True,
                )
            ]
        )
        frame = db.get_cross_section(
            "astock", "daily_all", "1d", end_ms=_ms("2026-10-16")
        )
        assert len(frame) == 1
        assert pd.isna(frame.iloc[0]["prev_close"])

    def test_partition_max_timestamp(self, db):
        """How the EOD loop tells 'already ran today' from 'lagging'."""
        assert db.get_partition_max_timestamp("astock", "daily_all", "1d") is None

        db.put_klines(
            [
                KlineEvent(
                    exchange="astock",
                    market_type="daily_all",
                    symbol="600519.SH",
                    interval="1d",
                    timestamp=_ms("2026-10-16"),
                    open=1.0,
                    high=1.0,
                    low=1.0,
                    close=1.0,
                    volume=1.0,
                    closed=True,
                )
            ]
        )
        assert db.get_partition_max_timestamp("astock", "daily_all", "1d") == _ms(
            "2026-10-16"
        )
