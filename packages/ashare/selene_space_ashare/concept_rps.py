# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 papisoV
# Source: https://github.com/papisoV/tick-stock-panel
"""Concept-level relative price strength (RPS) over a 120-session lookback.

RPS answers "how strong is this 概念 relative to every other 概念": each
concept's trailing return is averaged over its members and then ranked across
the eligible concepts, so a score is a percentile (0-100) rather than a return.
The individual-stock version already exists in
``selene_space_ashare_select.strategies.rps_breakout``; this module deliberately
does **not** reuse its arithmetic.

Two differences from that implementation, both load-bearing:

1. **Sessions, not rows.** ``rps_breakout`` shifts by ``rps_period`` *rows*.
   Measured on the daily base, 158 symbols inside a three-year window have
   real gaps (the ``ashare-wholemarket-daily-base`` plan's "0-1 gap" claim was
   disproven during B0). A row shift silently measures a longer span for those
   symbols. Here the baseline is the bar at session ``as_of - lookback`` in the
   exchange calendar, so a symbol halted on either edge has no return at all
   rather than a wrong one.

2. **Membership is a snapshot.** ``concept_map`` stores today's constituents,
   not a time series — there is no "membership at date T". Replaying a 120-day
   return therefore assumes today's membership held for the whole window. That
   assumption is quantified rather than hidden: measured 2026-10-18, dropping
   one member per concept moves a concept's rank by 9.8 percentile points on
   average for 3-4-member concepts, 7.4 for 5-7, 4.9 for 8-11, 3.2 for 12-20
   and 1.3 for 21+. :data:`DRIFT_NOTE` publishes this, :func:`_stability` bins
   it into ``thin`` / ``fair`` / ``solid``, and ``is_strong`` refuses to fire
   on anything below ``solid`` — a high RPS on a three-name concept is mostly
   the membership snapshot talking.

The return口径 matches :mod:`concept_rotation`: equal weight across members
that have a bar on the session, no market-cap weighting. A member suspended on
the as-of session is excluded from both numerator and denominator.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd

from selene_space_ashare.concept_map import MEMBERSHIP_NOTE
from selene_space_ashare.concept_rotation import DEFAULT_EXCLUDE_CONCEPTS

logger = logging.getLogger(__name__)

__all__ = [
    "CONCEPT_RPS_LOOKBACK",
    "DRIFT_NOTE",
    "FAIR_CONCEPT_MEMBERS",
    "MAX_LOOKBACK",
    "MIN_LOOKBACK",
    "MIN_RPS_MEMBERS",
    "RPS_STRONG_THRESHOLD",
    "SOLID_CONCEPT_MEMBERS",
    "build_concept_rps",
]

# 120 sessions is the threshold the individual-stock RPS uses, so the two
# numbers on the same page mean the same thing.
CONCEPT_RPS_LOOKBACK = 120

# The API bounds and this clamp must stay equal; a test pins the pairing.
# 20 sessions is the shortest window where "trailing strength" is not just
# noise, 250 is longer than any concept-level story worth telling and starts
# to overlap multiple regimes.
MIN_LOOKBACK = 20
MAX_LOOKBACK = 250

# A concept with two members is a coincidence (same reasoning as
# ``concept_rotation.MIN_MEMBERS``, which this mirrors rather than imports so
# the two thresholds can move independently if the measurements ever diverge).
MIN_RPS_MEMBERS = 3

# Stability bins, from the measured drop-one-member sensitivity: 3-7 members
# move 15.9-23.5 percentile points at p90, 8-20 move 7.0-11.3, 21+ move 2.6.
# The boundaries are measurements, not taste.
FAIR_CONCEPT_MEMBERS = 8
SOLID_CONCEPT_MEMBERS = 21

# Matches ``rps_breakout``'s threshold so "strong" means one thing on the page.
RPS_STRONG_THRESHOLD = 90.0

DRIFT_NOTE = (
    "概念 RPS 用当前成分快照回放历史: 早期交易日也按今天的成分计算。"
    "实测敏感度(每概念去掉 1 只成员, 排名平均绝对移动): 3-4 只 9.8, 5-7 只 7.4, "
    "8-11 只 4.9, 12-20 只 3.2, 21+ 只 1.3 个百分位。"
    "thin(3-7 只)的排名不可用于决策。"
)

_EXCHANGE = "astock"
_MARKET_TYPE = "daily_all"
_INTERVAL = "1d"

# Local mirror of ``concept_rotation._CALENDAR_ANCHORS``: liquid names whose
# index tails reconstruct the calendar cheaply. If the union comes up short,
# ``get_session_stamps`` falls back to the exact scan, so a stale list costs
# speed, never correctness.
_CALENDAR_ANCHORS: tuple[str, ...] = ("000001.SZ", "600000.SH", "600519.SH")


def _session_label(stamp_ms: int) -> str:
    """Beijing-midnight epoch ms → ``YYYY-MM-DD`` for the response's ``as_of``."""
    import pandas as pd

    from selene_space_ashare.sina_daily import CST

    moment = pd.Timestamp(int(stamp_ms), unit="ms", tz="UTC").tz_convert(CST)
    return str(moment.strftime("%Y-%m-%d"))


