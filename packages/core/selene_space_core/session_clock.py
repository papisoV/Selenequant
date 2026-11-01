"""When a daily bar stops being a snapshot and becomes the session's close.

A daily bar is fetched, not announced: the upstream endpoint answers with a row
for *today* from the moment the market opens, carrying the price so far and the
volume so far. Nothing in the payload distinguishes that row from yesterday's
final one — both are ``{day, open, high, low, close, volume}``. A consumer that
treats the intraday row as the session close acts on a half-formed bar, and a
*store* that treats it as final freezes it there: the restated row that arrives
after the close carries the same session date, so any "only strictly newer
timestamps" filter discards it and the wrong close is permanent.

The marker for this already exists on :class:`selene_space_types.market.KlineEvent`
— ``closed`` — and consumers already honour it (the strategy runtime, the
template strategy and the fusion runtimes all bail out on ``not event.closed``).
What was missing is a *rule* for setting it on a daily bar, and a single place
to put that rule so the market-data source and the ashare fetcher cannot drift
apart. The rule lives here because both packages depend on ``core`` and neither
may depend on the other.

Rule source: tick-stock-panel (MIT) ``backend/app/services/data_integrity.py``
discriminates snapshot rows from authoritative ones with a session-date +
cutoff-time test (``CLOSE_CUTOFF``). The *concept* is absorbed; the mechanism is
not — TSP stamps each row with the upstream's ``quote_ts`` and judges that,
which Sina's daily endpoint does not publish. With no per-row stamp available,
the honest proxy is when *we* fetched the row: a row for today fetched at 10:00
is a snapshot, and the same row fetched after the publisher has finalised the
session is not.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from typing import Mapping

# China has no DST, so a fixed +08:00 offset is exact. Every session-date
# calculation in the repo uses this convention.
CST = timezone(timedelta(hours=8))

# The session count ends at 15:00; the daily kline endpoint republishes the
# finalised row a few minutes later. 15:05 is the earliest time we are willing
# to call the row final, and it is deliberately the *same* instant the whole-
# market EOD loop is scheduled for (``selene_space_ashare.daily_all.EOD_TIME``):
# two constants answering "when is the session over" would eventually disagree,
# and the disagreement would show up as bars that are final for one writer and
# snapshots for another.
DAILY_SESSION_SETTLES_AT = time(15, 5)


def _as_cst(value: datetime) -> datetime:
    """Convert an aware datetime to Beijing wall time; pass a naive one through.

    A naive value is taken to *already be* Beijing wall time, because every
    caller in the repo works in Beijing time and several pass naive values (the
    trading calendar does). Attaching an offset to a naive value would be a
    no-op for the only caller — :func:`daily_bar_is_final` compares the date and
    the time-of-day of the same instant, and both fields move together — so this
    function deliberately does not do it. Attaching the *wrong* offset would be
    a no-op as well, which is why the naive branch carries no test: there is no
    behaviour there to protect.
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(CST)


def session_date_of(ts_ms: int) -> date:
    """Session date of a daily bar's midnight-CST timestamp.

    Inverse of the stamp the daily parsers apply; going back through the same
    fixed offset is what makes the round trip lossless.
    """
    return datetime.fromtimestamp(ts_ms / 1000, tz=CST).date()


def daily_bar_is_final(session: date, *, as_of: datetime | None = None) -> bool:
    """Whether a daily bar stamped for ``session`` is that session's close.

    ``as_of`` is the moment the bar was obtained. It defaults to the wall clock,
    which is the right answer for every past-dated bar regardless of when it is
    parsed — only a bar for *today* can be a snapshot — so a caller that omits
    it still gets the safe result for historical rows.

    A future-dated session is never final: the exchange has not held it yet, so
    the row is either a clock-skew artefact or a mis-stamped bar, and treating it
    as settled would let a bogus close into a baseline.
    """
    now = _as_cst(as_of) if as_of is not None else datetime.now(CST)
    today = now.date()
    if session < today:
        return True
    if session > today:
        return False
    return now.time() >= DAILY_SESSION_SETTLES_AT


def bar_payload_differs(
    stored: Mapping[str, object], incoming: Mapping[str, object]
) -> bool:
    """Whether two records of the same session bar disagree on any number.

    A daily bar is restated in place: the intraday snapshot and the session's
    close share one session-date key, so every dedupe rule that compares
    timestamps alone throws the final row away. This is the comparison those
    callers need instead — the OHLC/volume payload plus the ``closed`` verdict,
    which flips on the normal daily path without any other field having to
    change.

    Takes plain mappings rather than the event type because ``core`` cannot
    depend on ``selene-space-types``; both a bound ``KlineEvent`` and a row
    read back out of the store can be viewed through this signature.
    """
    return (
        stored.get("open"),
        stored.get("high"),
        stored.get("low"),
        stored.get("close"),
        stored.get("volume"),
        stored.get("closed"),
    ) != (
        incoming.get("open"),
        incoming.get("high"),
        incoming.get("low"),
        incoming.get("close"),
        incoming.get("volume"),
        incoming.get("closed"),
    )


__all__ = [
    "CST",
    "DAILY_SESSION_SETTLES_AT",
    "bar_payload_differs",
    "daily_bar_is_final",
    "session_date_of",
]
