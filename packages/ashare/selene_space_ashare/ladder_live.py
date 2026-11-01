"""Live ladder history + provisional sentiment, from public 10jqka endpoints.

Why this module exists
----------------------
``scripts/backfill_ladder_panel.py`` builds the daily ladder panel from deep
unadjusted klines. That path needs a whole-market daily history, which the
deployment box does **not** have: the KlineDB ``astock`` partition holds a few
hundred symbols frozen mid-September, and the live sync loop only walks the
favourites pool (one A-share). So the phase classifier had nothing current to
consume and ``/ashare/phase`` reported the panel's last backfilled day —
2024-10-31 — while the desk was trading October 2026.

The 10jqka limit-up endpoints are the way around that. Probed 2026-10-15:

* ``limit_up_pool`` + ``continuous_limit_up`` together give, per session, every
  limit-up's true board count (see
  :func:`~selene_space_ashare.ladder.ladder_history_from_pool`);
* ``limit_up_stats`` gives that session's 封板率 and 炸板数.

Chained over consecutive trading days they reproduce the same panel
``ladder_daily_panel`` emits — breadth, promotion and seal rate — reaching back
roughly twenty sessions, without any kline history at all. Beyond that reach the
endpoints answer empty, which is why the window is bounded rather than
open-ended.

Scope: this is a *closed-session* series. Today's in-progress row is folded from
the whole-market snapshot by
:func:`~selene_space_ashare.ladder.live_ladder_row` and belongs to the caller;
mixing the two would blur a settled day with one that can still move.
"""

from __future__ import annotations

import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Mapping

import pandas as pd

from selene_space_ashare.ladder import ladder_daily_panel, ladder_history_from_pool
from selene_space_ashare.ths_data import (
    LimitUpDataUnavailable,
    fetch_continuous_limit_up,
    fetch_limit_up_pool_full,
    fetch_limit_up_stats,
)
from selene_space_ashare.trading_calendar import prev_trading_day

logger = logging.getLogger(__name__)

# China has no DST, so a fixed +08:00 offset is exact — same convention as the
# snapshot source and the ladder cross-section.
_CST = timezone(timedelta(hours=8))

# Endpoint reach, measured 2026-10-15: a request for a session older than
# roughly twenty trading days comes back empty, so asking for more only adds
# failed round-trips. Twenty also spans the classifier's ~5-day EMA plus the
# 5-day lookback the rules compare against, with room to spare.
DEFAULT_DAYS_BACK = 20

# The closed history changes only when a session settles — at most once a day —
# so a long memo window is indistinguishable from fresh, while a short one would
# re-walk twenty sessions (measured ~57s cold) on a page that polls every few
# seconds. The *provisional* today row is deliberately not memoized: it comes
# from the snapshot, which has its own cadence.
_CACHE_TTL_SEC = 3600.0
# days_back -> (monotonic stamp, per-stock frame, aggregated panel). Both shapes
# are kept because they are not interchangeable: the panel is what the
# classifier eats, the per-stock frame is what continues a symbol's run into
# today (see `live_ladder_row`). Deriving one from the other on every call would
# re-hit the endpoints, which is the whole cost this cache exists to avoid.
_cache: dict[int, tuple[float, pd.DataFrame, pd.DataFrame]] = {}
# A failed walk is remembered for a short window too. Without it a sustained
# outage makes every request re-walk all 20 sessions (each a 15s-timeout
# request per endpoint, so the worst case is minutes of wall time per hit)
# before returning the same failure. Short enough that recovery is still
# noticed within a page refresh or two; long enough that a thundering herd of
# pollers does not each pay the walk.
_NEGATIVE_CACHE_TTL_SEC = 60.0
_failures: dict[int, float] = {}


def _closed_sessions(days_back: int, today: date) -> list[str]:
    """The ``days_back`` trading days strictly before ``today``, ascending."""
    days: list[str] = []
    cursor = today
    for _ in range(max(0, days_back)):
        cursor = prev_trading_day(cursor)
        days.append(cursor.strftime("%Y%m%d"))
    return sorted(days)


