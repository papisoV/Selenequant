"""Sina daily-kline fetch / parse / gap detection for A-share bars.

Why this is separate from ``astock_source._parse_rows`` rather than shared with
it: that parser hardcodes ``market_type="spot"`` — the *subscription* partition
the anomaly detector reads as a limit-up baseline. The whole-market daily base
writes ``("astock", "daily_all")`` and must not touch ``spot``. The two cannot
share code either way: ``market-data`` does not depend on ``ashare``, and
``core`` — the one package both depend on — cannot build a ``KlineEvent``
(it has no dependency on ``selene-space-types``). So the stamp and the
non-positive guard are stated in both places; only the board rule, which is
pure string logic, is shared, via :mod:`selene_space_core.sina_symbols`.

The ``closed`` verdict is shared for the same reason and under the same limit:
:func:`selene_space_core.session_clock.daily_bar_is_final` decides *whether* a
row is a session close (pure date/time logic, no event type involved), and each
parser assigns the flag. Two copies of that decision would be a live bug — one
parser would call a forming bar final while the other did not, and a store that
believes a half-formed bar is final has no way to notice.

Two conventions this module owns, both load-bearing:

* **Timestamp stamp.** A daily bar is stamped at its session date's midnight
  in Beijing time (+08:00, no DST). The anomaly detector's KlineDB cutoff
  filters on this, so stamping off local wall time would slide bars across the
  session-date boundary on a UTC host.
* **Non-positive OHLC rows are dropped.** Sina returns ``0`` prices for a
  halted/pre-open row and ``KlineEvent`` enforces ``open > 0``. Dropping is
  correct but must be *counted* — a silent drop reads as "no data that day".
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import requests
from selene_space_core.session_clock import daily_bar_is_final, session_date_of
from selene_space_core.sina_symbols import to_sina_symbol
from selene_space_types.market import KlineEvent

SINA_KLINE_URL = (
    "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://finance.sina.com.cn",
}

# China has no DST, so a fixed offset is exact.
CST = timezone(timedelta(hours=8))

# Sina's own ceiling: ``1024`` is accepted, ``2000`` returns an empty body.
# 1023 is the value ``astock_source`` already uses in production — kept so both
# paths ask for the same depth.
MAX_DATALEN = 1023

# A gap wider than this is not a holiday (Spring Festival is the longest, ~9
# calendar days) — it is a suspension or a data hole.
GAP_THRESHOLD_DAYS = 15


@dataclass(frozen=True)
class ParseResult:
    """Parsed events plus the honest count of rows that could not be used."""

    events: tuple[KlineEvent, ...]
    dropped_nonpositive: int  # OHLC had a <= 0 value (halted/pre-open row)
    dropped_unparsable: int  # malformed row (missing key / bad number)


def fetch_daily_rows(
    symbol: str,
    *,
    datalen: int = MAX_DATALEN,
    session: requests.Session | None = None,
    timeout: float = 15.0,
) -> list[dict[str, str]]:
    """Fetch raw Sina daily rows for one suffixed symbol (``600519.SH``).

    Raises on transport errors and on an empty body — an empty answer for a
    valid prefix means the symbol has no history, which the caller must see
    rather than silently treat as "zero bars".
    """
    sina_sym = to_sina_symbol(symbol)
    getter = session.get if session is not None else requests.get
    resp = getter(
        SINA_KLINE_URL,
        params={"symbol": sina_sym, "scale": 240, "ma": "no", "datalen": datalen},
        headers=HEADERS,
        timeout=timeout,
    )
    resp.raise_for_status()
    text = resp.text.strip()
    if text in ("", "null"):
        raise ConnectionError(f"Sina returned an empty body for {symbol}")
    payload = resp.json()
    if not isinstance(payload, list):
        raise ValueError(
            f"Sina returned {type(payload).__name__}, not a row list: {symbol}"
        )
    return payload


def parse_rows(
    rows: list[dict[str, str]],
    symbol: str,
    *,
    market_type: str = "daily_all",
    exchange: str = "astock",
    interval: str = "1d",
    as_of: datetime | None = None,
) -> ParseResult:
    """Sina rows → KlineEvents. Pure: does not mutate ``rows``.

    ``as_of`` is when the response was received, used to decide whether a bar
    for *today* is the session's close or a still-forming snapshot (see
    :func:`selene_space_core.session_clock.daily_bar_is_final`). It defaults to
    the wall clock; pass it explicitly to make a fetch's snapshot/final verdict
    reproducible in tests.

    The flag matters beyond bookkeeping: ``closed=False`` is how every strategy
    runtime in the repo knows not to act on a half-formed bar.
    """
    events: list[KlineEvent] = []
    dropped_nonpositive = 0
    dropped_unparsable = 0

    for row in rows:
        try:
            day = row["day"]
            ts_ms = int(
                datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=CST).timestamp()
                * 1000
            )
            ohlc = {k: float(row[k]) for k in ("open", "high", "low", "close")}
            volume = float(row["volume"])
        except (KeyError, TypeError, ValueError):
            dropped_unparsable += 1
            continue

        if any(v <= 0 for v in ohlc.values()):
            dropped_nonpositive += 1
            continue

        events.append(
            KlineEvent(
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                timestamp=ts_ms,
                interval=interval,
                volume=volume,
                closed=daily_bar_is_final(session_date_of(ts_ms), as_of=as_of),
                **ohlc,
            )
        )

    return ParseResult(
        events=tuple(events),
        dropped_nonpositive=dropped_nonpositive,
        dropped_unparsable=dropped_unparsable,
    )


def detect_gaps_in_days(
    days: Iterable[str], *, threshold_days: int = GAP_THRESHOLD_DAYS
) -> list[tuple[str, str, int]]:
    """Per-day date-jump detection — the only reliable hole check.

    Ratio heuristics (bars ÷ calendar span) cannot tell a *recently listed*
    stock from a *suspended* one and misreport small-history names. This walks
    the actual dates: a jump wider than ``threshold_days`` is a suspension or a
    data hole, returned as ``[(prev_day, next_day, calendar_gap_days), ...]``.

    Inputs are sorted defensively — Sina returns ascending order, but a hole
    check that trusts input order would silently miss a misordered page.

    Takes bare ``YYYY-MM-DD`` strings rather than rows so the same algorithm
    serves both the fetch path (:func:`detect_gaps`, rows straight from Sina)
    and the read-back path, which extracts dates from the DB. Two copies of
    this walk would be two chances to disagree about what a hole is.
    """
    ordered = sorted(days)
    gaps: list[tuple[str, str, int]] = []
    for prev_day, next_day in zip(ordered, ordered[1:]):
        try:
            prev = datetime.strptime(prev_day, "%Y-%m-%d")
            nxt = datetime.strptime(next_day, "%Y-%m-%d")
        except ValueError:
            continue
        calendar_gap = (nxt - prev).days
        if calendar_gap > threshold_days:
            gaps.append((prev_day, next_day, calendar_gap))
    return gaps


def detect_gaps(
    rows: list[dict[str, str]], *, threshold_days: int = GAP_THRESHOLD_DAYS
) -> list[tuple[str, str, int]]:
    """Hole check over raw Sina rows; see :func:`detect_gaps_in_days`."""
    return detect_gaps_in_days(
        (row["day"] for row in rows if "day" in row), threshold_days=threshold_days
    )
