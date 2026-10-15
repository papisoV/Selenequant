"""AgentStudio endpoints.

Exposes 8 providers (7 R&D loops + 1 prompt library) for multi-agent R&D
automation and domain prompt packs. Inspired by a commercial analytics
terminal's agent system (clean-room reimplementation, no source code copied).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.core.dependencies import get_agentstudio_service
from selene_space_agentstudio import AgentStudioProviderConfig
from selene_space_agentstudio.registry import list_registered

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/agentstudio", tags=["agentstudio"])

# R&D loop providers (vs prompt library)
_RD_LOOP_PROVIDERS = {
    "factor_loop", "model_loop", "quant_loop", "report_loop",
    "kaggle_loop", "ds_loop", "multitrace_loop",
}


class ProviderInfo(BaseModel):
    provider: str
    is_running: bool
    healthy: bool | None = None
    kind: str  # "rd_loop" or "prompt_library"


class ActionInfo(BaseModel):
    action: str
    description: str


class AgentStudioRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    action: str
    task: str | None = None
    params: dict[str, Any] | None = None


class AgentStudioResponse(BaseModel):
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
    cfg = AgentStudioProviderConfig(provider=provider)
    service = get_agentstudio_service()
    if service is None:
        raise HTTPException(status_code=503, detail="agentstudio service not initialized")
    return service.get_provider(cfg)


def _kind_of(name: str) -> str:
    return "rd_loop" if name in _RD_LOOP_PROVIDERS else "prompt_library"


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    """List all registered agentstudio providers (7 R&D loops + 1 prompt library)."""
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


@router.post("/report", response_model=AgentStudioResponse)
async def compute_report(req: AgentStudioRequest) -> AgentStudioResponse:
    """Compute an agentstudio report (R&D loop build/run or prompt library query).

    Routes through AgentStudioService.compute() for observability tracking
    (structured logging + best-effort MLflow).
    """
    service = get_agentstudio_service()
    if service is None:
        raise HTTPException(status_code=503, detail="agentstudio service not initialized")
    cfg = AgentStudioProviderConfig(provider=req.provider)
    try:
        report = await service.compute(
            cfg,
            action=req.action,
            task=req.task,
            params=req.params,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("agentstudio %s/%s failed: %s", req.provider, req.action, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return AgentStudioResponse(
        provider=report.provider,
        kind=report.kind.value,
        title=report.title,
        metrics=report.metrics,
        data=None,  # agentstudio reports use artifacts, not dataframes
        artifacts=report.artifacts,
        computed_at=report.computed_at.isoformat(),
        notes=report.notes,
    )


# ---------------------------------------------------------------------------
# Convenience endpoints
# ---------------------------------------------------------------------------


class PromptPackBody(BaseModel):
    """Body for /prompt-pack endpoint."""
    pack: str = "economic"


@router.post("/prompt-pack", response_model=AgentStudioResponse)
async def get_prompt_pack(body: PromptPackBody) -> AgentStudioResponse:
    """Convenience endpoint: get a prompt pack's full agent definitions."""
    return await compute_report(AgentStudioRequest(
        provider="prompt_library",
        action="get_pack",
        params={"pack": body.pack},
    ))


@router.get("/prompt-packs", response_model=list[dict[str, Any]])
async def list_prompt_packs() -> list[dict[str, Any]]:
    """List all 4 prompt packs with agent counts (lightweight, no compute)."""
    service = get_agentstudio_service()
    if service is None:
        raise HTTPException(status_code=503, detail="agentstudio service not initialized")
    cfg = AgentStudioProviderConfig(provider="prompt_library")
    report = await service.compute(cfg, action="list_packs")
    packs = report.artifacts.get("packs", [])
    return packs if isinstance(packs, list) else []
