"""Phase 4: Execution -- send approved signals through paper engine.

Calls DecisionPipeline.process_signal() via asyncio.to_thread (sync->async).
Resolution (per plan lines 945-992):
1. process_signal(signal) -> PipelineResult (has order_id: str | None, NOT order)
2. If result.order_id is not None AND result.action == EXECUTED:
   Call PaperOrderManager.get_order(order_id) -> Order
3. Collect Orders into list

Output is ACCEPTED orders (not FILLED). Fills happen asynchronously later.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from selene_space_daily_cycle.observability import track_compute
from selene_space_daily_cycle.types import (
    ExecutionReport,
    PhaseOutcome,
    ResearchSignal,
    WorkflowContext,
    WorkflowPhase,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)


class ExecutionPhase:
    """Phase 4: execute approved signals via paper engine."""

    def __init__(
        self,
        pipeline: Any | None = None,
        order_manager: Any | None = None,
    ) -> None:
        self._pipeline = pipeline
        self._order_mgr = order_manager

    @property
    def name(self) -> WorkflowPhase:
        return WorkflowPhase.EXECUTION

    async def execute(
        self, trading_date: date, context: WorkflowContext
    ) -> PhaseOutcome:
        started_at = datetime.utcnow()
        with track_compute(
            provider="daily_cycle.execution",
            action="process_signals",
        ) as ctx:
            try:
                ic_report = context.artifacts.get("ic_decision")
                signals: list[ResearchSignal] = []
                if ic_report is not None and hasattr(ic_report, "approved"):
                    signals = list(ic_report.approved)
                else:
                    signals = list(context.signals)

                orders: list[Any] = []

                if self._pipeline is None or self._order_mgr is None:
                    # No pipeline -- skip execution (orders empty)
                    disposition = "skip_remaining" if not signals else "continue"
                else:
                    PipelineAction = getattr(
                        self._pipeline.__class__, "_PipelineAction", None
                    )
                    # Try to import PipelineAction from decision_pipeline module
                    try:
                        from selene_space_paper.decision_pipeline import PipelineAction  # type: ignore
                    except ImportError:
                        PipelineAction = None  # type: ignore

                    for rs in signals:
                        try:
                            sig = rs.signal if isinstance(rs, ResearchSignal) else rs
                            result = await asyncio.to_thread(
                                self._pipeline.process_signal, sig
                            )
                            order_id = getattr(result, "order_id", None)
                            action = getattr(result, "action", None)
                            # action may be PipelineAction.EXECUTED enum or string "executed"
                            action_val = (
                                action.value
                                if hasattr(action, "value")
                                else str(action).lower()
                            )
                            if order_id is not None and action_val == "executed":
                                order = self._order_mgr.get_order(order_id)
                                if order is not None:
                                    orders.append(order)
                        except Exception as exc:  # noqa: BLE001
                            logger.warning(
                                "execution.process_signal failed for %s: %s",
                                getattr(rs, "signal_id", "?"),
                                exc,
                            )
                    disposition = "continue" if orders else "halt_graceful"

                report = ExecutionReport(orders=orders)
                context.artifacts["execution"] = report
                context.orders = list(orders)

                ctx["success"] = True
                ctx["metrics_keys"] = ["orders_placed"]
            except Exception as exc:  # noqa: BLE001
                ctx["success"] = False
                ctx["error"] = str(exc)
                return PhaseOutcome(
                    phase=WorkflowPhase.EXECUTION,
                    status=WorkflowStatus.FAILED,
                    disposition="halt_urgent",
                    started_at=started_at,
                    ended_at=datetime.utcnow(),
                    error=str(exc),
                )

        return PhaseOutcome(
            phase=WorkflowPhase.EXECUTION,
            status=WorkflowStatus.SUCCESS,
            disposition=disposition,  # type: ignore[arg-type]
            started_at=started_at,
            ended_at=datetime.utcnow(),
            metrics={
                "signals_in": len(signals),
                "orders_placed": len(orders),
            },
            artifacts={"execution_report": report},
        )
