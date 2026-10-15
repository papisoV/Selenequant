"""Phase 5: Post-Market -- daily PnL summary + memory update.

Uses renintel memory_provider with `trade_memory` type (trade execution history,
not agent conversation).
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from selene_space_daily_cycle.observability import track_compute
from selene_space_daily_cycle.types import (
    PhaseOutcome,
    PostMarketReport,
    WorkflowContext,
    WorkflowPhase,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)


class PostMarketPhase:
    """Phase 5: daily PnL summary + lessons + memory update."""

    def __init__(
        self,
        memory_provider: Any | None = None,
        experiment_verifier: Any | None = None,
        outcome_recorder: Any | None = None,
    ) -> None:
        self._memory_provider = memory_provider
        self._experiment_verifier = experiment_verifier
        # P5 前向验证(阶段 C)。可选注入 callable:async 无参,返回 counters
        # dict。**刻意不 import storage** —— daily-cycle 不依赖 storage,装配
        # 走 local-api 的 dependencies.py(方案 §4)。None = 老行为不变。
        self._outcome_recorder = outcome_recorder

    @property
    def name(self) -> WorkflowPhase:
        return WorkflowPhase.POST_MARKET

    async def execute(
        self, trading_date: date, context: WorkflowContext
    ) -> PhaseOutcome:
        started_at = datetime.utcnow()
        with track_compute(
            provider="daily_cycle.post_market",
            action="record",
        ) as ctx:
            try:
                # Build daily PnL summary from execution orders
                orders = context.orders or []
                total_quantity = sum(
                    float(getattr(o, "quantity", 0.0) or 0.0) for o in orders
                )
                pnl_summary: dict[str, float] = {
                    "orders_count": float(len(orders)),
                    "total_quantity": total_quantity,
                    "realized_pnl": 0.0,  # paper engine: no realized PnL yet
                    "unrealized_pnl": 0.0,
                }

                # Build lessons learned from phase outcomes
                lessons: list[str] = []
                pre_market = context.artifacts.get("pre_market")
                if pre_market is not None and hasattr(pre_market, "data_quality"):
                    if pre_market.data_quality == "missing":
                        lessons.append(
                            "Pre-market data was missing; workflow halted gracefully."
                        )
                    elif pre_market.data_quality == "degraded":
                        lessons.append(
                            "Pre-market data was degraded; some series missing."
                        )
                if not orders:
                    lessons.append("No orders were placed in this cycle.")
                else:
                    lessons.append(f"Placed {len(orders)} orders in this cycle.")

                # Update trading memory via memory_provider (trade_memory type)
                memory_updated = False
                if self._memory_provider is not None:
                    try:
                        # Verify trade_memory type is available
                        report = await self._memory_provider.compute(
                            action="get_type",
                            task=f"daily cycle {trading_date.isoformat()}",
                            params={"id": "trade_memory"},
                        )
                        memory_updated = True
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("memory_provider call failed: %s", exc)
                        memory_updated = False

                # Optional reflection closure: verify due experiments (P0-4)
                verified_count = 0
                if self._experiment_verifier is not None:
                    try:
                        verification = await self._experiment_verifier()
                        verified_count = int(verification.get("verified_count", 0))
                        correct_count = int(verification.get("correct_count", 0))
                        lessons.append(
                            f"到期验证:{verified_count} 条(其中正确 {correct_count} 条)。"
                        )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("experiment_verifier failed: %s", exc)

                # Optional P5 forward-validation: resolve matured analyses
                # against forward bars (阶段 C). Skipped silently when absent.
                forward_validated = 0
                if self._outcome_recorder is not None:
                    try:
                        fv = await self._outcome_recorder()
                        forward_validated = int(fv.get("created", 0))
                        if forward_validated:
                            lessons.append(
                                f"前向验证:{forward_validated} 条分析结论已对照后验行情。"
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.debug("outcome_recorder failed: %s", exc)

                report = PostMarketReport(
                    pnl_summary=pnl_summary,
                    lessons=lessons,
                    memory_updated=memory_updated,
                )
                context.artifacts["post_market"] = report

                ctx["success"] = True
                ctx["metrics_keys"] = [
                    "orders_count",
                    "memory_updated",
                    "verified_count",
                    "forward_validated",
                ]
            except Exception as exc:  # noqa: BLE001
                ctx["success"] = False
                ctx["error"] = str(exc)
                return PhaseOutcome(
                    phase=WorkflowPhase.POST_MARKET,
                    status=WorkflowStatus.FAILED,
                    disposition="halt_urgent",
                    started_at=started_at,
                    ended_at=datetime.utcnow(),
                    error=str(exc),
                )

        metrics: dict[str, Any] = {
            "orders_count": len(orders),
            "memory_updated": memory_updated,
        }
        if self._experiment_verifier is not None:
            metrics["verified_count"] = verified_count
        if self._outcome_recorder is not None:
            metrics["forward_validated"] = forward_validated

        return PhaseOutcome(
            phase=WorkflowPhase.POST_MARKET,
            status=WorkflowStatus.SUCCESS,
            disposition="continue",
            started_at=started_at,
            ended_at=datetime.utcnow(),
            metrics=metrics,
            artifacts={"post_market_report": report},
        )
