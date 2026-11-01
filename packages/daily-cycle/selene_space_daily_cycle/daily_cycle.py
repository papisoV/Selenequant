"""DailyTradingCycleWorkflow -- 5-phase daily trading cycle orchestrator.

Native selene implementation of renaissance DailyTradingCycleWorkflow concept.
Does NOT import agno; uses selene-native Protocol + observability.

Design decisions:
- Phases are injectable via constructor (not hardcoded) -> CI/CD/unit tests
  can swap individual phases
- Phase ordering validated at __init__: phases must be in canonical subset
  order, ValueError if violated
- Disposition semantics: halt_urgent -> break, halt_graceful -> flag but
  continue, skip_remaining -> skip downstream, continue -> proceed
- WorkflowStatus.HALTED when any phase disposition is halt_* (NOT SUCCESS)
- halted_at field records which phase triggered the halt
- asyncio.wait_for enforces per-phase timeouts from WorkflowConfig
- track_compute uses ctx-dict API (ctx["success"] = True, NOT .log())
- Step.depends_on (renaissance) rejected -> explicit WorkflowContext instead
- agno Workflow.run sync rejected -> async/await throughout
- agno Config rejected -> native WorkflowConfig dataclass
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime

import structlog

from selene_space_daily_cycle.observability import track_compute
from selene_space_daily_cycle.types import (
    Phase,
    PhaseOutcome,
    WorkflowConfig,
    WorkflowContext,
    WorkflowPhase,
    WorkflowResult,
    WorkflowStatus,
)

logger = structlog.get_logger(__name__)

# Canonical phase order for validation
_CANONICAL_ORDER: list[WorkflowPhase] = [
    WorkflowPhase.PRE_MARKET,
    WorkflowPhase.RISK_REVIEW,
    WorkflowPhase.IC_DECISION,
    WorkflowPhase.EXECUTION,
    WorkflowPhase.POST_MARKET,
]


class DailyTradingCycleWorkflow:
    """5-phase daily trading cycle orchestrator."""

    def __init__(
        self,
        phases: list[Phase],
        cfg: WorkflowConfig | None = None,
    ) -> None:
        if len(phases) < 1:
            raise ValueError("At least one phase is required")

        # Validate phase ordering: phases must appear in canonical order
        seen_indices: list[int] = []
        for phase in phases:
            if phase.name not in _CANONICAL_ORDER:
                raise ValueError(
                    f"Unknown phase {phase.name!r}; "
                    f"must be one of {[p.value for p in _CANONICAL_ORDER]}"
                )
            idx = _CANONICAL_ORDER.index(phase.name)
            if seen_indices and idx <= seen_indices[-1]:
                raise ValueError(
                    f"Phases must be in canonical order; "
                    f"got {[p.name.value for p in phases]}, "
                    f"expected subset of {[p.value for p in _CANONICAL_ORDER]}"
                )
            seen_indices.append(idx)

        self._phases = phases
        self._cfg = cfg or WorkflowConfig()

    async def run(self, trading_date: date) -> WorkflowResult:
        workflow_id = f"daily_cycle_{trading_date.isoformat()}_{uuid.uuid4().hex[:8]}"
        context = WorkflowContext()
        outcomes: list[PhaseOutcome] = []
        started_at = datetime.utcnow()
        halted_at: WorkflowPhase | None = None

        for phase in self._phases:
            with track_compute(
                provider=f"workflow.{workflow_id}",
                action=f"phase.{phase.name.value}",
            ) as ctx:
                try:
                    timeout = self._cfg.phase_timeouts.get(phase.name)
                    if timeout is not None:
                        outcome = await asyncio.wait_for(
                            phase.execute(trading_date, context),
                            timeout=timeout,
                        )
                    else:
                        outcome = await phase.execute(trading_date, context)
                    ctx["success"] = outcome.status == WorkflowStatus.SUCCESS
                    ctx["metrics_keys"] = list(outcome.metrics.keys())
                except asyncio.TimeoutError:
                    outcome = PhaseOutcome(
                        phase=phase.name,
                        status=WorkflowStatus.FAILED,
                        disposition="halt_urgent",
                        error=f"Phase timed out after {timeout}s",
                    )
                    ctx["success"] = False
                    ctx["error"] = outcome.error
                    ctx["metrics_keys"] = []
                except Exception as exc:  # noqa: BLE001
                    outcome = PhaseOutcome(
                        phase=phase.name,
                        status=WorkflowStatus.FAILED,
                        disposition="halt_urgent",
                        error=str(exc),
                    )
                    ctx["success"] = False
                    ctx["error"] = str(exc)
                    ctx["metrics_keys"] = []

                outcomes.append(outcome)
                logger.info(
                    "daily_cycle.phase_complete",
                    phase=phase.name.value,
                    status=outcome.status.value,
                    disposition=outcome.disposition,
                )

            # Respect disposition
            if outcome.disposition == "halt_urgent":
                halted_at = phase.name
                break
            elif outcome.disposition == "skip_remaining":
                # Mark remaining phases as SKIPPED
                current_idx = self._phases.index(phase)
                remaining = self._phases[current_idx + 1:]
                for skipped in remaining:
                    outcomes.append(
                        PhaseOutcome(
                            phase=skipped.name,
                            status=WorkflowStatus.SKIPPED,
                            disposition="skip_remaining",
                        )
                    )
                break
            # "continue" and "halt_graceful" proceed to next iteration
            # halt_graceful: downstream phases still execute, but final status is HALTED

        # Determine final status: HALTED if any phase had halt_* disposition
        halted_dispositions = {"halt_graceful", "halt_urgent"}
        any_halted = any(o.disposition in halted_dispositions for o in outcomes)

        if any_halted:
            final_status = WorkflowStatus.HALTED
        else:
            all_success = all(
                o.status in (WorkflowStatus.SUCCESS, WorkflowStatus.SKIPPED)
                for o in outcomes
            )
            final_status = WorkflowStatus.SUCCESS if all_success else WorkflowStatus.FAILED

        return WorkflowResult(
            workflow_id=workflow_id,
            trading_date=trading_date,
            phases=outcomes,
            started_at=started_at,
            ended_at=datetime.utcnow(),
            status=final_status,
            halted_at=halted_at,
        )
