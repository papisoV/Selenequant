"""System logs endpoint — GET /api/v1/system/logs.

Returns recent system log entries from the system_logs table (v008 migration).
Empty list if table is empty or storage unavailable.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta, get_db_manager
from selene_space_types.common import APIResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/logs")
async def get_system_logs(
    request: Request,
    limit: int = Query(50, ge=1, le=500, description="Max log entries to return"),
    level: str | None = Query(
        None, description="Filter by log level (debug/info/warning/error)"
    ),
) -> APIResponse[list[dict[str, Any]]]:
    """Return recent system logs ordered by timestamp desc."""
    db = get_db_manager()
    rows: list[dict[str, Any]] = []
    if db is None:
        return APIResponse(data=rows, meta=build_response_meta(request))
    try:
        if level:
            sql = (
                "SELECT id, timestamp, level, logger, message, module, line, exc "
                "FROM system_logs WHERE level = ? ORDER BY timestamp DESC LIMIT ?"
            )
            params: tuple[Any, ...] = (level.lower(), limit)
        else:
            sql = (
                "SELECT id, timestamp, level, logger, message, module, line, exc "
                "FROM system_logs ORDER BY timestamp DESC LIMIT ?"
            )
            params = (limit,)
        result = await db.execute(sql, params)
        for r in result:
            rows.append(dict(r))
    except Exception:
        logger.warning("Failed to fetch system_logs", exc_info=True)
    return APIResponse(data=rows, meta=build_response_meta(request))


__all__ = ["router", "get_system_logs"]
