# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""Concept drill-down — one concept's constituents, priced.

``GET /ashare/concept-members?concept=X``

The board page shows each concept's ``member_count`` but had no way to open the
list behind it. This endpoint is that list: the ``(symbol, concept)`` rows from
:mod:`selene_space_ashare.concept_map` joined with a price cross-section.

## Price source: snapshot first, daily fallback

Same two-tier rule as :mod:`app.api.v1.ashare_overview`, and for the same
reason: the snapshot is the only source that knows the *current* session before
the daily base has finished writing it, but it is absent outside trading hours
and on a cold start. Falling back to the daily base keeps the endpoint useful
after the close — a constituents list with no prices is still worth showing, but
"the table is empty after 15:00" is not.

The chosen source is reported in ``quote_source`` (``snapshot`` / ``daily`` /
``none``) so the UI can label which numbers it is showing rather than implying
live prices after the bell.

## De-duplication is load-bearing

The on-disk membership snapshot contains duplicate ``(symbol, concept)`` pairs —
669 of them as of 2026-10-25. A ``len(members)`` or a naive join silently
double-counts those constituents. Rows are de-duplicated on the pair before any
counting, and the count is computed *after* the join so a member with no price
is still counted (it exists; we just cannot price it).

## Truncation is explicit, never silent

Large concepts are real: the biggest had 3,888 members on 2026-10-25, and nine
concepts exceed 1,000. The endpoint caps the returned rows at ``limit`` and
reports ``total`` separately, so the UI can say "showing 1000 of 3888" instead
of presenting a truncated table as the whole concept. Silent truncation is the
failure mode this panel would be most tempting to hide behind.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from fastapi import APIRouter, Query, Request

from app.core.dependencies import (
    build_response_meta,
    get_kline_manager,
    get_market_service,
)
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ashare", tags=["ashare-watch"])

_CST_TZ = timezone(timedelta(hours=8))

#: Default page size. 1000 covers all but nine concepts (measured 2026-10-25)
#: while keeping the payload near 140 KB — the panel scrolls, the wire does not.
_DEFAULT_LIMIT = 1000
#: Hard ceiling. Above this the JSON itself, not the rendering, becomes the
#: problem; a caller wanting more should narrow the concept, not raise this.
_MAX_LIMIT = 5000


def _respond(request: Request, data: Any) -> APIResponse[Any]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


def _snapshot_rows() -> tuple[dict[str, Any] | None, str | None]:
    """Live whole-market cross-section, or ``(None, reason)``."""
    service = get_market_service()
    if service is None:
        return None, "market_service_unavailable"
    try:
        source = service.get_source("astock", "market_snapshot")
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        return None, f"source_lookup_failed: {exc}"
    if source is None or not hasattr(source, "get_market_rows"):
        return None, "snapshot_source_absent"
    try:
        rows = source.get_market_rows()
    except Exception as exc:  # noqa: BLE001
        return None, f"snapshot_read_failed: {exc}"
    if not rows:
        return None, "snapshot_empty"
    return rows, None


def _snapshot_session(rows: dict[str, Any]) -> str | None:
    """CST session date of a snapshot cross-section (latest row timestamp)."""
    stamps = []
    for row in rows.values():
        value = getattr(row, "timestamp", None)
        if value is None and isinstance(row, dict):
            value = row.get("timestamp")
        if value:
            try:
                stamps.append(int(value))
            except (TypeError, ValueError):
                continue
    if not stamps:
        return None
    moment = datetime.fromtimestamp(max(stamps) / 1000.0, tz=_CST_TZ)
    return moment.date().isoformat()


def _latest_session(db: Any) -> str | None:
    """Newest session present in the daily base, or ``None``."""
    getter = getattr(db, "get_session_stamps", None)
    if getter is None:
        return None
    end_ms = int(datetime.now(tz=_CST_TZ).timestamp() * 1000)
    try:
        stamps = getter("astock", "daily_all", "1d", end_ms=end_ms, limit=1)
    except Exception:  # noqa: BLE001 — caller reports "no quotes"
        logger.warning("concept-members: session stamps unavailable", exc_info=True)
        return None
    if stamps is None or len(stamps) == 0:
        return None
    latest = max(int(s) for s in stamps)
    return datetime.fromtimestamp(latest / 1000.0, tz=_CST_TZ).date().isoformat()


def _daily_db() -> Any:
    manager = get_kline_manager()
    return getattr(manager, "_db", None) if manager is not None else None


