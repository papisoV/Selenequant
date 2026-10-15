"""Liquidation heatmap endpoints — Z-score normalized density grid."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/heatmap", tags=["heatmap"])


@router.get("/current")
async def get_heatmap_current(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    leverage_type: str = Query("all_leverage", description="Leverage tier"),
) -> APIResponse[dict[str, Any]]:
    """Get Z-score normalized liquidation heatmap."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "heatmap_aggregator"):
        return APIResponse(
            data={"message": "Heatmap aggregator not available"},
            meta=build_response_meta(request),
            error=None,
        )

    matrix = market_svc.heatmap_aggregator.get_heatmap(exchange, symbol, leverage_type)
    if matrix is None:
        return APIResponse(
            data={"exchange": exchange, "symbol": symbol, "leverage_type": leverage_type, "message": "Insufficient data (need 2+ LiqMap snapshots)"},
            meta=build_response_meta(request),
            error=None,
        )

    return APIResponse(
        data=matrix.model_dump(),
        meta=build_response_meta(request),
        error=None,
    )
