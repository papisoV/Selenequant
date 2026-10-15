"""Multi-backend router with ordered fallback.

Wraps named providers in a chain; each conversational primitive walks the
chain and hands off to the next backend whenever the current one raises
any LLM failure. A health probe reports per-backend reachability.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, cast

from selene_space_llm.base import (
    LLMError,
    LLMMessage,
    LLMProvider,
)
from selene_space_llm.schemas import ToolCallResult

logger = logging.getLogger(__name__)


@dataclass
class ProviderHealth:
    """Outcome of probing one backend."""

    name: str
    available: bool
    error: Exception | None = None


class LLMRouter:
    """Dispatch chat work across a priority-ordered provider chain."""

    def __init__(self, providers: list[tuple[str, LLMProvider]]) -> None:
        if not providers:
            raise ValueError(
                "LLMRouter needs at least one provider; got an empty chain"
            )
        self._entries = list(providers)

    @property
    def provider_names(self) -> list[str]:
        return [name for name, _ in self._entries]

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        """Complete a conversation, trying each provider in chain order.

        Args:
            messages: Full conversation history, oldest first.
            **kwargs: Forwarded verbatim to every provider's ``chat``
                (e.g. temperature, max_tokens).

        Returns:
            Assistant reply text from the first provider that succeeds.

        Raises:
            LLMError: Every provider in the chain failed; the message lists
                each failure in order.
        """
        async for outcome in self._attempt("chat", messages, kwargs):
            return cast(str, outcome)
        raise RuntimeError("unreachable")  # pragma: no cover

    def chat_stream(
        self, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        """Stream a completion with provider fallback.

        Unlike the await-based primitives, fallback happens mid-stream: if a
        provider raises :class:`LLMError` after yielding some chunks, the
        next provider restarts from scratch (chunks are not interleaved).

        Raises:
            LLMError: Every provider failed; lists each failure in order.
        """
        return self._stream_with_fallback(messages, kwargs)

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Generate output constrained to a JSON schema.

        Args:
            messages: Conversation history ending in the extraction request.
            output_schema: JSON-Schema-style dict describing the expected
                object; passed through to the provider unchanged.
            **kwargs: Forwarded verbatim to every provider.

        Returns:
            Parsed dict matching ``output_schema`` from the first healthy
            provider.

        Raises:
            LLMError: All providers failed or returned unparseable output.
        """
        async for outcome in self._attempt(
            "generate_structured", messages, kwargs, output_schema=output_schema
        ):
            return cast("dict[str, Any]", outcome)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolCallResult:
        """Run a tool-calling turn with provider fallback.

        Args:
            messages: Conversation history; the model decides which tool to
                invoke based on this history.
            tools: OpenAI-style tool definitions the model may call, or
                ``None`` when no tools are offered this turn.
            **kwargs: Forwarded verbatim to every provider.

        Returns:
            Structured result (text and/or parsed tool calls) from the first
            healthy provider.

        Raises:
            LLMError: All providers failed.
        """
        async for outcome in self._attempt(
            "chat_with_tools", messages, kwargs, tools=tools
        ):
            return cast(ToolCallResult, outcome)
        raise RuntimeError("unreachable")  # pragma: no cover

    async def health_check(self) -> list[ProviderHealth]:
        """Probe every backend once, preserving chain order."""
        results: list[ProviderHealth] = []
        for name, provider in self._entries:
            try:
                await provider.chat(
                    [
                        LLMMessage(
                            role=__import__(
                                "selene_space_llm.base", fromlist=["LLMRole"]
                            ).LLMRole.USER,
                            content="ping",
                        )
                    ]
                )
            except LLMError as exc:
                logger.warning("Health check failed for provider %s: %s", name, exc)
                results.append(ProviderHealth(name=name, available=False, error=exc))
            else:
                results.append(ProviderHealth(name=name, available=True))
        return results

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    async def _attempt(
        self,
        method_name: str,
        messages: list[LLMMessage],
        kwargs: dict[str, Any],
        **fixed: Any,
    ):
        failures: list[str] = []
        for name, provider in self._entries:
            method = getattr(provider, method_name)
            try:
                result = await method(messages, **{**kwargs, **fixed})
            except LLMError as exc:
                failures.append(f"{name}: {exc}")
                continue
            yield result
            return
        raise LLMError(
            f"All LLM providers failed ({len(self._entries)} tried): "
            + "; ".join(failures)
        )

    async def _stream_with_fallback(
        self, messages: list[LLMMessage], kwargs: dict[str, Any]
    ) -> AsyncIterator[str]:
        failures: list[str] = []
        for name, provider in self._entries:
            try:
                iterator = provider.chat_stream(messages, **kwargs)
                # A provider may raise synchronously before returning its
                # async iterator (e.g. `raise ...; yield` stubs) — such a
                # call returns a plain generator that async-for rejects.
                if not hasattr(iterator, "__aiter__"):
                    raise LLMError(
                        f"{name}: chat_stream did not return an async iterator"
                    )
                async for chunk in iterator:
                    yield chunk
                return
            except LLMError as exc:
                failures.append(f"{name}: {exc}")
                continue
        raise LLMError(
            f"All LLM providers failed ({len(self._entries)} tried): "
            + "; ".join(failures)
        )


__all__ = ["LLMRouter", "ProviderHealth"]
