"""Memory provider — 5 Renaissance memory types.

Types: agent_memory / decision_memory / learning_memory / trade_memory /
seed_data. *` modules.
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


FALLBACK_MEMORY_TYPES: dict[str, dict[str, Any]] = {
    "agent_memory": {
        "name": "Agent Memory",
        "description": "Per-agent state and conversation history",
        "schema_fields": ["agent_id", "timestamp", "state", "messages"],
    },
    "decision_memory": {
        "name": "Decision Memory",
        "description": "Investment decisions and rationale log",
        "schema_fields": ["decision_id", "timestamp", "agent_id", "rationale"],
    },
    "learning_memory": {
        "name": "Learning Memory",
        "description": "Long-term learnings and patterns discovered",
        "schema_fields": ["learning_id", "timestamp", "pattern", "confidence"],
    },
    "trade_memory": {
        "name": "Trade Memory",
        "description": "Trade execution history and outcomes",
        "schema_fields": ["trade_id", "timestamp", "symbol", "side", "size", "price"],
    },
    "seed_data": {
        "name": "Seed Data",
        "description": "Initial seed data for new agents and scenarios",
        "schema_fields": ["seed_id", "scenario", "data"],
    },
}


def _load_memory_types() -> Any:
    """Return static fallback_memory_types data."""
    return FALLBACK_MEMORY_TYPES


class MemoryProvider:
    """Async memory library provider — 5 Renaissance memory types."""

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
        if action not in {"list_types", "get_type", "info"}:
            raise KeyError(f"Unknown action: {action}")
        if action == "info":
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.OTHER,
                title="Memory Library Info",
                metrics={
                    "type_count": len(_load_memory_types()),
                },
                computed_at=datetime.now(UTC),
            )

        memory_types = _load_memory_types()
        if action == "list_types":
            return RenIntelReport(
                provider=self._cfg.provider,
                kind=RenIntelKind.MEMORY,
                title="Renaissance Memory Types",
                metrics={"count": len(memory_types)},
                artifacts={"memory_types": memory_types},
                computed_at=datetime.now(UTC),
            )

        # get_type
        params = params or {}
        mid = str(params.get("id", "agent_memory"))
        if mid not in memory_types:
            raise ValueError(f"Memory type not found: {mid}. Available: {list(memory_types)}")
        return RenIntelReport(
            provider=self._cfg.provider,
            kind=RenIntelKind.MEMORY,
            title=f"Memory: {memory_types[mid].get('name', mid)}",
            metrics={"id": mid},
            artifacts={"memory_type": memory_types[mid]},
            computed_at=datetime.now(UTC),
        )

    def list_actions(self) -> list[str]:
        return ["list_types", "get_type", "info"]

    def describe_action(self, action: str) -> str:
        return {
            "list_types": "List all 5 memory types",
            "get_type": "Get a memory type by id (param: id=agent_memory)",
            "info": "Provider info",
        }.get(action, action)