def _clamp_lookback(lookback: int) -> int:
    return max(MIN_LOOKBACK, min(MAX_LOOKBACK, int(lookback)))


def _stability(member_count: int) -> str:
    """Bin a member count into the measured drift bands (see module docstring)."""
    if member_count >= SOLID_CONCEPT_MEMBERS:
        return "solid"
    if member_count >= FAIR_CONCEPT_MEMBERS:
        return "fair"
    return "thin"


def _is_strong(rps: float, stability: str) -> bool:
    """A high score only means something when the membership is stable.

    Kept as its own function because the call site is one expression and the
    rule is easy to "simplify" into ``rps >= 90``; the stability half is the
    whole point of :data:`DRIFT_NOTE` and must survive refactors.
    """
    return rps >= RPS_STRONG_THRESHOLD and stability == "solid"


def _edge_closes(bars: pd.DataFrame, session_starts_ms: list[int]) -> pd.DataFrame:
    """``symbol`` → ``(base_close, last_close)`` from the two window edges.

    The RPS return only needs the first and last session's closes, so the
    pipeline never materialises per-row dates: a 121-session window holds
    ~670k bars and the round-trip through ``tz_convert`` + ``strftime`` costs
    ~6.4 s (measured 2026-10-19) for information this function discards one
    line later. Matching on the raw session-boundary millisecond stamps is
    exact — see :func:`_session_start_ms` for why the equality holds.

    A symbol missing a bar on either edge is dropped, not backfilled: the
    edges are what "120 sessions" means, and borrowing a close from some
    earlier session is exactly the silent-wrong-answer mode a row-based shift
    has.
    """
    stamps = pd.to_numeric(bars["timestamp"], errors="coerce")
    starts = session_starts_ms
    edges = {starts[0]: "base", starts[-1]: "last"}
    framed = pd.DataFrame(
        {
            "symbol": bars["symbol"].astype(str),
            "close": pd.to_numeric(bars["close"], errors="coerce"),
            "edge": stamps.map(edges),
        }
    ).dropna(subset=["close", "edge"])
    if framed.empty:
        return pd.DataFrame(columns=["base", "last"])

    # A duplicated (symbol, edge) row would make ``pivot_table`` aggregate, so
    # drop it explicitly; the daily partition is one bar per symbol per session
    # by construction, and a violation should surface as a missing symbol here
    # rather than as an averaged close.
    framed = framed.drop_duplicates(subset=["symbol", "edge"], keep="last")
    # ``reindex`` before ``dropna``: when no bar at all lands on one edge (an
    # as-of session the base has not been updated for yet), ``pivot`` emits
    # only the surviving column and the caller's ``pairs["last"]`` would raise
    # KeyError instead of taking the ``no_returns_in_window`` path.
    pivoted = framed.pivot(index="symbol", columns="edge", values="close")
    return pivoted.reindex(columns=["base", "last"]).dropna()


