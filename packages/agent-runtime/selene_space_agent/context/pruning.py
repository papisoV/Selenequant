"""Context pruning — TTL-based trimming of stale tool results.

Three-tier policy for old ``tool`` messages:
  - age > ttl * hard_threshold  → content replaced by a cleared placeholder
  - age > ttl * soft_threshold  → content trimmed to a short excerpt
  - otherwise                   → untouched

``system`` messages are always preserved verbatim. The input list is never
mutated; a new list with new ChatMessage objects is returned.
"""

from __future__ import annotations

import time

from selene_space_types.agent import ChatMessage

_CLEARED_PLACEHOLDER = "[Old tool result cleared]"


class ContextPruner:
    """Prune stale tool results from conversation history."""

    def __init__(
        self,
        ttl_seconds: float = 3600.0,
        soft_threshold: float = 0.5,
        hard_threshold: float = 10.0,
        max_tool_chars: int = 400,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._soft_threshold = soft_threshold
        self._hard_threshold = hard_threshold
        self._max_tool_chars = max_tool_chars

    def prune(
        self,
        messages: list[ChatMessage],
        budget_tokens: int,
    ) -> list[ChatMessage]:
        now_ms = int(time.time() * 1000)
        result: list[ChatMessage] = []

        for msg in messages:
            if msg.role == "system":
                result.append(msg)
                continue

            age_seconds = (now_ms - msg.timestamp) / 1000.0
            is_stale_tool = msg.role == "tool" and age_seconds > (
                self._ttl_seconds * self._soft_threshold
            )
            if not is_stale_tool:
                result.append(msg)
                continue

            if age_seconds > self._ttl_seconds * self._hard_threshold:
                result.append(
                    ChatMessage(
                        role=msg.role,
                        content=_CLEARED_PLACEHOLDER,
                        timestamp=msg.timestamp,
                    )
                )
            else:
                excerpt = msg.content[: self._max_tool_chars]
                dropped = len(msg.content) - len(excerpt)
                new_content = f"{excerpt} ... [trimmed {dropped} chars]"
                result.append(
                    ChatMessage(
                        role=msg.role, content=new_content, timestamp=msg.timestamp
                    )
                )

        return result
