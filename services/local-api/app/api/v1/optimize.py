"""Optimize endpoints — RL training + portfolio optimization.

Exposes 8 providers: 5 RL (ppo/dqn/a2c/sac/td3) + 3 portfolio
(mean_variance/risk_parity/enhanced_index). RL providers lazy-import SB3.
"""

from __future__ import annotations

import logging
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.core.dependencies import get_optimize_service
from selene_space_optimize import OptimizeProviderConfig
from selene_space_optimize.registry import list_registered

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/optimize", tags=["optimize"])


class ProviderInfo(BaseModel):
    provider: str
    is_running: bool
    healthy: bool | None = None
    kind: str  # "rl" or "portfolio"


class ActionInfo(BaseModel):
    action: str
    description: str


class SeriesPayload(BaseModel):
    """Column-oriented series payload — same shape as analytics endpoint."""
    index: list[str] | None = None
    columns: dict[str, list[float]]


class OptimizeRequest(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    action: str
    prices: SeriesPayload | None = None
    returns: SeriesPayload | None = None
    weights: list[float] | None = None
    benchmark: SeriesPayload | None = None
    params: dict[str, Any] | None = None


class OptimizeResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    kind: str
    title: str
    metrics: dict[str, Any]
    data: dict[str, Any] | None = None
    artifacts: dict[str, Any] | None = None
    computed_at: str
    notes: str | None = None


_RL_PROVIDERS = {"ppo", "dqn", "a2c", "sac", "td3"}


def _series_from_payload(p: SeriesPayload | None) -> pd.Series | pd.DataFrame | None:
    if p is None:
        return None
    df = pd.DataFrame(p.columns)
    if p.index is not None:
        try:
            df.index = pd.to_datetime(p.index)
        except (ValueError, TypeError):
            df.index = p.index
    if len(df.columns) == 1:
        return df.iloc[:, 0]
    return df


def _df_to_dict(df: pd.DataFrame | pd.Series | None) -> dict[str, Any] | None:
    if df is None:
        return None
    try:
        if isinstance(df, pd.Series):
            df = df.to_frame("value")
        obj = df.to_dict(orient="split")
        return {
            "columns": list(obj.get("columns", [])),
            "index": [str(i) for i in obj.get("index", [])],
            "data": [list(row) for row in obj.get("data", [])],
        }
    except Exception as exc:  # noqa: BLE001
        logger.warning("df_to_dict failed: %s", exc)
        return None


def _get_provider(provider: str) -> Any:
    cfg = OptimizeProviderConfig(provider=provider)
    service = get_optimize_service()
    if service is None:
        raise HTTPException(status_code=503, detail="optimize service not initialized")
    return service.get_provider(cfg)


@router.get("/providers", response_model=list[ProviderInfo])
async def list_providers() -> list[ProviderInfo]:
    """List all registered optimize providers (RL + portfolio)."""
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
                kind="rl" if name in _RL_PROVIDERS else "portfolio",
            ))
        except HTTPException:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.warning("provider %s lookup failed: %s", name, exc)
            out.append(ProviderInfo(provider=name, is_running=False, healthy=False, kind="rl" if name in _RL_PROVIDERS else "portfolio"))
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


@router.post("/report", response_model=OptimizeResponse)
async def compute_report(req: OptimizeRequest) -> OptimizeResponse:
    """Compute an optimization report (RL train/predict or portfolio optimize).

    Same shape as /analytics/report — provider + action + payload + params.
    Routes through OptimizeService.compute() for observability tracking
    (structured logging + best-effort MLflow).
    """
    service = get_optimize_service()
    if service is None:
        raise HTTPException(status_code=503, detail="optimize service not initialized")
    cfg = OptimizeProviderConfig(provider=req.provider)
    try:
        prices = _series_from_payload(req.prices)
        returns = _series_from_payload(req.returns)
        benchmark = _series_from_payload(req.benchmark)
        report = await service.compute(
            cfg,
            action=req.action,
            returns=returns,
            prices=prices,
            weights=req.weights,
            benchmark=benchmark,
            params=req.params,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("optimize %s/%s failed: %s", req.provider, req.action, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return OptimizeResponse(
        provider=report.provider,
        kind=report.kind.value,
        title=report.title,
        metrics=report.metrics,
        data=_df_to_dict(report.data),
        artifacts=report.artifacts,
        computed_at=report.computed_at.isoformat(),
        notes=report.notes,
    )


class SeriesPayloadBody(BaseModel):
    """Wrapper body for convenience endpoints — {prices: SeriesPayload, params: ...}."""
    prices: SeriesPayload
    params: dict[str, Any] | None = None
    benchmark: SeriesPayload | None = None


@router.post("/rl/train", response_model=OptimizeResponse)
async def rl_train(
    provider: str,
    body: SeriesPayloadBody,
    total_timesteps: int = 5000,
    window: int = 20,
    cost_bps: float = 1.0,
) -> OptimizeResponse:
    """Convenience: train an RL agent (provider=ppo/dqn/a2c/sac/td3)."""
    if provider not in _RL_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"{provider} is not an RL provider")
    return await compute_report(OptimizeRequest(
        provider=provider,
        action="train",
        prices=body.prices,
        params={"total_timesteps": total_timesteps, "window": window, "cost_bps": cost_bps},
    ))


@router.post("/portfolio/optimize", response_model=OptimizeResponse)
async def portfolio_optimize(
    provider: str,
    action: str,
    body: SeriesPayloadBody,
) -> OptimizeResponse:
    """Convenience: run portfolio optimization (mean_variance/risk_parity/enhanced_index)."""
    if provider in _RL_PROVIDERS:
        raise HTTPException(status_code=400, detail=f"{provider} is an RL provider, not portfolio")
    return await compute_report(OptimizeRequest(
        provider=provider,
        action=action,
        prices=body.prices,
        benchmark=body.benchmark,
        params=body.params,
    ))
