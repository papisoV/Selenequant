"""LLM provider abstraction. See docs/DECISION_WORKFLOW_METHODOLOGY.md §5.1, §6."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol


@dataclass(frozen=True)
class LLMMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LLMResponse:
    content: str
    reasoning_content: str | None
    usage: dict[str, int]


class LLMProvider(Protocol):
    def call(self, messages: list[LLMMessage]) -> LLMResponse: ...


class StubLLMProvider:
    """Test stub that pops responses from a queue. Raises when exhausted."""

    last_messages: list[LLMMessage] | None = None

    def __init__(self, response_queue: list[str]) -> None:
        self._queue = list(response_queue)
        self.last_messages = None

    def call(self, messages: list[LLMMessage]) -> LLMResponse:
        if not self._queue:
            raise RuntimeError(
                "StubLLMProvider queue exhausted — test setup issue"
            )
        popped = self._queue.pop(0)
        self.last_messages = list(messages)
        return LLMResponse(
            content=popped,
            reasoning_content=None,
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )


class CannedLLMProvider:
    """v1 demo provider that returns canned responses on a loop.

    Unlike :class:`StubLLMProvider` (which pops a queue and raises when
    exhausted), this provider cycles through its response list forever — safe
    for repeated ``analyze()`` calls in a demo endpoint.

    Used by the Phase 2 FastAPI service to demonstrate the workflow
    end-to-end without a real LLM. Phase 4 will replace this with a real LLM
    provider.

    The service calls :meth:`reset` before each ``wf.analyze()`` so the
    cursor realigns to response[0] for Stage 1 and response[1] for Stage 2.
    """

    last_messages: list[LLMMessage] | None = None

    def __init__(self, responses: list[str]) -> None:
        if not responses:
            raise ValueError("CannedLLMProvider requires at least one response")
        self._responses = list(responses)
        self._cursor = 0
        self.last_messages = None

    def call(self, messages: list[LLMMessage]) -> LLMResponse:
        response = self._responses[self._cursor % len(self._responses)]
        self._cursor += 1
        self.last_messages = list(messages)
        return LLMResponse(
            content=response,
            reasoning_content=None,
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )

    def reset(self) -> None:
        """Reset cursor for a fresh analyze() call (Stage1 + Stage2)."""
        self._cursor = 0


class BadCannedLLMProvider:
    """Canned provider that returns a bad response at a specific call index.

    Wraps :class:`CannedLLMProvider` for normal-path responses, but on the
    call where ``_cursor == bad_at_call`` (0-indexed), returns ``bad_response``
    instead. Used by error-path tests (cheat detection / semantic error).

    The bad response is returned and the cursor advances, so subsequent calls
    resume the normal cycle. ``reset()`` realigns the cursor to 0 so a fresh
    analyze() call hits the bad branch deterministically.
    """

    last_messages: list[LLMMessage] | None = None

    def __init__(
        self,
        responses: list[str],
        *,
        bad_response: str,
        bad_at_call: int,
    ) -> None:
        if not responses:
            raise ValueError("BadCannedLLMProvider requires at least one response")
        if bad_at_call < 0:
            raise ValueError("bad_at_call must be >= 0")
        self._responses = list(responses)
        self._bad_response = bad_response
        self._bad_at_call = bad_at_call
        self._cursor = 0
        self.last_messages = None

    def call(self, messages: list[LLMMessage]) -> LLMResponse:
        if self._cursor == self._bad_at_call:
            response = self._bad_response
        else:
            response = self._responses[self._cursor % len(self._responses)]
        self._cursor += 1
        self.last_messages = list(messages)
        return LLMResponse(
            content=response,
            reasoning_content=None,
            usage={"prompt_tokens": 0, "completion_tokens": 0},
        )

    def reset(self) -> None:
        """Reset cursor for a fresh analyze() call."""
        self._cursor = 0


def build_system_prompt(tree_text: str, global_rules: list[str]) -> str:
    """Build the shared system prompt for Stage 1 and Stage 2.

    Methodology §5.1 — identical bytes for both stages to maximize KV cache
    reuse across the two LLM calls.
    """
    rules_joined_with_newline = "\n".join(f"- {r}" for r in global_rules)
    return (
        "你是一个严谨的风控/合规决策辅助 AI。请严格遵循以下决策树与全局原则进行判断。\n\n"
        "【决策树】\n"
        f"{tree_text}\n\n"
        "【全局原则】\n"
        f"{rules_joined_with_newline}"
    )
