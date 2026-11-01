# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""A-share market overview + AI recap — the dashboard's assembly and its prose.

Two endpoints:

* ``GET /ashare/overview`` — the assembled cross-section (breadth, limits,
  trend, rotation ranks, emotion radar). Fail-open like the rest of the watch
  surface: 200 with a ``status`` of ``ok`` / ``empty`` / ``degraded``.
* ``GET /ashare/recap/stream`` — SSE; ``meta`` → ``delta``* → ``done`` (or
  ``error``). The overview is the same one ``/ashare/overview`` serves, so the
  prose and the numbers cannot diverge.

## Why the baseline is cached here and not in the assembler

A 120-session whole-market read plus indicator computation costs ~15-25 s on
the real base (measured 2026-10-20); the per-request assembly over those
indicators costs ~0.4 s. The baseline is keyed by the **session being scored**
and is immutable once computed, so caching it is safe and makes the endpoint
usable interactively. The cache is keyed by ``(db identity, today)``; a restart
or a new session gets a fresh entry, and the entry is dropped when the day
rolls over.

## ``today`` resolution

The session being scored is, in order: the ``date`` query parameter; the live
snapshot's own newest timestamp; the daily base's newest session. The snapshot
is preferred because it is the only source that knows the current session
before the daily base has finished writing it.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd
from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from app.core.dependencies import (
    build_response_meta,
    get_kline_manager,
    get_market_service,
    get_settings_service,
)
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)


router = APIRouter(prefix="/ashare", tags=["ashare-overview"])

_CST_TZ = timezone(timedelta(hours=8))

# One baseline per (db, scoring session). Values are `(Baseline, dict)` — the
# dict carries the extra provider data (concept/industry ranks) that is fetched
# alongside and changes no more often than the session.
# Keyed by (id(db), today) and holding the db itself as well: a bare id is not an
# identity guarantee, because a collected db's address can be handed to a new
# object and would then inherit the stale baseline. The retained reference pins
# the address for the entry's lifetime.
_baseline_cache: dict[tuple[int, str], tuple[Any, Any]] = {}
_baseline_lock = threading.Lock()
_BASELINE_CACHE_MAX = 4


def _respond(request: Request, data: Any) -> APIResponse[Any]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


def _daily_db() -> Any:
    manager = get_kline_manager()
    return getattr(manager, "_db", None) if manager is not None else None


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
    except Exception:  # noqa: BLE001 — caller reports "no baseline"
        logger.warning("overview: session stamps unavailable", exc_info=True)
        return None
    if stamps is None or len(stamps) == 0:
        return None
    latest = max(int(s) for s in stamps)
    return datetime.fromtimestamp(latest / 1000.0, tz=_CST_TZ).date().isoformat()


def _baseline_for(db: Any, today: str) -> Any:
    """Cached :func:`compute_baseline` keyed by (db, session)."""
    from selene_space_ashare.market_overview import compute_baseline

    key = (id(db), today)
    with _baseline_lock:
        cached = _baseline_cache.get(key)
    if cached is not None:
        cached_db, cached_baseline = cached
        # Belt and braces: the retained reference makes id reuse impossible,
        # but an `is` check states the intent and costs nothing.
        if cached_db is db:
            return cached_baseline

    baseline = compute_baseline(db, today=today)
    with _baseline_lock:
        if len(_baseline_cache) >= _BASELINE_CACHE_MAX:
            # Small and session-keyed: the oldest key is the least likely to be
            # requested again (a re-scored past session), so eviction is FIFO.
            _baseline_cache.pop(next(iter(_baseline_cache)))
        _baseline_cache[key] = (db, baseline)
    return baseline


def _board_ranks() -> tuple[pd.DataFrame | None, pd.DataFrame | None, str | None]:
    """Eastmoney concept/industry rank frames. Failure degrades, never raises."""
    from selene_space_ashare.data_fetcher import (
        get_concept_boards,
        get_industry_comparison,
    )

    concept = industry = None
    reason = None
    try:
        concept = get_concept_boards(60)
    except Exception as exc:  # noqa: BLE001
        reason = f"concept_rank_failed: {exc}"
    try:
        industry = get_industry_comparison(90)
    except Exception as exc:  # noqa: BLE001
        reason = (reason + "; " if reason else "") + f"industry_rank_failed: {exc}"
    return concept, industry, reason


