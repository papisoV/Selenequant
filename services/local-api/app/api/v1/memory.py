"""Memory endpoints — market pattern recognition and strategy history.

Provides CRUD for market patterns (user-defined conditions + actions)
and strategy history lookup.  All data is stored in-memory for the
community edition.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import AuthenticatedUser, build_response_meta, optional_user
from selene_space_types.common import APIResponse
from selene_space_types.errors import NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/memory", tags=["memory"])

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

_market_patterns: dict[str, dict] = {}
# id -> {id, name, symbol, conditions, action, enabled, created_at, hit_count, last_hit_at}


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreatePatternRequest(BaseModel):
    """Body for POST /memory/market-patterns."""

    name: str = Field(..., description="Pattern name")
    symbol: str | None = Field(None, description="Target symbol (optional)")
    conditions: dict[str, Any] = Field(..., description="Pattern conditions")
    action: dict[str, Any] = Field(..., description="Action to take when pattern triggers")
    enabled: bool = Field(True, description="Whether the pattern is active")


class UpdatePatternRequest(BaseModel):
    """Body for PUT /memory/market-patterns/{pattern_id} — full replacement."""

    name: str = Field(..., description="Pattern name")
    symbol: str | None = Field(None, description="Target symbol")
    conditions: dict[str, Any] = Field(..., description="Pattern conditions")
    action: dict[str, Any] = Field(..., description="Action to take")
    enabled: bool = Field(True, description="Whether the pattern is active")


class PatchPatternRequest(BaseModel):
    """Body for PATCH /memory/market-patterns/{pattern_id} — partial update."""

    name: str | None = Field(None, description="Pattern name")
    symbol: str | None = Field(None, description="Target symbol")
    conditions: dict[str, Any] | None = Field(None, description="Pattern conditions")
    action: dict[str, Any] | None = Field(None, description="Action to take")
    enabled: bool | None = Field(None, description="Whether the pattern is active")


class ValidatePatternRequest(BaseModel):
    """Body for POST /memory/market-patterns/validate."""

    name: str | None = Field(None, description="Pattern name")
    symbol: str | None = Field(None, description="Target symbol")
    conditions: dict[str, Any] = Field(..., description="Pattern conditions to validate")
    action: dict[str, Any] = Field(..., description="Action to validate")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/market-patterns")
async def list_market_patterns(
    request: Request,
    enabled_only: bool | None = Query(None, description="Filter to enabled patterns only"),
    symbol: str | None = Query(None, description="Filter by symbol"),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List market patterns, optionally filtered by enabled status and symbol."""
    patterns = list(_market_patterns.values())

    if enabled_only is True:
        patterns = [p for p in patterns if p.get("enabled", True)]
    if symbol is not None:
        patterns = [p for p in patterns if p.get("symbol") == symbol]

    return APIResponse(
        data=patterns,
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/market-patterns")
async def create_market_pattern(
    request: Request,
    body: CreatePatternRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Create a new market pattern."""
    pattern_id = uuid.uuid4().hex[:12]
    now = int(time.time() * 1000)

    pattern: dict[str, Any] = {
        "id": pattern_id,
        "name": body.name,
        "symbol": body.symbol,
        "conditions": body.conditions,
        "action": body.action,
        "enabled": body.enabled,
        "created_at": now,
        "hit_count": 0,
        "last_hit_at": None,
    }
    _market_patterns[pattern_id] = pattern

    return APIResponse(
        data=pattern,
        meta=build_response_meta(request),
        error=None,
    )


@router.put("/market-patterns/{pattern_id}")
async def update_market_pattern(
    pattern_id: str,
    request: Request,
    body: UpdatePatternRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Full replacement update of a market pattern."""
    existing = _market_patterns.get(pattern_id)
    if existing is None:
        raise NotFoundError(f"Market pattern '{pattern_id}' not found")

    updated: dict[str, Any] = {
        "id": pattern_id,
        "name": body.name,
        "symbol": body.symbol,
        "conditions": body.conditions,
        "action": body.action,
        "enabled": body.enabled,
        "created_at": existing.get("created_at", 0),
        "hit_count": existing.get("hit_count", 0),
        "last_hit_at": existing.get("last_hit_at"),
    }
    _market_patterns[pattern_id] = updated

    return APIResponse(
        data=updated,
        meta=build_response_meta(request),
        error=None,
    )


@router.patch("/market-patterns/{pattern_id}")
async def patch_market_pattern(
    pattern_id: str,
    request: Request,
    body: PatchPatternRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Partial update of a market pattern."""
    existing = _market_patterns.get(pattern_id)
    if existing is None:
        raise NotFoundError(f"Market pattern '{pattern_id}' not found")

    patched = dict(existing)
    if body.name is not None:
        patched["name"] = body.name
    if body.symbol is not None:
        patched["symbol"] = body.symbol
    if body.conditions is not None:
        patched["conditions"] = body.conditions
    if body.action is not None:
        patched["action"] = body.action
    if body.enabled is not None:
        patched["enabled"] = body.enabled

    _market_patterns[pattern_id] = patched

    return APIResponse(
        data=patched,
        meta=build_response_meta(request),
        error=None,
    )


@router.delete("/market-patterns/{pattern_id}")
async def delete_market_pattern(
    pattern_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Delete a market pattern."""
    removed = _market_patterns.pop(pattern_id, None)
    return APIResponse(
        data={"deleted": removed is not None},
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/market-patterns/validate")
async def validate_market_pattern(
    request: Request,
    body: ValidatePatternRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Validate a pattern's conditions structure.

    Returns validation result with any errors found.
    backtest_preview is always None in community edition (requires full data pipeline).
    """
    errors: list[str] = []

    conditions = body.conditions
    if not isinstance(conditions, dict):
        errors.append("conditions must be a dict")
    elif not conditions:
        errors.append("conditions must not be empty")
    else:
        # Basic structural validation
        for key, value in conditions.items():
            if not isinstance(key, str):
                errors.append(f"condition key must be string, got {type(key).__name__}")
            if isinstance(value, dict):
                if "operator" not in value:
                    errors.append(f"condition '{key}' missing 'operator'")
                if "value" not in value:
                    errors.append(f"condition '{key}' missing 'value'")

    action = body.action
    if not isinstance(action, dict):
        errors.append("action must be a dict")
    elif not action:
        errors.append("action must not be empty")
    else:
        if "type" not in action:
            errors.append("action missing 'type'")

    valid = len(errors) == 0

    return APIResponse(
        data={
            "valid": valid,
            "errors": errors,
            "backtest_preview": None,
        },
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/strategy-history/{strategy_id}")
async def strategy_history(
    strategy_id: str,
    request: Request,
    regime: str | None = Query(None, description="Filter by market regime"),
    limit: int = Query(50, ge=1, le=200, description="Max results"),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get strategy execution history (stub).

    Full implementation requires persistent strategy execution logs.
    """
    return APIResponse(
        data={
            "strategy_id": strategy_id,
            "history": [],
            "regime": regime or "all",
        },
        meta=build_response_meta(request),
        error=None,
    )
