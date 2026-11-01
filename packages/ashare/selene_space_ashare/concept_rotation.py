# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 papisoV
# Source: https://github.com/papisoV/tick-stock-panel
"""Concept rotation matrix — which 概念 is leading, over a window of sessions.

Ported from TSP `backend/app/services/rps_rotation.py:build_rps_rotation`, with
three deliberate differences that the docstring of each piece records:

1. **pandas, not polars.** The ashare package has zero ``import polars``; 200
   lines do not justify a second dataframe library.
2. **The daily base, not an enriched parquet.** TSP reads a precomputed
   ``change_pct`` column from its enriched cache. selene's ``daily_all``
   partition stores raw OHLCV with a resolved ``prev_close``, so returns are
   computed here. That costs ~1.2 s across 5574 symbols (measured) and removes
   a whole class of "the enriched column disagrees with the bars" bugs.
3. **Membership is injected, not looked up.** The caller passes the map in.
   Coupling this module to ``concept_map`` would make it untestable without a
   network stub and would hide the fact that membership is a *snapshot* — see
   :data:`selene_space_ashare.concept_map.MEMBERSHIP_NOTE`.

The aggregation口径 matches TSP: each concept's daily move is the **equal
weight mean** of its members' moves that session, members with no bar that day
(suspended) are excluded from both numerator and denominator, and concepts with
fewer than :data:`MIN_MEMBERS` covered members are dropped rather than ranked on
a handful of names.

One rule TSP states but does not enforce: a bar whose predecessor is more than
one session earlier is a *resumption*, so its move spans the halt. Those rows
are dropped from the mean here. Measured 2026-10-18 over a 12-session window
that is 10 rows out of ~67k — too small to move today's top-10, but large
enough to invent a spike for whichever concept holds a resuming name, and the
window is configurable.
"""

from __future__ import annotations

import logging
from datetime import date as _date
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# A "concept" with two members is not a theme, it is a coincidence. Measured
# 2026-10-18: 1184 of 1199 concepts clear this bar; the 15 that do not are
# mostly freshly-minted labels with a single constituent so far.
MIN_MEMBERS = 3

# Attribute buckets, not themes: these are trading-mechanic tags whose "members"
# are whoever happened to qualify that week. Leaving them in makes every
# ranking open with 融资融券 / 沪股通 at the top because those buckets hold
# hundreds of names, drowning the actual theme. TSP filters on member *count*
# instead (max_members=600); a name list is more legible to a reader and does
# not require tuning a number.
DEFAULT_EXCLUDE_CONCEPTS: frozenset[str] = frozenset(
    {
        "融资融券",
        "沪股通",
        "深股通",
        "转融券",
        "转债标的",
        "含可转债",
        "ST股",
        "次新股",
        "预盈预增",
        "预亏预减",
    }
)

# TSP clamps the same way; a caller asking for 200 sessions is asking for the
# whole history under a name that promised a matrix view.
MIN_DAYS = 7
MAX_DAYS = 30

_EXCHANGE = "astock"
_MARKET_TYPE = "daily_all"
_INTERVAL = "1d"

# Session-calendar probe symbols. Chosen for depth of history and low halt
# risk; KlineDB falls back to the exact whole-partition scan when their union
# comes up short, so a bad list degrades speed, never correctness.
_CALENDAR_ANCHORS: tuple[str, ...] = ("000001.SZ", "600000.SH", "600519.SH")


def _clamp_days(days: int) -> int:
    return max(MIN_DAYS, min(MAX_DAYS, int(days)))


def _day_ms(text: str) -> int:
    """``YYYY-MM-DD`` → Beijing midnight epoch ms. The canonical daily stamp."""
    from datetime import datetime, time

    from selene_space_ashare.sina_daily import CST

    try:
        year, month, day = (int(part) for part in text.split("-"))
        moment = datetime.combine(_date(year, month, day), time.min, tzinfo=CST)
    except (ValueError, TypeError) as exc:
        raise ValueError(f"date must be YYYY-MM-DD, got {text!r}") from exc
    return int(moment.timestamp() * 1000)


