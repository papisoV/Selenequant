# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 papisoV
# Source: https://github.com/papisoV/tick-stock-panel
"""Mainline (主线) identification — which 概念 owns the limit-up ladder today.

Ported from TSP ``backend/app/services/market_mainline.py:compute_mainline_range``
with three deliberate differences, each recorded where it applies:

1. **pandas, not polars.** Same reason as :mod:`concept_rotation`.
2. **Boards are recomputed from bars, not read from an enriched column.** TSP
   reads ``consecutive_limit_ups`` from ``kline_daily_enriched``. selene's daily
   base has no such column and this module does not add one: the boards are a
   pure function of the close series plus :func:`market_rules.limit_up_price`,
   so recomputing them keeps a single source of truth. The cost is bounded by
   the window (default 20 sessions ≙ ~110k bars).
3. **The streak is window-bounded by construction.** TSP's enriched column
   carries the full history, so a 12-board streak shows as 12. Here the longest
   observable run is the window itself, and the response says so via
   ``streak_capped``. A number that saturates at the window length is still
   useful for ranking *within* the window; pretending it is the true streak
   would not be.

The scoring口径 is TSP's, unchanged: four cross-sectional features per concept
per session — limit-up count, highest board, rungs filled between 2 and the
high, and the count of members at 2+ boards — each rank-normalised across the
session's concepts and weighted 0.35 / 0.25 / 0.25 / 0.15 into a 0–100 score.
Concepts with fewer than :data:`MIN_LIMIT_UP` limit-ups that session do not
rank at all: a theme with two limit-ups is noise, and letting it into the
cross-section shifts every real concept's rank-normalised features.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from selene_space_ashare.concept_map import MEMBERSHIP_NOTE
from selene_space_ashare.concept_rotation import (
    DEFAULT_EXCLUDE_CONCEPTS,
    _clamp_days,
    _day_ms,
    _latest_session_date,
    _session_index,
    _window_frame,
)
from selene_space_ashare.market_rules import coerce_date, is_st, limit_up_price

logger = logging.getLogger(__name__)

__all__ = [
    "MEMBERSHIP_NOTE",
    "MIN_LIMIT_UP",
    "SCORE_WEIGHTS",
    "TOP_PER_DAY",
    "compute_mainline",
]

# TSP `_MIN_LIMIT_UP`. A concept needs at least this many limit-ups in a session
# to enter that session's ranking.
MIN_LIMIT_UP = 3

# TSP `_TOP_PER_DAY`. How many concepts per session survive into the response.
TOP_PER_DAY = 30

# TSP `_SCORE_WEIGHTS`, verbatim and in the same order.
SCORE_WEIGHTS: dict[str, float] = {
    "limit_up_count": 0.35,
    "max_boards": 0.25,
    "rungs_filled": 0.25,
    "ge2_count": 0.15,
}

_EXCHANGE = "astock"
_MARKET_TYPE = "daily_all"
_INTERVAL = "1d"

# TSP's board filter (min 4 / max 600 members). The upper bound exists because
# 融资融券-style buckets hold hundreds of names and would own every ladder;
# :data:`DEFAULT_EXCLUDE_CONCEPTS` catches the common ones by name, and this
# catches the ones nobody thought of by size.
MAX_MEMBERS = 600


def _core_code(symbol: str) -> str:
    """``300750.SZ`` → ``"300750"``; a bare code passes through."""
    text = str(symbol).strip().upper()
    return text.rsplit(".", 1)[0] if "." in text else text


def _boards_for_symbol(
    closes: pd.Series,
    gaps: pd.Series,
    symbol: str,
    name: str,
    dates: pd.Series | None = None,
) -> tuple[list[int], list[bool]]:
    """Per-bar board count and limit-up flag for one symbol over the window.

    ``closes`` and ``gaps`` are aligned ascending; ``gaps[i]`` is the number of
    sessions between bar ``i`` and bar ``i-1`` (1 = consecutive). A bar whose
    predecessor is not the previous session breaks the run: the streak must be
    a run of *consecutive trading days*, not a run of surviving rows.

    The first bar has no predecessor inside the window and therefore no
    limit-up flag — it is a baseline, exactly as in :mod:`concept_rotation`.

    ``dates`` (aligned with ``closes``) supplies each bar's session so the band
    in force on that date is used. Omitted, every bar is judged against the
    current rulebook — fine for a live scan, wrong for a window straddling
    2026-07-06.
    """
    core = _core_code(symbol)
    if len(closes) == 0:
        return [], []
    flags: list[bool] = [False]
    boards: list[int] = [0]
    run = 0
    prev = float(closes.iloc[0])
    for index in range(1, len(closes)):
        close = float(closes.iloc[index])
        if gaps.iloc[index] != 1:
            # Resumption after a halt: the previous close is not yesterday's,
            # so "did it close at the limit" is unanswerable. Break the run.
            run = 0
            flags.append(False)
            boards.append(0)
            prev = close
            continue
        day = coerce_date(dates.iloc[index]) if dates is not None else None
        hit = close >= float(limit_up_price(prev, core, name, trade_date=day))
        run = run + 1 if hit else 0
        flags.append(hit)
        boards.append(run)
        prev = close
    return boards, flags


def _rank_normalise(values: pd.Series) -> pd.Series:
    """Cross-sectional ``(rank - 1) / max(n - 1, 1)`` — TSP's normalisation.

    Ties share the average rank (``method="average"``), matching polars'
    default. The ``max(n - 1, 1)`` denominator is what TSP uses so a single
    concept in a session maps to 0 rather than dividing by zero.
    """
    count = len(values)
    if count == 0:
        return values.astype(float)
    ranks = values.rank(method="average")
    return (ranks - 1.0) / max(count - 1, 1)


def compute_mainline(
    db: Any,
    membership: pd.DataFrame,
    *,
    days: int = 20,
    end_date: str | None = None,
    exclude: frozenset[str] | set[str] | None = None,
    exclude_st_names: bool = True,
    names: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Per-session mainline ranking of concepts by limit-up ladder strength.

    Args:
        db: KlineDB-like — needs ``get_session_stamps`` / ``get_partition_range``.
        membership: long-form ``symbol`` / ``concept``.
        days: window length in *sessions* (clamped to [7, 30]); the last
            ``days`` sessions are scored. ``None`` end_date resolves to the
            newest session in the base.
        end_date: ``YYYY-MM-DD``, inclusive.
        exclude: concept names to drop; defaults to
            :data:`concept_rotation.DEFAULT_EXCLUDE_CONCEPTS`.
        exclude_st_names: drop names whose ``names`` entry carries an ST mark
            (see :func:`market_rules.is_st`). TSP defaults to on: an ST board's
            limit-ups are a different market (5% band) and do not describe the
            theme. Set ``False`` for a raw view; symbols without a name are
            kept — unknown is not the same as ST.
        names: ``symbol → display name``. Only consulted when
            ``exclude_st_names`` is true.

    Returns:
        ``{
            "dates": [... newest first ...],
            "columns": {date: [[concept, score, limit_up_count, max_boards, leader], ...]},
            "as_of": str | None,
            "streak_capped": True,   # boards cannot exceed the window
            "membership_note": str,
            "reason": str | None,
        }``

        ``columns`` entries are lists (JSON-ready) with ``score`` rounded to
        one decimal and ``leader`` the highest-board member, ties broken on
        symbol for determinism instead of on TSP's ``amount`` — the daily base
        carries no turnover column.
    """
    days = _clamp_days(days)
    empty: dict[str, Any] = {
        "dates": [],
        "columns": {},
        "as_of": None,
        "streak_capped": True,
        "membership_note": MEMBERSHIP_NOTE,
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

    # `sessions[0]` is the baseline session: its closes can flag a limit-up on
    # `sessions[1]`, but nothing scores on it. `days` counts scored sessions.
    sessions = _session_index(db, end_date=end_date, sessions=days)
    if len(sessions) < 2:
        return {**empty, "reason": f"insufficient_sessions({len(sessions)})"}

    bars = db.get_partition_range(
        _EXCHANGE,
        _MARKET_TYPE,
        _INTERVAL,
        start_ms=_day_ms(sessions[0]),
        end_ms=_day_ms(sessions[-1]) + 86_399_999,
    )
    if bars is None or getattr(bars, "empty", True):
        return {**empty, "reason": "daily_base_empty"}

    # Unlike rotation, the window-opening bar is *kept*: it is a baseline that
    # can flag a limit-up on the next session, so the gap column is built here
    # rather than through `_row_gap`, which would drop it.
    frame = _window_frame(bars, sessions)
    frame["prev_sidx"] = frame.groupby("symbol", sort=False)["sidx"].shift(1)
    frame["gap"] = (frame["sidx"] - frame["prev_sidx"]).fillna(0)

    # Boards per symbol. Done in a Python loop over groups rather than a
    # vectorised formula because the reset-on-break + halt-reset rules are
    # exactly the branchy part a clever one-liner would get subtly wrong; 5574
    # groups × 21 bars is ~0.3 s, which is a fine price for an obvious rule.
    boards_all: list[int] = []
    limit_all: list[bool] = []
    name_map = names or {}
    for symbol, group in frame.groupby("symbol", sort=False):
        boards, flags = _boards_for_symbol(
            group["close"],
            group["gap"],
            symbol,
            name_map.get(symbol, ""),
            group["date"],
        )
        boards_all.extend(boards)
        limit_all.extend(flags)
    frame["boards"] = boards_all
    frame["is_limit_up"] = limit_all

    # No `sidx >= 1` filter: `is_limit_up` is False for every window-opening bar
    # by construction (`_boards_for_symbol` marks index 0 as a baseline), so a
    # session-index guard here would be unreachable.
    limit_rows = frame[frame["is_limit_up"]]
    if limit_rows.empty:
        return {**empty, "reason": "no_limit_ups_in_window"}

    if exclude_st_names and name_map:
        st_symbols = {s for s, n in name_map.items() if is_st(n)}
        if st_symbols:
            limit_rows = limit_rows[~limit_rows["symbol"].isin(st_symbols)]
        if limit_rows.empty:
            return {**empty, "reason": "all_limit_ups_are_st"}

    banned = DEFAULT_EXCLUDE_CONCEPTS if exclude is None else exclude
    members = membership[["symbol", "concept"]].drop_duplicates()
    if banned:
        members = members[~members["concept"].isin(banned)]
    if members.empty:
        return {**empty, "reason": "membership_all_excluded"}

    # TSP's max_members filter, applied on the *whole* membership map (not on
    # the limit-up join) — a 700-member bucket is a bucket whether or not any
    # of its members limited up today.
    sizes = members.groupby("concept")["symbol"].nunique()
    members = members[members["concept"].isin(sizes[sizes <= MAX_MEMBERS].index)]
    if members.empty:
        return {**empty, "reason": "membership_all_oversized"}

    joined = limit_rows[["date", "symbol", "boards"]].merge(
        members, on="symbol", how="inner"
    )
    if joined.empty:
        return {**empty, "reason": "membership_daily_symbol_mismatch"}

    agg = joined.groupby(["date", "concept"], sort=False)["boards"].agg(
        limit_up_count="size",
        max_boards="max",
    )
    ge2 = (
        joined[joined["boards"] >= 2]
        .groupby(["date", "concept"], sort=False)["boards"]
        .agg(ge2_count="size", rungs_filled="nunique")
    )
    agg = agg.join(ge2, how="left").fillna({"ge2_count": 0, "rungs_filled": 0})
    agg[["ge2_count", "rungs_filled"]] = agg[["ge2_count", "rungs_filled"]].astype(
        "int64"
    )

    # Leader: highest board, deterministic tie-break on symbol. TSP breaks
    # the tie on turnover; the daily base carries no amount column, and
    # silently substituting volume would be a different statistic under the
    # same field name.
    leaders = (
        joined.sort_values(
            ["date", "concept", "boards", "symbol"], ascending=[True, True, False, True]
        )
        .drop_duplicates(subset=["date", "concept"], keep="first")
        .set_index(["date", "concept"])["symbol"]
    )
    agg["leader"] = leaders

    agg = agg[agg["limit_up_count"] >= MIN_LIMIT_UP]
    if agg.empty:
        return {**empty, "reason": f"no_concept_with_{MIN_LIMIT_UP}_limit_ups"}

    scored: dict[str, list[list[Any]]] = {}
    for session, block in agg.groupby(level=0, sort=False):
        block = block.droplevel(0).copy()
        total = pd.Series(0.0, index=block.index)
        for column, weight in SCORE_WEIGHTS.items():
            total = total + weight * _rank_normalise(block[column])
        block["score"] = (total * 100.0).round(1)
        block = block.sort_values(
            ["score", "limit_up_count", "max_boards"],
            ascending=[False, False, False],
            kind="stable",
        ).head(TOP_PER_DAY)
        scored[str(session)] = [
            [
                concept,
                float(row["score"]),
                int(row["limit_up_count"]),
                int(row["max_boards"]),
                str(row["leader"]),
            ]
            for concept, row in block.iterrows()
        ]

    dates = sorted(scored, reverse=True)
    return {
        "dates": dates,
        "columns": {day: scored[day] for day in dates},
        "as_of": dates[0] if dates else None,
        "streak_capped": True,
        "membership_note": MEMBERSHIP_NOTE,
        "reason": None if dates else "no_sessions_after_filter",
    }
