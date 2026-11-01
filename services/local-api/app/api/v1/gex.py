"""GEX (Gamma Exposure) endpoints.

Provides options gamma exposure analysis from Deribit data.
"""

from __future__ import annotations

import asyncio

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/gex", tags=["gex"])


@router.get("/current")
async def get_gex_current(
    request: Request,
    currency: str = Query("BTC", description="Underlying currency: BTC, ETH"),
) -> APIResponse[dict[str, Any]]:
    """Get current Gamma Exposure for a currency."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None:
        return APIResponse(
            data={"message": "Market service not available"},
            meta=build_response_meta(request),
            error=None,
        )

    # Use the options fetcher if available
    options_fetcher = getattr(market_svc, "_options_fetcher", None)
    if options_fetcher is None:
        return APIResponse(
            data={"message": "Options fetcher not configured"},
            meta=build_response_meta(request),
            error=None,
        )

    try:
        # 60s timeout: first fetch serializes ~300 Deribit order book calls
        # (concurrent w/ Semaphore 20 → ~15s). Subsequent calls hit 5min cache (<1s).
        result = await asyncio.wait_for(options_fetcher.fetch_gex(currency), timeout=60.0)
    except asyncio.TimeoutError:
        return APIResponse(
            data={"currency": currency, "message": "GEX fetch timed out (Deribit unreachable)"},
            meta=build_response_meta(request),
            error=None,
        )
    if result is None:
        return APIResponse(
            data={"currency": currency, "message": "No GEX data available (check proxy/network)"},
            meta=build_response_meta(request),
            error=None,
        )

    return APIResponse(
        data=result.model_dump(),
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/levels")
async def get_gex_levels(
    request: Request,
    currency: str = Query("BTC", description="Underlying currency: BTC, ETH"),
) -> APIResponse[dict[str, Any]]:
    """Get key gamma levels (call wall, put wall, gamma flip) for a currency."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None:
        return APIResponse(
            data={"message": "Market service not available"},
            meta=build_response_meta(request),
            error=None,
        )

    options_fetcher = getattr(market_svc, "_options_fetcher", None)
    if options_fetcher is None:
        return APIResponse(
            data={"message": "Options fetcher not configured"},
            meta=build_response_meta(request),
            error=None,
        )

    try:
        # 60s timeout: first fetch serializes ~300 Deribit order book calls
        # (concurrent w/ Semaphore 20 → ~15s). Subsequent calls hit 5min cache (<1s).
        result = await asyncio.wait_for(options_fetcher.fetch_gex(currency), timeout=60.0)
    except asyncio.TimeoutError:
        return APIResponse(
            data={"currency": currency, "message": "GEX fetch timed out (Deribit unreachable)"},
            meta=build_response_meta(request),
            error=None,
        )
    if result is None:
        return APIResponse(
            data={"currency": currency, "message": "No GEX data available"},
            meta=build_response_meta(request),
            error=None,
        )

    return APIResponse(
        data={
            "symbol": result.symbol,
            "underlying_price": result.underlying_price,
            "net_gex": result.net_gex,
            "dealer_positioning": result.dealer_positioning,
            "key_levels": [l.model_dump() for l in result.key_levels],
            "put_call_ratio": result.put_call_ratio,
            "max_pain": result.max_pain,
        },
        meta=build_response_meta(request),
        error=None,
    )
