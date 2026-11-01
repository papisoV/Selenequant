"""Tests for the Sina daily fetch/parse/gap layer (``sina_daily``).

Zero network: every transport call goes through an injected fake session, and
the parse/gap functions are pure.

The properties worth guarding, in the order they bite in production:

* **A bar is stamped at Beijing midnight, not host-local midnight.** The whole
  base keys on one epoch per session date. Naive ``datetime.strptime(...)``
  followed by ``.timestamp()`` resolves through the host zone, so on a UTC+8 dev
  box the bug is invisible — the same epoch either way. The subprocess test
  below is the only one with real discriminating power here.
* **A dropped row is counted, never silent.** Sina answers a halted session with
  ``0`` prices and occasionally a malformed row. Dropping is correct; dropping
  *quietly* reads downstream as "the market had no data that day", which is the
  exact confusion the counters exist to prevent.
* **A gap is measured on the actual dates, not on a ratio.** ``detect_gaps``
  walks consecutive days, so a recently-listed stock is not misreported as
  suspended. The threshold is a *strict* bound: a Spring-Festival-length break
  must not be flagged, and the comparison sits on the boundary.
* **An empty answer is an error, not "zero bars".** A valid prefix with no body
  means no history; the caller must see it rather than record an empty day.
"""

from __future__ import annotations

import os as _os

import pytest
from selene_space_ashare.sina_daily import (
    HEADERS,
    MAX_DATALEN,
    SINA_KLINE_URL,
    ParseResult,
    detect_gaps,
    detect_gaps_in_days,
    fetch_daily_rows,
    parse_rows,
)

# ---------------------------------------------------------------------------
# Helpers


def _row(day: str, close: float = 10.0, *, volume: str = "1234") -> dict[str, str]:
    """One Sina-shaped row: OHLC bracketing the given close."""
    return {
        "day": day,
        "open": str(close),
        "high": str(close + 1),
        "low": str(close - 1),
        "close": str(close),
        "volume": volume,
    }


def _halted(day: str) -> dict[str, str]:
    """A suspended session exactly as Sina returns it: every price zero."""
    return {
        "day": day,
        "open": "0",
        "high": "0",
        "low": "0",
        "close": "0",
        "volume": "0",
    }


class _Resp:
    """Minimal stand-in for ``requests.Response`` (text / raise / json)."""

    def __init__(self, text: str, payload: object = None, status: int = 200):
        self.text = text
        self._payload = payload
        self.status_code = status

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> object:
        return self._payload


class _Session:
    """Records the one call it received, so the request shape is assertable."""

    def __init__(self, resp: _Resp):
        self._resp = resp
        self.calls: list[tuple[str, dict]] = []

    def get(self, url: str, **kwargs) -> _Resp:
        self.calls.append((url, kwargs))
        return self._resp


# ---------------------------------------------------------------------------
# parse_rows


class TestParseRows:
    def test_maps_a_row_to_a_daily_all_event(self):
        result = parse_rows([_row("2026-01-05", 10.5)], "600519.SH")

        assert isinstance(result, ParseResult)
        assert len(result.events) == 1

        event = result.events[0]
        assert event.exchange == "astock"
        assert (
            event.market_type == "daily_all"
        )  # NOT "spot": that's the other partition
        assert event.symbol == "600519.SH"
        assert event.interval == "1d"
        assert event.closed is True
        assert event.open == pytest.approx(10.5)
        assert event.high == pytest.approx(11.5)
        assert event.low == pytest.approx(9.5)
        assert event.close == pytest.approx(10.5)
        assert event.volume == pytest.approx(1234.0)

    def test_empty_input_is_an_empty_result_not_an_error(self):
        result = parse_rows([], "600519.SH")

        assert result.events == ()
        assert result.dropped_nonpositive == 0
        assert result.dropped_unparsable == 0

    def test_input_is_not_mutated(self):
        """Pure by contract — a caller reusing the rows must not see edits."""
        rows = [_row("2026-01-05"), _halted("2026-01-06")]
        before = [dict(r) for r in rows]

        parse_rows(rows, "600519.SH")

        assert rows == before


class TestParseRowsDropsNonPositive:
    def test_zero_price_row_is_dropped_and_counted(self):
        """A halted row is not a bar; silence here reads as "no data today"."""
        rows = [_row("2026-01-05"), _halted("2026-01-06"), _row("2026-01-07", 11.0)]

        result = parse_rows(rows, "600519.SH")

        assert len(result.events) == 2
        assert result.dropped_nonpositive == 1
        assert result.dropped_unparsable == 0
        assert [e.timestamp for e in result.events] == sorted(
            e.timestamp for e in result.events
        )

    def test_a_single_negative_price_in_the_bracket_drops_the_row(self):
        """The guard is on the whole OHLC bracket, not just the close."""
        row = _row("2026-01-05")
        row["low"] = "-1"

        result = parse_rows([row], "600519.SH")

        assert result.events == ()
        assert result.dropped_nonpositive == 1

    def test_every_row_halted_yields_no_events_but_a_count(self):
        result = parse_rows([_halted("2026-01-05")], "600519.SH")

        assert result.events == ()
        assert result.dropped_nonpositive == 1


