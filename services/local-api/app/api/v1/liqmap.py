"""LiqMap (Liquidation Map) endpoints.

Provides estimated liquidation price clustering analysis.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/liqmap", tags=["liqmap"])


@router.get("/current")
async def get_liqmap_current(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    leverage_type: str = Query("all_leverage", description="Leverage tier: all_leverage, high_leverage, low_leverage, optical_opti"),
) -> APIResponse[dict[str, Any]]:
    """Get current LiqMap for a specific leverage tier."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "liqmap_engine"):
        return APIResponse(
            data={"message": "LiqMap engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    result = market_svc.liqmap_engine.compute_map(exchange, symbol, leverage_type)
    if result is None:
        return APIResponse(
            data={"exchange": exchange, "symbol": symbol, "leverage_type": leverage_type, "message": "No data (need mark_price first)"},
            meta=build_response_meta(request),
            error=None,
        )

    return APIResponse(
        data=result.model_dump(),
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/all-tiers")
async def get_liqmap_all_tiers(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
    symbol: str = Query("BTC/USDT", description="Trading pair"),
) -> APIResponse[dict[str, Any]]:
    """Get LiqMap for all leverage tiers."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "liqmap_engine"):
        return APIResponse(
            data={"message": "LiqMap engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    results = market_svc.liqmap_engine.get_all_tiers(exchange, symbol)
    return APIResponse(
        data={tier: r.model_dump() for tier, r in results.items()},
        meta=build_response_meta(request),
        error=None,
    )