def _with_turnover_counts(panel: pd.DataFrame, per_stock: pd.DataFrame) -> pd.DataFrame:
    """Add ``limit_up_count`` / ``touched_count``, matching the backfill panel.

    ``scripts/backfill_ladder_panel.py`` emits both from
    :func:`~selene_space_ashare.ladder.build_seal_rate_daily`, and the
    ``/ladder`` endpoint asks for ``limit_up_count`` in its trend columns — so a
    live panel without it would render different row keys than the backfill
    fallback does for the same endpoint.

    The two are derived here rather than re-read from the stats endpoint because
    the pool *is* the sealed set: ``limit_up_count`` is the number of codes in
    that session's ladder frame, which the endpoint's own ``limit_up_today``
    matched on every session checked (2026-10-15, 8/8 sessions, and equal to the
    panel's own ``first_board + ge2_count``). ``touched_count`` is that plus the
    炸板 count from the stats endpoint (``sealed + open``), the same definition
    :func:`build_seal_rate_daily` uses. A missing 炸板 count leaves
    ``touched_count`` null rather than understating it.

    Args:
        panel: the aggregated ladder panel (``date`` plus breadth columns).
        per_stock: the dense per-stock frame the panel was built from.

    Returns:
        A new frame with ``limit_up_count`` and ``touched_count`` appended.
    """
    counts = (
        per_stock.groupby("date")["consecutive_boards"]
        .apply(lambda s: int((pd.to_numeric(s, errors="coerce") >= 1).sum()))
        .rename("limit_up_count")
        .reset_index()
    )
    out = panel.merge(counts, on="date", how="left")
    if "open_count" in out.columns:
        opens = pd.to_numeric(out["open_count"], errors="coerce")
    else:
        # No 炸板 reading available: leave the total null rather than report the
        # sealed count as if nothing had ever touched the limit.
        opens = pd.Series(float("nan"), index=out.index, dtype="float64")
    out["touched_count"] = out["limit_up_count"] + opens
    return out


def _seal_rate_frame(days: list[str]) -> pd.DataFrame:
    """Per-session 封板率 and 炸板数 from the stats endpoint.

    Always refetches, never honours a caller's ``force``: the stats cache has no
    TTL and writes whatever it fetched, so a request made before a session's
    stats were published poisons that date with zeros for good (observed
    2026-10-15 — ``20261014`` served ``seal_rate=None`` from cache while a
    forced refetch returned ``0.6``). Forwarding the caller's ``force`` is not
    enough: the live endpoint calls with the default ``force=False``, so the
    poisoned entry would be served. The refetch is one cheap request per
    session and this runs only on a memo miss, so it is paid at most once per
    TTL window.

    Returns:
        ``date`` / ``seal_rate`` / ``open_count`` (炸板数) — one row per input
        day. ``seal_rate`` and ``open_count`` are ``None`` for a day the
        endpoint did not answer, never a fabricated ``0``.
    """
    rows: list[dict[str, Any]] = []
    for day in days:
        stats = fetch_limit_up_stats(date=day, force=True)
        rows.append(
            {
                "date": day,
                "seal_rate": stats.get("seal_rate") if stats else None,
                "open_count": stats.get("open_count") if stats else None,
            }
        )
    return pd.DataFrame(rows)


def _densify(
    cont: pd.DataFrame, symbols: list[str], sessions: list[str]
) -> pd.DataFrame:
    """One row per (symbol, session), ``0`` where the symbol did not limit up.

    ``ladder_history_from_pool`` returns only the sessions a symbol actually
    limited up, but every consumer here reads a row's *predecessor*: the phase
    panel's promotion rate shifts each symbol by one row and matches today's
    count to yesterday's, and ``live_ladder_row`` continues a run from the
    symbol's latest row. On a sparse frame that "previous row" can be a week
    old, so a name that last limited up days ago inherits a stale run and the
    promotion denominator counts names that were not in yesterday's pool at
    all. Measured 2026-10-15: sparse promotion rate disagreed with the
    endpoints on **12 of 12** sessions, dense on 0 of 12.

    This mirrors the production backfill, which derives each symbol's board
    count from its full daily kline and therefore already emits a row per
    session (``consecutive_boards = run_length(is_limit_up)``, ``0`` on a
    break). Densifying here keeps the live path on the same contract.

    Args:
        cont: sparse per-stock frame (``symbol`` / ``date`` /
            ``consecutive_boards``).
        symbols: the universe to cover — every symbol seen in any session.
        sessions: every session to cover (all answered ones).

    Returns:
        ``symbol`` / ``date`` / ``consecutive_boards``, dense over
        ``symbols × sessions``.
    """
    index = pd.MultiIndex.from_product([symbols, sessions], names=["symbol", "date"])
    base = cont.assign(symbol=cont["symbol"].astype(str), date=cont["date"].astype(str))
    base = base.drop_duplicates(subset=["symbol", "date"], keep="last")
    base = base.set_index(["symbol", "date"])
    out = base.reindex(index)
    out["consecutive_boards"] = (
        pd.to_numeric(out["consecutive_boards"], errors="coerce").fillna(0).astype(int)
    )
    # reindex fills absent (symbol, date) pairs; the zero is the point — it is
    # what breaks a run rather than letting a stale count carry forward.
    return out.reset_index()


