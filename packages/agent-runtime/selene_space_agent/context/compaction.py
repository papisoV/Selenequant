"""Context compaction — summarize old messages to free token budget.

Old messages (everything before the protected recent window) are grouped
and folded into single summary entries. With an injected summarizer
(anything exposing ``async summarize(text: str) -> str``) the groups are
LLM-summarized; without one, a size-only fallback collapses each group
into a placeholder entry.
"""

from __future__ import annotations

import time
from typing import Any

from selene_space_types.agent import ChatMessage

_CHARS_PER_TOKEN = 4
_GROUP_SIZE = 4


def _estimate_tokens(text: str) -> int:
    return max(1, len(text) // _CHARS_PER_TOKEN)


class ContextCompactor:
    """Compact old conversation turns into summaries."""

    def __init__(self, summarizer: Any = None) -> None:
        self._summarizer = summarizer

    async def compact(
        self,
        messages: list[ChatMessage],
        target_tokens: int,
        keep_recent: int = 6,
    ) -> list[ChatMessage]:
        total = sum(_estimate_tokens(m.content) for m in messages)
        if total <= target_tokens:
            return list(messages)

        protected_start = max(0, len(messages) - keep_recent)
        head = messages[:protected_start]
        tail = messages[protected_start:]

        if not head:
            return list(messages)

        compacted: list[ChatMessage] = []
        for start in range(0, len(head), _GROUP_SIZE):
            group = head[start : start + _GROUP_SIZE]
            compacted.append(await self._fold_group(group))

        return compacted + list(tail)

    async def _fold_group(self, group: list[ChatMessage]) -> ChatMessage:
        now_ms = int(time.time() * 1000)
        if self._summarizer is not None:
            joined = "\n".join(f"{m.role}: {m.content}" for m in group)
            try:
                summary = await self._summarizer.summarize(joined)
                return ChatMessage(role="assistant", content=summary, timestamp=now_ms)
            except Exception:
                pass
        return ChatMessage(
            role="assistant",
            content=f"[{len(group)} messages compacted]",
            timestamp=now_ms,
        )
