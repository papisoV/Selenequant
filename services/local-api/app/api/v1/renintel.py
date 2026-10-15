"""RenIntel (Renaissance) endpoints.

Exposes 5 providers (personas / reasoning / guardrails / strategies / memory)
inspired by a hedge-fund persona agent pattern (clean-room reimplementation,
no source code copied).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.core.dependencies import get_renintel_service
from selene_space_renintel import RenIntelProviderConfig
from selene_space_renintel.registry import list_registered

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/renintel", tags=["renintel"])


class ProviderInfo(BaseModel):
    provider: str
    is_running: bool
    healthy: bool | None = None
    kind: str  # personas / reasoning / guardrails / strategies / memory


class ActionInfo(BaseModel):
    action: str
    description: str


class RenIntelRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    action: str
    task: str | None = None
    params: dict[str, Any] | None = None


class RenIntelResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    kind: str
    title: str
    metrics: dict[str, Any]
    data: dict[str, Any] | None = None
    artifacts: dict[str, Any] | None = None
    computed_at: str
    notes: str | None = None


def _get_provider(provider: str) -> Any:
    cfg = RenIntelProviderConfig(provider=provider)
    service = get_renintel_service()
    if service is None:
        raise HTTPException(status_code=503, detail="renintel service not initialized")
    return service.get_provider(cfg)


def _kind_of(name: str) -> str:
    return name  # provider id IS the kind


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    """List all registered renintel providers (5: personas/reasoning/guardrails/strategies/memory)."""
    out: list[ProviderInfo] = []
    for name in list_registered():
        try:
            p = _get_provider(name)
            healthy = None
            try:
                if p.is_running:
                    healthy = await p.healthcheck()
            except Exception as exc:  # noqa: BLE001
                logger.warning("healthcheck %s failed: %s", name, exc)
            out.append(ProviderInfo(
                provider=name,
                is_running=p.is_running,
                healthy=healthy,
                kind=_kind_of(name),
            ))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("provider %s lookup failed: %s", name, exc)
            out.append(ProviderInfo(
                provider=name,
                is_running=False,
                healthy=False,
                kind=_kind_of(name),
            ))
    return out


@router.get("/{provider}/actions", response_model=list[ActionInfo])
async def list_actions(provider: str) -> list[ActionInfo]:
    """List a provider's available actions."""
    p = _get_provider(provider)
    return [ActionInfo(action=a, description=p.describe_action(a)) for a in p.list_actions()]


@router.get("/{provider}/healthcheck")
async def healthcheck(provider: str) -> dict[str, Any]:
    """Provider health check."""
    p = _get_provider(provider)
    if not p.is_running:
        await p.start()
    ok = await p.healthcheck()
    return {"provider": provider, "healthy": ok, "is_running": p.is_running}


@router.post("/report", response_model=RenIntelResponse)
async def compute_report(req: RenIntelRequest) -> RenIntelResponse:
    """Compute a renintel report (list_X / get_X / info across the 5 libraries).

    Routes through RenIntelService.compute() for observability tracking
    (structured logging + best-effort MLflow).
    """
    service = get_renintel_service()
    if service is None:
        raise HTTPException(status_code=503, detail="renintel service not initialized")
    cfg = RenIntelProviderConfig(provider=req.provider)
    try:
        report = await service.compute(
            cfg,
            action=req.action,
            task=req.task,
            params=req.params,
        )
    except KeyError as exc:
        # KeyError's str() wraps message in quotes — use args[0] for clean text
        detail = exc.args[0] if exc.args else str(exc)
        raise HTTPException(status_code=404, detail=detail) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("renintel %s/%s failed: %s", req.provider, req.action, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return RenIntelResponse(
        provider=report.provider,
        kind=report.kind.value,
        title=report.title,
        metrics=report.metrics,
        data=None,
        artifacts=report.artifacts,
        computed_at=report.computed_at.isoformat(),
        notes=report.notes,
    )


# ---------------------------------------------------------------------------
# Convenience endpoints
# ---------------------------------------------------------------------------


class ListBody(BaseModel):
    """Body for convenience list endpoints."""


@router.get("/personas", response_model=RenIntelResponse)
async def list_personas() -> RenIntelResponse:
    """Convenience: list all 12 Renaissance personas."""
    return await compute_report(RenIntelRequest(
        provider="personas",
        action="list_personas",
    ))


@router.get("/strategies", response_model=RenIntelResponse)
async def list_strategies() -> RenIntelResponse:
    """Convenience: list all 6 Renaissance quant strategies."""
    return await compute_report(RenIntelRequest(
        provider="strategies",
        action="list_strategies",
    ))


@router.get("/guardrails", response_model=RenIntelResponse)
async def list_guardrails() -> RenIntelResponse:
    """Convenience: list all 4 guardrail categories."""
    return await compute_report(RenIntelRequest(
        provider="guardrails",
        action="list_guardrails",
    ))


@router.get("/reasoning", response_model=RenIntelResponse)
async def list_reasoning() -> RenIntelResponse:
    """Convenience: list all 4 reasoning patterns."""
    return await compute_report(RenIntelRequest(
        provider="reasoning",
        action="list_patterns",
    ))


@router.get("/memory", response_model=RenIntelResponse)
async def list_memory() -> RenIntelResponse:
    """Convenience: list all 5 memory types."""
    return await compute_report(RenIntelRequest(
        provider="memory",
        action="list_types",
    ))
