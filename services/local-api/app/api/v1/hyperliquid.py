"""Hyperliquid on-chain intelligence endpoints.

Provides address screening, profiling, and copy-trading signals
from Hyperliquid's public API:
- Screen wallets by classification (elite/inverse/neutral)
- Get detailed address profiles (equity curve, positions, trades)
- Pipeline status and prefetch management
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import build_response_meta
from app.engines.hyperliquid_scorer import build_screened_address
from app.models.hyperliquid import PipelineStatus
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/hyperliquid", tags=["hyperliquid"])

# ---------------------------------------------------------------------------
# Singleton client
# ---------------------------------------------------------------------------

_hl_client = None


async def _get_client():
    global _hl_client
    if _hl_client is None:
        try:
            from app.clients.hyperliquid import HyperliquidClient
        except ImportError as exc:  # pragma: no cover - module ships with the app
            raise SeleneSpaceError(
                code=ErrorCode.SERVICE_UNAVAILABLE,
                message="Hyperliquid client module is missing from this build",
            ) from exc

        _hl_client = HyperliquidClient()
    return _hl_client


# ---------------------------------------------------------------------------
# In-memory caches with TTL
# ---------------------------------------------------------------------------

_screen_cache: dict[str, tuple[float, list[dict]]] = {}  # key -> (timestamp, data)
_detail_cache: dict[str, tuple[float, dict]] = {}  # address -> (timestamp, data)
_CACHE_TTL = 300.0  # 5 minutes


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/addresses/screen")
async def screen_addresses(
    request: Request,
    classification: str = Query(
        "all", description="Filter: all, elite, inverse, neutral"
    ),
    window_days: int = Query(7, ge=1, le=365, description="Time window in days"),
    limit: int = Query(200, ge=1, le=1000, description="Max results"),
    min_score: int = Query(0, ge=0, le=100, description="Minimum score"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    client=Depends(_get_client),
) -> APIResponse[dict[str, Any]]:
    """Screen Hyperliquid wallets by classification."""
    try:
        cache_key = f"screen:{classification}:{window_days}"
        now = time.time()

        if cache_key in _screen_cache:
            ts, cached = _screen_cache[cache_key]
            if now - ts < _CACHE_TTL:
                results = cached
            else:
                results = None
        else:
            results = None

        if results is None:
            window_map = {
                1: "1d",
                7: "7d",
                30: "30d",
                90: "90d",
                180: "180d",
                365: "365d",
            }
            hl_window = window_map.get(window_days, "7d")
            leaderboard = await client.get_leaderboard(window=hl_window)
            results = leaderboard
            _screen_cache[cache_key] = (now, results)

        addresses = []
        for entry in results:
            addr = entry.get("address", entry.get("ethAddress", ""))
            if not addr:
                continue
            addresses.append((addr, entry))

        screened = []
        for addr, lb_data in addresses:
            sa = build_screened_address(
                addr, leaderboard=lb_data, window_days=window_days
            )
            if classification != "all" and sa.classification != classification:
                continue
            if sa.score < min_score:
                continue
            screened.append(sa.model_dump())

        total = len(screened)
        paginated = screened[offset : offset + limit]
        has_more = offset + limit < total

        return APIResponse(
            data={
                "results": paginated,
                "count": total,
                "classification": classification,
                "window_days": window_days,
                "min_score": min_score,
                "limit": limit,
                "offset": offset,
                "has_more": has_more,
            },
            meta=build_response_meta(request),
            error=None,
        )
    except Exception as exc:
        logger.error("Failed to screen addresses: %s", exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Hyperliquid API error: {exc}",
        ) from exc


@router.get("/addresses/{address}/details")
async def get_address_details(
    address: str,
    request: Request,
    window_days: int = Query(7, ge=1, le=365),
    client=Depends(_get_client),
) -> APIResponse[dict[str, Any]]:
    """Get detailed profile for a specific Hyperliquid address."""
    try:
        now = time.time()
        if address in _detail_cache:
            ts, cached = _detail_cache[address]
            if now - ts < _CACHE_TTL:
                return APIResponse(
                    data=cached, meta=build_response_meta(request), error=None
                )

        state = await client.get_user_state(address)
        fills = await client.get_user_fills(address, limit=500)
        portfolio = await client.get_portfolio_history(address)

        sa = build_screened_address(address, fills=fills, window_days=window_days)

        from app.models.hyperliquid import (
            AddressDetails,
            AddressScore,
            EquityCurvePoint,
            PositionInfo,
        )

        # Build equity curve from portfolio
        equity_curve = []
        if isinstance(portfolio, dict):
            for bucket_name in ("day", "week", "month"):
                bucket = portfolio.get(bucket_name, {})
                if isinstance(bucket, dict):
                    avh = bucket.get("accountValueHistory", [])
                    for ts_val, val_str in avh:
                        try:
                            equity_curve.append(
                                EquityCurvePoint(
                                    time_ms=int(ts_val),
                                    equity=float(val_str),
                                    pnl=float(val_str) - float(avh[0][1])
                                    if avh
                                    else 0.0,
                                )
                            )
                        except (ValueError, IndexError):
                            pass

        # Parse positions
        positions = []
        for pos in state.get("assetPositions", []):
            pos_data = pos.get("position", {})
            positions.append(
                PositionInfo(
                    coin=pos_data.get("coin", ""),
                    szi=pos_data.get("szi", "0"),
                    entry_px=pos_data.get("entryPx"),
                    position_value=pos_data.get("positionValue", "0"),
                    unrealized_pnl=pos_data.get("unrealizedPnl", "0"),
                )
            )

        # Parse open orders
        open_orders = state.get("openOrders", [])

        score = AddressScore(
            address=address,
            classification=sa.classification,
            score=sa.score,
            metrics=sa.metrics,
            dimensions=sa.dimensions,
            reasons=sa.reasons,
            risk_flags=sa.risk_flags,
            data_quality=sa.data_quality,
            copy_signal=sa.copy_signal,
        )

        details = AddressDetails(
            address=address,
            window_days=window_days,
            score=score,
            equity_curve=equity_curve[-200:],
            current_positions=positions,
            open_orders=open_orders[:50],
            closed_trades=fills[:100],
            portfolio=portfolio if isinstance(portfolio, dict) else None,
        )

        result = details.model_dump()
        _detail_cache[address] = (now, result)

        return APIResponse(data=result, meta=build_response_meta(request), error=None)
    except Exception as exc:
        logger.error("Failed to get details for %s: %s", address[:10], exc)
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"Hyperliquid API error: {exc}",
        ) from exc


@router.get("/addresses/pipeline")
async def get_pipeline_status(
    request: Request,
) -> APIResponse[dict[str, Any]]:
    """Get cache and pipeline status.

    No client dependency on purpose: this reports only local cache state, and
    requiring a live upstream client made a pure status read fail whenever the
    client could not be built.
    """
    now = time.time()
    screen_age = min((now - ts for ts, _ in _screen_cache.values()), default=0.0)
    pipeline = PipelineStatus(
        screen_cache_entries=len(_screen_cache),
        screen_cache_age_s=screen_age,
        detail_cache_entries=len(_detail_cache),
    )
    return APIResponse(
        data=pipeline.model_dump(), meta=build_response_meta(request), error=None
    )


@router.post("/addresses/prefetch")
async def prefetch_addresses(
    request: Request,
    body: dict[str, Any] | None = None,
    client=Depends(_get_client),
) -> APIResponse[dict[str, Any]]:
    """Prefetch profiles for a list of addresses."""
    addresses = (body or {}).get("addresses", [])
    if not addresses:
        return APIResponse(
            data={"job_id": "", "status": "no_addresses", "loaded": 0, "target": 0},
            meta=build_response_meta(request),
            error=None,
        )

    loaded = 0
    for addr in addresses[:20]:
        try:
            fills = await client.get_user_fills(addr, limit=200)
            sa = build_screened_address(addr, fills=fills)
            _detail_cache[addr] = (time.time(), sa.model_dump())
            loaded += 1
        except Exception:
            pass

    return APIResponse(
        data={
            "job_id": f"prefetch-{int(time.time()*1000)}",
            "status": "complete",
            "loaded": loaded,
            "target": len(addresses),
        },
        meta=build_response_meta(request),
        error=None,
    )