class TestParseRowsDropsUnparsable:
    def test_missing_day_is_counted_as_unparsable(self):
        rows = [
            _row("2026-01-05"),
            {"open": "1", "high": "2", "low": "0.5", "close": "1"},
        ]

        result = parse_rows(rows, "600519.SH")

        assert len(result.events) == 1
        assert result.dropped_unparsable == 1
        assert result.dropped_nonpositive == 0

    def test_unparsable_day_counts_as_unparsable_not_nonpositive(self):
        """The two counters must not fold together: one is a halt, one is a bug."""
        row = _row("2026-01-05")
        row["day"] = "not-a-date"  # after the spread, or _row's own day wins

        result = parse_rows([row], "600519.SH")

        assert result.events == ()
        assert result.dropped_unparsable == 1
        assert result.dropped_nonpositive == 0

    def test_bad_number_counts_as_unparsable(self):
        row = _row("2026-01-05")
        row["open"] = "N/A"

        result = parse_rows([row], "600519.SH")

        assert result.events == ()
        assert result.dropped_unparsable == 1
        assert result.dropped_nonpositive == 0

    def test_a_malformed_row_does_not_abort_the_rest(self):
        """One bad row must cost one row, not the whole session."""
        rows = [
            _row("2026-01-05"),
            {"day": "2026-01-06", "open": "x", "high": "2", "low": "0.5", "close": "1"},
            _row("2026-01-07"),
        ]

        result = parse_rows(rows, "600519.SH")

        assert len(result.events) == 2
        assert result.dropped_unparsable == 1


# ---------------------------------------------------------------------------
# stamp


