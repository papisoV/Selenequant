"""Prompt library provider — 4 domain agent prompt packs.

Exposes 4 domain agent prompt packs (inspired by commercial quant terminal
patterns, clean-room reimplementation, no source code copied):
- EconomicAgents (capitalism / keynesian / neoliberal / socialist / marxist)
- GeopoliticsAgents (GrandChessboard / PrisonersOfGeography)
- TraderInvestorsAgent
- hedgeFundAgents (renaissance_technologies)

Each pack is loaded lazily on first access. If the underlying config files
are not available, returns a stub prompt template for UI display.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from selene_space_agentstudio.base import (
    AgentStudioKind,
    AgentStudioProviderConfig,
    AgentStudioReport,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Prompt pack registry — name -> (label, source_dir)
# ---------------------------------------------------------------------------

PROMPT_PACKS: dict[str, dict[str, str]] = {
    "economic": {
        "label": "Economic Agents",
        "source_dir": "EconomicAgents/configs",
        "description": "5 schools of economic thought (capitalism/keynesian/neoliberal/socialist/marxist)",
    },
    "geopolitics": {
        "label": "Geopolitics Agents",
        "source_dir": "GeopoliticsAgents/configs",
        "description": "Grand Chessboard + Prisoners of Geography geopolitical analysts",
    },
    "trader": {
        "label": "Trader Investors Agent",
        "source_dir": "TraderInvestorsAgent/configs",
        "description": "Trading strategy agent with risk management focus",
    },
    "hedgefund_renaissance": {
        "label": "Renaissance Technologies Hedge Fund",
        "source_dir": "hedgeFundAgents/renaissance_technologies_hedge_fund_agent",
        "description": "Quant hedge fund agent modeled after Renaissance Technologies",
    },
}


def _find_prompt_pack_root() -> Path | None:
    """Locate the prompt pack root directory (optional, graceful fallback)."""
    candidates = [
        Path("../scripts/agents"),
        Path("../../scripts/agents"),
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _load_pack_prompts(pack_name: str) -> list[dict[str, Any]]:
    """Load prompts for a pack from config files (if available).

    Returns a list of agent definition dicts. Empty list if not found.
    """
    root = _find_prompt_pack_root()
    if root is None:
        return []
    pack_info = PROMPT_PACKS.get(pack_name)
    if pack_info is None:
        return []
    pack_dir = root / pack_info["source_dir"]
    if not pack_dir.exists():
        return []

    agents: list[dict[str, Any]] = []
    # Try agent_definitions.json first
    json_path = pack_dir / "agent_definitions.json"
    if json_path.exists():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "agents" in data:
                agents.extend(data["agents"])
            elif isinstance(data, list):
                agents.extend(data)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to parse %s: %s", json_path, exc)

    # For hedgefund_renaissance, scan subdirectories
    if pack_name == "hedgefund_renaissance":
        for sub in pack_dir.iterdir():
            if sub.is_dir() and (sub / "config.py").exists():
                agents.append({
                    "id": sub.name,
                    "name": sub.name.replace("_", " ").title(),
                    "source": "renaissance_hedgefund",
                })

    return agents


# ---------------------------------------------------------------------------
# PromptLibraryProvider
# ---------------------------------------------------------------------------


class PromptLibraryProvider:
    """Async prompt library provider — exposes 4 domain agent prompt packs."""

    def __init__(self, cfg: AgentStudioProviderConfig) -> None:
        self._cfg = cfg
        self._running = False

    @property
    def config(self) -> AgentStudioProviderConfig:
        return self._cfg

    @property
    def is_running(self) -> bool:
        return self._running

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False

    async def healthcheck(self) -> bool:
        return True  # always available (degrades gracefully)

    async def compute(
        self,
        *,
        action: str,
        task: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> AgentStudioReport:
        if action not in {"list_packs", "get_pack", "info"}:
            raise KeyError(f"Unknown action: {action}")
        if action == "info":
            return AgentStudioReport(
                provider=self._cfg.provider,
                kind=AgentStudioKind.OTHER,
                title="Prompt Library Info",
                metrics={
                    "pack_count": len(PROMPT_PACKS),
                    "packs": list(PROMPT_PACKS.keys()),
                    "prompt_pack_root_found": _find_prompt_pack_root() is not None,
                },
                computed_at=datetime.now(UTC),
            )

        if action == "list_packs":
            packs_data = []
            for name, info in PROMPT_PACKS.items():
                agents = _load_pack_prompts(name)
                packs_data.append({
                    "name": name,
                    "label": info["label"],
                    "description": info["description"],
                    "agent_count": len(agents),
                    "agent_ids": [a.get("id", "?") for a in agents],
                })
            return AgentStudioReport(
                provider=self._cfg.provider,
                kind=AgentStudioKind.PROMPT_LIBRARY,
                title="Prompt Library Packs",
                metrics={"pack_count": len(packs_data)},
                artifacts={"packs": packs_data},
                computed_at=datetime.now(UTC),
            )

        # action == "get_pack"
        params = params or {}
        pack_name = str(params.get("pack", "economic"))
        if pack_name not in PROMPT_PACKS:
            raise ValueError(f"Unknown pack: {pack_name}. Available: {list(PROMPT_PACKS)}")
        agents = _load_pack_prompts(pack_name)
        pack_info = PROMPT_PACKS[pack_name]
        return AgentStudioReport(
            provider=self._cfg.provider,
            kind=AgentStudioKind.PROMPT_LIBRARY,
            title=f"Prompt Pack: {pack_info['label']}",
            metrics={
                "pack": pack_name,
                "agent_count": len(agents),
                "source_dir": pack_info["source_dir"],
            },
            artifacts={
                "pack_info": pack_info,
                "agents": agents,
            },
            computed_at=datetime.now(UTC),
            notes=f"Loaded {len(agents)} agents from {pack_info['label']}",
        )

    def list_actions(self) -> list[str]:
        return ["list_packs", "get_pack", "info"]

    def describe_action(self, action: str) -> str:
        return {
            "list_packs": "List all 4 prompt packs with agent counts",
            "get_pack": "Get full prompt definitions for a pack (param: pack=economic)",
            "info": "Provider info",
        }.get(action, action)