def _session_index(db: Any, *, end_date: str, sessions: int) -> list[str]:
    """The last ``sessions`` session dates ending at ``end_date``, ascending.

    Resolved from the database rather than from a calendar: the base is the
    only authority on which days actually traded (holidays, half-days, and the
    National Day block are not something a ``timedelta`` knows about).

    Returns ``sessions + 1`` dates when available, because the first session in
    a window needs its predecessor's close to produce a return. The caller uses
    the extra leading date as the "prior session" and scores from index 1.
    """
    from selene_space_ashare.sina_daily import CST

    end_ms = _day_ms(end_date) + 86_399_999
    stamps = db.get_session_stamps(
        _EXCHANGE,
        _MARKET_TYPE,
        _INTERVAL,
        end_ms=end_ms,
        limit=sessions + 1,
        # Liquid names whose index tails give the calendar in ~0 ms. Overlapping
        # anchors are the safety net: if one is halted for a stretch the union
        # still fills, and KlineDB falls back to the exact scan if it does not.
        anchors=_CALENDAR_ANCHORS,
    )
    return [
        pd.to_datetime(stamp, unit="ms", utc=True).tz_convert(CST).strftime("%Y-%m-%d")
        for stamp in stamps
    ]


def _latest_session_date(db: Any) -> str | None:
    """The most recent session date in the daily base, or ``None`` if empty."""
    from datetime import datetime

    from selene_space_ashare.sina_daily import CST

    stamps = db.get_session_stamps(
        _EXCHANGE,
        _MARKET_TYPE,
        _INTERVAL,
        end_ms=2**62,  # an upper bound no timestamp can exceed
        limit=1,
        anchors=_CALENDAR_ANCHORS,
    )
    if not stamps:
        return None
    # `pd.Timestamp` subclasses the stdlib `datetime`; annotating through it
    # keeps `.strftime` typed `str` instead of pandas' `Any`.
    moment: datetime = pd.Timestamp(stamps[-1], unit="ms", tz="UTC").tz_convert(CST)
    return moment.strftime("%Y-%m-%d")


def _row_gap(frame: pd.DataFrame) -> pd.DataFrame:
    """Add ``prev_close`` / ``gap`` and keep only one-session-apart returns.

    ``gap`` is how many sessions separate a bar from the symbol's previous row:
    ``1`` = consecutive, ``>1`` = the symbol was halted and this bar's "daily"
    return actually spans the halt. Only consecutive sessions produce a daily
    return, so the rest are dropped. After this filter every surviving row has
    an in-window predecessor at the previous session, which also excludes the
    window's opening bar (it has no predecessor at all).
    """
    frame = frame.sort_values(["symbol", "sidx"], kind="stable").copy()
    frame["prev_close"] = frame.groupby("symbol", sort=False)["close"].shift(1)
    frame["prev_sidx"] = frame.groupby("symbol", sort=False)["sidx"].shift(1)
    frame = frame.dropna(subset=["prev_close", "prev_sidx"])
    frame["gap"] = frame["sidx"] - frame["prev_sidx"]
    frame = frame[frame["prev_close"] > 0]
    return frame[frame["gap"] == 1]


def _window_frame(bars: pd.DataFrame, sessions: list[str]) -> pd.DataFrame:
    """Bars → ``symbol`` / ``date`` / ``close`` / ``sidx``, indexable by session.

    Rows whose close is unparsable, or whose date is not one of ``sessions``,
    are dropped. Sorted by ``(symbol, sidx)`` so per-symbol shifts walk the
    session calendar rather than row order.
    """
    from selene_space_ashare.sina_daily import CST

    frame = pd.DataFrame(
        {
            "symbol": bars["symbol"].astype(str),
            "date": pd.to_datetime(
                pd.to_numeric(bars["timestamp"], errors="coerce"),
                unit="ms",
                utc=True,
            )
            .dt.tz_convert(CST)
            .dt.strftime("%Y-%m-%d"),
            "close": pd.to_numeric(bars["close"], errors="coerce"),
        }
    ).dropna(subset=["close"])

    order = {session: index for index, session in enumerate(sessions)}
    frame["sidx"] = frame["date"].map(order)
    frame = frame.dropna(subset=["sidx"])
    frame["sidx"] = frame["sidx"].astype("int64")
    return frame.sort_values(["symbol", "sidx"], kind="stable").copy()