class TestStamp:
    def test_bar_is_stamped_at_beijing_midnight(self):
        """The epoch must be CST midnight, not host-local midnight."""
        from datetime import datetime

        from selene_space_ashare.sina_daily import CST

        expected = int(datetime(2026, 1, 5, tzinfo=CST).timestamp() * 1000)
        event = parse_rows([_row("2026-01-05")], "600519.SH").events[0]

        assert event.timestamp == expected
        # And it must resolve back to 00:00 Beijing, not merely be "some" epoch.
        back = datetime.fromtimestamp(event.timestamp / 1000, tz=CST)
        assert (back.hour, back.minute) == (0, 0)
        assert back.date().isoformat() == "2026-01-05"

    def test_stamp_does_not_move_with_the_host_timezone(self):
        """Only the child process can see this: at UTC+8 the naive form agrees.

        ``datetime.strptime(...).timestamp()`` without a tzinfo resolves through
        the host zone, so on this box a host-local stamp *equals* Beijing
        midnight and the assertion above passes with the bug present. Forcing
        the child into UTC makes the two disagree by the offset.
        """
        import subprocess
        import sys

        snippet = (
            "from selene_space_ashare.sina_daily import parse_rows;"
            "print(parse_rows([{'day':'2026-10-02','open':'10.0','high':'11.0',"
            "'low':'9.5','close':'10.5','volume':'12345'}], '600519.SH')"
            ".events[0].timestamp)"
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


# ---------------------------------------------------------------------------
# fetch_daily_rows


class TestFetchDailyRows:
    def test_returns_the_row_list_and_asks_for_production_params(self):
        session = _Session(_Resp('[{"day":"2026-01-05"}]', [{"day": "2026-01-05"}]))

        rows = fetch_daily_rows("600519.SH", session=session)

        assert rows == [{"day": "2026-01-05"}]
        url, kwargs = session.calls[0]
        assert url == SINA_KLINE_URL
        # The prefix conversion happens here — a bare code would raise instead.
        assert kwargs["params"]["symbol"] == "sh600519"
        assert kwargs["params"]["datalen"] == MAX_DATALEN
        assert kwargs["params"]["scale"] == 240
        assert kwargs["headers"] == HEADERS

    def test_datalen_is_overridable(self):
        session = _Session(_Resp("[]", []))

        fetch_daily_rows("600519.SH", datalen=200, session=session)

        assert session.calls[0][1]["params"]["datalen"] == 200

    def test_a_bare_code_raises_rather_than_guessing_a_board(self):
        """Prefixing is the shared board rule's job; guessing here would misroute."""
        session = _Session(_Resp('[{"day":"2026-01-05"}]', [{"day": "2026-01-05"}]))

        with pytest.raises(ValueError, match="600519"):
            fetch_daily_rows("600519", session=session)

        assert session.calls == []  # never reached the network

    @pytest.mark.parametrize("body", ["", "null", "   \n "])
    def test_an_empty_body_is_an_error_not_zero_bars(self, body):
        """An empty answer for a valid prefix means no history — say so."""
        session = _Session(_Resp(body, None))

        with pytest.raises(ConnectionError, match="empty body"):
            fetch_daily_rows("600519.SH", session=session)

    def test_a_non_list_payload_is_an_error(self):
        session = _Session(_Resp('{"err":"x"}', {"err": "x"}))

        with pytest.raises(ValueError, match="not a row list"):
            fetch_daily_rows("600519.SH", session=session)

    def test_http_error_propagates(self):
        session = _Session(_Resp("nope", None, status=404))

        with pytest.raises(RuntimeError, match="404"):
            fetch_daily_rows("600519.SH", session=session)


# ---------------------------------------------------------------------------
# detect_gaps_in_days / detect_gaps


class TestDetectGapsInDays:
    def test_a_holiday_length_break_is_not_a_gap(self):
        """Spring Festival is the longest break (~9 days); it must stay quiet."""
        assert detect_gaps_in_days(["2026-02-10", "2026-02-19"]) == []

    def test_contiguous_days_have_no_gap(self):
        assert detect_gaps_in_days(["2026-01-05", "2026-01-06", "2026-01-07"]) == []

    def test_the_threshold_is_a_strict_bound(self):
        """Exactly the threshold is not a hole; one day more is."""
        assert detect_gaps_in_days(["2026-01-01", "2026-01-16"]) == []
        assert detect_gaps_in_days(["2026-01-01", "2026-01-17"]) == [
            ("2026-01-01", "2026-01-17", 16)
        ]

    def test_a_wide_jump_is_reported_with_its_span(self):
        assert detect_gaps_in_days(["2026-01-01", "2026-03-01"]) == [
            ("2026-01-01", "2026-03-01", 59)
        ]

    def test_input_order_is_irrelevant(self):
        """Trusting input order would silently miss a misordered page."""
        forward = detect_gaps_in_days(["2026-01-01", "2026-02-01", "2026-03-01"])
        reversed_ = detect_gaps_in_days(["2026-03-01", "2026-02-01", "2026-01-01"])

        assert forward == reversed_
        assert forward == [
            ("2026-01-01", "2026-02-01", 31),
            ("2026-02-01", "2026-03-01", 28),
        ]

    def test_a_malformed_day_is_skipped_not_crashed(self):
        """One unparsable date must not take the hole check down with it."""
        assert detect_gaps_in_days(["2026-01-01", "not-a-date", "2026-03-01"]) == [
            ("2026-01-01", "2026-03-01", 59)
        ]

    def test_all_malformed_yields_no_gaps(self):
        assert detect_gaps_in_days(["not-a-date", "also-not"]) == []

    def test_fewer_than_two_days_has_nothing_to_compare(self):
        assert detect_gaps_in_days(["2026-01-01"]) == []
        assert detect_gaps_in_days([]) == []


class TestDetectGaps:
    def test_reads_the_day_key_off_raw_rows(self):
        rows = [_row("2026-01-01"), _row("2026-03-01")]

        assert detect_gaps(rows) == [("2026-01-01", "2026-03-01", 59)]

    def test_rows_without_a_day_key_are_skipped(self):
        rows = [{"open": "1"}, _row("2026-01-01"), _row("2026-03-01")]

        assert detect_gaps(rows) == [("2026-01-01", "2026-03-01", 59)]

    def test_threshold_is_passed_through(self):
        rows = [_row("2026-01-01"), _row("2026-01-10")]

        assert detect_gaps(rows, threshold_days=5) == [("2026-01-01", "2026-01-10", 9)]
        assert detect_gaps(rows, threshold_days=30) == []


# ---------------------------------------------------------------------------
# snapshot vs close


class TestClosedVerdict:
    """A daily row for *today* is published from the opening bell and carries
    nothing to distinguish it from a final one; ``closed`` is the only signal
    that tells a strategy runtime not to act on a half-formed bar.

    Catches: the old unconditional ``closed=True``.
    """

    def test_settled_session_is_closed(self):
        event = parse_rows([_row("2026-01-05")], "600519.SH").events[0]

        assert event.closed is True

    def test_todays_row_before_the_close_is_a_snapshot(self):
        from datetime import datetime

        from selene_space_ashare.sina_daily import CST

        result = parse_rows(
            [_row("2026-01-05")],
            "600519.SH",
            as_of=datetime(2026, 1, 5, 10, 0, tzinfo=CST),
        )

        assert result.events[0].closed is False

    def test_todays_row_after_the_close_is_final(self):
        from datetime import datetime

        from selene_space_ashare.sina_daily import CST

        result = parse_rows(
            [_row("2026-01-05")],
            "600519.SH",
            as_of=datetime(2026, 1, 5, 15, 30, tzinfo=CST),
        )

        assert result.events[0].closed is True
