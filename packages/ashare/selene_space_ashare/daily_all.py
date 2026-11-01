"""Whole-market A-share EOD increment into ``("astock","daily_all","1d")``.

Why this exists: :mod:`selene_space_ashare.sina_daily` can *fetch and parse*
bars and ``scripts/backfill_klines_astock.py`` can lay down three years of
them, but nothing advances the tail afterwards. A one-shot backfill is a
frozen snapshot — the day after it runs, every moving-average condition a
consumer evaluates is one session stale and drifting. This module is the
daily half: fetch the tail of every symbol, upsert it, and report honestly
what did not arrive.

It writes the *whole-market* partition, not ``("astock","spot")``. The two
cannot share a loop: ``spot`` is the favorites subscription capped at
``MAX_FAVORITES`` that feeds the anomaly detector's limit-up baseline, while
``daily_all`` is the universe a screener filters on. Sharing either the
partition or the loop would let a universe-side bug in one corrupt the
other's baseline.

Three properties this module owns:

* **Idempotent by construction.** ``put_klines`` is ``INSERT OR REPLACE``
  keyed on the full partition tuple, so re-running is safe and a restated bar
  heals itself. That is also why there is no resume state: an interrupted run
  is repaired by running it again, and a resume file would be a second source
  of truth free to disagree with the database.
* **Every skip is counted.** Symbols that failed, codes that failed prefix
  validation, rows dropped for non-positive OHLC, and symbols that did not
  print on the newest session all reach the report. A silent drop reads as
  "the market had no data that day".
* **The window heals, the count warns.** ``EOD_DATALEN`` is deliberately
  larger than one session; see its comment.
"""

from __future__ import annotations

import logging
import time as _time
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time as _clock_time, timedelta
from typing import cast

import requests
from selene_space_ashare.sina_daily import CST, fetch_daily_rows, parse_rows
from selene_space_core.sina_symbols import code_to_symbol
from selene_space_ashare.trading_calendar import is_trading_day, prev_trading_day
from selene_space_types.market import KlineEvent

logger = logging.getLogger(__name__)

EXCHANGE = "astock"
MARKET_TYPE = "daily_all"
INTERVAL = "1d"

# Sina publishes the session's final bar shortly after the 15:00 close. 15:05
# leaves the usual publishing lag; running earlier would fetch a bar that is
# still forming and then treat it as settled.
EOD_TIME = _clock_time(15, 5)

# Tail depth per symbol. One session is the literal minimum, but a single
# missed run (process restart, network blip, a run that starts before the bar
# is published) would then leave a permanent one-day hole no later run
# repairs — the increment only ever extends forward from whatever it fetches.
# 30 sessions ≈ six weeks, which covers any outage worth recovering without a
# separate replay path. Cost is ≤30 rows × 5574 symbols ≈ 167k upserts per
# run, negligible against a 3.9M-row table.
EOD_DATALEN = 30

# Per-symbol pacing. Serial on purpose: one request at a time, no concurrency.
# Measured on this network with a reused Session, a full run lands in the
# 10–20 minute budget the plan states (a fresh TLS handshake per request
# measured 0.73s vs 0.15s with connection reuse, which is why the caller is
# expected to pass one Session for the whole run).
SYMBOL_SLEEP_S = 0.05
BATCH_SIZE = 1000

# How far ahead to look for the next trading day. A 15-day walk always finds
# one (Spring Festival is the longest closure at ~9 calendar days); the bound
# exists only so a broken calendar cannot spin forever.
_MAX_LOOKAHEAD_DAYS = 15