def _walk_sessions(days: list[str], *, force: bool) -> dict[str, pd.DataFrame]:
    """Per-session per-stock ladder frames, keyed by the session answered.

    A session the endpoints do not answer for is absent from the mapping —
    never a fabricated frame — so the caller can tell an empty tape from an
    unreachable endpoint.

    The fetchers are called in strict mode, so a *transport failure* skips the
    session instead of degrading to an empty frame. That distinction matters:
    an empty continuous frame reads as "no 连板 that day", and
    :func:`~selene_space_ashare.ladder.ladder_history_from_pool` would then
    label every pool code a 首板 — a whole-session fabrication from one timed-out
    request. The same walk degrades to a fabricated width if the *pool* goes
    unanswered, so it is skipped too. A genuinely empty answer (the endpoint
    replied, listing no name) is not an outage and is handled as before: an
    empty pool skips, an empty continuous list yields the all-首板 frame, which
    is correct for a session whose every limit-up was a first board.
    """
    frames: dict[str, pd.DataFrame] = {}
    outage_days: list[str] = []
    for day in days:
        try:
            pool = fetch_limit_up_pool_full(date=day, force=force, strict=True)
            if pool is None or pool.empty:
                continue
            continuous = fetch_continuous_limit_up(date=day, force=force, strict=True)
        except LimitUpDataUnavailable:
            outage_days.append(day)
            continue
        frames[day] = ladder_history_from_pool(pool, continuous)
    if days and not frames and len(outage_days) == len(days):
        # Every session failed to answer. That is an unreachable endpoint, not
        # a quiet tape, and the caller must be able to tell — the warm-up loop
        # re-reads this to decide whether to retry, and an empty panel would
        # read as a successful warm.
        raise LimitUpDataUnavailable(
            f"10jqka answered no session in {days[0]}..{days[-1]}"
        )
    if outage_days:
        # A partial outage leaves a hole: the sessions around it become
        # adjacent to the successor-row consumers. Bounded and visible beats
        # the alternative (a fabricated all-首板 session), so it is logged
        # rather than escalated.
        logger.warning(
            "live ladder: 10jqka did not answer %d of %d sessions (%s); those "
            "sessions are omitted and the surviving rows are not contiguous",
            len(outage_days),
            len(days),
            ",".join(outage_days),
        )
    return frames


