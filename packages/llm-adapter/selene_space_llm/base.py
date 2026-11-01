"""Provider-neutral chat model interface.

The module defines the vocabulary every backend speaks (roles, messages,
settings), the failure taxonomy used across the stack, and the abstract
backend contract plus a tool-calling shim that degrades to plain text.
"""

from __future__ import annotations

import enum
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from selene_space_llm.schemas import ToolCallResult


class LLMRole(str, enum.Enum):
    """Speaker of a conversation turn."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class LLMMessage(BaseModel):
    """One turn of a chat transcript."""

    role: LLMRole
    content: str


def coerce_messages(messages: list[Any]) -> list[LLMMessage]:
    """Accept both LLMMessage objects and plain ``{"role","content"}`` dicts.

    Several in-repo callers (react loop, grid/sentinel tools) pass raw
    dicts; Pydantic would reject them, so normalize here at the boundary.
    """
    return [
        m if isinstance(m, LLMMessage) else LLMMessage.model_validate(m)
        for m in messages
    ]


class LLMConfig(BaseModel):
    """Connection and sampling settings for a backend."""

    model: str = Field(..., min_length=1)
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(4096, gt=0)
    api_key: str | None = None
    base_url: str | None = None


class LLMError(Exception):
    """Root of every failure this package raises."""


class LLMConnectionError(LLMError):
    """The remote endpoint could not be reached or answered unusably."""


class LLMRateLimitError(LLMError):
    """The endpoint asked us to slow down."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class LLMAuthError(LLMError):
    """Credentials were rejected."""


class LLMContextLengthError(LLMError):
    """The request exceeded what the model can attend over."""


class LLMTruncatedError(LLMError):
    """The reply stopped because the model ran out of output budget.

    Distinct from ``LLMContextLengthError``, which is about the *input* being
    too long. Here the request was fine and the answer is real, just
    unfinished -- so the fix is a larger output budget, not shorter input.
    """


class LLMIncompleteStreamError(LLMError):
    """A stream ended without the endpoint ever saying it was finished.

    Raised when the wire goes quiet mid-reply: no ``finish_reason`` on an
    OpenAI-shaped stream, no ``done`` frame on Ollama. The connection may have
    dropped, so the text collected so far is not a complete answer.
    """


class LLMStreamInterruptedError(LLMError):
    """A stream failed after text had already been handed to the caller.

    Raised instead of falling back to another backend, because the emitted
    fragments cannot be retracted: concatenating a second provider's answer
    onto them would produce one reply stitched from two models with nothing
    marking the seam.
    """


class LLMProvider(ABC):
    """Abstract chat backend.

    Concrete backends implement the three primitives; tool calling is
    provided as a degrade-to-text default that subclasses may override
    with native support.
    """

    def __init__(self, config: LLMConfig) -> None:
        self.config = config

    @abstractmethod
    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        """Return the assistant reply for ``messages``."""

    @abstractmethod
    def chat_stream(
        self, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        """Yield reply fragments incrementally.

        Implementations must not let a clipped reply look like a finished one:
        after the last fragment, raise ``LLMTruncatedError`` when the model
        stopped for want of output budget, and ``LLMIncompleteStreamError``
        when the endpoint never signalled an ending. Fragments already yielded
        stay valid text -- what changes is that the caller learns it is
        partial instead of silently shipping it.

        A finish that carried no text at all raises ``LLMError`` rather than
        yielding nothing: an empty stream is never a usable answer, and
        returning quietly would let the caller mistake it for one. Callers that
        expect an empty reply (a tool-call-only turn, say) must not take this
        path -- ``chat_with_tools`` exists for those.
        """

    @abstractmethod
    async def generate_structured(
        self,
        messages: list[LLMMessage],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Return the reply parsed against ``output_schema``."""

    async def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolCallResult:
        """Text-only fallback: no native tool plumbing here.

        Subclasses with native function calling override this; the base
        path simply wraps whatever plain text comes back.
        """
        from selene_space_llm.schemas import ToolCallResult

        text = await self.chat(messages, **kwargs)
        return ToolCallResult(text=text)


__all__ = [
    "LLMAuthError",
    "LLMConfig",
    "LLMConnectionError",
    "LLMContextLengthError",
    "LLMError",
    "LLMIncompleteStreamError",
    "LLMMessage",
    "LLMProvider",
    "LLMRateLimitError",
    "LLMRole",
    "LLMStreamInterruptedError",
    "LLMTruncatedError",
]
