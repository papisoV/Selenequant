"""Whole-market daily read face — the screener's starting cross-section.

``market_daily_universe`` is the *read* half of the daily base. B0 wrote three
years of bars into ``("astock","daily_all","1d")``; without a consumer that is
a write-only pipeline (the stored artifact looks like coverage while nothing
reads it), so this module exists as much to close that loop as to serve
callers.

Two conventions a caller must not have to rediscover:

* **``adjust="raw"`` is the only supported value, and it is explicit.** Raw
  (unadjusted) prices jump across ex-dividend dates; a moving average over a
  raw series steps at every such date. The alternative — quietly returning
  something else — would be worse, so the parameter is in the signature and
  the fact is carried on ``df.attrs``.

* **Suspension holes are real and must be handled by date, not by row count.**
  Of 5574 symbols, 158 carry genuine date gaps (`data/gap_report.json` is the
  input for that), and a suspended name simply has no bar for those sessions.
  Any indicator that advances one row per bar will therefore silently compute
  a 20-day mean over a longer calendar span, which is the classic way a
  screener reports a signal on a stock that did not trade. Compare ``date``
  values; do not index by position.

The module deliberately does not import ``selene_space_market``. ``ashare``
does not depend on ``market-data`` (the dependency runs the other way, and
``market-data`` importing ``ashare`` would be a cycle), so the ``db`` argument
is duck-typed: anything exposing ``get_cross_section`` works, which keeps this
importable from the service layer while remaining testable with a stub.
"""

from __future__ import annotations

import logging
from datetime import datetime, time
from collections.abc import Iterable
from typing import Any, Literal

import pandas as pd
from selene_space_ashare.data_fetcher import get_a_share_list

logger = logging.getLogger(__name__)

EXCHANGE = "astock"
MARKET_TYPE = "daily_all"
INTERVAL = "1d"

# The columns the screener contracts on. Order is part of the contract: the
# plan fixes it, and a positional consumer (a CSV writer, a positional indexer)
# would silently misread a reordered frame.
COLUMNS: tuple[str, ...] = (
    "symbol",
    "name",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "prev_close",
)

# `amount` is absent by design: Sina's kline endpoint does not return turnover,
# so every candidate value would be approximated (close × volume) and carry a
# precision the column does not have. Callers that need turnover should compute
# it where they can label the approximation; see plan §4 B3 and §7 risk #2.


def _empty_frame() -> pd.DataFrame:
    """Empty frame carrying every contract column, so ``df["close"]`` is clean.

    A dict of typed Series rather than a bare ``DataFrame()``: a caller slicing
    a column off an empty result must get an empty Series, not a KeyError that
    looks like a schema regression.
    """
    return pd.DataFrame({column: pd.Series(dtype="object") for column in COLUMNS})


def _session_date(ts_ms: int, cst) -> str:
    """CST-midnight epoch millis → ``YYYY-MM-DD``."""
    return datetime.fromtimestamp(ts_ms / 1000, tz=cst).date().isoformat()


