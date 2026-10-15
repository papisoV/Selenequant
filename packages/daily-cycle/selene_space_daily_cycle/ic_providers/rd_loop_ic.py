"""RDLoopICDecisionProvider -- wraps agentstudio rd_loop_provider for factor-based IC.

Adapter contract: maps ICDecisionProvider.decide() ->
rd_loop_provider.compute(*, action, task, params).

KNOWN LIMITATION: The adapter expects `report.metrics["ic_scores"]`
(dict of signal_id -> IC score), but the current rd_loop provider does
not produce this key. Until the rd_loop provider is enhanced to compute
IC scores (out of scope for batch 6c), `ic_scores` in the returned
ICDecisionReport will be empty ({}). All signals will be returned as
approved with their existing IC values unchanged.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from selene_space_daily_cycle.types import (
    ICDecisionReport,
    ResearchSignal,
)

logger = logging.getLogger(__name__)


class RDLoopICDecisionProvider:
    """Wraps agentstudio rd_loop_provider for factor-based IC decision."""

    def __init__(
        self,
        rd_loop_provider: Any,
        loop_name: str = "factor_loop",
    ) -> None:
        self._provider = rd_loop_provider
        self._loop_name = loop_name

    async def decide(
        self, trading_date: date, signals: list[ResearchSignal]
    ) -> ICDecisionReport:
        report = await self._provider.compute(
            action="run",
            task=f"factor loop for IC on {trading_date.isoformat()}",
            params={
                "workspace_dir": f"ic_decision_{trading_date.isoformat()}",
                "target_market": "US",
                "loop_n": 1,
            },
        )

        # KNOWN LIMITATION: ic_scores key not produced by current rd_loop provider
        # All signals approved as-is; ic_scores will be {} until rd_loop enhanced
        ic_scores: dict[str, float] = report.metrics.get("ic_scores", {})  # type: ignore[union-attr]
        approved: list[ResearchSignal] = []
        for sig in signals:
            ic_val = ic_scores.get(sig.signal_id, sig.ic)
            approved.append(
                ResearchSignal(
                    signal=sig.signal,
                    p_value=sig.p_value,
                    ic=ic_val,
                    signal_id=sig.signal_id,
                )
            )
        return ICDecisionReport(
            approved=approved,
            rejected=[],  # rd_loop does not reject signals
            ic_scores=ic_scores,
        )
