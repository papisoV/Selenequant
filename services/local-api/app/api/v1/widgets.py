"""Widgets registry endpoint.

Provides a listing endpoint for the demo front-end widget configuration
registry. Currently returns an empty list (placeholder); concrete widget
definitions will be populated by future integration with the dashboard
config service.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/widgets", tags=["widgets"])


@router.get("")
async def list_widgets(request: Request) -> APIResponse[list[dict[str, Any]]]:
    """Return widget registry for demo front-end.

    Returns an empty list by default. Concrete widget configurations will
    be populated by future integration with the dashboard config service.
    """
    return APIResponse(
        data=[],
        meta=build_response_meta(request),
        error=None,
    )
