"""CVD (Cumulative Volume Delta) endpoints.

Provides real-time and historical CVD data for order flow analysis.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/cvd", tags=["cvd"])


@router.get("/current")
async def get_cvd_current(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    interval: str = Query("5m", description="Bar interval: 5m, 1h, 4h"),
) -> APIResponse[dict[str, Any]]:
    """Get the current (in-progress) CVD bar."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "cvd_aggregator"):
        return APIResponse(
            data={"message": "CVD aggregator not available"},
            meta=build_response_meta(request),
            error=None,
        )

    bar = market_svc.cvd_aggregator.get_current_bar(exchange, symbol, interval)
    if bar is None:
        return APIResponse(
            data={"exchange": exchange, "symbol": symbol, "interval": interval, "message": "No CVD data yet"},
            meta=build_response_meta(request),
            error=None,
        )

    return APIResponse(
        data=bar.model_dump(),
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/all")
async def get_cvd_all_intervals(
    request: Request,
    exchange: str = Query("binance", description="Exchange identifier"),
    symbol: str = Query("BTC/USDT", description="Trading pair"),
) -> APIResponse[dict[str, Any]]:
    """Get current CVD bars for all intervals."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "cvd_aggregator"):
        return APIResponse(
            data={"message": "CVD aggregator not available"},
            meta=build_response_meta(request),
            error=None,
        )

    bars = market_svc.cvd_aggregator.get_all_current_bars(exchange, symbol)
    return APIResponse(
        data={interval: bar.model_dump() for interval, bar in bars.items()},
        meta=build_response_meta(request),
        error=None,
    )
