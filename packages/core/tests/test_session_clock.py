"""Tests for the snapshot/close verdict on a daily bar.

The defect these pin: a daily bar for *today* is published from the opening
bell with the price so far, and its payload is identical in shape to yesterday's
final row. A parser that marks it ``closed=True`` both lets strategy runtimes
act on a half-formed bar and — through any "strictly newer timestamp" store
filter — freezes it there forever, because the post-close restatement carries
the same session date.

Every test names the mutation it is meant to catch.
"""

from __future__ import annotations

from datetime import date, datetime

import pytest

from selene_space_core.session_clock import (
    CST,
    DAILY_SESSION_SETTLES_AT,
    bar_payload_differs,
    daily_bar_is_final,
    session_date_of,
)


class TestSessionDateOf:
    """Catches: reading a midnight-CST stamp as UTC, which shifts the day back."""

    def test_midnight_cst_belongs_to_that_day(self) -> None:
        ts_ms = int(datetime(2026, 10, 22, tzinfo=CST).timestamp() * 1000)
        assert session_date_of(ts_ms) == date(2026, 10, 22)

    def test_last_millisecond_of_the_day_is_still_that_day(self) -> None:
        """A UTC read of this instant lands on the 23rd."""
        ts_ms = int(datetime(2026, 10, 22, 23, 59, 59, tzinfo=CST).timestamp() * 1000)
        assert session_date_of(ts_ms) == date(2026, 10, 22)


class TestDailyBarIsFinal:
    """The rule itself. ``as_of`` is pinned everywhere so the tests cannot
    depend on when they are run."""

    def test_past_session_is_always_final(self) -> None:
        """Catches: gating on wall-clock time alone, which would mark every
        historical bar of a backfill as a snapshot."""
        assert daily_bar_is_final(
            date(2026, 10, 21), as_of=datetime(2026, 10, 22, 9, 30)
        )

    def test_today_before_the_settle_time_is_a_snapshot(self) -> None:
        """The core assertion: 10:00 is mid-session, not a close."""
        assert not daily_bar_is_final(
            date(2026, 10, 22), as_of=datetime(2026, 10, 22, 10, 0)
        )

    @pytest.mark.parametrize(
        "clock",
        [
            datetime(2026, 10, 22, 9, 30),  # open
            datetime(2026, 10, 22, 11, 30),  # morning close — NOT a session close
            datetime(2026, 10, 22, 13, 0),  # afternoon open
            datetime(2026, 10, 22, 14, 59, 59),
        ],
    )
    def test_every_intraday_clock_is_a_snapshot(self, clock: datetime) -> None:
        """Catches: using the lunch break (11:30) as the settle time."""
        assert not daily_bar_is_final(date(2026, 10, 22), as_of=clock)

    def test_settle_instant_is_inclusive(self) -> None:
        """Catches: a strict ``>`` comparison that leaves the very first
        publishable moment marked as a snapshot."""
        assert daily_bar_is_final(
            date(2026, 10, 22),
            as_of=datetime.combine(date(2026, 10, 22), DAILY_SESSION_SETTLES_AT),
        )

    def test_one_second_before_settle_is_not_final(self) -> None:
        """Catches: rounding the cutoff down to the whole minute."""
        just_before = datetime(2026, 10, 22, 15, 4, 59)
        assert not daily_bar_is_final(date(2026, 10, 22), as_of=just_before)

    def test_after_the_close_is_final(self) -> None:
        assert daily_bar_is_final(
            date(2026, 10, 22), as_of=datetime(2026, 10, 22, 18, 0)
        )

    def test_future_session_is_never_final(self) -> None:
        """A clock-skew or mis-stamped row must not enter a baseline as a close."""
        assert not daily_bar_is_final(
            date(2026, 10, 23), as_of=datetime(2026, 10, 22, 18, 0)
        )

    def test_naive_as_of_is_read_as_beijing_time(self) -> None:
        """A naive clock is compared as written, not shifted into another zone.

        This pins the documented contract rather than a mutation: the helper
        hands naive values through untouched, so the verdict reads their literal
        date and time-of-day. 23:30 is past the settle time; 00:30 is before it.
        """
        assert daily_bar_is_final(
            date(2026, 10, 22), as_of=datetime(2026, 10, 22, 23, 30)
        )
        assert not daily_bar_is_final(
            date(2026, 10, 22), as_of=datetime(2026, 10, 22, 0, 30)
        )

    def test_aware_utc_as_of_is_converted(self) -> None:
        """07:10 UTC is 15:10 CST — past the settle time."""
        from datetime import timedelta, timezone

        assert daily_bar_is_final(
            date(2026, 10, 22),
            as_of=datetime(2026, 10, 22, 7, 10, tzinfo=timezone(timedelta(hours=0))),
        )


class TestBarPayloadDiffers:
    """Catches: deduping on the timestamp alone, which drops the restatement."""

    _BASE = {
        "open": 10.0,
        "high": 10.5,
        "low": 9.8,
        "close": 10.2,
        "volume": 1000.0,
        "closed": False,
    }

    @pytest.mark.parametrize("field", ["open", "high", "low", "close", "volume"])
    def test_any_price_or_volume_change_is_a_difference(self, field: str) -> None:
        incoming = {**self._BASE, field: self._BASE[field] + 0.1}
        assert bar_payload_differs(self._BASE, incoming)

    def test_flipping_to_closed_is_a_difference(self) -> None:
        """The normal daily path: same numbers, flag flips. This is the one the
        old timestamp-only dedupe silently discarded."""
        assert bar_payload_differs(self._BASE, {**self._BASE, "closed": True})

    def test_identical_payload_is_not_a_difference(self) -> None:
        """An unchanged repoll must not be republished — that would make the
        poll loop write the same row on every tick."""
        assert not bar_payload_differs(self._BASE, dict(self._BASE))

    def test_missing_field_reads_as_none_and_still_compares(self) -> None:
        """A row read back from a store without ``closed`` must not quietly
        equal a bound event that has it."""
        stored = {k: v for k, v in self._BASE.items() if k != "closed"}
        assert bar_payload_differs(stored, {**self._BASE, "closed": True})
