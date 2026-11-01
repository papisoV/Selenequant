"""Events endpoint — GET /api/v1/events/recent.

Returns recent system events from the events_recent table (v008 migration).
Empty list if table is empty or storage unavailable.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta, get_db_manager
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/events", tags=["events"])

_INSERT_SQL = """
INSERT INTO events_recent (
    id, timestamp, kind, source, symbol, severity, title, body, payload_json
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
"""


async def record_event(
    *,
    kind: str,
    title: str,
    body: str = "",
    source: str = "",
    symbol: str | None = None,
    severity: str = "info",
    payload: dict[str, Any] | None = None,
) -> None:
    """Append one row to ``events_recent`` (v008), best-effort.

    P0-3:让 AI 的写操作在事件流里留下痕迹。此前 AI 工具回调(save/deploy/
    stop)只触发前端重拉数据,不写事件流,用户看不到"AI 刚刚动了什么"。

    注:事件流面板长期为空的主因不在这里 —— 是读取端调用了不存在的
    db.query()(见下方 get_events_recent),每次读取都抛异常被吞成空列表。
    db 里其实一直有历史事件行。两处都已修。

    Never raises: observability writes must not break the action being
    observed (a failed audit write must not abort an AI tool call).
    """
    db = get_db_manager()
    if db is None:
        return
    try:
        await db.execute(
            _INSERT_SQL,
            (
                uuid.uuid4().hex,
                int(time.time()),
                kind,
                source,
                symbol,
                severity,
                title,
                body,
                json.dumps(payload or {}, ensure_ascii=False),
            ),
        )
    except Exception:
        logger.debug("Failed to record event kind=%s", kind, exc_info=True)


async def record_system_event(
    kind: str,
    source: str,
    title: str,
    *,
    body: str | None = None,
    severity: str = "info",
    symbol: str | None = None,
    payload: dict[str, Any] | None = None,
) -> None:
    """系统事件写入 events_recent(透明性批3-12:复活死表)。

    Fire-and-forget:DB 未就绪/写入失败一律静默,绝不影响调用方主流程。
    写入方:任务完成(backtests)、漂移核查(drift-check)、心跳异常(health probe)。
    """
    try:
        from app.core.dependencies import get_db_manager

        db = get_db_manager()
        if db is None:
            return
        await db.execute(
            "INSERT INTO events_recent (id, timestamp, kind, source, symbol, severity, title, body, payload_json) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                f"evt-{uuid.uuid4().hex[:12]}",
                int(time.time() * 1000),
                kind,
                source,
                symbol,
                severity,
                title,
                body,
                json.dumps(payload, ensure_ascii=False) if payload else None,
            ),
        )
    except Exception:
        logger.debug("Failed to record system event (kind=%s)", kind, exc_info=True)


@router.get("/recent")
async def get_events_recent(
    request: Request,
    limit: int = Query(50, ge=1, le=500, description="Max events to return"),
    kind: str | None = Query(None, description="Filter by event kind"),
) -> APIResponse[list[dict[str, Any]]]:
    """Return recent events ordered by timestamp desc."""
    db = get_db_manager()
    rows: list[dict[str, Any]] = []
    if db is None:
        return APIResponse(data=rows, meta=build_response_meta(request))
    try:
        if kind:
            sql = (
                "SELECT id, timestamp, kind, source, symbol, severity, title, body, payload_json "
                "FROM events_recent WHERE kind = ? ORDER BY timestamp DESC LIMIT ?"
            )
            params: tuple[Any, ...] = (kind, limit)
        else:
            sql = (
                "SELECT id, timestamp, kind, source, symbol, severity, title, body, payload_json "
                "FROM events_recent ORDER BY timestamp DESC LIMIT ?"
            )
            params = (limit,)
        # P0-3 / 批3-12(双边独立发现):原读路径调用了不存在的 db.query(),
        # AttributeError 被 except 吞成空列表 —— 事件流永远空的读侧真因。
        # AsyncSQLiteManager 只有 execute;写入方见 record_event / record_system_event。
        result = await db.execute(sql, params)
        for r in result:
            rows.append(dict(r))
    except Exception:
        logger.warning("Failed to fetch events_recent", exc_info=True)
    return APIResponse(data=rows, meta=build_response_meta(request))


__all__ = ["router", "get_events_recent", "record_event", "record_system_event"]
