# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: exchange-calendars
# based trading-calendar queries with fail-open weekday approximation.
"""A-share trading calendar helpers.

All datetime arguments are naive and interpreted as Beijing time (Asia/Shanghai,
UTC+8); no timezone conversion is performed. Callers must pass Beijing-time
naive datetimes.

Failure mode: if the exchange-calendars query raises for any reason, the
helpers fall back to a Mon-Fri approximation and log a warning — they never
propagate exceptions to the caller.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import cast

import pandas as pd

from exchange_calendars import get_calendar

logger = logging.getLogger(__name__)

_XSHG = "XSHG"


def _to_date(value: date | datetime) -> date:
    if isinstance(value, datetime):
        return value.date()
    return value


def _calendar_session(value: date):
    """Return True if ``value`` is an XSHG session; None on calendar failure."""
    try:
        cal = get_calendar(_XSHG)
        return cal.is_session(value)
    except Exception:
        logger.warning(
            "trading_calendar: exchange-calendars lookup failed for %s; "
            "falling back to Mon-Fri approximation",
            value,
            exc_info=True,
        )
        return None


def is_trading_day(value: date | datetime) -> bool:
    """Return True if ``value`` (Beijing-time naive) is an A-share trading day."""
    d = _to_date(value)
    session = _calendar_session(d)
    if session is not None:
        return bool(session)
    return d.weekday() < 5


def next_trading_day(value: date | datetime) -> date:
    """Return the first trading day strictly after ``value``."""
    d = _to_date(value)
    try:
        cal = get_calendar(_XSHG)
        ts = pd.Timestamp(d)
        if ts < cal.first_session or ts >= cal.last_session:
            raise ValueError(f"{d} outside calendar range")
        return cast(date, cal.next_session(d).date())
    except Exception:
        logger.warning(
            "trading_calendar: next_session lookup failed for %s; "
            "falling back to Mon-Fri approximation",
            d,
            exc_info=True,
        )
        d = d + timedelta(days=1)
        while d.weekday() >= 5:
            d = d + timedelta(days=1)
        return d


def prev_trading_day(value: date | datetime) -> date:
    """Return the last trading day strictly before ``value``."""
    d = _to_date(value)
    try:
        cal = get_calendar(_XSHG)
        ts = pd.Timestamp(d)
        if ts <= cal.first_session or ts > cal.last_session:
            raise ValueError(f"{d} outside calendar range")
        return cast(date, cal.previous_session(d).date())
    except Exception:
        logger.warning(
            "trading_calendar: previous_session lookup failed for %s; "
            "falling back to Mon-Fri approximation",
            d,
            exc_info=True,
        )
        d = d - timedelta(days=1)
        while d.weekday() >= 5:
            d = d - timedelta(days=1)
        return d


def market_phase(now: datetime | None = None) -> str:
    """Classify ``now`` (Beijing-time naive) into an intraday market phase.

    Phases: PRE_OPEN (08:30-09:15), AUCTION (09:15-09:25), OPEN
    (09:30-11:30 and 13:00-14:57), CLOSING_AUCTION (14:57-15:00),
    LUNCH_BREAK (11:30-13:00), otherwise CLOSED. Non-trading days are
    always CLOSED regardless of time.
    """
    if now is None:
        now = datetime.now()
    if not is_trading_day(now):
        return "CLOSED"
    minutes = now.hour * 60 + now.minute
    if 8 * 60 + 30 <= minutes < 9 * 60 + 15:
        return "PRE_OPEN"
    if 9 * 60 + 15 <= minutes < 9 * 60 + 25:
        return "AUCTION"
    if 9 * 60 + 30 <= minutes < 11 * 60 + 30:
        return "OPEN"
    if 11 * 60 + 30 <= minutes < 13 * 60:
        return "LUNCH_BREAK"
    if 13 * 60 <= minutes < 14 * 60 + 57:
        return "OPEN"
    if 14 * 60 + 57 <= minutes < 15 * 60:
        return "CLOSING_AUCTION"
    return "CLOSED"
