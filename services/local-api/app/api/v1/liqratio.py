"""LiqRatio (Liquidation Ratio) endpoints.

Provides cross-coin liquidation weight comparison across all tracked symbols.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/liqratio", tags=["liqratio"])


@router.get("/current")
async def get_liqratio_current(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
    leverage_type: str = Query("all_leverage", description="Leverage tier: all_leverage, high_leverage, low_leverage, optical_opti"),
) -> APIResponse[list[dict[str, Any]]]:
    """Get current LiqRatio across all tracked symbols for a leverage tier."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "liqmap_engine"):
        return APIResponse(
            data={"message": "LiqMap engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    items = market_svc.liqmap_engine.compute_ratios(exchange, leverage_type)
    return APIResponse(
        data=[item.model_dump() for item in items],
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/all-tiers")
async def get_liqratio_all_tiers(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
) -> APIResponse[dict[str, Any]]:
    """Get LiqRatio for all leverage tiers."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "liqmap_engine"):
        return APIResponse(
            data={"message": "LiqMap engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    tiers = ["all_leverage", "high_leverage", "low_leverage", "optical_opti"]
    results: dict[str, list[dict[str, Any]]] = {}
    for tier in tiers:
        items = market_svc.liqmap_engine.compute_ratios(exchange, tier)
        results[tier] = [item.model_dump() for item in items]

    return APIResponse(
        data=results,
        meta=build_response_meta(request),
        error=None,
    )