def market_daily_universe(
    db: Any,
    *,
    end_date: str,
    adjust: Literal["raw"] = "raw",
) -> pd.DataFrame:
    """Latest bar per symbol as of ``end_date``, plus each symbol's prior close.

    ``end_date`` is inclusive and interpreted as a Beijing-time session date.
    Symbols whose newest bar is older than ``end_date`` are still returned —
    that is how a suspended stock appears (with its true, older ``date``) —
    so callers that want "only names that traded on ``end_date``" must filter
    on ``date == end_date`` themselves. Returning them is the safer default:
    dropping halted names from a cross-section turns a suspension into an
    invisible absence, and the caller cannot tell that from a delisting.

    ``name`` is attached from the *current* universe cross-section, so it is a
    present-day label (``ST`` status included, which limit rules depend on) and
    not a historical one. A symbol that has left the universe keeps its row and
    gets an empty name rather than being dropped, so the count stays comparable
    to the stored partition.

    Raises ``ValueError`` on an unsupported ``adjust`` rather than coercing it:
    a silent fallback would hand back raw prices to a caller who asked for
    adjusted ones and now believes a back-adjusted series is raw.
    """
    if adjust != "raw":
        raise ValueError(
            f"market_daily_universe supports adjust='raw' only (got {adjust!r}); "
            "the daily base stores unadjusted Sina bars"
        )

    from datetime import date as _date

    from selene_space_ashare.sina_daily import CST

    try:
        year, month, day = (int(part) for part in end_date.split("-"))
        end_ms = int(
            datetime.combine(_date(year, month, day), time.min, tzinfo=CST).timestamp()
            * 1000
        )
    except (ValueError, TypeError) as exc:
        raise ValueError(f"end_date must be YYYY-MM-DD, got {end_date!r}") from exc

    raw = db.get_cross_section(EXCHANGE, MARKET_TYPE, INTERVAL, end_ms=end_ms)
    if raw.empty:
        return _empty_frame()

    frame = pd.DataFrame(
        {
            "symbol": raw["symbol"].astype(str),
            "date": raw["timestamp"].map(lambda ts: _session_date(int(ts), CST)),
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": pd.to_numeric(raw["close"], errors="coerce"),
            "volume": pd.to_numeric(raw["volume"], errors="coerce"),
            "prev_close": pd.to_numeric(raw["prev_close"], errors="coerce"),
        }
    )

    names = _universe_names()
    frame.insert(1, "name", frame["symbol"].map(names).fillna(""))

    frame = (
        frame[list(COLUMNS)].sort_values("symbol", kind="stable").reset_index(drop=True)
    )
    # attrs survive most pandas ops but not all (a concat drops them), so the
    # provenance is also documented here rather than only carried in-band.
    frame.attrs = {"adjust": "raw", "source": "sina"}
    return frame


def _universe_names() -> dict[str, str]:
    """Current code → name map, keyed by *suffixed* symbol.

    Empty on failure: the universe is a cached akshare call that can fail
    offline, and a missing display name is not worth failing a price query
    over. The failure is logged rather than swallowed silently.
    """
    from selene_space_core.sina_symbols import code_to_symbol

    try:
        frame = get_a_share_list()
    except Exception:
        logger.warning(
            "market_daily_universe: universe names unavailable; names left blank",
            exc_info=True,
        )
        return {}

    names: dict[str, str] = {}
    for code, name in zip(frame["code"], frame["name"], strict=False):
        try:
            names[code_to_symbol(str(code).strip())] = str(name)
        except ValueError:
            continue
    return names


