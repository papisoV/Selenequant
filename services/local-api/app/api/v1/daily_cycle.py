"""Daily Trading Cycle endpoints.

Exposes the 5-phase DailyTradingCycleWorkflow (Pre-Market / Risk Review /
IC Decision / Execution / Post-Market) absorbed from renaissance concept.

Workflow is instantiated per-request (stateless); providers are optional and
default to None (degraded mode). When renintel / agentstudio services are
available, they are wired into the IC Decision phase.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from selene_space_daily_cycle import (
    DailyTradingCycleWorkflow,
    Phase,
    WorkflowConfig,
    WorkflowPhase,
    WorkflowResult,
)
from selene_space_daily_cycle.ic_providers import (
    PersonaICDecisionProvider,
    RDLoopICDecisionProvider,
)
from selene_space_daily_cycle.phases import (
    ExecutionPhase,
    ICDecisionPhase,
    PostMarketPhase,
    PreMarketPhase,
    RiskReviewPhase,
)

from app.core.dependencies import (
    get_agentstudio_service,
    get_outcome_recorder,
    get_renintel_service,
)
from app.core.signal_grading import signal_grading

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/daily-cycle", tags=["daily-cycle"])


class PhaseInfo(BaseModel):
    name: str
    status: str
    disposition: str | None = None
    error: str | None = None
    metrics: dict[str, Any] = Field(default_factory=dict)
    artifacts: dict[str, Any] = Field(default_factory=dict)


class WorkflowResultResponse(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    workflow_id: str
    trading_date: str
    status: str
    halted_at: str | None = None
    started_at: str
    ended_at: str
    phases: list[PhaseInfo]


class RunRequest(BaseModel):
    trading_date: str
    config: dict[str, Any] | None = None
    ic_decision_provider: str = "persona"  # "persona" or "rd_loop"


def _build_workflow(req: RunRequest) -> DailyTradingCycleWorkflow:
    cfg = WorkflowConfig()
    if req.config:
        # Selectively override known fields; ignore unknown keys.
        if "ic_decision_provider" in req.config:
            ic_val = req.config["ic_decision_provider"]
            if ic_val in ("persona", "rd_loop"):
                cfg = WorkflowConfig(
                    trading_date_timezone=cfg.trading_date_timezone,
                    market_open_time=cfg.market_open_time,
                    phase_timeouts=cfg.phase_timeouts,
                    provider_configs=req.config.get(
                        "provider_configs", cfg.provider_configs
                    ),
                    ic_decision_provider=ic_val,  # type: ignore[arg-type]
                )
            else:
                raise ValueError(
                    f"ic_decision_provider must be 'persona' or 'rd_loop', got {ic_val!r}"
                )
        else:
            cfg = WorkflowConfig(
                trading_date_timezone=cfg.trading_date_timezone,
                market_open_time=cfg.market_open_time,
                phase_timeouts=cfg.phase_timeouts,
                provider_configs=req.config.get(
                    "provider_configs", cfg.provider_configs
                ),
                ic_decision_provider=req.ic_decision_provider,  # type: ignore[arg-type]
            )

    ic_provider_name = cfg.ic_decision_provider

    ic_provider: Any = None
    if ic_provider_name == "persona":
        renintel = get_renintel_service()
        if renintel is not None:
            from selene_space_renintel import RenIntelProviderConfig

            try:
                persona_provider = renintel.get_provider(
                    RenIntelProviderConfig(provider="personas")
                )
                ic_provider = PersonaICDecisionProvider(persona_provider)
            except Exception as exc:  # noqa: BLE001
                logger.warning("persona IC provider init failed: %s", exc)
    elif ic_provider_name == "rd_loop":
        agentstudio = get_agentstudio_service()
        if agentstudio is not None:
            from selene_space_agentstudio import AgentStudioProviderConfig

            try:
                rd_loop_provider = agentstudio.get_provider(
                    AgentStudioProviderConfig(provider="rd_loop")
                )
                ic_provider = RDLoopICDecisionProvider(rd_loop_provider)
            except Exception as exc:  # noqa: BLE001
                logger.warning("rd_loop IC provider init failed: %s", exc)

    phases: list[Phase] = [
        PreMarketPhase(cfg=cfg),
        RiskReviewPhase(),
        ICDecisionPhase(ic_provider=ic_provider),
        ExecutionPhase(),
        PostMarketPhase(outcome_recorder=get_outcome_recorder()),
    ]
    return DailyTradingCycleWorkflow(phases=phases, cfg=cfg)


def _result_to_response(result: WorkflowResult) -> WorkflowResultResponse:
    phase_infos: list[PhaseInfo] = []
    for o in result.phases:
        metrics = dict(o.metrics) if o.metrics else {}
        # 信号数分级(US-303):纯资源档位标注,不改任何决策语义。
        # 实战检验发现相位实际发的是 signals_in(=len(signals)),n_signals
        # 只是 US-303 测试约定的键 —— 两个都认,否则生产永远不触发。
        n_signals = metrics.get("n_signals", metrics.get("signals_in"))
        if n_signals is not None:
            metrics["signal_grade"] = signal_grading(int(n_signals))
            logger.info(
                "daily-cycle signal grading: phase=%s n_signals=%s grade=%s",
                o.phase.value,
                n_signals,
                metrics["signal_grade"],
            )
        phase_infos.append(
            PhaseInfo(
                name=o.phase.value,
                status=o.status.value,
                disposition=o.disposition,
                error=o.error,
                metrics=metrics,
                artifacts=dict(o.artifacts) if o.artifacts else {},
            )
        )
    return WorkflowResultResponse(
        workflow_id=result.workflow_id,
        trading_date=result.trading_date.isoformat(),
        status=result.status.value,
        halted_at=result.halted_at.value if result.halted_at else None,
        started_at=result.started_at.isoformat(),
        ended_at=result.ended_at.isoformat(),
        phases=phase_infos,
    )


@router.get("/info")
async def workflow_info() -> dict[str, Any]:
    """Return workflow metadata (provider name, kind, phase count)."""
    return {
        "provider": "DailyTradingCycleWorkflow",
        "kind": "OTHER",
        "phases": [p.value for p in WorkflowPhase],
        "ic_decision_providers": ["persona", "rd_loop"],
        "signal_grades": {
            "full_scan": "信号数低于阈值,全市场粗筛",
            "watchlist": "信号数达到阈值,只盯票池",
        },
    }


@router.post("/run", response_model=WorkflowResultResponse)
async def run_workflow(req: RunRequest) -> WorkflowResultResponse:
    """Run the 5-phase daily trading cycle for a trading date."""
    try:
        trading_date = date.fromisoformat(req.trading_date)
    except ValueError as exc:
        raise HTTPException(
            status_code=400, detail=f"invalid trading_date: {exc}"
        ) from exc

    try:
        workflow = _build_workflow(req)
        result = await workflow.run(trading_date)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except KeyError as exc:
        detail = exc.args[0] if exc.args else str(exc)
        raise HTTPException(status_code=404, detail=detail) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("daily-cycle run failed: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error") from exc

    return _result_to_response(result)


@router.get("/healthcheck")
async def healthcheck() -> dict[str, Any]:
    """Return healthcheck status (always healthy — workflow is stateless)."""
    return {
        "provider": "DailyTradingCycleWorkflow",
        "healthy": True,
        "is_running": True,
        "renintel_available": get_renintel_service() is not None,
        "agentstudio_available": get_agentstudio_service() is not None,
    }