def _with_change(quotes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Add ``change_pct`` to raw quote rows.

    Neither :func:`quotes_from_snapshot` nor :func:`quotes_from_daily` carries
    ``change_pct`` — only :func:`build_quote_rows` computes it, and that needs a
    120-session baseline the constituent list has no reason to load. The
    derivation is copied verbatim from there (``(last - prev) / prev * 100``) so
    the two surfaces cannot disagree about the same stock on the same day; a
    missing or non-positive ``prev_close`` yields ``None``, never a fake 0.
    """
    out: list[dict[str, Any]] = []
    for quote in quotes:
        last = quote.get("last")
        prev = quote.get("prev_close")
        change_pct = None
        if isinstance(last, (int, float)) and isinstance(prev, (int, float)) and prev > 0:
            change_pct = (last - prev) / prev * 100.0
        out.append({**quote, "change_pct": change_pct})
    return out


def _quote_index() -> tuple[dict[str, dict[str, Any]], str, str | None, str | None]:
    """``(symbol -> quote, source, as_of, reason)`` — snapshot first, daily second.

    Returns an empty index with ``source == "none"`` when neither tier has data;
    that is a normal answer (the membership list is still returned), not an
    error. ``as_of`` is the session the numbers belong to, never "now".
    """
    from selene_space_ashare.market_overview import (
        quotes_from_daily,
        quotes_from_snapshot,
    )

    snapshot, snapshot_reason = _snapshot_rows()
    if snapshot:
        quotes = _with_change(quotes_from_snapshot(snapshot))
        if quotes:
            index = {str(q["symbol"]): q for q in quotes}
            return index, "snapshot", _snapshot_session(snapshot), None

    db = _daily_db()
    if db is None:
        return {}, "none", None, snapshot_reason or "kline_manager_unavailable"

    today = _latest_session(db)
    if today is None:
        return {}, "none", None, snapshot_reason or "no_session"

    try:
        from selene_space_ashare.market_daily import market_daily_universe

        frame = market_daily_universe(db, end_date=today)
        quotes = _with_change(quotes_from_daily(frame, today=today))
    except Exception as exc:  # noqa: BLE001 — degrade to unpriced members
        logger.warning("concept-members: daily fallback failed", exc_info=True)
        return {}, "none", today, f"daily_quotes_failed: {exc}"

    return {str(q["symbol"]): q for q in quotes}, "daily", today, None


def _members_of(frame: pd.DataFrame, concept: str) -> list[str]:
    """Symbols belonging to ``concept``, de-duplicated, in frame order.

    The pair duplication trap (669 duplicate pairs on 2026-10-25) is why this
    does not simply ``len()`` the filtered frame.
    """
    if frame.empty or "concept" not in frame.columns:
        return []
    matched = frame[frame["concept"] == concept]
    if matched.empty:
        return []
    return list(dict.fromkeys(str(s) for s in matched["symbol"]))


def _name_index(frame: pd.DataFrame) -> dict[str, str]:
    """Symbol -> display name from the membership snapshot itself.

    The daily quote carries ``name`` too, but a member with no quote would then
    render as a bare code even though the membership table knows its name.
    """
    if frame.empty or "name" not in frame.columns:
        return {}
    pairs = frame[["symbol", "name"]].dropna()
    return {str(s): str(n) for s, n in zip(pairs["symbol"], pairs["name"]) if n}


def _sorted_rows(
    symbols: list[str],
    quotes: dict[str, dict[str, Any]],
    names: dict[str, str],
) -> list[dict[str, Any]]:
    """One row per member, change descending, unpriced members last.

    Sorting server-side keeps the truncation honest: "top 1000" means the
    strongest 1000 of the whole concept, not the first 1000 the snapshot
    happened to emit. Unpriced rows sort last and are never dropped.
    """
    rows: list[dict[str, Any]] = []
    for symbol in symbols:
        quote = quotes.get(symbol)
        rows.append(
            {
                "symbol": symbol,
                "name": names.get(symbol) or (quote or {}).get("name") or "",
                "last": (quote or {}).get("last"),
                "change_pct": (quote or {}).get("change_pct"),
                "amount": (quote or {}).get("amount"),
                "volume": (quote or {}).get("volume"),
                "priced": quote is not None,
            }
        )
    rows.sort(
        key=lambda r: (
            r["change_pct"] is None,
            -(r["change_pct"] if r["change_pct"] is not None else 0.0),
            r["symbol"],
        )
    )
    return rows


@router.get("/concept-members")
def get_concept_members_endpoint(
    request: Request,
    concept: str = Query(..., min_length=1, description="概念名(精确匹配)"),
    limit: int = Query(
        _DEFAULT_LIMIT,
        ge=1,
        le=_MAX_LIMIT,
        description="返回行数上限;total 始终是完整成员数",
    ),
) -> APIResponse[Any]:
    """某概念的全部成分股 + 行情。``total`` 是完整成员数,``rows`` 是被截断后的。

    fail-open:成分表不可用 → ``degraded``;概念不存在 → ``empty``;
    行情不可用 → 仍然返回成员(``quote_source="none"``,价格字段为 ``null``)。
    """
    from selene_space_ashare.concept_map import (
        MEMBERSHIP_NOTE,
        fetch_concept_membership,
    )

    name = str(concept).strip()
    base: dict[str, Any] = {
        "concept": name,
        "limit": limit,
        "membership_note": MEMBERSHIP_NOTE,
    }

    try:
        frame = fetch_concept_membership()
    except Exception:  # noqa: BLE001 — fail-open by contract
        logger.exception("concept-members: membership fetch failed")
        return _respond(
            request,
            {**base, "status": "degraded", "reason": "concept_membership_unavailable"},
        )

    if frame is None or getattr(frame, "empty", True):
        return _respond(
            request,
            {**base, "status": "degraded", "reason": "concept_membership_empty"},
        )

    symbols = _members_of(frame, name)
    if not symbols:
        # Distinct from "degraded": the map loaded fine, this concept is not in
        # it. An empty panel here would read as a broken panel.
        return _respond(
            request,
            {
                **base,
                "status": "empty",
                "reason": "unknown_concept",
                "total": 0,
                "rows": [],
                "quote_source": "none",
                "as_of": None,
                "priced_count": 0,
            },
        )

    quotes, source, as_of, quote_reason = _quote_index()
    rows = _sorted_rows(symbols, quotes, _name_index(frame))
    priced = sum(1 for r in rows if r["priced"])
    total = len(rows)

    return _respond(
        request,
        {
            **base,
            "status": "ok",
            "reason": None,
            "total": total,
            "returned": min(total, limit),
            "truncated": total > limit,
            "rows": rows[:limit],
            "quote_source": source,
            "quote_reason": quote_reason,
            "as_of": as_of,
            "priced_count": priced,
        },
    )
