"""Base types for selene_space_agentstudio — Protocol + Config + Report."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

import pandas as pd
from pydantic import BaseModel, ConfigDict


class AgentStudioKind(str, Enum):
    """Kind of agent studio result."""

    RD_LOOP = "rd_loop"
    PROMPT_LIBRARY = "prompt_library"
    ORCHESTRATION = "orchestration"
    OTHER = "other"


class AgentStudioProviderConfig(BaseModel):
    """Config for instantiating an agent studio provider.

    `provider` is the registry id (e.g. "factor_loop", "economic_prompts").
    Other fields are provider-specific and passed through via `params`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    params: dict[str, Any] = {}


class AgentStudioReport(BaseModel):
    """Result of an agent studio run."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    provider: str
    kind: AgentStudioKind
    title: str
    metrics: dict[str, Any] = {}
    data: pd.DataFrame | pd.Series | None = None
    artifacts: dict[str, Any] = {}  # e.g. {"prompt_template": "...", "loop_config": {...}}
    computed_at: datetime
    notes: str | None = None


@runtime_checkable
class AgentStudioProvider(Protocol):
    """Async agent studio provider protocol — mirrors OptimizeProvider."""

    @property
    def config(self) -> AgentStudioProviderConfig: ...

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
    ) -> AgentStudioReport: ...

    def list_actions(self) -> list[str]: ...

    def describe_action(self, action: str) -> str: ...
