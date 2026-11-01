"""Context manager — conversation history with token budgeting.

Tracks messages, estimates token usage (tiktoken when available with a
chars/4 fallback), auto-trims to the configured message/token budget, and
supports replacing a live market-prices system message.

Serialization round-trips through ``serialize()`` / ``deserialize()`` so
chat sessions can be persisted and restored.
"""

from __future__ import annotations

import time
from typing import Any

from selene_space_types.agent import ChatMessage

try:
    import tiktoken

    _ENCODING: Any = tiktoken.get_encoding("cl100k_base")
    TIKTOKEN_AVAILABLE = True
except Exception:  # pragma: no cover - depends on optional dependency
    _ENCODING = None
    TIKTOKEN_AVAILABLE = False

_FALLBACK_CHARS_PER_TOKEN = 4

_MARKET_CONTEXT_HEADER = "Live Market Prices"


def count_tokens(text: str) -> int:
    """Token estimate for ``text``: tiktoken cl100k_base when installed,
    else ``len(text) // 4``. Always >= 1, even for empty input."""
    if _ENCODING is not None:
        return max(1, len(_ENCODING.encode(text)))
    return max(1, len(text) // _FALLBACK_CHARS_PER_TOKEN)


def _now_ms() -> int:
    return int(time.time() * 1000)


class ContextManager:
    """Maintain a trimmed conversation history within a token budget."""

    def __init__(
        self,
        max_messages: int = 100,
        max_tokens: int = 8192,
        compaction_threshold: float = 0.8,
        compactor: Any | None = None,
    ) -> None:
        self._max_messages = max_messages
        self._max_tokens = max_tokens
        self._compaction_threshold = compaction_threshold
        self._compactor = compactor
        self._messages: list[ChatMessage] = []

    # ------------------------------------------------------------------
    # Core history operations
    # ------------------------------------------------------------------

    def estimate_tokens(self, text: str) -> int:
        return count_tokens(text)

    def add_message(self, role: str, content: str) -> None:
        """Append a message, then enforce budget: oldest non-system
        messages are dropped until under both caps; if still above the
        compaction threshold a registered compactor runs (failures are
        swallowed — trimming already guarantees the hard cap).
        """
        self._messages.append(
            ChatMessage(role=role, content=content, timestamp=_now_ms())
        )
        self._trim()
        self._maybe_compact()  # type: ignore[unused-coroutine]

    def get_messages(self) -> list[ChatMessage]:
        return list(self._messages)

    @property
    def message_count(self) -> int:
        return len(self._messages)

    def total_tokens(self) -> int:
        return sum(count_tokens(m.content) for m in self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def _trim(self) -> None:
        while len(self._messages) > self._max_messages:
            drop_idx = next(
                (i for i, m in enumerate(self._messages) if m.role != "system"),
                None,
            )
            if drop_idx is None:
                break
            self._messages.pop(drop_idx)

        while self.total_tokens() > self._max_tokens:
            drop_idx = next(
                (i for i, m in enumerate(self._messages) if m.role != "system"),
                None,
            )
            if drop_idx is None or len(self._messages) <= 1:
                break
            self._messages.pop(drop_idx)

    async def _maybe_compact(self) -> None:
        if (
            self._compactor is not None
            and self.total_tokens() > self._max_tokens * self._compaction_threshold
        ):
            try:
                compacted = await self._compactor.compact(
                    self._messages, target_tokens=self._max_tokens
                )
                self._messages = compacted
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Tool result truncation
    # ------------------------------------------------------------------

    def truncate_tool_result(self, content: str, max_chars: int | None = None) -> str:
        """Clip oversized tool output before it enters history.

        Args:
            content: Raw tool output.
            max_chars: Hard clip point; defaults to
                ``max_tokens * 1.2`` (chars-per-token slack).

        Returns:
            ``content`` unchanged when short enough, else the head plus a
            ``... [Truncated N chars]`` marker (marker fits inside the cap).
        """
        limit_chars = (
            max_chars if max_chars is not None else int(self._max_tokens * 1.2)
        )
        if len(content) <= limit_chars:
            return content
        keep = max(0, limit_chars - 40)
        dropped = len(content) - keep
        return f"{content[:keep]} ... [Truncated {dropped} chars]"

    # ------------------------------------------------------------------
    # Market context injection
    # ------------------------------------------------------------------

    async def inject_market_context(
        self,
        symbols: list[str],
        prices: dict[str, float],
    ) -> None:
        if not prices:
            return

        budget_chars = int(self._max_tokens * 0.10 * _FALLBACK_CHARS_PER_TOKEN)
        lines: list[str] = [_MARKET_CONTEXT_HEADER]
        used_symbols: list[str] = []
        for symbol in symbols:
            price = prices.get(symbol)
            if price is None:
                continue
            line = f"{symbol} = ${price:,.2f}"
            candidate_len = sum(len(ln) for ln in [*lines, line]) + len([*lines, line])
            if candidate_len > budget_chars and len(used_symbols) > 0:
                break
            lines.append(line)
            used_symbols.append(symbol)

        if len(lines) == 1:
            return

        content = "\n".join(lines)
        while count_tokens(content) > self._max_tokens // 10 and len(lines) > 2:
            lines.pop()
            used_symbols.pop()
            content = "\n".join(lines)

        self._remove_market_context()
        self._messages.insert(
            0, ChatMessage(role="system", content=content, timestamp=_now_ms())
        )
        self._trim()

    def _is_market_context(self, msg: ChatMessage) -> bool:
        return bool(
            msg.role == "system" and msg.content.startswith(_MARKET_CONTEXT_HEADER)
        )

    def _remove_market_context(self) -> None:
        self._messages = [m for m in self._messages if not self._is_market_context(m)]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def serialize(self) -> list[dict]:
        return [
            {
                "role": m.role,
                "content": m.content,
                "timestamp": m.timestamp,
                "metadata": m.metadata,
            }
            for m in self._messages
        ]

    @classmethod
    def deserialize(cls, data: list[dict]) -> "ContextManager":
        manager = cls()
        for entry in data:
            timestamp = entry.get("timestamp")
            if timestamp is None:
                timestamp = _now_ms()
            manager._messages.append(
                ChatMessage(
                    role=entry.get("role", "user"),
                    content=entry.get("content", ""),
                    timestamp=timestamp,
                    metadata=entry.get("metadata"),
                )
            )
        return manager
