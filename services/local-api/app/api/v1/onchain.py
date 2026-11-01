"""On-chain data endpoints — active addresses, NVT ratio, SOPR, whale tracking.

Provides on-chain analytics sourced from Glassnode API and other blockchain
data providers.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import build_response_meta, get_onchain_fetcher
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

router = APIRouter(prefix="/onchain", tags=["onchain"])

_SYMBOL_QUERY = Query("BTC", description="Asset symbol: BTC, ETH")
_FORCE_QUERY = Query(False, description="Force refresh (bypass cache)")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _require_onchain_fetcher(fetcher: Any = Depends(get_onchain_fetcher)) -> Any:
    if fetcher is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Onchain data service is not available",
        )
    return fetcher


def _ok(request: Request, data: dict[str, Any]) -> APIResponse[dict[str, Any]]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


def _metric_response(
    request: Request, symbol: str, metric: str, result: Any
) -> APIResponse[dict[str, Any]]:
    """Shared shape for the three single-metric endpoints.

    A missing metric answers with an explicit null so callers can distinguish
    "no data" from "endpoint broke".
    """
    if result is None:
        return _ok(request, {"symbol": symbol, "metric": metric, "value": None})
    return _ok(request, result.model_dump())


@router.get("/metrics")
async def get_onchain_metrics(
    request: Request,
    symbol: str = _SYMBOL_QUERY,
    force: bool = _FORCE_QUERY,
    fetcher: Any = Depends(_require_onchain_fetcher),
) -> APIResponse[dict[str, Any]]:
    """Get on-chain metrics (active addresses, NVT, SOPR) for an asset."""
    symbol = symbol.upper()

    # Fetch all three metrics in parallel
    active_addresses = await fetcher.get_active_addresses(symbol)
    nvt_ratio = await fetcher.get_nvt_ratio(symbol)
    sopr = await fetcher.get_sopr(symbol)

    metrics: dict[str, Any] = {}
    for key, value in (
        ("active_addresses", active_addresses),
        ("nvt_ratio", nvt_ratio),
        ("sopr", sopr),
    ):
        if value:
            metrics[key] = value.model_dump()

    return _ok(request, {"symbol": symbol, "metrics": metrics})


@router.get("/metrics/active-addresses")
async def get_active_addresses(
    request: Request,
    symbol: str = _SYMBOL_QUERY,
    force: bool = _FORCE_QUERY,
    fetcher: Any = Depends(_require_onchain_fetcher),
) -> APIResponse[dict[str, Any]]:
    """Get active address count for an asset."""
    symbol = symbol.upper()
    return _metric_response(
        request, symbol, "active_addresses", await fetcher.get_active_addresses(symbol)
    )


@router.get("/metrics/nvt-ratio")
async def get_nvt_ratio(
    request: Request,
    symbol: str = _SYMBOL_QUERY,
    force: bool = _FORCE_QUERY,
    fetcher: Any = Depends(_require_onchain_fetcher),
) -> APIResponse[dict[str, Any]]:
    """Get NVT (Network Value to Transactions) ratio for an asset."""
    symbol = symbol.upper()
    return _metric_response(
        request, symbol, "nvt_ratio", await fetcher.get_nvt_ratio(symbol)
    )


@router.get("/metrics/sopr")
async def get_sopr(
    request: Request,
    symbol: str = _SYMBOL_QUERY,
    force: bool = _FORCE_QUERY,
    fetcher: Any = Depends(_require_onchain_fetcher),
) -> APIResponse[dict[str, Any]]:
    """Get SOPR (Spent Output Profit Ratio) for an asset."""
    symbol = symbol.upper()
    return _metric_response(request, symbol, "sopr", await fetcher.get_sopr(symbol))


@router.get("/whales")
async def get_whale_tracking(
    request: Request,
    symbol: str = _SYMBOL_QUERY,
    limit: int = Query(
        50, ge=1, le=200, description="Number of recent whale movements"
    ),
) -> APIResponse[dict[str, Any]]:
    """Get recent whale movements for an asset.

    Note: This endpoint currently returns placeholder data as whale tracking
    requires additional data sources (e.g., blockchain explorers, whale alert APIs).
    """
    return _ok(
        request,
        {
            "symbol": symbol.upper(),
            "movements": [],
            "count": 0,
            "message": "Whale tracking not yet implemented. Requires integration with "
            "blockchain explorer or whale alert API.",
        },
    )


# direction → (label, classified) for smart-money tagging
_DIRECTION_TAGS = {
    "in": ("smart_money_in", "accumulate"),
    "inflow": ("smart_money_in", "accumulate"),
    "out": ("smart_money_out", "distribute"),
    "outflow": ("smart_money_out", "distribute"),
}
_DEFAULT_TAGS = ("exchange_transfer", "transfer")


@router.get("/smart-money")
async def get_smart_money(
    request: Request,
    symbol: str = _SYMBOL_QUERY,
    limit: int = Query(50, ge=1, le=200, description="Max movements to return"),
) -> APIResponse[dict[str, Any]]:
    """Smart-money classification of whale movements (Phase 4).

    Returns whale movements enriched with classification tags:
    ``smart_money_in`` / ``smart_money_out`` / ``exchange_outflow`` / ``exchange_inflow``.

    Currently mirrors the placeholder data from /onchain/whales (no real
    classification source wired yet), but exposes the stable contract.
    """
    symbol = symbol.upper()

    # Reuse /whales data so callers get one consistent source. When a real
    # classification fetcher is wired (e.g., whale alert + cluster labels),
    # only this function needs updating.
    whale_resp = await get_whale_tracking(request=request, symbol=symbol, limit=limit)
    whale_data = getattr(whale_resp, "data", None) or {}

    movements: list[dict[str, Any]] = []
    for raw in whale_data.get("movements", []):
        direction = (raw.get("direction") or "neutral").lower()
        label, classified = _DIRECTION_TAGS.get(direction, _DEFAULT_TAGS)
        movements.append(
            {
                "ts": raw.get("ts") or raw.get("timestamp"),
                "symbol": symbol,
                "direction": classified,
                "value_usd": raw.get("value_usd") or raw.get("value"),
                "label": label,
                "confidence": 0.5,
                "hash": raw.get("hash"),
            }
        )

    return _ok(
        request,
        {
            "symbol": symbol,
            "movements": movements,
            "count": len(movements),
            "timestamp": _now_ms(),
        },
    )
