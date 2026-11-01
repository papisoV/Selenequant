"""Toxic Orderflow (TOF) endpoints.

Provides composite toxicity scores combining CVD, liquidation, and orderbook imbalance.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/tof", tags=["tof"])


@router.get("/current")
async def get_tof_current(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    cvd_interval: str = Query("5m", description="CVD bar interval: 5m, 1h, 4h"),
    liq_window: str = Query("1h", description="Liquidation stats window: 15m, 30m, 1h, 4h, 24h"),
) -> APIResponse[dict[str, Any]]:
    """Get current Toxic Orderflow score for a specific symbol."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "tof_engine"):
        return APIResponse(
            data={"message": "TOF engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    score = market_svc.tof_engine.compute(exchange, symbol, cvd_interval, liq_window)
    if score is None:
        return APIResponse(
            data={"exchange": exchange, "symbol": symbol, "message": "No data available"},
            meta=build_response_meta(request),
            error=None,
        )

    return APIResponse(
        data=score.model_dump(),
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/all")
async def get_tof_all(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
    cvd_interval: str = Query("5m", description="CVD bar interval: 5m, 1h, 4h"),
    liq_window: str = Query("1h", description="Liquidation stats window: 15m, 30m, 1h, 4h, 24h"),
) -> APIResponse[dict[str, Any]]:
    """Get Toxic Orderflow scores for all tracked symbols."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "tof_engine"):
        return APIResponse(
            data={"message": "TOF engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    scores = market_svc.tof_engine.compute_all(exchange, cvd_interval, liq_window)
    return APIResponse(
        data=[s.model_dump() for s in scores],
        meta=build_response_meta(request),
        error=None,
    )