def _window_stamps(db: Any, *, end_ms: int | None, lookback: int) -> list[int]:
    """Raw Beijing-midnight stamps for the ``lookback + 1`` window sessions.

    ``end_ms`` is a millisecond bound on the calendar, not a ``YYYY-MM-DD``
    string: the base's sessions *are* stamps, and formatting 121 dates then
    mapping 670k bars back through them costs ~6.4 s (measured 2026-10-19) for
    a comparison that can be done on the integers directly. The round trip is
    also where the L2 sealed incident lived — a stamp that leaves and returns
    as a string can land on the wrong side of a session boundary.

    The equality is exact: daily bars are stamped at Beijing midnight
    (documented on ``KlineDB.get_session_stamps``) and the window bounds are
    inclusive whole-day ranges, so a bar timestamp either equals one of these
    stamps or falls outside the window.

    One query serves both ends of the contract: the last element is the
    ``as_of`` session, so resolving "the newest session" and building the
    window can never disagree about which day they mean.
    """
    stamps = db.get_session_stamps(
        _EXCHANGE,
        _MARKET_TYPE,
        _INTERVAL,
        # An upper bound no timestamp can exceed, when the caller wants the
        # newest session the base holds.
        end_ms=2**62 if end_ms is None else int(end_ms),
        limit=lookback + 1,
        anchors=_CALENDAR_ANCHORS,
    )
    return [int(stamp) for stamp in stamps]


