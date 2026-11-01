"""Tests for selene_space_ashare.trading_calendar."""

from __future__ import annotations

import logging
from datetime import date, datetime

import pytest

from selene_space_ashare import trading_calendar as tc


class TestIsTradingDay:
    def test_national_day_holiday_not_trading(self) -> None:
        # 2026-10-01 National Day holiday (Thursday)
        assert tc.is_trading_day(date(2026, 10, 1)) is False

    def test_normal_friday_trading(self) -> None:
        assert tc.is_trading_day(date(2026, 10, 9)) is True

    def test_weekend_not_trading(self) -> None:
        assert tc.is_trading_day(date(2026, 10, 10)) is False  # Saturday
        assert tc.is_trading_day(date(2026, 10, 11)) is False  # Sunday

    def test_accepts_datetime(self) -> None:
        assert tc.is_trading_day(datetime(2026, 10, 9, 10, 0)) is True
        assert tc.is_trading_day(datetime(2026, 10, 1, 10, 0)) is False

    def test_fail_open_weekday_approximation(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("calendar unavailable")

        monkeypatch.setattr(tc, "get_calendar", boom)
        with caplog.at_level(
            logging.WARNING, logger="selene_space_ashare.trading_calendar"
        ):
            assert tc.is_trading_day(date(2026, 10, 9)) is True  # Friday
            assert tc.is_trading_day(date(2026, 10, 10)) is False  # Saturday
        assert "falling back" in caplog.text


class TestNextPrevTradingDay:
    def test_next_across_weekend(self) -> None:
        # Friday 2026-10-09 -> Monday 2026-10-12
        assert tc.next_trading_day(date(2026, 10, 9)) == date(2026, 10, 12)

    def test_prev_across_weekend(self) -> None:
        # Monday 2026-10-12 -> Friday 2026-10-09
        assert tc.prev_trading_day(date(2026, 10, 12)) == date(2026, 10, 9)

    def test_next_across_holiday(self) -> None:
        # 2026-09-30 (Wed) -> first trading day after National Day holiday
        result = tc.next_trading_day(date(2026, 9, 30))
        assert result > date(2026, 10, 7)
        assert tc.is_trading_day(result) is True

    def test_fail_open_next(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(*args: object, **kwargs: object) -> None:
            raise RuntimeError("calendar unavailable")

        monkeypatch.setattr(tc, "get_calendar", boom)
        assert tc.next_trading_day(date(2026, 10, 9)) == date(2026, 10, 12)
        assert tc.prev_trading_day(date(2026, 10, 12)) == date(2026, 10, 9)


class TestMarketPhase:
    TRADING_DAY = datetime(2026, 10, 9)  # Friday

    @pytest.mark.parametrize(
        ("hh", "mm", "expected"),
        [
            (8, 29, "CLOSED"),
            (8, 30, "PRE_OPEN"),
            (9, 14, "PRE_OPEN"),
            (9, 15, "AUCTION"),
            (9, 24, "AUCTION"),
            (9, 25, "CLOSED"),  # gap between auction and open
            (9, 31, "OPEN"),
            (11, 29, "OPEN"),
            (11, 31, "LUNCH_BREAK"),
            (12, 59, "LUNCH_BREAK"),
            (13, 0, "OPEN"),
            (14, 56, "OPEN"),
            (14, 58, "CLOSING_AUCTION"),
            (15, 1, "CLOSED"),
        ],
    )
    def test_phase_boundaries(self, hh: int, mm: int, expected: str) -> None:
        assert tc.market_phase(self.TRADING_DAY.replace(hour=hh, minute=mm)) == expected

    def test_non_trading_day_closed(self) -> None:
        assert tc.market_phase(datetime(2026, 10, 1, 10, 0)) == "CLOSED"
        assert tc.market_phase(datetime(2026, 10, 10, 10, 0)) == "CLOSED"

    def test_none_uses_now(self) -> None:
        # Just verify it returns a valid phase string without error.
        assert tc.market_phase() in {
            "PRE_OPEN",
            "AUCTION",
            "OPEN",
            "CLOSING_AUCTION",
            "LUNCH_BREAK",
            "CLOSED",
        }
