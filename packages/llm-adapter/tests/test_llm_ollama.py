"""Behavior locks for selene_space_llm.ollama against a mocked HTTP surface."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest

from selene_space_llm.base import (
    LLMConfig,
    LLMConnectionError,
    LLMError,
    LLMIncompleteStreamError,
    LLMMessage,
    LLMRole,
    LLMTruncatedError,
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
        return cast("dict[str, Any]", json.loads(self.requests[0].content))

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


class TestStreamCompleteness:
    """Ollama says ``done`` even on a clean stop, so only two things are
    suspicious: a done frame that names a ceiling, and a stream that never
    reaches a done frame at all. The clean case (``done`` with no
    ``done_reason``) must keep working -- treating "no reason" as "incomplete"
    would fail every ordinary completion.
    """

    @pytest.mark.asyncio
    async def test_done_without_reason_is_a_clean_stop(self) -> None:
        wire = _ndjson(
            {"message": {"content": "all good"}, "done": False},
            {"message": {"content": ""}, "done": True},
        )
        h = Harness(lambda _: httpx.Response(200, content=wire))
        got = [chunk async for chunk in h.provider.chat_stream(h.turn())]
        assert "".join(got) == "all good"

    @pytest.mark.asyncio
    async def test_num_predict_ceiling_raises_after_delivering_the_text(
        self,
    ) -> None:
        wire = _ndjson(
            {"message": {"content": "半截"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "num_predict"},
        )
        h = Harness(lambda _: httpx.Response(200, content=wire))

        seen: list[str] = []
        with pytest.raises(LLMTruncatedError):
            async for chunk in h.provider.chat_stream(h.turn()):
                seen.append(chunk)
        assert seen == ["半截"]

    @pytest.mark.asyncio
    async def test_length_done_reason_is_also_a_ceiling(self) -> None:
        """Newer Ollama builds report ``done_reason: "length"`` rather than
        ``num_predict``. Both mean the same thing, and the shared reason set
        is what makes the newer spelling work without a second branch.
        """
        wire = _ndjson(
            {"message": {"content": "半截"}, "done": False},
            {"message": {"content": ""}, "done": True, "done_reason": "length"},
        )
        h = Harness(lambda _: httpx.Response(200, content=wire))

        seen: list[str] = []
        with pytest.raises(LLMTruncatedError):
            async for chunk in h.provider.chat_stream(h.turn()):
                seen.append(chunk)
        assert seen == ["半截"]

    @pytest.mark.asyncio
    async def test_stream_without_a_done_frame_is_incomplete(self) -> None:
        wire = _ndjson(
            {"message": {"content": "cut"}, "done": False},
        )
        h = Harness(lambda _: httpx.Response(200, content=wire))

        seen: list[str] = []
        with pytest.raises(LLMIncompleteStreamError):
            async for chunk in h.provider.chat_stream(h.turn()):
                seen.append(chunk)
        assert seen == ["cut"]

    @pytest.mark.asyncio
    async def test_error_in_a_200_body_is_reported_as_itself(self) -> None:
        """Ollama reports failures as ``{"error": ...}`` with a 200 status.

        Without reading that key the caller is told "no content" (or, mid
        stream, "the connection was cut off") and never sees the actionable
        text -- ``pull the model``, ``context window exceeded``.
        """
        wire = _ndjson({"error": "model 'qwen3:32b' not found, try pulling it"})
        h = Harness(lambda _: httpx.Response(200, content=wire))

        delivered: list[str] = []
        with pytest.raises(LLMError, match="not found, try pulling it"):
            async for chunk in h.provider.chat_stream(h.turn()):
                delivered.append(chunk)
        # The error frame carried no text, so nothing may reach the caller:
        # a stray empty fragment would look like a reply that had started.
        assert delivered == []

    @pytest.mark.asyncio
    async def test_error_after_partial_text_also_surfaces_the_message(self) -> None:
        wire = _ndjson(
            {"message": {"content": "partial"}, "done": False},
            {"error": "context window exceeded"},
        )
        h = Harness(lambda _: httpx.Response(200, content=wire))

        seen: list[str] = []
        with pytest.raises(LLMError, match="context window exceeded"):
            async for chunk in h.provider.chat_stream(h.turn()):
                seen.append(chunk)
        assert seen == ["partial"]


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
        h = Harness(
            lambda _: httpx.Response(200, json=_assistant_reply('{"ok": true}'))
        )
        await h.provider.generate_structured(h.turn(), output_schema={"type": "object"})
        assert h.sent_body()["format"] == "json"

    @pytest.mark.asyncio
    async def test_unparseable_content_raises(self) -> None:
        h = Harness(
            lambda _: httpx.Response(200, json=_assistant_reply("not json at all"))
        )
        with pytest.raises(LLMError, match="Failed to parse"):
            await h.provider.generate_structured(
                h.turn(), output_schema={"type": "object"}
            )
