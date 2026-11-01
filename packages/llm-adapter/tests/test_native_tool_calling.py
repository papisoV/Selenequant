"""Behavior locks for native function calling (chat_with_tools) across
OpenAI-compat, Ollama, Router, and the base-class fallback path."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from selene_space_llm.base import (
    LLMConfig,
    LLMConnectionError,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRole,
)
from selene_space_llm.ollama import OllamaProvider
from selene_space_llm.openai_compat import OpenAICompatProvider
from selene_space_llm.router import LLMRouter
from selene_space_llm.schemas import ToolCallResult

TICKER_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "market_ticker",
            "description": "Get the current price of a trading pair",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        },
    }
]

DEFAULT_USAGE = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}


def _tool_call(
    call_id: str, name: str = "market_ticker", arguments: str | dict = '{"symbol": "BTC/USDT"}'
) -> dict[str, Any]:
    return {
        "id": call_id,
        "type": "function",
        "function": {"name": name, "arguments": arguments},
    }


def _completion(
    *,
    tool_calls: list[dict[str, Any]] | None = None,
    content: str | None = None,
    usage: dict[str, int] | None = None,
    model: str = "gpt-4o",
) -> dict[str, Any]:
    """Assemble an OpenAI-shaped chat completion carrying optional tool calls."""
    message: dict[str, Any] = {"role": "assistant"}
    if content is not None:
        message["content"] = content
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    finish = "tool_calls" if tool_calls else "stop"
    return {
        "choices": [{"message": message, "finish_reason": finish}],
        "model": model,
        "usage": usage if usage is not None else DEFAULT_USAGE,
    }


def _openai_provider(client: httpx.AsyncClient) -> OpenAICompatProvider:
    return OpenAICompatProvider(
        config=LLMConfig(model="gpt-4o", api_key="sk-test"),
        client=client,
    )


def _ollama_provider(client: httpx.AsyncClient) -> OllamaProvider:
    return OllamaProvider(
        config=LLMConfig(model="llama3", base_url="http://localhost:11434"),
        client=client,
    )


def _client_serving(body: dict[str, Any]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=body))
    )


def _client_raising_connect_error() -> httpx.AsyncClient:
    def refused(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    return httpx.AsyncClient(transport=httpx.MockTransport(refused))


USER_TURN = [LLMMessage(role=LLMRole.USER, content="hi")]


# --- tools parameter on the wire --------------------------------------------


class TestWireFormat:
    @pytest.mark.asyncio
    async def test_tools_list_sent_verbatim(self) -> None:
        seen_bodies: list[dict[str, Any]] = []

        def record(req: httpx.Request) -> httpx.Response:
            seen_bodies.append(json.loads(req.content))
            return httpx.Response(200, json=_completion(content="no tools needed"))

        provider = _openai_provider(httpx.AsyncClient(transport=httpx.MockTransport(record)))
        await provider.chat_with_tools(
            [LLMMessage(role=LLMRole.USER, content="price of BTC")],
            tools=TICKER_TOOLS,
        )
        assert seen_bodies[0]["tools"] == TICKER_TOOLS

    @pytest.mark.asyncio
    async def test_no_tools_means_no_tools_key(self) -> None:
        seen_bodies: list[dict[str, Any]] = []

        def record(req: httpx.Request) -> httpx.Response:
            seen_bodies.append(json.loads(req.content))
            return httpx.Response(200, json=_completion(content="hello"))

        provider = _openai_provider(httpx.AsyncClient(transport=httpx.MockTransport(record)))
        await provider.chat_with_tools(USER_TURN, tools=None)
        assert set(seen_bodies[0]).isdisjoint({"tools"})


# --- tool call parsing ------------------------------------------------------


class TestToolCallParsing:
    @pytest.mark.asyncio
    async def test_single_call_id_name_arguments(self) -> None:
        body = _completion(tool_calls=[_tool_call("call_abc123")])
        provider = _openai_provider(_client_serving(body))
        result = await provider.chat_with_tools(
            [LLMMessage(role=LLMRole.USER, content="btc price")],
            tools=TICKER_TOOLS,
        )
        (only,) = result.tool_calls
        assert only.id == "call_abc123"
        assert only.name == "market_ticker"
        assert only.arguments == {"symbol": "BTC/USDT"}

    @pytest.mark.asyncio
    async def test_parallel_calls_preserved_in_order(self) -> None:
        body = _completion(
            tool_calls=[
                _tool_call("call_1", arguments='{"symbol": "BTC/USDT"}'),
                _tool_call("call_2", arguments='{"symbol": "ETH/USDT"}'),
            ]
        )
        provider = _openai_provider(_client_serving(body))
        result = await provider.chat_with_tools(
            [LLMMessage(role=LLMRole.USER, content="compare")],
            tools=TICKER_TOOLS,
        )
        assert [c.id for c in result.tool_calls] == ["call_1", "call_2"]
        assert {c.name for c in result.tool_calls} == {"market_ticker"}


# --- usage ------------------------------------------------------------------


class TestUsageParsing:
    @pytest.mark.asyncio
    async def test_token_counts_and_model_echoed(self) -> None:
        counts = {"prompt_tokens": 42, "completion_tokens": 13, "total_tokens": 55}
        body = _completion(content="done", usage=counts, model="gpt-4o-mini")
        provider = _openai_provider(_client_serving(body))
        result = await provider.chat_with_tools(USER_TURN)
        assert (result.usage.prompt_tokens,
                result.usage.completion_tokens,
                result.usage.total_tokens) == (42, 13, 55)
        assert result.model == "gpt-4o-mini"


# --- text-only responses ----------------------------------------------------


class TestTextOnlyResponses:
    @pytest.mark.asyncio
    async def test_plain_answer_without_tool_calls(self) -> None:
        body = _completion(content="BTC is at $67,234")
        provider = _openai_provider(_client_serving(body))
        result = await provider.chat_with_tools(
            [LLMMessage(role=LLMRole.USER, content="price?")],
            tools=TICKER_TOOLS,
        )
        assert not result.tool_calls
        assert result.text == "BTC is at $67,234"


# --- base-class fallback ----------------------------------------------------


class TestBaseClassFallback:
    @pytest.mark.asyncio
    async def test_unimplemented_provider_degrades_to_text(self) -> None:
        """Invoking the LLMProvider default directly yields a text-only result."""
        body = {"choices": [{"message": {"role": "assistant", "content": "fallback text"}}]}
        provider = _openai_provider(_client_serving(body))

        result = await LLMProvider.chat_with_tools(provider, USER_TURN)
        assert type(result) is ToolCallResult
        assert result.text == "fallback text"
        assert not result.tool_calls


# --- error paths ------------------------------------------------------------


class TestErrorPaths:
    @pytest.mark.asyncio
    async def test_transport_failure_propagates(self) -> None:
        provider = _openai_provider(_client_raising_connect_error())
        with pytest.raises(LLMConnectionError):
            await provider.chat_with_tools(USER_TURN, tools=TICKER_TOOLS)

    @pytest.mark.asyncio
    async def test_non_json_body_is_flagged(self) -> None:
        client = httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(200, text="not json", headers={"content-type": "text/plain"})
            )
        )
        provider = _openai_provider(client)
        with pytest.raises(LLMError, match="non-JSON"):
            await provider.chat_with_tools(USER_TURN, tools=TICKER_TOOLS)

    @pytest.mark.asyncio
    async def test_corrupt_arguments_become_empty_dict(self) -> None:
        body = _completion(
            tool_calls=[_tool_call("call_bad", arguments="not valid json {{{")]
        )
        provider = _openai_provider(_client_serving(body))
        result = await provider.chat_with_tools(USER_TURN, tools=TICKER_TOOLS)
        (only,) = result.tool_calls
        assert only.arguments == {}


# --- ollama native tool calling ---------------------------------------------


class TestOllamaNativeTools:
    @pytest.mark.asyncio
    async def test_native_dict_arguments_and_counts(self) -> None:
        body = {
            "model": "llama3",
            "message": {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "market_ticker",
                            "arguments": {"symbol": "BTC/USDT"},
                        }
                    }
                ],
            },
            "done": True,
            "prompt_eval_count": 20,
            "eval_count": 8,
        }
        provider = _ollama_provider(_client_serving(body))
        result = await provider.chat_with_tools(
            [LLMMessage(role=LLMRole.USER, content="btc price")],
            tools=TICKER_TOOLS,
        )
        (only,) = result.tool_calls
        assert only.name == "market_ticker"
        assert result.usage.prompt_tokens == 20
        assert result.usage.completion_tokens == 8

    @pytest.mark.asyncio
    async def test_plain_reply_without_tools_stays_text(self) -> None:
        body = {
            "model": "llama3",
            "message": {"role": "assistant", "content": "just text"},
            "done": True,
        }
        provider = _ollama_provider(_client_serving(body))
        result = await provider.chat_with_tools(USER_TURN, tools=None)
        assert isinstance(result, ToolCallResult)
        assert result.text == "just text"


# --- router integration -----------------------------------------------------


class TestRouterToolPath:
    @pytest.mark.asyncio
    async def test_single_member_serves_tool_request(self) -> None:
        primary = _openai_provider(_client_serving(_completion(content="from primary")))
        router = LLMRouter(providers=[("primary", primary)])
        result = await router.chat_with_tools(USER_TURN, tools=TICKER_TOOLS)
        assert result.text == "from primary"

    @pytest.mark.asyncio
    async def test_dead_primary_defers_to_backup(self) -> None:
        primary = _openai_provider(_client_raising_connect_error())
        backup = _openai_provider(_client_serving(_completion(content="from fallback")))
        router = LLMRouter(providers=[("primary", primary), ("fallback", backup)])
        result = await router.chat_with_tools(USER_TURN, tools=TICKER_TOOLS)
        assert result.text == "from fallback"
