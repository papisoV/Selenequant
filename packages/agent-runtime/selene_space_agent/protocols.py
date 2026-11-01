"""Protocol interfaces for the open-core component architecture.

Every protocol is ``runtime_checkable`` so callers can verify that a
registered component satisfies the expected shape with a plain
``isinstance`` check.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable


@dataclass(frozen=True)
class MemoryEntry:
    """A single memory record stored by a MemoryBackend."""

    id: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    timestamp: float | None = None


@dataclass(frozen=True)
class DelegationRequest:
    """Description of a task handed from one agent role to another."""

    task: str
    context: dict[str, Any] = field(default_factory=dict)
    target_role: str | None = None


@runtime_checkable
class MemoryBackend(Protocol):
    """Key-content memory with substring recall."""

    async def store(self, entry: MemoryEntry) -> None: ...

    async def recall(self, query: str) -> list[MemoryEntry]: ...


@runtime_checkable
class ModelRouter(Protocol):
    """Pick which model identifier should serve a conversation."""

    def route(self, messages: list[dict[str, Any]]) -> str: ...


@runtime_checkable
class ContextEngine(Protocol):
    """Assemble working-context items under a token budget."""

    async def bootstrap(self, config: dict[str, Any]) -> None: ...

    async def ingest(self, item: dict[str, Any]) -> None: ...

    async def assemble(self, token_budget: int) -> list[dict[str, Any]]: ...


@runtime_checkable
class AgentOrchestrator(Protocol):
    """Run one agent task end-to-end and return the final answer."""

    async def run(self, task: str, context: dict[str, Any]) -> str: ...


@runtime_checkable
class FeedbackEngine(Protocol):
    """Turn backtest metrics into an actionable recommendation."""

    async def analyze(self, metrics: dict[str, Any]) -> dict[str, Any]: ...
