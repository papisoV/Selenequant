"""Reasoning provider — 4 Renaissance reasoning patterns.

Patterns: signal_analysis / risk_assessment / ic_deliberation / investment_reasoning.
*` modules.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from selene_space_renintel.base import (
    RenIntelKind,
    RenIntelProviderConfig,
    RenIntelReport,
)

logger = logging.getLogger(__name__)


FALLBACK_PATTERNS: dict[str, dict[str, Any]] = {
    "signal_analysis": {
        "name": "Signal Analysis Reasoning",
        "description": "Multi-step signal analysis with chain-of-thought",
        "steps": ["identify_signal", "validate_strength", "assess_quality", "decision"],
    },
    "risk_assessment": {
        "name": "Risk Assessment Reasoning",
        "description": "Comprehensive risk evaluation before position sizing",
        "steps": ["identify_risks", "quantify_exposure", "set_limits", "approve"],
    },
    "ic_deliberation": {
        "name": "Investment Committee Deliberation",
        "description": "Multi-perspective IC deliberation pattern",
        "steps": ["present_thesis", "challenge", "defend", "vote"],
    },
    "investment_reasoning": {
        "name": "Investment Reasoning",
        "description": "General investment thesis construction",
        "steps": ["hypothesis", "evidence", "test", "conclude"],
    },
}


def _load_patterns() -> Any:
    """Return static fallback_patterns data."""
    return FALLBACK_PATTERNS


class ReasoningProvider:
    """Async reasoning pattern provider — 4 Renaissance reasoning patterns."""

    def __init__(self, cfg: RenIntelProviderConfig) -> None:
        self._cfg = cfg
        self._running = False

    @property
    def config(self) -> RenIntelProviderConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def healthcheck(self) -> bool:
        return True

    async def compute(
        self,
        *,
        action: str,
        task: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> RenIntelReport:
        if action not in {"list_patterns", "get_pattern", "info"}:
            raise KeyError(f"Unknown action: {action}")
        if action == "info":
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.OTHER,
                title="Reasoning Library Info",
                metrics={
                    "pattern_count": len(_load_patterns()),
                },
                computed_at=datetime.now(UTC),
            )

        patterns = _load_patterns()
        if action == "list_patterns":
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.REASONING,
                title="Renaissance Reasoning Patterns",
                metrics={"count": len(patterns)},
                artifacts={"patterns": patterns},
                computed_at=datetime.now(UTC),
            )

        # get_pattern
        params = params or {}
        pid = str(params.get("id", "signal_analysis"))
        if pid not in patterns:
            raise ValueError(f"Pattern not found: {pid}. Available: {list(patterns)}")
        return RenIntelReport(
            provider=self._cfg.provider,
            kind=RenIntelKind.REASONING,
            title=f"Reasoning: {patterns[pid].get('name', pid)}",
            metrics={"id": pid},
            artifacts={"pattern": patterns[pid]},
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["list_patterns", "get_pattern", "info"]

    def describe_action(self, action: str) -> str:
        return {
            "list_patterns": "List all 4 reasoning patterns",
            "get_pattern": "Get a single pattern by id (param: id=signal_analysis)",
            "info": "Provider info",
        }.get(action, action)
