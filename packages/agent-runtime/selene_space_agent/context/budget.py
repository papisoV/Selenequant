"""Token budget management — context window guardrails.

Monitors context usage and drives recovery actions (prune → drop oldest)
when thresholds are exceeded. Status ladder:

    OVERFLOW  used > max_tokens
    CRITICAL  remaining < hard_floor
    WARNING   remaining < warning_threshold
    OK        otherwise
"""

from __future__ import annotations

from enum import Enum

from selene_space_types.agent import ChatMessage

from selene_space_agent.context.compaction import ContextCompactor
from selene_space_agent.context.pruning import ContextPruner


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


class BudgetStatus(str, Enum):
    OK = "ok"
    WARNING = "warning"
    CRITICAL = "critical"
    OVERFLOW = "overflow"


class TokenBudget:
    """Track a token budget and recover headroom when it runs low."""

    def __init__(
        self,
        max_tokens: int = 200_000,
        warning_threshold: int = 32_000,
        hard_floor: int = 16_000,
    ) -> None:
        self._max_tokens = max_tokens
        self._warning_threshold = warning_threshold
        self._hard_floor = hard_floor

    def check_budget(self, used_tokens: int) -> BudgetStatus:
        if used_tokens > self._max_tokens:
            return BudgetStatus.OVERFLOW
        remaining = self._max_tokens - used_tokens
        if remaining < self._hard_floor:
            return BudgetStatus.CRITICAL
        if remaining < self._warning_threshold:
            return BudgetStatus.WARNING
        return BudgetStatus.OK

    def get_remaining(self, used_tokens: int) -> int:
        return max(0, self._max_tokens - used_tokens)

    def _total(self, messages: list[ChatMessage]) -> int:
        return sum(_estimate_tokens(m.content) for m in messages)

    async def recover(
        self,
        messages: list[ChatMessage],
        current_tokens: int,
        pruner: ContextPruner,
        compactor: ContextCompactor | None = None,
    ) -> list[ChatMessage]:
        """Reclaim budget: prune stale tool results first, then drop oldest."""
        target = max(1, self._max_tokens - self._hard_floor)

        pruned = pruner.prune(messages, budget_tokens=self._max_tokens)
        if self.check_budget(self._total(pruned)) in (
            BudgetStatus.OK,
            BudgetStatus.WARNING,
        ):
            return pruned

        kept = list(pruned)
        while kept and self._total(kept) > target and len(kept) > 1:
            drop_idx = next(
                (i for i, m in enumerate(kept) if m.role != "system"),
                None,
            )
            if drop_idx is None:
                break
            kept.pop(drop_idx)

        if compactor is not None and self.check_budget(self._total(kept)) not in (
            BudgetStatus.OK,
            BudgetStatus.WARNING,
        ):
            kept = await compactor.compact(kept, target_tokens=target)

        return kept
