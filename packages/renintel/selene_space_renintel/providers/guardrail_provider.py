"""Guardrail provider — 4 Renaissance guardrail categories.

Categories: compliance / execution / risk / signal. Each with severity
levels (INFO / WARNING / ERROR / CRITICAL) and actions (ALLOW / WARN /
MODIFY / BLOCK). *` modules, fallback static.
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


FALLBACK_GUARDRAILS: dict[str, dict[str, Any]] = {
    "compliance": {
        "name": "Compliance Guardrails",
        "description": "Regulatory and compliance checks",
        "severity_levels": ["INFO", "WARNING", "ERROR", "CRITICAL"],
        "actions": ["ALLOW", "WARN", "MODIFY", "BLOCK"],
    },
    "execution": {
        "name": "Execution Guardrails",
        "description": "Trade execution constraints (slippage / size / venue)",
        "severity_levels": ["INFO", "WARNING", "ERROR", "CRITICAL"],
        "actions": ["ALLOW", "WARN", "MODIFY", "BLOCK"],
    },
    "risk": {
        "name": "Risk Guardrails",
        "description": "Position and portfolio risk limits",
        "severity_levels": ["INFO", "WARNING", "ERROR", "CRITICAL"],
        "actions": ["ALLOW", "WARN", "MODIFY", "BLOCK"],
    },
    "signal": {
        "name": "Signal Guardrails",
        "description": "Signal quality and validity checks",
        "severity_levels": ["INFO", "WARNING", "ERROR", "CRITICAL"],
        "actions": ["ALLOW", "WARN", "MODIFY", "BLOCK"],
    },
}


def _load_guardrails() -> Any:
    """Return static fallback_guardrails data."""
    return FALLBACK_GUARDRAILS


class GuardrailProvider:
    """Async guardrail library provider — 4 Renaissance guardrail categories."""

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
        if action not in {"list_guardrails", "get_guardrail", "info"}:
            raise KeyError(f"Unknown action: {action}")
        if action == "info":
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.OTHER,
                title="Guardrail Library Info",
                metrics={
                    "category_count": len(_load_guardrails()),
                },
                computed_at=datetime.now(UTC),
            )

        guardrails = _load_guardrails()
        if action == "list_guardrails":
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.GUARDRAIL,
                title="Renaissance Guardrails",
                metrics={"count": len(guardrails)},
                artifacts={"guardrails": guardrails},
                computed_at=datetime.now(UTC),
            )

        # get_guardrail
        params = params or {}
        cat = str(params.get("category", "risk"))
        if cat not in guardrails:
            raise ValueError(f"Guardrail not found: {cat}. Available: {list(guardrails)}")
        return RenIntelReport(
            provider=self._cfg.provider,
            kind=RenIntelKind.GUARDRAIL,
            title=f"Guardrail: {guardrails[cat].get('name', cat)}",
            metrics={"category": cat},
            artifacts={"guardrail": guardrails[cat]},
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["list_guardrails", "get_guardrail", "info"]

    def describe_action(self, action: str) -> str:
        return {
            "list_guardrails": "List all 4 guardrail categories",
            "get_guardrail": "Get a guardrail by category (param: category=risk)",
            "info": "Provider info",
        }.get(action, action)
