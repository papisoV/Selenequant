"""Composite Signal endpoints.

Provides multi-factor trading signals combining CVD, TOF, LiqMap, GEX, funding.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/signals", tags=["signals"])


@router.get("/current")
async def get_signal_current(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
    symbol: str = Query("BTC/USDT", description="Trading pair"),
) -> APIResponse[dict[str, Any]]:
    """Get composite signal for a specific symbol."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "signal_engine"):
        return APIResponse(
            data={"message": "Signal engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    signal = market_svc.signal_engine.compute(exchange, symbol)
    if signal is None:
        return APIResponse(
            data={"exchange": exchange, "symbol": symbol, "message": "No data available"},
            meta=build_response_meta(request),
            error=None,
        )

    return APIResponse(
        data=signal.model_dump(),
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/dashboard")
async def get_signal_dashboard(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
) -> APIResponse[list[dict[str, Any]]]:
    """Get composite signals for all tracked symbols."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "signal_engine"):
        return APIResponse(
            data=[],
            meta=build_response_meta(request),
            error=None,
        )

    signals = market_svc.signal_engine.compute_all(exchange)
    return APIResponse(
        data=[s.model_dump() for s in signals],
        meta=build_response_meta(request),
        error=None,
    )
