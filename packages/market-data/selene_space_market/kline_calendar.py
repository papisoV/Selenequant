"""Session-aware gap predicates for markets that are not open 24/7.

``KlineDB.find_gaps`` assumes a step-uniform series: every jump wider than two
steps is a hole. That is true for crypto and wrong for anything with closed
sessions. Every one of these is a legitimate jump the fixed-step rule calls a
hole:

* Fri close → Mon open (daily and intraday),
* a holiday closure of any length,
* the overnight close on intraday bars,
* a half-day early close.

And in the other direction the rule cannot see a session dropped out of the
middle of a holiday: the observed delta simply looks like the holiday one.

The predicate built here answers "should the series ever jump straight from
``left_ts`` to ``right_ts``?" by walking the calendar in *target bar slots*
rather than wall-clock steps. A slot is the offset of ``left`` within the
session's trading window modulo the bar interval, so each session contributes
exactly the bars the exchange publishes (4 for A-share 1h, 7 for the US 1h
window, one per daily bar, and so on). Any slot strictly between the two labels
that is not present in the series is a missing bar.

The slot grid is calibrated from the series itself: of the recent jumps, the
modal intra-session delta defines the bar spacing within a day, and the
fractional bar offset of either endpoint defines the grid phase. A series whose
grid cannot be calibrated falls back to "no missing sessions", i.e. the
calendar still suppresses false holes but never invents new ones.

Bar-label conventions differ per source and are the caller's business:

* A-share (``astock``) daily bars are labelled at Beijing midnight; intraday
  bars at bar open. ``Asia/Shanghai``.
* US (``yahoo``) bars are labelled at bar open in New York time. ``XNYS``.

Sources that register these partitions but are unavailable at runtime fall
back to a weekday approximation, mirroring
``selene_space_ashare.trading_calendar``: a missing calendar degrades detection
quality, it must not stop the sweep.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Callable

from selene_space_types.calendars import MarketDomain
from selene_space_types.routing import detect_market_domain

# Shares of the sample used to calibrate the slot grid. Enough to be robust to
# the odd duplicate or off-grid bar, small enough to stay cheap per partition.
_CALIBRATION_WINDOW = 240
_DELTA_SAMPLE = 120
# A session contributes fewer bars than this only on a half day or a partially
# fetched session; such sessions cannot vouch for a missing bar, so they are
# excluded from the slot projection (the fixed-step rule already flags jumps
# that are not explained by them, and they are not what this predicate adds).
_MIN_PROJECTED_BARS = 2

_US_ALIASES = frozenset({"yahoo", "us_stock", "nasdaq", "nyse", "amex"})
_A_SHARE_ALIASES = frozenset({"astock", "ashare", "xshg", "sse", "szse"})


def _safe_get_calendar(name: str):
    try:  # pragma: no cover - exercised via the fallback in tests
        import exchange_calendars as xc

        return xc.get_calendar(name)
    except Exception:
        return None


@lru_cache(maxsize=2)
def _xshg():
    return _safe_get_calendar("XSHG")


@lru_cache(maxsize=2)
def _xnys():
    return _safe_get_calendar("XNYS")


def _sessions(calendar, start: datetime, end: datetime) -> list[datetime]:
    return list(calendar.sessions_in_range(start.date(), end.date()))


def _default_weekdays(start: datetime, end: datetime) -> list[datetime]:
    days: list[datetime] = []
    cur = start.date()
    while cur <= end.date():
        if cur.weekday() < 5:
            days.append(datetime(cur.year, cur.month, cur.day, tzinfo=timezone.utc))
        cur += timedelta(days=1)
    return days


def inspectable_domain(
    exchange: str, market_type: str, symbol: str
) -> MarketDomain | None:
    """The calendar domain for a partition, or ``None`` for 24/7 (crypto)."""
    domain = detect_market_domain(symbol, exchange)
    if domain is MarketDomain.A_SHARE or market_type in _A_SHARE_ALIASES:
        return MarketDomain.A_SHARE
    if domain is MarketDomain.US_STOCK or market_type in _US_ALIASES:
        return MarketDomain.US_STOCK
    return None


def gap_predicate(
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    interval: str,
    step_ms: int,
    stamps: list[int],
    max_gap_ratio: float = 2.0,
    sessions: Callable[[datetime, datetime], list[datetime]] | None = None,
    session_windows: Callable[[datetime], tuple[datetime, datetime]] | None = None,
    tz: str | None = None,
) -> Callable[[int, int], bool] | None:
    """Session-aware predicate for an equity partition, or ``None`` for crypto.

    ``stamps`` is the partition's ordered label series; it is used only to
    calibrate where within a session the bars sit. Applicability is decided by
    the market domain, not by the caller's sweep list: a partition that
    resolves to CRYPTO keeps the fixed-step rule verbatim, which is what makes
    the crypto path bit-identical.

    The ``sessions`` / ``session_windows`` / ``tz`` parameters override the
    calendar, the session window lookup, and the label timezone. That seam
    exists so tests can drive the logic without a real calendar.
    """
    if step_ms <= 0:
        return None
    domain = inspectable_domain(exchange, market_type, symbol)
    if domain is None:
        return None

    if tz is None:
        tz = "Asia/Shanghai" if domain is MarketDomain.A_SHARE else "America/New_York"
    if sessions is None or session_windows is None:
        calendar = _xshg() if domain is MarketDomain.A_SHARE else _xnys()
        if calendar is None:
            sessions = sessions or _default_weekdays
            session_windows = session_windows or _default_weekday_window
        else:
            sessions = sessions or (lambda start, end: _sessions(calendar, start, end))
            session_windows = session_windows or (
                lambda day: (
                    calendar.session_open(day.date()).to_pydatetime(),
                    calendar.session_close(day.date()).to_pydatetime(),
                )
            )
    from zoneinfo import ZoneInfo

    label_tz = ZoneInfo(tz)

    def _sessions_or_weekdays(start: datetime, end: datetime) -> list[datetime]:
        try:
            return sessions(start, end)
        except Exception:
            # Calendar out of its supported range (or unavailable at call
            # time): degrade to the weekday approximation rather than letting
            # the sweep lose the partition.
            return _default_weekdays(start, end)

    if interval == "1d":
        # A daily label sits at the source's day boundary (Beijing midnight for
        # A-share, session open for yahoo) and carries no within-session slot.
        # One session per bar, so the session count is the whole rule.
        def _daily(left_ts: int, right_ts: int) -> bool:
            left = datetime.fromtimestamp(left_ts / 1000, label_tz)
            right = datetime.fromtimestamp(right_ts / 1000, label_tz)
            if left.date() == right.date():
                return right_ts - left_ts > max_gap_ratio * step_ms
            # Consecutive sessions (Fri→Mon, either side of a holiday) span two
            # entries; anything wider means at least one session is missing.
            return len(_sessions_or_weekdays(left, right)) > 2

        return _daily

    spec = _calibrate(stamps, step_ms, label_tz, _sessions_or_weekdays, session_windows)

    def _predicate(left_ts: int, right_ts: int) -> bool:
        # Same session day: the slot grid has nothing to add, the plain ratio
        # rule applies.
        if spec is None or spec.bar_ms <= 0:
            return right_ts - left_ts > max_gap_ratio * step_ms
        return spec.missing_between(left_ts, right_ts) > 0

    return _predicate


def _default_weekday_window(day: datetime) -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, 9, 30, tzinfo=timezone.utc)
    return start, start + timedelta(hours=6, minutes=30)


class _SlotGrid:
    """Maps a label inside a session window to its slot index."""

    def __init__(
        self,
        *,
        bar_ms: int,
        offset_ms: int,
        label_tz,
        sessions,
        session_windows,
    ) -> None:
        self.bar_ms = bar_ms
        self.offset_ms = offset_ms
        self._tz = label_tz
        self._sessions = sessions
        self._windows = session_windows
        self._counts: dict[object, int] = {}

    def session_bars(self, day: datetime) -> int:
        if day in self._counts:
            return self._counts[day]
        try:
            open_, close_ = self._windows(day)
        except Exception:
            count = 0
        else:
            span = int((close_ - open_).total_seconds() * 1000)
            count = max(span // self.bar_ms, 0)
        self._counts[day] = count
        return count

    def slot_of(self, ts: int, day: datetime) -> int | None:
        try:
            open_, _ = self._windows(day)
        except Exception:
            return None
        local_open = open_.astimezone(self._tz)
        open_ms = int(local_open.timestamp() * 1000)
        offset = ts - open_ms - self.offset_ms
        slot = int(round(offset / self.bar_ms))
        if slot < 0 or slot >= self.session_bars(day):
            return None
        return slot

    def missing_between(self, left_ts: int, right_ts: int) -> int:
        left = datetime.fromtimestamp(left_ts / 1000, self._tz)
        right = datetime.fromtimestamp(right_ts / 1000, self._tz)
        try:
            span = self._sessions(left, right)
        except Exception:
            # Calendar out of its supported range: decline to judge rather than
            # let the sweep lose the partition to an exception.
            return 0
        if not span:
            return 0
        left_slot = self.slot_of(left_ts, left)
        right_slot = (
            self.slot_of(right_ts, right) if left.date() == right.date() else None
        )
        for day in span:
            count = self.session_bars(day)
            if count < _MIN_PROJECTED_BARS:
                continue  # half day / partial session: cannot vouch for a bar
            start = (
                left_slot + 1
                if day.date() == left.date() and left_slot is not None
                else 0
            )
            for slot in range(start, count):
                if right_slot is not None and slot >= right_slot:
                    break
                try:
                    open_, _ = self._windows(day)
                except Exception:
                    break
                open_ms = int(open_.astimezone(self._tz).timestamp() * 1000)
                slot_ts = open_ms + self.offset_ms + slot * self.bar_ms
                # A candidate slot has to be a whole bar away from both labels to
                # be a distinct bar: exactly one bar is the common single-missing
                # bar, while anything closer means an off-grid label (a 09:48
                # quote, a 30-minute stub) already occupies that slot.
                if (
                    slot_ts - left_ts >= self.bar_ms
                    and right_ts - slot_ts >= self.bar_ms
                ):
                    return 1
        return 0


def _calibrate(
    stamps: list[int],
    step_ms: int,
    label_tz,
    sessions,
    session_windows,
) -> _SlotGrid | None:
    """Infer the within-session slot phase from the series' own deltas."""
    if len(stamps) < 3:
        return None
    window = stamps[-_CALIBRATION_WINDOW:]
    deltas = [
        right - left for left, right in zip(window, window[1:]) if right - left > 0
    ]
    if not deltas:
        return None
    intraday = [d for d in deltas if d <= 2 * step_ms]
    source = intraday or deltas[:_DELTA_SAMPLE]
    counts = Counter(source)
    bar_ms = counts.most_common(1)[0][0]
    if bar_ms <= 0:
        return None

    # Phase: where do labels sit relative to the session open, modulo a bar?
    # Watch the most common residue over the sample; a data source that labels
    # at bar open yields zero, one that labels at bar close yields bar_ms.
    residues: Counter[int] = Counter()
    for ts in window:
        day = datetime.fromtimestamp(ts / 1000, label_tz)
        try:
            open_, _ = session_windows(day)
        except Exception:
            continue
        local_open = open_.astimezone(label_tz)
        open_ms = int(local_open.timestamp() * 1000)
        residues[(ts - open_ms) % bar_ms] += 1
    if not residues:
        return None
    offset_ms = residues.most_common(1)[0][0]
    # A grid calibrated on a handful of points is noise; the fixed-step rule is
    # at least predictable, so require a clear mode.
    if residues[offset_ms] < max(len(window) // 4, 3):
        return None
    return _SlotGrid(
        bar_ms=bar_ms,
        offset_ms=offset_ms,
        label_tz=label_tz,
        sessions=sessions,
        session_windows=session_windows,
    )


__all__ = ["gap_predicate", "inspectable_domain"]
