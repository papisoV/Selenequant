"""Bridge selene_space_llm (async pydantic ABC) → decision_workflow sync Protocol.

The two provider interfaces are structurally incompatible:
- ``selene_space_llm.base.LLMProvider`` is an async pydantic-backed ABC whose
  ``chat()`` returns bare ``str`` (usage only via ``chat_with_usage`` /
  ``chat_with_tools``).
- ``selene_space_decision_workflow.llm_provider.LLMProvider`` is a sync
  ``Protocol`` requiring ``call(messages) -> LLMResponse(content,
  reasoning_content, usage)``.

This adapter is dependency-injected: it never imports ``selene_space_llm``
(the decision-workflow package must not depend on llm-adapter). It duck-types
the async backend so any provider exposing ``chat_with_tools`` works — both
built-ins (OpenAICompatProvider, OllamaProvider) qualify.

Async→sync bridging: all coroutines execute on one dedicated private
single-thread loop (created lazily on a daemon thread). A fresh
``asyncio.run`` per call would break backends that hold a long-lived
``httpx.AsyncClient``: the second call reuses pooled connections bound to the
first (now closed) loop and crashes with "Event loop is closed".
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from typing import Any, Protocol

from .llm_provider import LLMMessage, LLMResponse

# Transient gateway errors (e.g. Cloudflare 524 "upstream temporarily
# unavailable") are worth retrying; the reasoning endpoint behind the
# configured provider intermittently serves them under load.
_TRANSIENT_RETRIES = 3
_TRANSIENT_BACKOFF_S = 20.0


class _ChatBackend(Protocol):
    """Structural subset of selene_space_llm providers this adapter needs."""

    async def chat_with_tools(
        self, messages: Any, tools: Any = None, **kwargs: Any
    ) -> Any: ...


def _role_value(role: Any) -> str:
    return role.value if hasattr(role, "value") else str(role)


def _is_transient(err: Exception) -> bool:
    """True for retryable gateway/model-provider hiccups (5xx, timeouts).

    "Server disconnected without sending a response" (httpx RemoteProtocolError)
    is on this list too: the reasoning endpoints behind real providers hold
    connections for minutes while the model thinks, and both the local proxy
    and the upstream gateway intermittently drop such mid-flight connections.
    Measured 2026-09-17 on glm-5.3-flash: two full diagnoses died to this one
    error, while a retry after backoff succeeded.
    """
    text = str(err)
    lowered = text.lower()
    return (
        "524" in text
        or "502" in text
        or "503" in text
        or "temporarily unavailable" in lowered
        or "timeout" in lowered
        or "server disconnected" in lowered
        or "remoteprotocolerror" in lowered
    )


class AsyncBackendToSyncAdapter:
    """Adapt an async selene_space_llm backend to the sync LLMProvider Protocol."""

    def __init__(self, backend: _ChatBackend) -> None:
        self._backend = backend
        # Dedicated loop + thread: ALL backend coroutines run here so a
        # long-lived AsyncClient stays bound to a single loop. Shutdown is a
        # no-op by design (daemon thread dies with process).
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        # Real per-call token usage from the most recent response. Kept for
        # single-call consumers (e.g. the follow-up endpoint's per-question
        # usage); the orchestrator now aggregates its own usage_total across
        # every call, so this is NOT the run total.
        self.last_usage: dict[str, int] = {}

    def call(self, messages: list[LLMMessage]) -> LLMResponse:
        # Attribute-style messages (role/content) — what selene_space_llm's
        # _build_payload expects. Plain str roles pass through its
        # _role_value (str has no .value → str(role) identity).
        payload = [
            SimpleNamespace(role=_role_value(m.role), content=m.content)
            for m in messages
        ]
        last_err: Exception | None = None
        result: Any = None
        for attempt in range(_TRANSIENT_RETRIES + 1):
            try:
                result = self._run_detached(
                    self._backend.chat_with_tools(payload, None)
                )
                last_err = None
                break
            except Exception as err:  # noqa: BLE001 - re-raised below
                last_err = err
                if attempt == _TRANSIENT_RETRIES or not _is_transient(err):
                    raise
                time.sleep(_TRANSIENT_BACKOFF_S * (attempt + 1))
        assert last_err is None  # loop always breaks clean or raises
        resp = self._to_llm_response(result)
        if resp.usage:
            self.last_usage = resp.usage
        return resp

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _run_detached(self, coro: Any) -> Any:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._thread.start()
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result()

    @staticmethod
    def _to_llm_response(result: Any) -> LLMResponse:
        text = getattr(result, "text", None)
        if not isinstance(text, str):
            text = str(result)
        usage_obj = getattr(result, "usage", None)
        usage: dict[str, int] = {}
        if usage_obj is not None:
            for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
                value = getattr(usage_obj, field, None)
                if isinstance(value, int):
                    usage[field] = value
        # selene_space_llm schemas carry no reasoning field today; map
        # model-echoed <think> blocks downstream if a backend surfaces one.
        return LLMResponse(content=text, reasoning_content=None, usage=usage)


__all__ = ["AsyncBackendToSyncAdapter"]
