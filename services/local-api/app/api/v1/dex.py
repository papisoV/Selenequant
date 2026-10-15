"""DEX data endpoints — DEX pair prices, volume, liquidity from DexScreener.

Provides real-time DEX analytics sourced from DexScreener API
across 80+ blockchain networks.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query, Request

from app.core.dependencies import build_response_meta, get_dex_fetcher
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

router = APIRouter(prefix="/dex", tags=["dex"])


def _require_dex_fetcher(fetcher: Any = Depends(get_dex_fetcher)) -> Any:
    if fetcher is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="DEX fetcher service is not available",
        )
    return fetcher


def _pairs_payload(pairs: list[Any], **extra: Any) -> dict[str, Any]:
    """Serialize a pair list plus its count (and any query echo fields)."""
    return {"pairs": [p.to_dict() for p in pairs], "count": len(pairs), **extra}


def _ok(request: Request, data: dict[str, Any]) -> APIResponse[dict[str, Any]]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.get("/pairs")
async def get_dex_pairs(
    request: Request,
    chain: str = Query(
        "ethereum", description="Chain ID: ethereum, bsc, solana, arbitrum, base, etc."
    ),
    limit: int = Query(50, ge=1, le=200),
    fetcher: Any = Depends(_require_dex_fetcher),
) -> APIResponse[dict[str, Any]]:
    """Get top DEX pairs on a specific chain sorted by liquidity."""
    pairs = (await fetcher.get_pairs_by_chain(chain))[:limit]
    return _ok(request, _pairs_payload(pairs, chain=chain))


@router.get("/search")
async def search_dex_pairs(
    request: Request,
    q: str = Query(..., min_length=1, description="Token name or symbol to search"),
    fetcher: Any = Depends(_require_dex_fetcher),
) -> APIResponse[dict[str, Any]]:
    """Search for DEX pairs by token name or symbol across all chains."""
    pairs = await fetcher.search_pairs(q)
    return _ok(request, _pairs_payload(pairs, query=q))


@router.get("/pair/{chain}/{pair_address}")
async def get_dex_pair_detail(
    request: Request,
    chain: str,
    pair_address: str,
    fetcher: Any = Depends(_require_dex_fetcher),
) -> APIResponse[dict[str, Any]]:
    """Get detailed data for a specific DEX pair by chain and address."""
    pair = await fetcher.get_pair(chain, pair_address)
    if pair is None:
        return _ok(
            request,
            {"pair": None, "message": f"Pair {pair_address} not found on {chain}"},
        )
    return _ok(request, {"pair": pair.to_dict(), "chain": chain})


@router.get("/tokens/{token_address}")
async def get_token_pairs(
    request: Request,
    token_address: str,
    chain: str = Query("ethereum", description="Chain ID"),
    limit: int = Query(50, ge=1, le=200),
    fetcher: Any = Depends(_require_dex_fetcher),
) -> APIResponse[dict[str, Any]]:
    """Get all DEX pairs for a specific token address."""
    pairs = (await fetcher.get_token_pairs(chain, token_address))[:limit]
    return _ok(request, _pairs_payload(pairs, token_address=token_address, chain=chain))
