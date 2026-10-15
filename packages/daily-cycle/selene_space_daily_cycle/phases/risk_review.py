"""Phase 2: Risk Review -- portfolio risk assessment via risk-engine.

Calls RiskEngine.pre_check() via asyncio.to_thread (sync->async adapter).
Constructs a synthetic TradeIntent from PreMarketReport fields per plan
lines 766-823.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime
from typing import Any

from selene_space_daily_cycle.observability import track_compute
from selene_space_daily_cycle.types import (
    PhaseOutcome,
    PreMarketReport,
    RiskReviewReport,
    WorkflowContext,
    WorkflowPhase,
    WorkflowStatus,
)
from selene_space_types.agent import TradeIntent
from selene_space_types.common import Symbol
from selene_space_types.trading import OrderSide, OrderType

logger = logging.getLogger(__name__)


class RiskReviewPhase:
    """Phase 2: risk assessment via risk-engine provider."""

    def __init__(
        self,
        risk_engine: Any | None = None,
        representative_symbol: str = "BTC/USDT",
    ) -> None:
        self._risk_engine = risk_engine
        self._representative_symbol = representative_symbol

    @property
    def name(self) -> WorkflowPhase:
        return WorkflowPhase.RISK_REVIEW

    async def execute(
        self, trading_date: date, context: WorkflowContext
    ) -> PhaseOutcome:
        started_at = datetime.utcnow()
        with track_compute(
            provider="daily_cycle.risk_review",
            action="pre_check",
        ) as ctx:
            try:
                pre_market: PreMarketReport | None = context.artifacts.get(
                    "pre_market"
                )
                if pre_market is None:
                    # No pre_market data -- halt gracefully
                    ctx["success"] = True
                    ctx["metrics_keys"] = ["pre_market_missing"]
                    report = RiskReviewReport(
                        var_estimate=0.0,
                        position_limits_ok=False,
                        kill_switch_triggered=False,
                    )
                    context.artifacts["risk_review"] = report
                    return PhaseOutcome(
                        phase=WorkflowPhase.RISK_REVIEW,
                        status=WorkflowStatus.SUCCESS,
                        disposition="halt_graceful",
                        started_at=started_at,
                        ended_at=datetime.utcnow(),
                        metrics={"pre_market_missing": True},
                        artifacts={"risk_review_report": report},
                    )

                # Build synthetic TradeIntent for risk scanning
                macro_summary = ", ".join(
                    f"{k}={v:.2f}" for k, v in pre_market.macro_indicators.items()
                )
                avg_strength = 0.5
                if pre_market.overnight_signals:
                    strengths = [
                        getattr(s, "strength", None)
                        for s in pre_market.overnight_signals
                    ]
                    valid = [s for s in strengths if isinstance(s, (int, float))]
                    if valid:
                        avg_strength = sum(valid) / len(valid)

                intent = TradeIntent(
                    symbol=Symbol(self._representative_symbol),
                    side=OrderSide.BUY,
                    quantity=0.01,
                    price=None,
                    order_type=OrderType.MARKET,
                    reasoning=f"Pre-market risk scan: {macro_summary}",
                    confidence=avg_strength,
                    risk_params={},
                    timestamp=int(time.time() * 1000),
                )

                risk_context = {
                    "strategy_id": "daily_cycle_risk_review",
                    "positions": context.artifacts.get("positions", {}),
                    "trading_date": trading_date.isoformat(),
                }

                var_estimate = 0.0
                position_limits_ok = True
                kill_switch_triggered = False

                if self._risk_engine is not None:
                    risk_decision = await asyncio.to_thread(
                        self._risk_engine.pre_check, intent, risk_context
                    )
                    # RiskDecision has: allowed (bool), alerts (list), reason (str)
                    position_limits_ok = bool(getattr(risk_decision, "allowed", True))
                    alerts = getattr(risk_decision, "alerts", []) or []
                    kill_switch_triggered = any(
                        "kill" in str(getattr(a, "rule_id", "")).lower()
                        or "kill" in str(getattr(a, "level", "")).lower()
                        for a in alerts
                    )
                    var_estimate = float(
                        getattr(risk_decision, "var_estimate", 0.0) or 0.0
                    )
                else:
                    # No risk engine -- use safe defaults
                    position_limits_ok = True
                    kill_switch_triggered = False

                report = RiskReviewReport(
                    var_estimate=var_estimate,
                    position_limits_ok=position_limits_ok,
                    kill_switch_triggered=kill_switch_triggered,
                )
                context.artifacts["risk_review"] = report

                disposition = (
                    "halt_urgent"
                    if kill_switch_triggered
                    else ("halt_graceful" if not position_limits_ok else "continue")
                )
                ctx["success"] = True
                ctx["metrics_keys"] = [
                    "var_estimate",
                    "position_limits_ok",
                    "kill_switch_triggered",
                ]
            except Exception as exc:  # noqa: BLE001
                ctx["success"] = False
                ctx["error"] = str(exc)
                return PhaseOutcome(
                    phase=WorkflowPhase.RISK_REVIEW,
                    status=WorkflowStatus.FAILED,
                    disposition="halt_urgent",
                    started_at=started_at,
                    ended_at=datetime.utcnow(),
                    error=str(exc),
                )

        return PhaseOutcome(
            phase=WorkflowPhase.RISK_REVIEW,
            status=WorkflowStatus.SUCCESS,
            disposition=disposition,  # type: ignore[arg-type]
            started_at=started_at,
            ended_at=datetime.utcnow(),
            metrics={
                "var_estimate": var_estimate,
                "position_limits_ok": position_limits_ok,
                "kill_switch_triggered": kill_switch_triggered,
            },
            artifacts={"risk_review_report": report},
        )
