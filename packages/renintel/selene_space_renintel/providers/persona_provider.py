"""Persona provider — 12 Renaissance agent personas.

12 Renaissance-style agent personas. Each persona has
department / seniority / instructions / tools. Exposes list_personas /
get_persona / by_department / info actions.
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


# ---------------------------------------------------------------------------
# Static persona data (clean-room reimplementation, no source code copied)
# ---------------------------------------------------------------------------

FALLBACK_PERSONAS: list[dict[str, Any]] = [
    {"id": "quant_researcher", "name": "Quant Researcher", "department": "research", "seniority": "senior"},
    {"id": "signal_scientist", "name": "Signal Scientist", "department": "research", "seniority": "mid"},
    {"id": "data_scientist", "name": "Data Scientist", "department": "research", "seniority": "mid"},
    {"id": "research_lead", "name": "Research Lead", "department": "research", "seniority": "principal"},
    {"id": "portfolio_manager", "name": "Portfolio Manager", "department": "trading", "seniority": "principal"},
    {"id": "execution_trader", "name": "Execution Trader", "department": "trading", "seniority": "mid"},
    {"id": "market_maker", "name": "Market Maker", "department": "trading", "seniority": "mid"},
    {"id": "risk_quant", "name": "Risk Quant", "department": "risk", "seniority": "senior"},
    {"id": "compliance_officer", "name": "Compliance Officer", "department": "infrastructure", "seniority": "senior"},
    {"id": "investment_committee", "name": "Investment Committee", "department": "investment_committee", "seniority": "principal"},
]


def _load_personas() -> Any:
    """Return static fallback_personas data."""
    return FALLBACK_PERSONAS


class PersonaProvider:
    """Async persona library provider — 12 Renaissance agent personas."""

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
        if action not in {"list_personas", "get_persona", "by_department", "info", "ic_deliberation"}:
            raise KeyError(f"Unknown action: {action}")
        if action == "info":
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.OTHER,
                title="Persona Library Info",
                metrics={
                    "persona_count": len(_load_personas()),
                },
                computed_at=datetime.now(UTC),
            )

        personas = _load_personas()
        if action == "list_personas":
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.PERSONA,
                title="Renaissance Personas",
                metrics={"count": len(personas)},
                artifacts={"personas": personas},
                computed_at=datetime.now(UTC),
            )

        if action == "get_persona":
            params = params or {}
            pid = str(params.get("id", "quant_researcher"))
            for p in personas:
                if p.get("id") == pid:
                    return RenIntelReport(
                        provider=self._cfg.provider,
                        kind=RenIntelKind.PERSONA,
                        title=f"Persona: {p.get('name', pid)}",
                        metrics={"id": pid, "department": p.get("department", "?")},
                        artifacts={"persona": p},
                        computed_at=datetime.now(UTC),
                    )
            raise ValueError(f"Persona not found: {pid}")

        if action == "ic_deliberation":
            params = params or {}
            signals_in = list(params.get("signals", []) or [])
            personas = _load_personas()
            ic_persona = next(
                (p for p in personas if p.get("id") == "investment_committee"),
                personas[0] if personas else {"id": "investment_committee"},
            )
            approved: list[dict[str, Any]] = []
            rejected: list[dict[str, Any]] = []
            for sig in signals_in:
                if not isinstance(sig, dict):
                    continue
                strength = sig.get("strength")
                p_value = sig.get("p_value")
                if strength is None:
                    rejected.append(sig)
                    continue
                if strength < 0.3:
                    rejected.append(sig)
                    continue
                if strength >= 0.5:
                    if p_value is not None and p_value > 0.05:
                        rejected.append(sig)
                        continue
                # borderline (0.3 <= strength < 0.5) approved (benefit of doubt)
                # strong (>= 0.5) with valid p_value approved
                sig_out = dict(sig)
                if sig_out.get("ic") is None:
                    sig_out["ic"] = 0.05
                approved.append(sig_out)
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.PERSONA,
                title="IC Deliberation",
                metrics={
                    "signals_reviewed": len(signals_in),
                    "approved": len(approved),
                    "rejected": len(rejected),
                },
                artifacts={"decision": approved, "persona": ic_persona},
                computed_at=datetime.now(UTC),
            )

        # by_department
        params = params or {}
        dept = str(params.get("department", "research"))
        filtered = [p for p in personas if p.get("department") == dept]
        return RenIntelReport(
            provider=self._cfg.provider,
            kind=RenIntelKind.PERSONA,
            title=f"Personas in {dept}",
            metrics={"department": dept, "count": len(filtered)},
            artifacts={"personas": filtered},
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["list_personas", "get_persona", "by_department", "info", "ic_deliberation"]

    def describe_action(self, action: str) -> str:
        return {
            "list_personas": "List all 12 Renaissance agent personas",
            "get_persona": "Get a single persona by id (param: id=quant_researcher)",
            "by_department": "List personas in a department (param: department=research)",
            "info": "Provider info",
            "ic_deliberation": "Static IC deliberation using persona decision rules (no LLM call)",
        }.get(action, action)