def market_daily_window(
    db: Any,
    *,
    start_date: str,
    end_date: str,
    symbols: Iterable[str] | None = None,
    adjust: Literal["raw"] = "raw",
) -> pd.DataFrame:
    """Every stored bar in ``[start_date, end_date]``, all symbols, as of the base.

    The multi-session companion to :func:`market_daily_universe`, for callers
    that need a *series* rather than one cross-section — rotation returns
    computed from N sessions of closes is the motivating case.

    ``prev_close`` is resolved from the symbol's own immediately preceding bar
    in the whole partition, not from the first bar inside the window: a window
    that starts on session k still needs session k-1's close to score session
    k, or every window computes N-1 returns and silently drops the first day.

    Two conventions carry over unchanged from :func:`market_daily_universe`, and
    for the same reasons: ``adjust="raw"`` is the only supported value (and
    explicit, so nobody mistakes raw for back-adjusted), and suspended symbols
    simply have fewer rows — callers must compare ``date`` values rather than
    assume one row per symbol per session.

    Args:
        db: anything exposing ``get_partition_range`` (see KlineDB) — duck-typed
            because ``ashare`` must not import ``market-data``.
        start_date / end_date: ``YYYY-MM-DD``, both inclusive, Beijing session
            dates.
        symbols: restrict the result to these symbols. ``None`` (default) means
            the whole universe. Unknown symbols are not an error — they simply
            contribute no rows.
        adjust: only ``"raw"``.

    Returns:
        ``COLUMNS`` ordered by ``(symbol, date)`` ascending. Empty (with the
        contract columns) when the window holds no bars.

    Raises:
        ValueError: unsupported ``adjust``, or a malformed date. Malformed
            dates are rejected rather than coerced — ``"2026-13-45"`` passed
            through ``datetime`` would raise a different error type, and a
            caller catching ``ValueError`` should get this one.
    """
    if adjust != "raw":
        raise ValueError(
            f"market_daily_window supports adjust='raw' only (got {adjust!r}); "
            "the daily base stores unadjusted Sina bars"
        )

    from datetime import date as _date

    from selene_space_ashare.sina_daily import CST

    def _ms(text: str) -> int:
        try:
            year, month, day = (int(part) for part in text.split("-"))
            moment = datetime.combine(_date(year, month, day), time.min, tzinfo=CST)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"date must be YYYY-MM-DD, got {text!r}") from exc
        return int(moment.timestamp() * 1000)

    start_ms = _ms(start_date)
    end_ms = _ms(end_date)
    if start_ms > end_ms:
        raise ValueError(f"start_date {start_date!r} is after end_date {end_date!r}")

    raw = db.get_partition_range(
        EXCHANGE, MARKET_TYPE, INTERVAL, start_ms=start_ms, end_ms=end_ms
    )
    if raw.empty:
        return _empty_frame()
    if symbols is not None:
        # Filter after the ranged read rather than adding a giant IN clause: the
        # range is the selective predicate (13 sessions ≈ 67k rows), and 5k
        # bind variables would hit SQLite's limit for a benefit that is not
        # there. Callers passing two or three symbols pay ~0.6 s; a caller
        # passing the whole universe pays what the unfiltered read costs.
        wanted = {str(symbol) for symbol in symbols}
        raw = raw[raw["symbol"].astype(str).isin(wanted)]
        if raw.empty:
            return _empty_frame()

    frame = pd.DataFrame(
        {
            "symbol": raw["symbol"].astype(str),
            "date": raw["timestamp"].map(lambda ts: _session_date(int(ts), CST)),
            "open": pd.to_numeric(raw["open"], errors="coerce"),
            "high": pd.to_numeric(raw["high"], errors="coerce"),
            "low": pd.to_numeric(raw["low"], errors="coerce"),
            "close": pd.to_numeric(raw["close"], errors="coerce"),
            "volume": pd.to_numeric(raw["volume"], errors="coerce"),
        }
    )

    # Prior close from the partition, resolved per symbol, *before* the window's
    # first row — so the first session inside the window is scorable.
    prev_close = _prev_close_lookup(db, start_ms)
    frame["prev_close"] = frame["symbol"].map(prev_close)

    names = _universe_names()
    frame.insert(1, "name", frame["symbol"].map(names).fillna(""))

    frame = (
        frame[list(COLUMNS)]
        .sort_values(["symbol", "date"], kind="stable")
        .reset_index(drop=True)
    )
    frame.attrs = {"adjust": "raw", "source": "sina"}
    return frame


def _prev_close_lookup(db: Any, start_ms: int) -> dict[str, float]:
    """``symbol → close of its last bar strictly before ``start_ms```.

    One query for the whole cross-section. Symbols with no earlier bar are
    absent from the mapping — the caller's ``map`` then yields NaN, which is
    the correct "no prior close" signal: the first session of a new listing is
    unscorable, not silently scored against itself.
    """
    getter = getattr(db, "get_last_before", None)
    if getter is None:
        return {}
    prior = getter(EXCHANGE, MARKET_TYPE, INTERVAL, before_ms=start_ms)
    if prior is None or getattr(prior, "empty", True):
        return {}
    out: dict[str, float] = {}
    for symbol, value in zip(prior["symbol"], prior["close"], strict=False):
        number = pd.to_numeric(value, errors="coerce")
        if pd.notna(number):
            out[str(symbol)] = float(number)
    return out