def _history_frames(
    days_back: int,
    *,
    force: bool,
    now: datetime | None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """``(per_stock, panel)`` over the last ``days_back`` settled sessions.

    ``per_stock`` carries ``symbol`` / ``date`` / ``consecutive_boards``, dense
    over the answered sessions — the shape
    :func:`~selene_space_ashare.ladder.live_ladder_row` needs to extend a
    symbol's run into today (see :func:`_densify`). ``panel`` is that frame
    aggregated by :func:`~selene_space_ashare.ladder.ladder_daily_panel`, joined
    to the per-session 封板率 and given the turnover counts — the shape the phase
    classifier needs, and the same column set the backfill panel persists.
    Both are memoized together: they are two views of one endpoint walk.

    Only sessions the endpoints answer for are covered, so a hole in the middle
    of the window would silently make two non-adjacent sessions look adjacent
    to the successor-row consumers. In practice the walk is contiguous (the
    endpoint reached every trading day back to its horizon in testing), and a
    fabricated row for a session the source did not publish would be worse.

    A *total* outage propagates as :class:`LimitUpDataUnavailable` rather than
    an empty frame: an empty panel is indistinguishable from a quiet tape, and
    the background warm-up needs the distinction to know whether to retry. The
    failure is also memoized briefly (see ``_NEGATIVE_CACHE_TTL_SEC``) so a
    sustained outage does not make every request pay the full 20-session walk.
    """
    if not force:
        cached = _cache.get(days_back)
        if cached is not None and (time.time() - cached[0]) < _CACHE_TTL_SEC:
            return cached[1].copy(), cached[2].copy()
        failed_at = _failures.get(days_back)
        if (
            failed_at is not None
            and (time.time() - failed_at) < _NEGATIVE_CACHE_TTL_SEC
        ):
            raise LimitUpDataUnavailable(
                "recent 10jqka outage; not re-walking within the negative-cache window"
            )

    moment = now if now is not None else datetime.now(_CST)
    days = _closed_sessions(days_back, moment.date())
    try:
        by_day = _walk_sessions(days, force=force)
    except LimitUpDataUnavailable:
        _failures[days_back] = time.time()
        raise
    _failures.pop(days_back, None)

    if not by_day:
        # Only reachable when the window itself is empty (``days_back <= 0``);
        # a total outage raised above, and a partially-answered walk is non-empty.
        logger.warning(
            "live_ladder_history: no sessions in the window (days_back=%d); "
            "returning an empty panel",
            days_back,
        )
        per_stock = pd.DataFrame(columns=["symbol", "date", "consecutive_boards"])
        panel = ladder_daily_panel(pd.DataFrame())
        panel = panel.assign(
            seal_rate=pd.Series(dtype="float64"),
            open_count=pd.Series(dtype="float64"),
            limit_up_count=pd.Series(dtype="int64"),
            touched_count=pd.Series(dtype="float64"),
        )
        return per_stock, panel

    answered = sorted(by_day)
    sparse = pd.concat([by_day[d] for d in answered], ignore_index=True)
    symbols = sorted(set(sparse["symbol"].astype(str)))
    per_stock = _densify(sparse, symbols, answered)
    panel = ladder_daily_panel(per_stock)
    panel = panel.merge(_seal_rate_frame(answered), on="date", how="left")
    panel = _with_turnover_counts(panel, per_stock)

    _cache[days_back] = (time.time(), per_stock, panel)
    return per_stock.copy(), panel.copy()


def live_ladder_history(
    days_back: int = DEFAULT_DAYS_BACK,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Contiguous daily ladder panel over the last ``days_back`` closed sessions.

    Composes the same columns ``ladder_daily_panel`` produces and joins the
    per-session 封板率 and turnover counts, i.e. exactly the frame
    :func:`~selene_space_ashare.market_phase.classify_phase_series` requires and
    the same column set ``scripts/backfill_ladder_panel.py`` persists.
    The per-stock frame this aggregates from is available separately via
    :func:`live_ladder_per_stock`; the two are distinct shapes, which is why one
    is not derived from the other.

    Sessions the endpoints do not answer for are skipped, so the result can be
    shorter than ``days_back`` on a fresh deployment; it is never padded with
    fabricated rows. A *total* outage raises
    :class:`~selene_space_ashare.ths_data.LimitUpDataUnavailable` instead of
    returning an empty frame — the two are otherwise indistinguishable to a
    background warm-up deciding whether to retry — and the failure is
    negative-cached for a minute so a sustained outage does not re-walk the
    endpoints on every request.

    Args:
        days_back: how many closed sessions to assemble (bounded by endpoint
            reach; see module docstring).
        force: bypass the memo cache and re-walk the endpoints.
        now: override "now" (for tests); defaults to Beijing time.

    Returns:
        Ascending-by-date frame: ``date`` / ``first_board`` / ``ge2_count`` /
        ``ge3_count`` / ``ge5_count`` / ``rungs_filled`` / ``max_consecutive``
        / ``promo_pool`` / ``promo_ok`` / ``promo_rate`` / ``ladder_completeness``
        / ``limit_up_count`` / ``touched_count`` / ``seal_rate``.

    Raises:
        LimitUpDataUnavailable: when the endpoints answer no session at all.
    """
    _, panel = _history_frames(days_back, force=force, now=now)
    return panel


def live_ladder_per_stock(
    days_back: int = DEFAULT_DAYS_BACK,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> pd.DataFrame:
    """Per-stock ladder history over the last ``days_back`` closed sessions.

    Same endpoint walk as :func:`live_ladder_history` (and the same cache), but
    returned un-aggregated: ``symbol`` / ``date`` / ``consecutive_boards``,
    **dense** over the answered sessions — a ``0`` marks a session where the
    symbol did not limit up, which is what breaks a run (see :func:`_densify`).
    This is the baseline :func:`~selene_space_ashare.ladder.live_ladder_row`
    continues from, so today's rows extend a settled run instead of restarting
    at 1, and a name that last limited up days ago does not carry a stale
    count into today.

    Returns an empty frame with the right columns when the window is empty;
    raises :class:`~selene_space_ashare.ths_data.LimitUpDataUnavailable` when
    the endpoints answer no session at all (see :func:`live_ladder_history`).
    """
    per_stock, _ = _history_frames(days_back, force=force, now=now)
    return per_stock


def clear_cache() -> None:
    """Drop the memoized histories (tests; also a manual refresh hook)."""
    _cache.clear()
    _failures.clear()


def live_phase_series(
    snapshot_rows: Mapping[str, object],
    days_back: int = DEFAULT_DAYS_BACK,
    *,
    force: bool = False,
    now: datetime | None = None,
) -> tuple[pd.DataFrame, str | None]:
    """Label the closed history plus today's in-progress row.

    The closed part of the series is settled; only the final row is folded from
    a session that has not closed, so only that row's label is provisional.

    The snapshot is folded **only when it describes a session newer than the
    last settled one**. Off-session the source keeps serving a frozen
    cross-section whose timestamp is the last *trade* time, not the fetch time
    (verified live 2026-10-15 19:02 — the newest row read ``15:36`` that day),
    so pre-open its date is yesterday's close and on a weekend/holiday it is
    Friday's. In both cases that session is already in the settled history, and
    folding would replace the authoritative 10jqka EOD row with a row derived
    from a frozen Sina quote — dropping the real classification in favour of a
    staler one. When the snapshot is not newer it is discarded and the settled
    series is returned with scope ``None``.

    When ``snapshot_rows`` is empty (the fetch produced nothing, or the source
    is unreachable) only the settled series is returned and the scope is
    ``None``: an absent cross-section must never be scored as a live tape.

    Args:
        snapshot_rows: the whole-market cross-section
            (``AstockMarketSnapshotSource.get_market_rows()``).
        days_back: closed sessions to assemble.
        force: bypass the memo cache and the 10jqka caches.
        now: override "now" (for tests).

    Returns:
        ``(labeled, scope)`` where ``labeled`` carries the classifier's
        ``phase`` column, and ``scope`` is ``"intraday_provisional"`` when the
        last row is today's in-progress session, else ``None``.
    """
    from selene_space_ashare import market_phase
    from selene_space_ashare.ladder import live_ladder_row

    history = live_ladder_history(days_back=days_back, force=force, now=now)
    scope: str | None = None

    if snapshot_rows:
        # `live_ladder_row` continues each symbol's run from the *per-stock*
        # frame, not the aggregated panel — the panel has no `symbol` column.
        per_stock = live_ladder_per_stock(days_back=days_back, force=force, now=now)
        today = live_ladder_row(snapshot_rows, per_stock)
        snap_date = str(today.get("date") or "")
        settled_dates = set(history["date"].astype(str)) if not history.empty else set()
        last_settled = max(settled_dates) if settled_dates else ""
        # Fold only a genuinely newer session (see the docstring): a snapshot
        # whose date is already settled is a frozen off-session quote, and
        # appending it would shadow the authoritative EOD row.
        if snap_date and snap_date > last_settled:
            combined = pd.concat([history, pd.DataFrame([today])], ignore_index=True)
            labeled = market_phase.classify_phase_series(combined)
            scope = "intraday_provisional"
            return labeled.reset_index(drop=True), scope

    if history.empty:
        return market_phase.classify_phase_series(history), None

    return market_phase.classify_phase_series(history).reset_index(drop=True), scope