def build_rotation_matrix(
    db: Any,
    membership: pd.DataFrame,
    *,
    days: int = 12,
    end_date: str | None = None,
    exclude: frozenset[str] | set[str] | None = None,
    min_members: int = MIN_MEMBERS,
) -> dict[str, Any]:
    """Equal-weight concept returns over the last ``days`` sessions.

    Args:
        db: exposes ``get_partition_range`` / ``get_session_stamps`` (KlineDB).
        membership: long-form ``symbol`` / ``concept`` (extra columns ignored).
            Symbols must be the suffixed upper-case form.
        days: window length in *sessions*; clamped to [7, 30].
        end_date: last session, ``YYYY-MM-DD``. Inclusive. ``None`` resolves to
            the newest session in the base — the convenience default for a
            live page. Pass it explicitly for a reproducible result.
        exclude: concept names to drop; defaults to
            :data:`DEFAULT_EXCLUDE_CONCEPTS`. Pass ``frozenset()`` to keep them.
        min_members: drop concepts with fewer covered members on the session.

    Returns:
        ``{
            "dates": [... newest first ...],
            "columns": {date: [[concept, pct], ... desc]},
            "concept_count": int,   # concepts that produced at least one day
            "as_of": str,           # the last session actually scored
            "reason": str | None,   # why it is empty, when it is
        }
        ``pct`` is a percentage number (``5.22`` means +5.22%), matching TSP's
        ``avg_pct`` and the A-share convention the frontend renders.

    Empty is a normal answer: no membership, no bars, or a window shorter than
    two sessions all return ``reason`` set and no exception.
    """
    days = _clamp_days(days)
    empty: dict[str, Any] = {
        "dates": [],
        "columns": {},
        "concept_count": 0,
        "as_of": None,
        "reason": None,
    }
    if membership is None or membership.empty:
        return {**empty, "reason": "membership_empty"}
    if not {"symbol", "concept"} <= set(membership.columns):
        return {**empty, "reason": "membership_missing_columns"}

    if end_date is None:
        end_date = _latest_session_date(db)
        if end_date is None:
            return {**empty, "reason": "daily_base_empty"}

    banned = DEFAULT_EXCLUDE_CONCEPTS if exclude is None else exclude

    sessions = _session_index(db, end_date=end_date, sessions=days)
    if len(sessions) < 2:
        return {**empty, "reason": f"insufficient_sessions({len(sessions)})"}

    # `sessions[0]` is the prior session (left edge — every symbol's baseline
    # close), `sessions[1]` is the first *scored* session. Fetching the prior
    # session's bars alongside the window means the baseline comes from one
    # ranged read instead of a per-symbol "last bar before" join, which on this
    # base costs ~4 s because the outer scan cannot be narrowed by symbol.
    window_start = sessions[0]
    window_end = sessions[-1]

    bars = db.get_partition_range(
        _EXCHANGE,
        _MARKET_TYPE,
        _INTERVAL,
        start_ms=_day_ms(window_start),
        end_ms=_day_ms(window_end) + 86_399_999,
    )
    if bars is None or getattr(bars, "empty", True):
        return {**empty, "reason": "daily_base_empty"}

    frame = _row_gap(_window_frame(bars, sessions))
    if frame.empty:
        return {**empty, "reason": "no_returns_in_window"}
    frame["pct"] = (frame["close"] / frame["prev_close"] - 1.0) * 100.0

    joined = frame[["symbol", "date", "pct"]].merge(
        membership[["symbol", "concept"]].drop_duplicates(),
        on="symbol",
        how="inner",
    )
    if joined.empty:
        return {**empty, "reason": "membership_daily_symbol_mismatch"}
    if banned:
        joined = joined[~joined["concept"].isin(banned)]

    agg = joined.groupby(["date", "concept"], sort=False)["pct"].agg(["mean", "count"])
    agg = agg[agg["count"] >= min_members]
    if agg.empty:
        return {**empty, "reason": "all_concepts_below_min_members"}

    columns: dict[str, list[list[Any]]] = {}
    for session, block in agg.groupby(level=0, sort=False):
        block = block.droplevel(0).sort_values("mean", ascending=False)
        columns[str(session)] = [
            [concept, round(float(mean), 2)]
            for concept, mean in zip(block.index, block["mean"], strict=False)
        ]

    ordered_dates = sorted(columns, reverse=True)
    return {
        "dates": ordered_dates,
        "columns": {d: columns[d] for d in ordered_dates},
        "concept_count": int(agg.index.get_level_values(1).nunique()),
        "as_of": ordered_dates[0] if ordered_dates else None,
        "reason": None if ordered_dates else "no_sessions_after_filter",
    }
