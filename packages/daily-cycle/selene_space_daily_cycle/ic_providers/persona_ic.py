"""PersonaICDecisionProvider -- wraps renintel persona_provider for IC deliberation.

Adapter contract: maps ICDecisionProvider.decide() ->
persona_provider.compute(*, action="ic_deliberation", task, params).

The `ic_deliberation` action was added to PersonaProvider in batch 6c.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from selene_space_daily_cycle.types import (
    ICDecisionReport,
    ResearchSignal,
)
from selene_space_types.strategy import Signal

logger = logging.getLogger(__name__)


class PersonaICDecisionProvider:
    """Wraps renintel persona_provider for IC deliberation."""

    def __init__(
        self,
        persona_provider: Any,
        persona_name: str = "investment_committee",
    ) -> None:
        self._provider = persona_provider
        self._persona_name = persona_name

    async def decide(
        self, trading_date: date, signals: list[ResearchSignal]
    ) -> ICDecisionReport:
        report = await self._provider.compute(
            action="ic_deliberation",
            task=f"review {len(signals)} signals for {trading_date.isoformat()}",
            params={
                "trading_date": str(trading_date),
                "signals": [self._signal_to_dict(s) for s in signals],
            },
        )

        decisions: list[dict[str, Any]] = report.artifacts.get("decision", [])
        approved: list[ResearchSignal] = []
        rejected: list[ResearchSignal] = []
        ic_scores: dict[str, float] = {}

        # Map approved decisions back to ResearchSignal objects
        # Use signal_id to find the original ResearchSignal
        sig_by_id = {s.signal_id: s for s in signals}
        for sig_dict in decisions:
            sid = sig_dict.get("signal_id")
            original = sig_by_id.get(sid)
            if original is not None:
                ic_val = sig_dict.get("ic")
                if ic_val is not None:
                    approved.append(
                        ResearchSignal(
                            signal=original.signal,
                            p_value=original.p_value,
                            ic=float(ic_val),
                            signal_id=original.signal_id,
                        )
                    )
                    ic_scores[original.signal_id] = float(ic_val)
                else:
                    approved.append(original)
            else:
                # signal_id not in original list -- create from dict if possible
                try:
                    sig = Signal(
                        strategy_id=sig_dict.get("strategy_id", "ic"),
                        symbol=sig_dict.get("symbol", "BTC/USDT"),
                        side=sig_dict.get("side", "buy"),
                        strength=float(sig_dict.get("strength", 0.5)),
                        timestamp=int(sig_dict.get("timestamp", 0)),
                    )
                    approved.append(
                        ResearchSignal(
                            signal=sig,
                            p_value=sig_dict.get("p_value"),
                            ic=sig_dict.get("ic"),
                            signal_id=sid or "",
                        )
                    )
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Failed to construct ResearchSignal: %s", exc)

        # rejected signals are those in the input but not in approved
        approved_ids = {a.signal_id for a in approved}
        rejected = [s for s in signals if s.signal_id not in approved_ids]

        return ICDecisionReport(
            approved=approved,
            rejected=rejected,
            ic_scores=ic_scores,
        )

    def _signal_to_dict(self, rs: ResearchSignal) -> dict[str, Any]:
        """Convert ResearchSignal to dict for persona_provider.compute params."""
        sig = rs.signal
        return {
            "signal_id": rs.signal_id,
            "strategy_id": getattr(sig, "strategy_id", "ic"),
            "symbol": getattr(sig, "symbol", "BTC/USDT"),
            "side": getattr(sig, "side", "buy"),
            "strength": getattr(sig, "strength", 0.5),
            "p_value": rs.p_value,
            "ic": rs.ic,
            "timestamp": getattr(sig, "timestamp", 0),
        }