def build_concept_rps(
    db: Any,
    membership: pd.DataFrame,
    *,
    end_ms: int | None = None,
    lookback: int = CONCEPT_RPS_LOOKBACK,
    exclude: frozenset[str] | set[str] | None = None,
    min_members: int = MIN_RPS_MEMBERS,
) -> dict[str, Any]:
    """Rank concepts by equal-weight trailing ``lookback``-session return.

    Args:
        db: exposes ``get_partition_range`` / ``get_session_stamps`` (KlineDB).
        membership: long-form ``symbol`` / ``concept`` (extra columns ignored).
            Symbols must be the suffixed upper-case form. Duplicate
            ``(symbol, concept)`` rows are collapsed before any counting —
            the on-disk snapshot contains 670 of them, and ``sum()``-style
            counts silently double.
        end_ms: inclusive millisecond bound on the last scored session; ``None``
            (default) scores the newest session in the base. A bare date's
            ``23:59:59.999`` works: bars are stamped at Beijing midnight, so
            any bound within the day selects the same session.
        lookback: window length in *sessions*; clamped to [20, 250].
        exclude: concept names to drop; defaults to
            :data:`concept_rotation.DEFAULT_EXCLUDE_CONCEPTS`. Pass
            ``frozenset()`` to keep them.
        min_members: concepts with fewer contributing members do not rank.

    Returns:
        ``{
            "as_of": str,               # the last session actually scored
            "lookback": int,
            "concepts": [               # rps descending
                {"concept": str, "rps": float, "ret": float,
                 "member_count": int, "stability": str, "is_strong": bool},
                ...
            ],
            "concept_count": int,
            "membership_note": str,
            "drift_note": str,
            "reason": str | None,
        }
        ``ret`` is a percentage number (``5.22`` means +5.22%), matching the
        convention :mod:`concept_rotation` publishes.

    Empty is a normal answer: missing membership, missing bars, or a base
    shorter than ``lookback + 1`` sessions all return ``reason`` and no
    exception.
    """
    lookback = _clamp_lookback(lookback)
    empty: dict[str, Any] = {
        "as_of": None,
        "lookback": lookback,
        "concepts": [],
        "concept_count": 0,
        "membership_note": MEMBERSHIP_NOTE,
        "drift_note": DRIFT_NOTE,
        "reason": None,
    }
    if membership is None or membership.empty:
        return {**empty, "reason": "membership_empty"}
    if not {"symbol", "concept"} <= set(membership.columns):
        return {**empty, "reason": "membership_missing_columns"}

    # Collapse before any counting: ``member_count`` and the equal-weight mean
    # both read this frame, and the snapshot on disk carries 670 exact
    # duplicates (measured 2026-10-18). ``concept_rotation`` dedupes at its own
    # join and so does ``mainline``; doing it here means a future consumer that
    # forgets cannot reintroduce the double count.
    members = membership[["symbol", "concept"]].drop_duplicates()

    # Raw session stamps throughout: the two edges are the only thing this
    # function needs from the calendar, and one query resolves both "which is
    # the as-of session" and "where does the window start", so the two answers
    # cannot disagree.
    session_starts = _window_stamps(db, end_ms=end_ms, lookback=lookback)
    if not session_starts:
        return {**empty, "reason": "daily_base_empty"}
    if len(session_starts) < lookback + 1:
        return {**empty, "reason": f"insufficient_sessions({len(session_starts)})"}

    bars = db.get_partition_range(
        _EXCHANGE,
        _MARKET_TYPE,
        _INTERVAL,
        start_ms=session_starts[0],
        # Inclusive whole-day bound: the last session's bars are stamped at
        # that day's midnight, so the range must reach the end of the day to
        # contain them (the same convention ``get_session_stamps`` is called
        # with above).
        end_ms=session_starts[-1] + 86_399_999,
    )
    if bars is None or getattr(bars, "empty", True):
        return {**empty, "reason": "daily_base_empty"}

    pairs = _edge_closes(bars, session_starts)
    if pairs.empty:
        return {**empty, "reason": "no_returns_in_window"}
    # A zero or negative baseline would make the return infinite or invert the
    # sign, and the base has no such bars — so one appearing means the frame
    # handed in is not a close series. Drop rather than divide.
    pairs = pairs[pairs["base"] > 0]
    if pairs.empty:
        return {**empty, "reason": "no_returns_in_window"}
    pairs = pairs.assign(ret=(pairs["last"] / pairs["base"] - 1.0) * 100.0)

    joined = members.merge(
        pairs[["ret"]].reset_index().rename(columns={"index": "symbol"}),
        on="symbol",
        how="inner",
    )
    if joined.empty:
        return {**empty, "reason": "membership_daily_symbol_mismatch"}

    banned = DEFAULT_EXCLUDE_CONCEPTS if exclude is None else exclude
    if banned:
        joined = joined[~joined["concept"].isin(banned)]

    agg = joined.groupby("concept", sort=False)["ret"].agg(["mean", "count"])
    agg = agg[agg["count"] >= min_members]
    if agg.empty:
        return {**empty, "reason": "all_concepts_below_min_members"}

    agg["rps"] = agg["mean"].rank(pct=True) * 100.0
    agg = agg.sort_values(["rps", "mean"], ascending=[False, False], kind="stable")

    concepts: list[dict[str, Any]] = []
    for concept, row in agg.iterrows():
        member_count = int(row["count"])
        stability = _stability(member_count)
        rps = round(float(row["rps"]), 1)
        concepts.append(
            {
                "concept": str(concept),
                "rps": rps,
                "ret": round(float(row["mean"]), 2),
                "member_count": member_count,
                "stability": stability,
                # Round first, compare second: the highlight must agree with
                # the number the reader sees, not with a hidden float.
                "is_strong": _is_strong(rps, stability),
            }
        )

    return {
        "as_of": _session_label(session_starts[-1]),
        "lookback": lookback,
        "concepts": concepts,
        "concept_count": len(concepts),
        "membership_note": MEMBERSHIP_NOTE,
        "drift_note": DRIFT_NOTE,
        "reason": None,
    }
