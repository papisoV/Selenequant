"""Liquidation data endpoints - heatmap and statistics.

Provides liquidation analytics from CoinGlass API for crypto futures.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import build_response_meta, get_liquidation_fetcher
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

router = APIRouter(prefix="/liquidations", tags=["liquidations"])


def _require_liquidation_fetcher(
    fetcher: Any = Depends(get_liquidation_fetcher),
) -> Any:
    if fetcher is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Liquidation fetcher is not available",
        )
    return fetcher


def _ok(request: Request, data: dict[str, Any]) -> APIResponse[dict[str, Any]]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


def _heatmap_point(item: Any) -> dict[str, Any]:
    return {
        "timestamp": item.timestamp,
        "short_amount_usd": item.short_amount_usd,
        "long_amount_usd": item.long_amount_usd,
        "total_amount_usd": item.total_amount_usd,
        "short_count": item.short_count,
        "long_count": item.long_count,
        "price_high": item.price_high,
        "price_low": item.price_low,
    }


@router.get("/heatmap")
async def get_liquidation_heatmap(
    request: Request,
    symbol: str = Query("BTC", description="Trading pair symbol (e.g., BTC, ETH)"),
    timeframe: str = Query("h4", description="Time range: h1, h4, day"),
    force: bool = Query(False, description="Force refresh (bypass cache)"),
    fetcher: Any = Depends(_require_liquidation_fetcher),
) -> APIResponse[dict[str, Any]]:
    """Get liquidation heatmap data for a symbol.

    Returns liquidation levels across price ranges showing where
    liquidations are clustered, useful for identifying support/resistance.
    """
    items = await fetcher.get_liquidation_heatmap(symbol, timeframe, force=force)
    return _ok(
        request,
        {
            "symbol": symbol,
            "timeframe": timeframe,
            "items": [_heatmap_point(item) for item in items],
            "count": len(items),
        },
    )


@router.get("/stats")
async def get_liquidation_stats(
    request: Request,
    symbol: str = Query("BTC", description="Trading pair symbol (e.g., BTC, ETH)"),
    timeframe: str = Query("h4", description="Time range: h1, h4, day"),
    fetcher: Any = Depends(_require_liquidation_fetcher),
) -> APIResponse[dict[str, Any]]:
    """Get aggregated liquidation statistics for a symbol.

    Returns totals for long/short liquidations, counts, and average sizes.
    """
    stats = await fetcher.get_liquidation_stats(symbol, timeframe)
    if stats is None:
        return _ok(
            request,
            {
                "symbol": symbol,
                "timeframe": timeframe,
                "message": "No liquidation data available",
            },
        )
    return _ok(
        request,
        {
            "symbol": stats.symbol,
            "timeframe": stats.timeframe,
            "total_long_liquidations_usd": stats.total_long_liquidations_usd,
            "total_short_liquidations_usd": stats.total_short_liquidations_usd,
            "total_liquidations_usd": stats.total_liquidations_usd,
            "long_count": stats.long_count,
            "short_count": stats.short_count,
            "avg_long_size_usd": stats.avg_long_size_usd,
            "avg_short_size_usd": stats.avg_short_size_usd,
            "timestamp": stats.timestamp,
        },
    )
