"""Beta play scenarios endpoint — serves demo front-end page `beta`.

Returns hedge-play scenarios for the demo front-end beta page. Currently
returns an empty list (placeholder); concrete scenarios will be populated
by future integration with the strategy engine.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/beta-play", tags=["beta-play"])


@router.get("")
async def list_beta_plays(request: Request) -> APIResponse[list[dict[str, Any]]]:
    """Return hedge-play scenarios for demo front-end.

    Returns an empty list by default. Concrete scenarios will be populated
    by future integration with the strategy engine.
    """
    return APIResponse(
        data=[],
        meta=build_response_meta(request),
        error=None,
    )
