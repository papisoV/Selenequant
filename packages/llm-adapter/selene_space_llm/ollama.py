"""Local Ollama backend.

Talks to a local Ollama daemon: /api/chat for conversation (NDJSON when
streaming), ``format: "json"`` for structured output, and Ollama's own
tool-call message shape for function calling.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from selene_space_llm.base import (
    LLMConfig,
    LLMConnectionError,
    LLMError,
    LLMMessage,
    LLMProvider,
    coerce_messages,
)
from selene_space_llm.schemas import ToolCall, ToolCallResult, TokenUsage

_DEFAULT_BASE_URL = "http://localhost:11434"
_CHAT_PATH = "/api/chat"


def _role_value(message: LLMMessage) -> str:
    role = message.role
    return role.value if hasattr(role, "value") else str(role)


class OllamaProvider(LLMProvider):
    """Chat backend against a locally running Ollama server."""

    def __init__(
        self,
        config: LLMConfig,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient()
        self._base_url = (config.base_url or _DEFAULT_BASE_URL).rstrip("/")

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        payload = self._build_payload(
            messages, stream=False, extra=overrides_with(kwargs)
        )
        response = await self._post_chat(payload)
        if response.status_code >= 400:
            raise LLMError(
                f"Ollama returned {response.status_code}: {response.text[:200]}"
            )
        body = _decode_json(response)
        return str((body.get("message") or {}).get("content") or "")

    async def chat_stream(
        self, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        payload = self._build_payload(
            messages, stream=True, extra=overrides_with(kwargs)
        )
        try:
            async with self._client.stream(
                "POST", f"{self._base_url}{_CHAT_PATH}", json=payload
            ) as response:
                if response.status_code >= 400:
                    raw = (await response.aread()).decode(errors="replace")
                    raise LLMError(
                        f"Ollama returned {response.status_code}: {raw[:200]}"
                    )
                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        event = json.loads(stripped)
                    except json.JSONDecodeError:
                        continue
                    content = (event.get("message") or {}).get("content")
                    if content:
                        yield content
                    if event.get("done"):
                        break
        except httpx.HTTPError as exc:
            raise LLMConnectionError(f"Ollama unreachable: {exc}") from exc

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        from selene_space_llm.schemas import extract_structured

        kwargs["format"] = "json"
        text = await self.chat(messages, **kwargs)
        result = extract_structured(text, _AnyObject)  # type: ignore[type-var]
        if not isinstance(result, dict):
            raise LLMError(
                f"Ollama structured output was not a JSON object "
                f"(model={self.config.model!r}, got {type(result).__name__}): "
                f"{text[:200]!r}"
            )
        return result

    async def chat_with_tools(
        self,
        messages: list[LLMMessage],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolCallResult:
        payload = self._build_payload(
            messages, stream=False, extra=overrides_with(kwargs)
        )
        if tools is not None:
            payload["tools"] = tools

        response = await self._post_chat(payload)
        if response.status_code >= 400:
            raise LLMError(
                f"Ollama returned {response.status_code}: {response.text[:200]}"
            )
        body = _decode_json(response)
        message = body.get("message") or {}

        calls: list[ToolCall] = []
        for entry in message.get("tool_calls") or []:
            fn = entry.get("function") or {}
            args = fn.get("arguments")
            calls.append(
                ToolCall(
                    id=str(entry.get("id") or ""),
                    name=str(fn.get("name") or ""),
                    arguments=args if isinstance(args, dict) else {},
                )
            )

        usage = TokenUsage(
            prompt_tokens=int(body.get("prompt_eval_count") or 0),
            completion_tokens=int(body.get("eval_count") or 0),
            total_tokens=int(body.get("prompt_eval_count") or 0)
            + int(body.get("eval_count") or 0),
        )
        return ToolCallResult(
            text=str(message.get("content") or ""),
            tool_calls=calls,
            usage=usage,
            model=body.get("model"),
        )

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: list[LLMMessage],
        *,
        stream: bool,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        options: dict[str, Any] = {"temperature": self.config.temperature}
        if self.config.max_tokens is not None:
            options["num_predict"] = self.config.max_tokens
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": [
                {"role": _role_value(m), "content": m.content}
                for m in coerce_messages(messages)
            ],
            "stream": stream,
            "options": options,
        }
        if extra:
            payload.update(extra)
        return payload

    async def _post_chat(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post(
                f"{self._base_url}{_CHAT_PATH}", json=payload
            )
        except httpx.HTTPError as exc:
            raise LLMConnectionError(f"Ollama unreachable: {exc}") from exc


def overrides_with(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Fold keyword overrides (format etc.) into the payload extras."""
    extras = {k: v for k, v in kwargs.items() if k in ("format",)}
    return extras


def _decode_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise LLMError(f"Ollama returned non-JSON body: {response.text[:200]}") from exc
    return data if isinstance(data, dict) else {}


class _AnyObject:
    """Duck-typed schema so extract_structured can hand back a raw dict."""

    @classmethod
    def model_validate(cls, obj: Any) -> dict:
        if not isinstance(obj, dict):
            raise ValueError(f"expected a JSON object, got {type(obj).__name__}")
        return obj

    @property
    def __name__(self) -> str:  # pragma: no cover - class attr proxy
        return "_AnyObject"


__all__ = ["OllamaProvider"]
