"""Baseline community-edition implementations of the component protocols.

These are deliberately simple reference implementations: they satisfy the
protocol shapes and are good enough for a single-user local deployment.
"""

from __future__ import annotations

from typing import Any

from selene_space_agent.protocols import (
    AgentOrchestrator,
    ContextEngine,
    FeedbackEngine,
    MemoryBackend,
    MemoryEntry,
    ModelRouter,
)


class KeywordMemoryBackend(MemoryBackend):
    """In-process memory with case-sensitive substring recall."""

    def __init__(self) -> None:
        self._entries: list[MemoryEntry] = []

    async def store(self, entry: MemoryEntry) -> None:
        self._entries.append(entry)

    async def recall(self, query: str) -> list[MemoryEntry]:
        return [e for e in self._entries if query in e.content]


class SingleAgentRunner(AgentOrchestrator):
    """Run every task inline; there is only one agent to orchestrate."""

    async def run(self, task: str, context: dict[str, Any]) -> str:
        return f"Completed task: {task}"


class FixedModelRouter(ModelRouter):
    """Always route to one configured model identifier."""

    def __init__(self, model: str = "default") -> None:
        self._model = model

    def route(self, messages: list[dict[str, Any]]) -> str:
        return self._model


class BasicContextManager(ContextEngine):
    """Collect ingested items and hand them back under any budget."""

    def __init__(self) -> None:
        self._config: dict[str, Any] = {}
        self._items: list[dict[str, Any]] = []

    async def bootstrap(self, config: dict[str, Any]) -> None:
        self._config = dict(config)

    async def ingest(self, item: dict[str, Any]) -> None:
        self._items.append(dict(item))

    async def assemble(self, token_budget: int) -> list[dict[str, Any]]:
        return list(self._items)


class RuleBasedFeedback(FeedbackEngine):
    """Threshold rules on sharpe ratio, drawdown, and win rate."""

    _DRAWDOWN_LIMIT = -0.30
    _WIN_RATE_FLOOR = 0.35

    async def analyze(self, metrics: dict[str, Any]) -> dict[str, Any]:
        recommendation: str
        notes: list[str] = []

        sharpe = metrics.get("sharpe_ratio")
        if isinstance(sharpe, (int, float)) and sharpe < 0:
            recommendation = "Strategy underperforms: negative Sharpe ratio."
            notes.append("negative_sharpe")
        else:
            recommendation = "Performance is acceptable."

        drawdown = metrics.get("max_drawdown")
        if isinstance(drawdown, (int, float)) and drawdown <= self._DRAWDOWN_LIMIT:
            notes.append("excessive_drawdown")

        win_rate = metrics.get("win_rate")
        if isinstance(win_rate, (int, float)) and win_rate < self._WIN_RATE_FLOOR:
            notes.append("low_win_rate")

        result: dict[str, Any] = {"recommendation": recommendation}
        if notes:
            result["issues"] = notes
        return result
