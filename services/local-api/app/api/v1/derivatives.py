"""Derivatives data endpoints — funding rate, open interest, liquidation stats.

Provides real-time derivatives analytics sourced from exchange WebSocket
streams and aggregated by the market-data aggregators.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import build_response_meta, get_market_service
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

router = APIRouter(prefix="/derivatives", tags=["derivatives"])


def _ok(request: Request, data: dict[str, Any]) -> APIResponse[dict[str, Any]]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


def _require_market_service(svc: Any = Depends(get_market_service)) -> Any:
    if svc is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Market data service is not available",
        )
    return svc


@router.get("/liquidation-stats")
async def get_liquidation_stats(
    request: Request,
    window: str = Query("1h", description="Time window: 15m, 30m, 1h, 4h, 24h"),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get aggregated liquidation statistics for a time window."""
    liq_agg = getattr(svc, "_liquidation_aggregator", None)
    if liq_agg is None:
        return _ok(
            request,
            {
                "window": window,
                "message": "Liquidation aggregator not initialized",
            },
        )
    stats = liq_agg.get_stats(window)
    if stats is None:
        return _ok(
            request,
            {
                "window": window,
                "total_liquidated_usd": 0,
                "long_count": 0,
                "short_count": 0,
            },
        )
    return _ok(request, stats.model_dump())


@router.get("/liquidation-stats/all")
async def get_all_liquidation_stats(
    request: Request,
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get liquidation statistics for all time windows at once."""
    liq_agg = getattr(svc, "_liquidation_aggregator", None)
    if liq_agg is None:
        return _ok(request, {"windows": {}})
    all_stats = liq_agg.get_all_stats()
    return _ok(request, {"windows": {k: v.model_dump() for k, v in all_stats.items()}})


@router.get("/liquidations/recent")
async def get_recent_liquidations(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get recent raw liquidation events."""
    liq_agg = getattr(svc, "_liquidation_aggregator", None)
    if liq_agg is None:
        return _ok(request, {"events": [], "count": 0})
    events = liq_agg.get_recent_events(limit)
    return _ok(
        request,
        {
            "events": [e.model_dump() for e in events],
            "count": len(events),
        },
    )


@router.get("/large-trades/recent")
async def get_recent_large_trades(
    request: Request,
    limit: int = Query(50, ge=1, le=200),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get recent large trades above threshold."""
    detector = getattr(svc, "_large_trade_detector", None)
    if detector is None:
        return _ok(request, {"events": [], "count": 0})
    events = detector.get_recent(limit)
    return _ok(
        request,
        {
            "events": [e.model_dump() for e in events],
            "count": len(events),
            "threshold_usd": detector.threshold_usd,
        },
    )


@router.get("/large-trades/stats")
async def get_large_trade_stats(
    request: Request,
    window_ms: int = Query(3_600_000, description="Time window in milliseconds"),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get large trade summary statistics."""
    detector = getattr(svc, "_large_trade_detector", None)
    if detector is None:
        return _ok(request, {"message": "Large trade detector not initialized"})
    stats = detector.get_stats(window_ms)
    return _ok(request, stats)


@router.get("/funding-rates")
async def get_funding_rates(
    request: Request,
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get current funding rates for all tracked symbols (WS-based)."""
    store = getattr(svc, "_funding_rate_store", None)
    if store is None:
        return _ok(request, {"rates": {}})
    return _ok(request, {"rates": {k: v.model_dump() for k, v in store.items()}})


@router.get("/funding-rates/all-exchanges")
async def get_all_exchange_funding_rates(
    request: Request,
    exchange: str = Query("all", description="Exchange filter: all, binance, okx"),
    force: bool = Query(False, description="Force refresh (bypass cache)"),
) -> APIResponse[dict[str, Any]]:
    """Get ALL funding rates from Binance and OKX (REST bulk fetch, cached)."""
    from app.core.dependencies import get_funding_rate_fetcher

    fetcher = get_funding_rate_fetcher()
    if fetcher is None:
        return _ok(request, {"rates": [], "count": 0, "exchanges": []})

    items = await fetcher.get_all(force=force)

    if exchange != "all":
        items = [it for it in items if it.exchange == exchange]

    freshness = fetcher.freshness() if hasattr(fetcher, "freshness") else None
    data: dict[str, Any] = {
        "rates": [it.to_dict() for it in items],
        "count": len(items),
        "exchanges": list({it.exchange for it in items}),
    }
    if freshness is not None:
        data["freshness"] = freshness

    return _ok(request, data)


@router.get("/open-interest")
async def get_open_interest(
    request: Request,
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get current open interest for all tracked symbols."""
    store = getattr(svc, "_open_interest_store", None)
    if store is None:
        return _ok(request, {"snapshots": {}})
    return _ok(request, {"snapshots": {k: v.model_dump() for k, v in store.items()}})


# ---------------------------------------------------------------------------
# Footprint endpoints (from selene-master1)
# ---------------------------------------------------------------------------


@router.get("/footprint")
async def get_footprint(
    request: Request,
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    interval: str = Query("5m", description="Bar interval: 1m, 5m, 15m, 1h, 4h"),
    exchange: str = Query("binance", description="Exchange"),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get the latest footprint bar (buy/sell volume per price level)."""
    bar = svc.get_footprint(symbol, interval=interval, exchange=exchange)
    if bar is None:
        return _ok(request, {"message": "No footprint data available"})
    return _ok(request, bar.model_dump())


@router.get("/footprint/history")
async def get_footprint_history(
    request: Request,
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    interval: str = Query("5m", description="Bar interval"),
    exchange: str = Query("binance", description="Exchange"),
    limit: int = Query(50, ge=1, le=200, description="Number of bars"),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get recent completed footprint bars."""
    bars = svc.get_footprint_history(
        symbol, interval=interval, exchange=exchange, limit=limit
    )
    return _ok(request, {"bars": [b.model_dump() for b in bars], "count": len(bars)})


# ---------------------------------------------------------------------------
# VPVR endpoints (from selene-master1)
# ---------------------------------------------------------------------------


@router.get("/vpvr")
async def get_vpvr(
    request: Request,
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    exchange: str = Query("binance", description="Exchange"),
    window: str = Query("1h", description="Lookback window: 15m, 30m, 1h, 4h, 24h"),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get Volume Profile Visible Range data."""
    profile = svc.get_vpvr(symbol, exchange=exchange, window=window)
    if profile is None:
        return _ok(request, {"message": "No VPVR data available"})
    return _ok(request, profile.model_dump())


# ---------------------------------------------------------------------------
# Merged book endpoint (from selene-master1)
# ---------------------------------------------------------------------------


@router.get("/merged-book")
async def get_merged_book(
    request: Request,
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get cross-exchange merged orderbook."""
    merged = svc.get_merged_book(symbol)
    if merged is None:
        return _ok(request, {"message": "No merged book data available"})
    return _ok(request, merged.model_dump())


# ---------------------------------------------------------------------------
# LiqMap heatmap endpoint (from selene-master1)
# ---------------------------------------------------------------------------


@router.get("/liqmap")
async def get_liqmap(
    request: Request,
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    current_price: float = Query(0.0, description="Current price for zone calculation"),
    window: str = Query("1h", description="Aggregation window: 15m, 30m, 1h, 4h, 24h"),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Get liquidation heatmap data with clusters and zones."""
    heatmap = svc.get_liqmap(symbol, current_price=current_price, window=window)
    if heatmap is None:
        return _ok(request, {"message": "No liqmap data available"})
    return _ok(request, heatmap.model_dump())