def _sealed(
    rows: dict[str, Any] | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Sealed counts over the cross-section already fetched by the caller."""
    if rows is None:
        return None, "snapshot_unavailable"
    from selene_space_ashare.sealed import sealed_counts

    try:
        counts = sealed_counts(rows)
    except Exception as exc:  # noqa: BLE001
        return None, f"sealed_counts_failed: {exc}"
    if not counts.get("ready"):
        return None, "not_in_continuous_trading"
    return counts, None


def _universe_size() -> int | None:
    """Current A-share universe count (cached by the fetcher's 7-day file)."""
    try:
        from selene_space_ashare.data_fetcher import get_a_share_list

        return int(len(get_a_share_list()))
    except Exception:  # noqa: BLE001 — coverage is advisory, not load-bearing
        logger.warning("overview: universe size unavailable", exc_info=True)
        return None


def build_overview(date: str = "") -> dict[str, Any]:
    """Assemble the overview for ``date`` (or the current session).

    Shared by both endpoints so the recap's prompt and the dashboard's numbers
    are literally the same dict. Every degradation is reported in the payload
    under ``status`` / ``reason`` / ``*_reason`` — this never raises.
    """
    from selene_space_ashare.market_overview import (
        assemble_overview,
        quotes_from_daily,
        quotes_from_snapshot,
    )

    db = _daily_db()
    snapshot, snapshot_reason = _snapshot_rows()

    requested = str(date or "").strip()
    today = requested or (_snapshot_session(snapshot) if snapshot else None)
    if not today and db is not None:
        today = _latest_session(db)
    if not today:
        return {
            "status": "degraded",
            "reason": "no_session",
            "as_of": None,
            "snapshot_reason": snapshot_reason,
        }

    quotes: list[dict[str, Any]] = []
    quote_source = "none"
    quote_reason = snapshot_reason
    if snapshot:
        quotes = quotes_from_snapshot(snapshot)
        quote_source = "snapshot"
        quote_reason = None

    baseline = None
    if db is not None:
        try:
            baseline = _baseline_for(db, today)
        except Exception as exc:  # noqa: BLE001 — the page degrades, not 500s
            logger.exception("overview: baseline failed for %s", today)
            return {
                "status": "degraded",
                "reason": f"baseline_failed: {exc}",
                "as_of": today,
                "snapshot_reason": snapshot_reason,
            }

    if not quotes and db is not None:
        # Daily fallback: today's own rows only. `quotes_from_daily` filters on
        # `date == today`, so a suspended name on an older bar cannot enter.
        from selene_space_ashare.market_daily import market_daily_universe

        try:
            frame = market_daily_universe(db, end_date=today)
            quotes = quotes_from_daily(frame, today=today)
            if quotes:
                quote_source = "daily"
        except Exception as exc:  # noqa: BLE001
            quote_reason = f"daily_fallback_failed: {exc}"

    if baseline is None:
        from selene_space_ashare.market_overview import Baseline

        baseline = Baseline(as_of=None, sessions=0, frame=pd.DataFrame(), symbols=0)

    from selene_space_ashare.index_quotes import CORE_INDEX_NAMES, fetch_index_quotes

    indices, indices_reason = fetch_index_quotes()
    # Sina names the index; fall back to the table when a row's name is blank.
    for quote in indices:
        if not quote.get("name"):
            quote["name"] = CORE_INDEX_NAMES.get(quote.get("symbol", ""), "")

    concept_rank, industry_rank, rank_reason = _board_ranks()
    sealed, sealed_reason = _sealed(snapshot)

    overview = assemble_overview(
        today=today,
        quotes=quotes,
        baseline=baseline,
        indices=indices,
        indices_reason=indices_reason,
        concept_rank=concept_rank,
        industry_rank=industry_rank,
        sealed=sealed,
        sealed_reason=sealed_reason,
        quote_source=quote_source,
        universe_size=_universe_size(),
    )
    overview["snapshot_reason"] = quote_reason if quote_source != "snapshot" else None
    overview["rank_reason"] = rank_reason
    overview["requested_date"] = requested or None
    return overview


@router.get("/overview")
def get_overview_endpoint(
    request: Request,
    date: str = Query("", description="YYYY-MM-DD,空=当前交易日"),
) -> APIResponse[Any]:
    """A股大盘总览:指数、涨跌家数、涨停/炸板、均线站位、板块排名、情绪雷达。

    ``status``: ``ok`` / ``empty`` / ``degraded``。``coverage`` 给出行情覆盖度,
    ``quote_source`` 说明行情来自实时快照还是日线回退 —— 降级不静默。
    """
    try:
        payload = build_overview(date)
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        logger.exception("overview assembly failed")
        payload = {"status": "degraded", "reason": f"assembly_failed: {exc}"}

    from selene_space_ashare.market_recap_prompt import recap_summary

    if payload.get("status") in ("ok", "empty"):
        payload.setdefault("summary", recap_summary(payload))
    return _respond(request, payload)


def _sse(event: dict[str, Any]) -> str:
    """One SSE frame: ``data: {json}\\n\\n`` (same shape as market_analysis)."""
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _llm_messages(system: str, user: str) -> list[Any]:
    from selene_space_llm.base import LLMMessage, LLMRole

    # A raw dict raises "'dict' object has no attribute 'role'" inside the
    # provider's payload builder; the message type is mandatory.
    return [
        LLMMessage(role=LLMRole.SYSTEM, content=system),
        LLMMessage(role=LLMRole.USER, content=user),
    ]


@router.get("/recap/stream")
async def get_recap_stream_endpoint(
    request: Request,
    date: str = Query("", description="YYYY-MM-DD,空=当前交易日"),
    focus: str = Query("", description="追加关注点(不改变红线)"),
    lhb: bool = Query(True, description="是否附龙虎榜"),
) -> StreamingResponse:
    """AI 大盘复盘 (SSE): ``meta`` → ``delta``* → ``done``。

    数据与 ``/ashare/overview`` 同源。LLM 未配置时立即发 ``error``,不产生
    任何替代文本。
    """

    async def event_stream() -> Any:
        from selene_space_ashare import market_recap_prompt as prompt_mod

        try:
            overview = build_overview(date)
        except Exception as exc:  # noqa: BLE001
            logger.exception("recap: overview assembly failed")
            yield _sse({"type": "error", "message": f"总览装配失败: {exc}"})
            return

        if not overview.get("as_of"):
            yield _sse(
                {
                    "type": "error",
                    "message": "暂无市场数据:日线底座与实时快照均不可用",
                    "reason": overview.get("reason"),
                }
            )
            return

        emotion = overview.get("emotion") or {}
        yield _sse(
            {
                "type": "meta",
                "as_of": overview.get("as_of"),
                "emotion_score": emotion.get("score"),
                "emotion_label": emotion.get("label"),
                "summary": prompt_mod.recap_summary(overview),
                "status": overview.get("status"),
            }
        )

        lhb_context = ""
        if lhb:
            try:
                from selene_space_ashare import lhb_daily

                rows, _ = lhb_daily.fetch_lhb_day(str(overview["as_of"]))
                lhb_context = lhb_daily.build_recap_context(rows)
            except Exception:  # noqa: BLE001 — the block is optional
                logger.warning("recap: 龙虎榜 unavailable", exc_info=True)

        user_prompt = prompt_mod.build_user_prompt(
            overview, focus=focus, lhb_context=lhb_context
        )

        settings = get_settings_service()
        try:
            from app.core.llm_helper import build_provider

            provider = await build_provider(settings)
        except Exception as exc:  # noqa: BLE001
            logger.exception("recap: provider build failed")
            yield _sse({"type": "error", "message": f"LLM 初始化失败: {exc}"})
            return
        if provider is None:
            yield _sse(
                {
                    "type": "error",
                    "message": "未配置 LLM 密钥:请先在设置中配置模型后重试",
                    "reason": "llm_not_configured",
                }
            )
            return

        collected: list[str] = []
        try:
            messages = _llm_messages(prompt_mod.SYSTEM_PROMPT, user_prompt)
            # No `prefer_final_answer`: the provider's payload builder applies
            # `payload.update(overrides)`, so any unknown kwarg would be sent to
            # the upstream API as a literal field.
            async for delta in provider.chat_stream(
                messages, temperature=0.5, max_tokens=None
            ):
                collected.append(delta)
                yield _sse({"type": "delta", "content": delta})
        except Exception as exc:  # noqa: BLE001 — surface, never hang
            logger.exception("recap: stream failed")
            yield _sse({"type": "error", "message": f"AI 复盘失败: {exc}"})
            return

        if not collected:
            yield _sse(
                {"type": "error", "message": "AI 未返回正文(输出可能被截断),请重试"}
            )
            return

        text = "".join(collected)
        tail = prompt_mod.ensure_disclaimer("")
        if "不构成任何投资建议" not in text:
            # The model dropped section 8; append the disclaimer rather than
            # ship an AI market report with none.
            yield _sse({"type": "delta", "content": "\n\n" + tail})
        yield _sse({"type": "done"})

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


__all__ = ["build_overview", "router"]
