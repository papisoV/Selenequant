"""Phase 3: IC Decision -- Investment Committee deliberation.

Uses ICDecisionProvider Protocol. Runtime choice between:
- PersonaICDecisionProvider (renintel persona_provider, calls ic_deliberation action)
- RDLoopICDecisionProvider (agentstudio rd_loop_provider)
"""

from __future__ import annotations

import logging
from datetime import date, datetime

from selene_space_daily_cycle.observability import track_compute
from selene_space_daily_cycle.types import (
    ICDecisionReport,
    ICDecisionProvider,
    PhaseOutcome,
    ResearchSignal,
    WorkflowContext,
    WorkflowPhase,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)


class ICDecisionPhase:
    """Phase 3: IC deliberation via ICDecisionProvider Protocol."""

    def __init__(self, ic_provider: ICDecisionProvider | None = None) -> None:
        self._ic_provider = ic_provider

    @property
    def name(self) -> WorkflowPhase:
        return WorkflowPhase.IC_DECISION

    async def execute(
        self, trading_date: date, context: WorkflowContext
    ) -> PhaseOutcome:
        started_at = datetime.utcnow()
        with track_compute(
            provider="daily_cycle.ic_decision",
            action="decide",
        ) as ctx:
            try:
                # Signals come from context (or pre_market overnight_signals)
                signals: list[ResearchSignal] = list(context.signals)
                # If context has no signals, try pre_market.overnight_signals
                if not signals:
                    pre_market = context.artifacts.get("pre_market")
                    if pre_market is not None and pre_market.overnight_signals:
                        signals = list(pre_market.overnight_signals)

                if self._ic_provider is None:
                    # No IC provider -- pass through all signals as approved
                    report = ICDecisionReport(
                        approved=signals,
                        rejected=[],
                        ic_scores={},
                    )
                    disposition = "continue" if signals else "halt_graceful"
                    ctx["success"] = True
                    ctx["metrics_keys"] = ["approved_count", "rejected_count"]
                    context.artifacts["ic_decision"] = report
                    return PhaseOutcome(
                        phase=WorkflowPhase.IC_DECISION,
                        status=WorkflowStatus.SUCCESS,
                        disposition=disposition,  # type: ignore[arg-type]
                        started_at=started_at,
                        ended_at=datetime.utcnow(),
                        metrics={
                            "signals_in": len(signals),
                            "approved": len(report.approved),
                            "rejected": len(report.rejected),
                        },
                        artifacts={"ic_decision_report": report},
                    )

                report = await self._ic_provider.decide(trading_date, signals)
                context.artifacts["ic_decision"] = report
                # Approved signals stored back to context for execution phase
                context.signals = list(report.approved)

                disposition = "halt_graceful" if not report.approved else "continue"
                ctx["success"] = True
                ctx["metrics_keys"] = ["approved_count", "rejected_count", "ic_scores_count"]
            except Exception as exc:  # noqa: BLE001
                ctx["success"] = False
                ctx["error"] = str(exc)
                return PhaseOutcome(
                    phase=WorkflowPhase.IC_DECISION,
                    status=WorkflowStatus.FAILED,
                    disposition="halt_urgent",
                    started_at=started_at,
                    ended_at=datetime.utcnow(),
                    error=str(exc),
                )

        return PhaseOutcome(
            phase=WorkflowPhase.IC_DECISION,
            status=WorkflowStatus.SUCCESS,
            disposition=disposition,  # type: ignore[arg-type]
            started_at=started_at,
            ended_at=datetime.utcnow(),
            metrics={
                "signals_in": len(signals),
                "approved": len(report.approved),
                "rejected": len(report.rejected),
            },
            artifacts={"ic_decision_report": report},
        )