@dataclass(frozen=True)
class EodSyncReport:
    """One EOD pass, with every non-write accounted for.

    Frozen so a caller cannot edit the evidence a log line was built from
    after the fact; sequences are tuples for the same reason.
    """

    symbols_total: int
    symbols_updated: int
    symbols_unchanged: int
    symbols_empty: int
    symbols_behind: int
    symbols_failed: int
    latest_session: str | None
    expected_session: str | None
    rows_upserted: int
    dropped_nonpositive: int
    dropped_unparsable: int
    skipped_invalid: tuple[str, ...] = ()
    failed: tuple[str, ...] = ()
    elapsed_s: float = 0.0

    @property
    def ok(self) -> bool:
        """True when no requested symbol failed."""
        return not self.failed

    @property
    def stale(self) -> bool:
        """True when the newest bar found is older than the session we asked for.

        This is the difference between "the pass succeeded" and "the pass
        produced today's bar": all symbols agreeing on yesterday's date is a
        consistent result that is nonetheless a day behind, which happens when
        the run beats Sina's publish or the calendar is wrong about a holiday.
        """
        if self.expected_session is None or self.latest_session is None:
            return False
        return self.latest_session < self.expected_session

    def summary(self) -> str:
        """One line for the loop's log — mirrors the backfill's closing block."""
        session = self.latest_session or "none"
        line = (
            f"daily_all EOD: {self.symbols_updated} updated / "
            f"{self.symbols_unchanged} unchanged / {self.symbols_behind} behind"
            f" / {self.symbols_empty} empty, latest session {session}, "
            f"{self.rows_upserted:,} rows upserted in {self.elapsed_s:.0f}s"
        )
        if self.dropped_nonpositive or self.dropped_unparsable:
            line += (
                f" (dropped nonpositive={self.dropped_nonpositive}"
                f" unparsable={self.dropped_unparsable})"
            )
        if self.skipped_invalid:
            line += f" (invalid codes={len(self.skipped_invalid)})"
        if self.stale:
            line += f" (STALE: expected {self.expected_session})"
        if self.failed:
            line += f" (FAILED={len(self.failed)} e.g. {list(self.failed[:5])})"
        return line


def whole_market_symbols() -> tuple[list[str], list[str]]:
    """The A-share universe as suffixed symbols, plus codes that failed to map.

    Returns ``(symbols, invalid)`` rather than dropping bad codes silently: a
    universe entry that cannot be mapped to a venue is a data problem the
    caller must see, and an ``INVALID:`` sentinel smuggled through the symbol
    list would be indistinguishable from a real symbol downstream.
    """
    from selene_space_ashare.data_fetcher import get_a_share_list

    frame = get_a_share_list()
    symbols: list[str] = []
    invalid: list[str] = []
    for raw in frame["code"]:
        code = str(raw).strip()
        try:
            symbols.append(code_to_symbol(code))
        except ValueError:
            invalid.append(code)
    return symbols, invalid


def ts_to_session_date(ts_ms: int) -> str:
    """CST-midnight epoch millis → ``YYYY-MM-DD`` session date.

    Inverse of the stamp :func:`sina_daily.parse_rows` applies. Going back
    through the same fixed offset is what makes the round trip lossless.
    """
    return datetime.fromtimestamp(ts_ms / 1000, tz=CST).date().isoformat()


def expected_session(as_of: datetime | None = None) -> str:
    """The session whose bar should exist once ``as_of`` is past the EOD time.

    Before the close this is the *previous* session — asking for today's bar
    mid-session would mark the whole market stale on every intraday run. After
    it, on a trading day, it is today.
    """
    now = as_of if as_of is not None else datetime.now()
    if is_trading_day(now) and now.time() < EOD_TIME:
        # cast: selene-space-risk ships no py.typed, so this cross-package call
        # types as Any under the hook's --follow-imports=silent.
        return cast("date", prev_trading_day(now)).isoformat()
    if is_trading_day(now):
        return now.date().isoformat()
    return cast("date", prev_trading_day(now)).isoformat()


def next_eod_run_at(as_of: datetime | None = None) -> datetime:
    """The next trading-day 15:05 strictly after ``as_of`` (Beijing-time naive).

    Strictly after, so a loop that wakes at the target and immediately
    recomputes sleeps a full day rather than busy-spinning on a zero delay.
    """
    now = as_of if as_of is not None else datetime.now()
    for offset in range(_MAX_LOOKAHEAD_DAYS):
        day = (now + timedelta(days=offset)).date()
        if not is_trading_day(day):
            continue
        candidate = datetime.combine(day, EOD_TIME)
        if candidate > now:
            return candidate
    # Unreachable with a working calendar; a 24h fallback keeps the loop alive
    # rather than raising inside a background task.
    return now + timedelta(days=1)


def is_due(db, as_of: datetime | None = None) -> bool:
    """Whether the partition still lacks the session that should exist by ``as_of``.

    The scheduler needs to tell "already ran today" from "started after the
    close and has not caught up" — without it, a process restarted at 16:00
    would wait a full day and silently skip a session, and one restarted at
    09:00 would refetch everything pointlessly.

    Compares against the newest timestamp *in the partition* rather than
    recording its own last-run state: the partition is already the source of
    truth for what has been written, and a separate marker could disagree with
    it — the failure mode that makes a loop believe it ran when it did not.

    Reads through :meth:`KlineDB.get_partition_max_timestamp`; the ``db`` is
    duck-typed for the same reason as in :mod:`selene_space_ashare.market_daily`.
    """
    max_ts = db.get_partition_max_timestamp(EXCHANGE, MARKET_TYPE, INTERVAL)
    if max_ts is None:
        # Nothing stored yet (fresh install): the backfill owns the history, so
        # the increment should not attempt to create it.
        return False
    return ts_to_session_date(max_ts) < expected_session(as_of)


