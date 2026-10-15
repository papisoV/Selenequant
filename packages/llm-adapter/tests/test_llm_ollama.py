"""Behavior locks for selene_space_llm.ollama against a mocked HTTP surface."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from selene_space_llm.base import (
    LLMConfig,
    LLMConnectionError,
    LLMError,
    LLMMessage,
    LLMRole,
)
from selene_space_llm.ollama import OllamaProvider


def _assistant_reply(content: str = "hello", done: bool = True) -> dict[str, Any]:
    return {"message": {"role": "assistant", "content": content}, "done": done}


class Harness:
    """Provider wired to a MockTransport that records every outgoing request."""

    def __init__(self, responder: Callable[[httpx.Request], httpx.Response]) -> None:
        self.requests: list[httpx.Request] = []

        def tracked(req: httpx.Request) -> httpx.Response:
            self.requests.append(req)
            return responder(req)

        client = httpx.AsyncClient(transport=httpx.MockTransport(tracked))
        self.provider = OllamaProvider(config=LLMConfig(model="llama3"), client=client)

    def sent_body(self) -> dict[str, Any]:
        return json.loads(self.requests[0].content)

    @staticmethod
    def turn(text: str = "hi") -> list[LLMMessage]:
        return [LLMMessage(role=LLMRole.USER, content=text)]


def _refused(_: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("Connection refused")


# --- chat -------------------------------------------------------------------


class TestChat:
    @pytest.mark.asyncio
    async def test_returns_assistant_content(self) -> None:
        h = Harness(lambda _: httpx.Response(200, json=_assistant_reply("test reply")))
        assert await h.provider.chat(h.turn()) == "test reply"

    @pytest.mark.asyncio
    async def test_connect_failure_names_ollama(self) -> None:
        h = Harness(_refused)
        with pytest.raises(LLMConnectionError, match="Ollama"):
            await h.provider.chat(h.turn())

    @pytest.mark.asyncio
    async def test_http_500_surfaces_status(self) -> None:
        h = Harness(lambda _: httpx.Response(500, text="model not found"))
        with pytest.raises(LLMError, match="500"):
            await h.provider.chat(h.turn())

    @pytest.mark.asyncio
    async def test_request_payload_shape(self) -> None:
        h = Harness(lambda _: httpx.Response(200, json=_assistant_reply("ok")))
        await h.provider.chat(h.turn())
        body = h.sent_body()
        assert body["model"] == "llama3"
        assert body["stream"] is False
        assert "options" in body
        assert "temperature" in body["options"]

    @pytest.mark.asyncio
    async def test_hits_local_chat_endpoint(self) -> None:
        h = Harness(lambda _: httpx.Response(200, json=_assistant_reply("ok")))
        await h.provider.chat(h.turn())
        url = str(h.requests[0].url)
        assert "/api/chat" in url
        assert "localhost:11434" in url


# --- chat_stream ------------------------------------------------------------


def _ndjson(*items: dict[str, Any]) -> bytes:
    return "\n".join(json.dumps(item) for item in items).encode()


class TestStream:
    @pytest.mark.asyncio
    async def test_ndjson_chunks_concatenate_in_order(self) -> None:
        wire = _ndjson(
            {"message": {"content": "Hello"}, "done": False},
            {"message": {"content": " World"}, "done": True},
        )
        h = Harness(lambda _: httpx.Response(200, content=wire))
        got = [chunk async for chunk in h.provider.chat_stream(h.turn())]
        assert "".join(got) == "Hello World"

    @pytest.mark.asyncio
    async def test_connect_failure_during_stream(self) -> None:
        h = Harness(_refused)
        with pytest.raises(LLMConnectionError, match="Ollama"):
            async for _ in h.provider.chat_stream(h.turn()):
                pass


# --- generate_structured ----------------------------------------------------


class TestStructured:
    @pytest.mark.asyncio
    async def test_parses_json_content_into_dict(self) -> None:
        h = Harness(
            lambda _: httpx.Response(
                200, json=_assistant_reply('{"action": "buy", "symbol": "BTC/USDT"}')
            )
        )
        out = await h.provider.generate_structured(
            h.turn("trade"), output_schema={"type": "object"}
        )
        assert out == {"action": "buy", "symbol": "BTC/USDT"}

    @pytest.mark.asyncio
    async def test_demands_json_format_upstream(self) -> None:
        h = Harness(lambda _: httpx.Response(200, json=_assistant_reply('{"ok": true}')))
        await h.provider.generate_structured(h.turn(), output_schema={"type": "object"})
        assert h.sent_body()["format"] == "json"

    @pytest.mark.asyncio
    async def test_unparseable_content_raises(self) -> None:
        h = Harness(lambda _: httpx.Response(200, json=_assistant_reply("not json at all")))
        with pytest.raises(LLMError, match="Failed to parse"):
            await h.provider.generate_structured(h.turn(), output_schema={"type": "object"})
