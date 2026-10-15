"""Aggregated Orderbook endpoints.

Cross-exchange merged L2 orderbook with per-exchange quantity breakdowns.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/agg-orderbook", tags=["agg-orderbook"])
logger = logging.getLogger(__name__)


@router.get("/current")
async def get_agg_orderbook_current(
    request: Request,
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    depth: int = Query(25, ge=5, le=100, description="Number of levels per side"),
) -> APIResponse[dict[str, Any]]:
    """Get current aggregated orderbook across all exchanges."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "agg_orderbook_engine"):
        return APIResponse(
            data={"message": "Aggregated orderbook engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    # A transiently malformed L2 snapshot (NaN/None price) used to bubble out of
    # compute() as an unhandled ValueError and surface as a 500. Degrade to the
    # same "no data" payload the missing-book case uses — the panel refetches.
    try:
        snapshot = market_svc.agg_orderbook_engine.compute(symbol, depth=depth)
    except Exception:
        logger.warning("agg-orderbook compute failed for %s", symbol, exc_info=True)
        snapshot = None

    if snapshot is None:
        return APIResponse(
            data={"symbol": symbol, "message": "No orderbook data available"},
            meta=build_response_meta(request),
            error=None,
        )

    return APIResponse(
        data=snapshot.model_dump(),
        meta=build_response_meta(request),
        error=None,
    )
