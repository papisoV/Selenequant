"""QEvolve endpoints — LLM-driven strategy evolution.

Provides 3 endpoints for strategy evolution:
  1. POST /qevolve — create evolution run
  2. GET /qevolve/{id} — get run status/results
  3. DELETE /qevolve/{id} — cancel evolution run
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.dependencies import AuthenticatedUser, build_response_meta, get_settings_service, optional_user
from app.engines.evolver import run_evolution
from selene_space_types.common import APIResponse
from selene_space_types.errors import NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/qevolve", tags=["qevolve"])

# ---------------------------------------------------------------------------
# In-memory store
# ---------------------------------------------------------------------------

_evolve_runs: dict[str, dict] = {}
# id -> {id, seed_strategy_id, status, config, generations, best_strategy, created_at}


# ---------------------------------------------------------------------------
# Request body
# ---------------------------------------------------------------------------


class CreateEvolveRequest(BaseModel):
    """Body for POST /qevolve."""

    seed_strategy_id: str = Field(..., description="Seed strategy to evolve from")
    generations: int = Field(5, ge=1, le=50, description="Number of evolution generations")
    variants_per_gen: int = Field(5, ge=2, le=20, description="Variants per generation")
    selection_pressure: float = Field(0.5, ge=0.1, le=1.0, description="Selection pressure")
    mutation_rate: float = Field(0.3, ge=0.0, le=1.0, description="Mutation rate")


# ---------------------------------------------------------------------------
# Background runner
# ---------------------------------------------------------------------------


async def _run_evolve_task(evolve_id: str) -> None:
    """Execute an evolution run in the background."""
    run = _evolve_runs.get(evolve_id)
    if run is None:
        return

    run["status"] = "running"
    try:
        settings_service = get_settings_service()
        config = {
            "generations": run["config"].get("generations", 5),
            "variants_per_gen": run["config"].get("variants_per_gen", 5),
            "selection_pressure": run["config"].get("selection_pressure", 0.5),
            "mutation_rate": run["config"].get("mutation_rate", 0.3),
        }
        result = await run_evolution(
            seed_strategy_id=run["seed_strategy_id"],
            config=config,
            settings_service=settings_service,
        )
        run["generations"] = result.get("generation_results", [])
        run["best_strategy"] = result.get("best_strategy", {})
        run["status"] = "completed"
    except Exception as exc:
        logger.warning("Evolution run %s failed: %s", evolve_id, exc, exc_info=True)
        run["status"] = "failed"
        run["error"] = str(exc)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("")
async def create_evolve(
    request: Request,
    body: CreateEvolveRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Create a new strategy evolution run."""
    evolve_id = f"evo-{uuid.uuid4().hex[:8]}"
    now = int(time.time() * 1000)

    run: dict[str, Any] = {
        "id": evolve_id,
        "seed_strategy_id": body.seed_strategy_id,
        "status": "pending",
        "config": {
            "generations": body.generations,
            "variants_per_gen": body.variants_per_gen,
            "selection_pressure": body.selection_pressure,
            "mutation_rate": body.mutation_rate,
        },
        "generations": [],
        "best_strategy": None,
        "created_at": now,
    }
    _evolve_runs[evolve_id] = run

    # Start evolution in background
    asyncio.create_task(_run_evolve_task(evolve_id))

    return APIResponse(
        data={"id": evolve_id, "status": "pending", "seed_strategy_id": body.seed_strategy_id},
        meta=build_response_meta(request),
        error=None,
    )


# ⚠️ MUST stay registered before GET /{evolve_id}, else "current" is captured as an evolve_id (404)
@router.get("/current")
async def get_current_evolution(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get the most recent evolution run for the qevolve research page."""
    runs = sorted(_evolve_runs.values(), key=lambda r: r.get("created_at", 0), reverse=True)
    if not runs:
        return APIResponse(data={}, meta=build_response_meta(request), error=None)
    run = runs[0]
    return APIResponse(
        data={
            "active_evolution": run["id"],
            "generation": len(run.get("generations", [])),
            "best_fitness": (run.get("best_strategy") or {}).get("sharpe_ratio", 0),
            "population_size": run.get("config", {}).get("variants_per_gen", 5),
        },
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/{evolve_id}")
async def get_evolve(
    evolve_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get evolution run status and results."""
    run = _evolve_runs.get(evolve_id)
    if run is None:
        raise NotFoundError(f"Evolution run '{evolve_id}' not found")

    return APIResponse(
        data=run,
        meta=build_response_meta(request),
        error=None,
    )


@router.delete("/{evolve_id}")
async def cancel_evolve(
    evolve_id: str,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Cancel an evolution run."""
    run = _evolve_runs.pop(evolve_id, None)
    if run is None:
        raise NotFoundError(f"Evolution run '{evolve_id}' not found")

    return APIResponse(
        data={"cancelled": True},
        meta=build_response_meta(request),
        error=None,
    )
