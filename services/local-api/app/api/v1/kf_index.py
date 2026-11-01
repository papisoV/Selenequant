"""KF Index endpoints.

Custom composite index creation, management, and computation.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse
from selene_space_types.derivatives import KFIndexConfig

router = APIRouter(prefix="/kf-index", tags=["kf-index"])


@router.get("/list")
async def list_indices(
    request: Request,
) -> APIResponse[list[dict[str, Any]]]:
    """List all KF Index configurations."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "kf_index_engine"):
        return APIResponse(
            data={"message": "KF Index engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    indices = market_svc.kf_index_engine.list_indices()
    return APIResponse(
        data=[i.model_dump() for i in indices],
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/compute")
async def compute_index(
    request: Request,
    index_id: str = Query(..., description="Index identifier"),
    symbol: str = Query("BTC/USDT", description="Trading pair"),
    exchange: str = Query("binance", description="Exchange identifier"),
) -> APIResponse[dict[str, Any]]:
    """Compute current value of a KF Index for a symbol."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "kf_index_engine"):
        return APIResponse(
            data={"message": "KF Index engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    value = market_svc.kf_index_engine.compute_index(index_id, symbol, exchange)
    if value is None:
        return APIResponse(
            data={"index_id": index_id, "message": "Index not found or no data"},
            meta=build_response_meta(request),
            error=None,
        )

    return APIResponse(
        data=value.model_dump(),
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/compute-all")
async def compute_index_all(
    request: Request,
    index_id: str = Query(..., description="Index identifier"),
    exchange: str = Query("binance", description="Exchange identifier"),
) -> APIResponse[dict[str, Any]]:
    """Compute a KF Index for all tracked symbols."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "kf_index_engine"):
        return APIResponse(
            data={"message": "KF Index engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    values = market_svc.kf_index_engine.compute_all_symbols(index_id, exchange)
    return APIResponse(
        data=[v.model_dump() for v in values],
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/create")
async def create_index(
    request: Request,
    config: KFIndexConfig,
) -> APIResponse[dict[str, Any]]:
    """Create a new KF Index configuration."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "kf_index_engine"):
        return APIResponse(
            data={"message": "KF Index engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    market_svc.kf_index_engine.create_index(config)
    return APIResponse(
        data={"id": config.id, "name": config.name, "message": "Index created"},
        meta=build_response_meta(request),
        error=None,
    )


@router.delete("/delete")
async def delete_index(
    request: Request,
    index_id: str = Query(..., description="Index identifier"),
) -> APIResponse[dict[str, Any]]:
    """Delete a KF Index configuration."""
    from app.core.dependencies import get_market_service

    market_svc = get_market_service()
    if market_svc is None or not hasattr(market_svc, "kf_index_engine"):
        return APIResponse(
            data={"message": "KF Index engine not available"},
            meta=build_response_meta(request),
            error=None,
        )

    deleted = market_svc.kf_index_engine.delete_index(index_id)
    return APIResponse(
        data={"id": index_id, "deleted": deleted},
        meta=build_response_meta(request),
        error=None,
    )
