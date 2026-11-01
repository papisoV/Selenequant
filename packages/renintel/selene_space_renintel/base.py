"""Base types for selene_space_renintel — Protocol + Config + Report."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class RenIntelKind(str, Enum):
    """Kind of Renaissance Intel result."""

    PERSONA = "persona"
    REASONING = "reasoning"
    GUARDRAIL = "guardrail"
    STRATEGY = "strategy"
    MEMORY = "memory"
    OTHER = "other"


class RenIntelProviderConfig(BaseModel):
    """Config for instantiating a RenIntel provider.

    `provider` is the registry id (e.g. "personas", "ic_deliberation").
    Other fields are provider-specific and passed through via `params`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    params: dict[str, Any] = {}


class RenIntelReport(BaseModel):
    """Result of a RenIntel provider run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    kind: RenIntelKind
    title: str
    metrics: dict[str, Any] = {}
    artifacts: dict[str, Any] = {}  # e.g. {"personas": [...], "reasoning_pattern": ...}
    computed_at: datetime
    notes: str | None = None


@runtime_checkable
class RenIntelProvider(Protocol):
    """Async RenIntel provider protocol — mirrors AgentStudioProvider."""

    @property
    def config(self) -> RenIntelProviderConfig: ...

    @property
    def is_running(self) -> bool: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def healthcheck(self) -> bool: ...

    async def compute(
        self,
        *,
        action: str,
        task: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> RenIntelReport: ...

    def list_actions(self) -> list[str]: ...

    def describe_action(self, action: str) -> str: ...
