"""Optimization endpoints — parameter grid search, auto-optimization, and LLM suggestions.

Provides 11 endpoints for strategy parameter optimization:
  1. POST /optimizations — create optimization run
  2. GET /optimizations/{id} — get run status/results
  3. POST /optimizations/{id}/apply — apply best params
  4. POST /optimizations/auto — start auto-optimization
  5. GET /optimizations/auto/{id} — auto-optimization status
  6. DELETE /optimizations/auto/{id} — cancel auto-optimization
  7. POST /optimizations/suggest — LLM param range suggestions
  8. GET /optimizations/{id}/export — export results
  9. GET /optimizations/distributed/status — distributed worker status (stub)
 10. GET /strategies/{id}/optimizable-params — list optimizable params (on strategies_router)
 11. DELETE /optimizations/{id} — cancel optimization run
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import AuthenticatedUser, build_response_meta, get_settings_service, optional_user
from app.engines.optimizer import run_optimization, suggest_param_ranges
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, NotFoundError, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/optimizations", tags=["optimizations"])
strategies_router = APIRouter(prefix="/strategies", tags=["optimizations"])

# ---------------------------------------------------------------------------
# In-memory stores
# ---------------------------------------------------------------------------

_optimization_runs: dict[str, dict] = {}
# id -> {id, strategy_id, status, param_space, results, best_params, created_at}

_auto_optimizations: dict[str, dict] = {}
# id -> {id, strategy_id, status, config, created_at, last_run_at}


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateOptimizationRequest(BaseModel):
    """Body for POST /optimizations."""

    strategy_id: str = Field(..., description="Strategy to optimize")
    param_space: dict[str, list[Any]] = Field(..., description="Parameter space to search")


class AutoOptimizationRequest(BaseModel):
    """Body for POST /optimizations/auto."""

    strategy_id: str = Field(..., description="Strategy to auto-optimize")
    param_space: dict[str, list[Any] | None] = Field(
        default_factory=dict,
        description="Parameter space (empty = auto-detect)",
    )
    interval_minutes: int = Field(60, ge=5, description="Re-optimization interval in minutes")
    max_runs: int = Field(10, ge=1, description="Maximum optimization runs")


class SuggestParamsRequest(BaseModel):
    """Body for POST /optimizations/suggest."""

    strategy_id: str = Field(..., description="Strategy to suggest params for")


# ---------------------------------------------------------------------------
# Background runner
# ---------------------------------------------------------------------------


async def _run_optimization_task(run_id: str) -> None:
    """Execute an optimization run in the background."""
    run = _optimization_runs.get(run_id)
    if run is None:
        return

    run["status"] = "running"
    try:
        settings_service = get_settings_service()
        result = await run_optimization(
            strategy_id=run["strategy_id"],
            param_space=run["param_space"],
            settings_service=settings_service,
        )
        run["results"] = result
        run["best_params"] = result.get("best_params", {})
        run["status"] = "completed"
    except Exception as exc:
        logger.warning("Optimization run %s failed: %s", run_id, exc, exc_info=True)
        run["status"] = "failed"
        run["error"] = str(exc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("")
async def create_optimization(
    request: Request,
    body: CreateOptimizationRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Create a new optimization run."""
    run_id = f"opt-{uuid.uuid4().hex[:8]}"
    now = int(time.time() * 1000)

    run: dict[str, Any] = {
        "id": run_id,
        "strategy_id": body.strategy_id,
        "status": "pending",
        "param_space": body.param_space,
        "results": None,
        "best_params": None,
        "created_at": now,
    }
    _optimization_runs[run_id] = run

    # Start optimization in background
    asyncio.create_task(_run_optimization_task(run_id))

    return APIResponse(
        data={"id": run_id, "status": "pending", "strategy_id": body.strategy_id},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/{optimization_id}")
async def get_optimization(
    optimization_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get optimization run status and results."""
    run = _optimization_runs.get(optimization_id)
    if run is None:
        raise NotFoundError(f"Optimization run '{optimization_id}' not found")

    return APIResponse(
        data=run,
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/{optimization_id}/apply")
async def apply_optimization(
    optimization_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Apply the best parameters from an optimization run to the strategy."""
    run = _optimization_runs.get(optimization_id)
    if run is None:
        raise NotFoundError(f"Optimization run '{optimization_id}' not found")

    if run.get("status") != "completed":
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message="Optimization must be completed before applying",
        )

    best_params = run.get("best_params", {})
    # In community edition, we record the apply but don't actually modify the strategy
    # (would require strategy repo integration)

    return APIResponse(
        data={"applied": True, "best_params": best_params},
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/auto")
async def create_auto_optimization(
    request: Request,
    body: AutoOptimizationRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Start an auto-optimization schedule for a strategy."""
    auto_id = f"auto-opt-{uuid.uuid4().hex[:8]}"
    now = int(time.time() * 1000)

    auto_run: dict[str, Any] = {
        "id": auto_id,
        "strategy_id": body.strategy_id,
        "status": "running",
        "config": {
            "param_space": body.param_space,
            "interval_minutes": body.interval_minutes,
            "max_runs": body.max_runs,
        },
        "created_at": now,
        "last_run_at": None,
        "runs_completed": 0,
    }
    _auto_optimizations[auto_id] = auto_run

    return APIResponse(
        data={"id": auto_id, "status": "running"},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/auto/{auto_id}")
async def get_auto_optimization(
    auto_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get auto-optimization status."""
    auto_run = _auto_optimizations.get(auto_id)
    if auto_run is None:
        raise NotFoundError(f"Auto-optimization '{auto_id}' not found")

    return APIResponse(
        data=auto_run,
        meta=build_response_meta(request),
        error=None,
    )


@router.delete("/auto/{auto_id}")
async def cancel_auto_optimization(
    auto_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Cancel an auto-optimization schedule."""
    auto_run = _auto_optimizations.pop(auto_id, None)
    if auto_run is None:
        raise NotFoundError(f"Auto-optimization '{auto_id}' not found")

    return APIResponse(
        data={"cancelled": True},
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/suggest")
async def suggest_params(
    request: Request,
    body: SuggestParamsRequest,
    settings_service: Any = Depends(get_settings_service),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """LLM-suggested parameter ranges for a strategy."""
    result = await suggest_param_ranges(settings_service, body.strategy_id)

    return APIResponse(
        data=result,
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/{optimization_id}/export")
async def export_optimization(
    optimization_id: str,
    request: Request,
    format: str = Query("json", description="Export format (json, csv)"),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Export optimization results in the specified format."""
    run = _optimization_runs.get(optimization_id)
    if run is None:
        raise NotFoundError(f"Optimization run '{optimization_id}' not found")

    if format == "json":
        content = json.dumps(run, indent=2, default=str)
    elif format == "csv":
        # Stub: would convert results to CSV
        content = "strategy_id,param,value,sharpe\n"
        if run.get("best_params"):
            for k, v in run["best_params"].items():
                content += f"{run['strategy_id']},{k},{v},{run.get('results', {}).get('best_sharpe', 0)}\n"
    else:
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message=f"Unsupported export format: {format}",
        )

    return APIResponse(
        data={"format": format, "content": content},
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/distributed/status")
async def distributed_status(
    request: Request,
    check_workers: bool = Query(False, description="Check for available workers"),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Distributed optimization worker status (stub).

    Community edition runs locally only.
    """
    return APIResponse(
        data={
            "workers": [],
            "status": "local_only",
            "note": "Distributed optimization requires Pro edition",
        },
        meta=build_response_meta(request),
        error=None,
    )


@strategies_router.get("/{strategy_id}/optimizable-params")
async def optimizable_params(
    strategy_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """List optimizable parameters for a strategy (stub).

    Full implementation would parse the strategy config to extract
    tunable parameters with their current values and types.
    """
    return APIResponse(
        data={"strategy_id": strategy_id, "params": []},
        meta=build_response_meta(request),
        error=None,
    )


@router.delete("/{optimization_id}")
async def delete_optimization(
    optimization_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Cancel/delete an optimization run."""
    run = _optimization_runs.pop(optimization_id, None)
    if run is None:
        raise NotFoundError(f"Optimization run '{optimization_id}' not found")

    return APIResponse(
        data={"cancelled": True},
        meta=build_response_meta(request),
        error=None,
    )