def sync_eod(
    db,
    *,
    symbols: Sequence[str] | None = None,
    datalen: int = EOD_DATALEN,
    sleep_s: float = SYMBOL_SLEEP_S,
    batch_size: int = BATCH_SIZE,
    session: requests.Session | None = None,
    as_of: datetime | None = None,
) -> EodSyncReport:
    """Fetch each symbol's tail and upsert it into the daily partition.

    ``symbols=None`` resolves the whole market (an akshare call — pass an
    explicit list in tests and in callers that already hold the universe).
    ``session=None`` opens one Session for the run and closes it; connection
    reuse is worth ~5× on this endpoint.

    Never raises on a single symbol's failure: a transport error marks that
    symbol failed and the pass continues, because aborting the market because
    one request blinked would leave the other 5573 un-advanced. The caller
    reads :attr:`EodSyncReport.failed` and decides.
    """
    started = _time.monotonic()
    skipped_invalid: list[str] = []
    if symbols is None:
        symbols, skipped_invalid = whole_market_symbols()
    symbols = list(symbols)

    owns_session = session is None
    if session is None:
        session = requests.Session()
        session.trust_env = False

    updated = 0
    unchanged = 0
    empty = 0
    rows_upserted = 0
    dropped_nonpositive = 0
    dropped_unparsable = 0
    failed: list[str] = []
    newest_by_day: dict[str, str] = {}
    buf: list[KlineEvent] = []

    def flush() -> None:
        nonlocal rows_upserted
        if buf:
            rows_upserted += db.put_klines(buf)
            buf.clear()

    try:
        for sym in symbols:
            try:
                rows = fetch_daily_rows(sym, datalen=datalen, session=session)
                result = parse_rows(
                    rows,
                    sym,
                    market_type=MARKET_TYPE,
                    exchange=EXCHANGE,
                    interval=INTERVAL,
                )
            except Exception:
                # Counted, not swallowed: the report carries the symbol list so
                # a caller can retry exactly what failed.
                failed.append(sym)
                logger.warning("daily_all EOD: fetch failed for %s", sym)
                continue

            dropped_nonpositive += result.dropped_nonpositive
            dropped_unparsable += result.dropped_unparsable
            if not result.events:
                empty += 1
                continue

            # Newest *usable* bar, not newest raw row: a halted session comes
            # back as a zero-price row that ``parse_rows`` drops, and counting
            # it would make a suspended symbol look current.
            newest = max(ts_to_session_date(e.timestamp) for e in result.events)
            newest_by_day[sym] = newest
            prior = db.get_latest_timestamp(EXCHANGE, MARKET_TYPE, sym, INTERVAL)
            if prior is not None and newest <= ts_to_session_date(prior):
                unchanged += 1
            else:
                updated += 1

            buf.extend(result.events)
            if len(buf) >= batch_size:
                flush()
            _time.sleep(sleep_s)
        flush()
    finally:
        if owns_session:
            session.close()

    # "Behind" is measured against the newest session the market actually
    # printed, not against the wall clock: a run before the close, or on a day
    # the calendar disagrees about, would otherwise mark the entire market
    # suspended. Failed symbols are excluded — they are unknown, not halted,
    # and folding the two together would hide a transport problem inside a
    # market statistic. Suspensions are real here: 528+ symbols carry genuine
    # date holes, so this count is expected to be non-zero.
    latest_session = max(newest_by_day.values()) if newest_by_day else None
    behind = (
        sum(1 for day in newest_by_day.values() if day < latest_session)
        if latest_session is not None
        else 0
    )

    return EodSyncReport(
        symbols_total=len(symbols),
        symbols_updated=updated,
        symbols_unchanged=unchanged,
        symbols_empty=empty,
        symbols_behind=behind,
        symbols_failed=len(failed),
        latest_session=latest_session,
        expected_session=expected_session(as_of),
        rows_upserted=rows_upserted,
        dropped_nonpositive=dropped_nonpositive,
        dropped_unparsable=dropped_unparsable,
        skipped_invalid=tuple(skipped_invalid),
        failed=tuple(failed),
        elapsed_s=_time.monotonic() - started,
    )
