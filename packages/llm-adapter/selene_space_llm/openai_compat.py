"""OpenAI-compatible chat-completions backend.

Speaks the wire protocol shared by OpenAI, DeepSeek, OpenRouter and other
vLLM-style gateways: /chat/completions for conversation, SSE deltas for
streaming, json_object response format (with one retry without it) for
structured output, and native function-call arrays for tool use.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

from selene_space_llm.base import (
    LLMAuthError,
    LLMConfig,
    LLMConnectionError,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRateLimitError,
    coerce_messages,
)
from selene_space_llm.schemas import ToolCall, ToolCallResult, TokenUsage

_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_COMPLETIONS_PATH = "/chat/completions"
_MODELS_PATH = "/models"

_AUTH_STATUS = {401, 403}
_SLOWDOWN_STATUS = {429}


def _status_to_error(status: int, body: str) -> LLMError:
    if status in _AUTH_STATUS:
        return LLMAuthError(f"Authentication failed ({status}): {body}")
    if status in _SLOWDOWN_STATUS:
        return LLMRateLimitError(f"Rate limited ({status}): {body}")
    return LLMConnectionError(f"Endpoint returned {status}: {body}")


def _role_value(message: LLMMessage) -> str:
    role = message.role
    return role.value if hasattr(role, "value") else str(role)


class OpenAICompatProvider(LLMProvider):
    """Chat backend against any OpenAI-shaped HTTP endpoint."""

    def __init__(
        self,
        config: LLMConfig,
        timeout: float | httpx.Timeout | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        super().__init__(config)
        if isinstance(timeout, (int, float)):
            resolved_timeout: Any = httpx.Timeout(timeout)
        else:
            resolved_timeout = timeout

        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=resolved_timeout)
        self._base_url = (config.base_url or _DEFAULT_BASE_URL).rstrip("/")

    def _headers(self) -> dict[str, str]:
        # An injected client may carry its own transport/timeout; auth still
        # has to ride along on every request when a key is configured.
        if self.config.api_key:
            return {"Authorization": f"Bearer {self.config.api_key}"}
        return {}

    async def aclose(self) -> None:
        # 只在自建 client 时关闭;注入的 client 归调用方管
        if self._owns_client:
            await self._client.aclose()

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        content, _ = await self.chat_with_usage(messages, **kwargs)
        return content

    async def chat_with_usage(
        self, messages: list[LLMMessage], **kwargs: Any
    ) -> tuple[str, TokenUsage]:
        payload = self._build_payload(messages, stream=False, **kwargs)
        body = await self._request_json(await self._post_json(payload))
        choices = body.get("choices") or []
        if not choices:
            raise LLMError(
                f"Empty choices returned by provider {self.config.model!r} "
                f"at {self._base_url}{_COMPLETIONS_PATH}"
            )
        message = choices[0].get("message") or {}
        return str(message.get("content") or ""), _parse_usage(body.get("usage"))

    async def chat_stream(
        self, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        payload = self._build_payload(messages, stream=True, **kwargs)
        try:
            async with self._client.stream(
                "POST",
                f"{self._base_url}{_COMPLETIONS_PATH}",
                json=payload,
                headers=self._headers(),
            ) as response:
                if response.status_code >= 400:
                    raw = (await response.aread()).decode(errors="replace")
                    raise _status_to_error(response.status_code, raw)
                async for line in response.aiter_lines():
                    stripped = line.strip()
                    if not stripped.startswith("data:"):
                        continue
                    if stripped[len("data:") :].strip() == "[DONE]":
                        break
                    piece = _sse_content(line)
                    if piece:
                        yield piece
        except httpx.HTTPError as exc:
            raise LLMConnectionError(f"Stream failed: {exc}") from exc

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        from selene_space_llm.schemas import extract_structured

        kwargs.setdefault("response_format", {"type": "json_object"})
        try:
            text = await self.chat(messages, **kwargs)
        except LLMBadRequest:
            kwargs.pop("response_format", None)
            text = await self.chat(messages, **kwargs)
        result = extract_structured(text, _AnyObject)  # type: ignore[type-var]
        if not isinstance(result, dict):
            raise LLMError(
                f"LLM structured output was not a JSON object "
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
        payload = self._build_payload(messages, stream=False, **kwargs)
        if tools is not None:
            payload["tools"] = [_openai_tool_spec(t) for t in tools]

        body = await self._request_json(await self._post_json(payload))
        choices = body.get("choices") or []
        if not choices:
            raise LLMError(
                f"Empty choices returned by provider {self.config.model!r} "
                f"for tool-call request"
            )
        message = choices[0].get("message") or {}

        calls: list[ToolCall] = []
        for entry in message.get("tool_calls") or []:
            fn = entry.get("function") or {}
            calls.append(
                ToolCall(
                    id=str(entry.get("id") or ""),
                    name=str(fn.get("name") or ""),
                    arguments=_parse_arguments(fn.get("arguments")),
                )
            )

        return ToolCallResult(
            text=str(message.get("content") or ""),
            tool_calls=calls,
            usage=_parse_usage(body.get("usage")),
            model=body.get("model"),
        )

    async def list_models(self) -> list[dict]:
        """Enumerate models offered by the endpoint (/models)."""
        try:
            response = await self._client.get(
                f"{self._base_url}{_MODELS_PATH}", headers=self._headers()
            )
        except httpx.HTTPError as exc:
            raise LLMConnectionError(f"Model listing failed: {exc}") from exc
        if response.status_code >= 400:
            raise _status_to_error(response.status_code, response.text)
        return list(response.json().get("data") or [])

    # ------------------------------------------------------------------
    # internals
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        messages: list[LLMMessage],
        *,
        stream: bool,
        **overrides: Any,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": overrides.pop("model", self.config.model),
            "messages": [
                {"role": _role_value(m), "content": m.content}
                for m in coerce_messages(messages)
            ],
            "temperature": overrides.pop("temperature", self.config.temperature),
            "stream": stream,
        }
        max_tokens = overrides.pop("max_tokens", self.config.max_tokens)
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        response_format = overrides.pop("response_format", None)
        if response_format is not None and not stream:
            payload["response_format"] = response_format
        payload.update(overrides)
        return payload

    async def _post_json(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post(
                f"{self._base_url}{_COMPLETIONS_PATH}",
                json=payload,
                headers=self._headers(),
            )
        except httpx.HTTPError as exc:
            raise LLMConnectionError(f"Request failed: {exc}") from exc

    async def _request_json(self, response: httpx.Response) -> dict[str, Any]:
        if response.status_code == 400:
            raise LLMBadRequest(response.text)
        if response.status_code >= 400:
            raise _status_to_error(response.status_code, response.text)
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type and "text/event-stream" in content_type:
            raise LLMError(f"Endpoint returned non-JSON body: {response.text[:200]}")
        try:
            data: Any = response.json()
        except ValueError as exc:
            raise LLMError(
                f"Endpoint returned non-JSON body: {response.text[:200]}"
            ) from exc
        if not isinstance(data, dict):
            raise LLMError(f"Endpoint returned non-object JSON: {response.text[:200]}")
        return data


class LLMBadRequest(Exception):
    """Signals the endpoint rejected the request shape (HTTP 400)."""


def _openai_tool_spec(raw: dict[str, Any]) -> dict[str, Any]:
    """Normalize a tool definition to the OpenAI wire shape.

    In-repo tool catalogs emit bare ``{name, description, parameters}``;
    the chat-completions protocol requires ``{"type": "function",
    "function": {...}}``. Already-wrapped specs pass through untouched.
    Caught live 2026-09-27: api.b.ai rejected ``tools[0].type`` missing.
    """
    if "function" in raw or raw.get("type") == "function":
        return raw
    return {
        "type": "function",
        "function": {
            "name": raw.get("name", ""),
            "description": raw.get("description", ""),
            "parameters": raw.get("parameters") or {"type": "object", "properties": {}},
        },
    }


def _parse_usage(raw: Any) -> TokenUsage:
    data = raw if isinstance(raw, dict) else {}
    return TokenUsage(
        prompt_tokens=int(data.get("prompt_tokens") or 0),
        completion_tokens=int(data.get("completion_tokens") or 0),
        total_tokens=int(data.get("total_tokens") or 0),
    )


def _parse_arguments(raw: Any) -> dict:
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    else:
        parsed = raw
    return parsed if isinstance(parsed, dict) else {}


def _sse_content(line: str) -> str | None:
    stripped = line.strip()
    if not stripped.startswith("data:"):
        return None
    data_str = stripped[len("data:") :].strip()
    if data_str == "[DONE]":
        return ""
    try:
        event = json.loads(data_str)
    except json.JSONDecodeError:
        return None
    choices = event.get("choices") or []
    if not choices:
        return None
    delta = choices[0].get("delta") or {}
    content = delta.get("content")
    return content if isinstance(content, str) and content else None


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


__all__ = ["OpenAICompatProvider"]
