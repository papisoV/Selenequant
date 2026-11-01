"""Behavior locks for selene_space_llm.openai_compat: status-code mapping,
SSE streaming, usage accounting, and structured-output retry semantics."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any, cast

import httpx
import pytest

from selene_space_llm.base import (
    LLMAuthError,
    LLMConfig,
    LLMConnectionError,
    LLMError,
    LLMIncompleteStreamError,
    LLMMessage,
    LLMRateLimitError,
    LLMRole,
    LLMTruncatedError,
)
from selene_space_llm.openai_compat import OpenAICompatProvider


def _chat_body(content: str = "hello") -> dict[str, Any]:
    return {"choices": [{"message": {"role": "assistant", "content": content}}]}


class Harness:
    """Provider wired to a MockTransport that records every outgoing request."""

    def __init__(
        self,
        responder: Callable[[httpx.Request], httpx.Response],
        *,
        model: str = "gpt-4o",
        base_url: str | None = None,
    ) -> None:
        self.requests: list[httpx.Request] = []

        def tracked(req: httpx.Request) -> httpx.Response:
            self.requests.append(req)
            return responder(req)

        client = httpx.AsyncClient(transport=httpx.MockTransport(tracked))
        self.provider = OpenAICompatProvider(
            config=LLMConfig(model=model, api_key="sk-test", base_url=base_url),
            client=client,
        )

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
    async def test_returns_message_content(self) -> None:
        h = Harness(lambda _: httpx.Response(200, json=_chat_body("test reply")))
        assert await h.provider.chat(h.turn()) == "test reply"

    @pytest.mark.parametrize("status", [401, 403], ids=["unauthorized", "forbidden"])
    @pytest.mark.asyncio
    async def test_auth_statuses_map_to_auth_error(self, status: int) -> None:
        h = Harness(lambda _: httpx.Response(status, text="nope"))
        with pytest.raises(LLMAuthError):
            await h.provider.chat(h.turn())

    @pytest.mark.asyncio
    async def test_429_maps_to_rate_limit_error(self) -> None:
        h = Harness(lambda _: httpx.Response(429, text="Too Many Requests"))
        with pytest.raises(LLMRateLimitError):
            await h.provider.chat(h.turn())

    @pytest.mark.asyncio
    async def test_500_maps_to_connection_error(self) -> None:
        h = Harness(lambda _: httpx.Response(500, text="Internal Server Error"))
        with pytest.raises(LLMConnectionError):
            await h.provider.chat(h.turn())

    @pytest.mark.asyncio
    async def test_zero_choices_is_an_error(self) -> None:
        h = Harness(lambda _: httpx.Response(200, json={"choices": []}))
        with pytest.raises(LLMError, match="Empty choices"):
            await h.provider.chat(h.turn())

    @pytest.mark.asyncio
    async def test_base_url_override_steers_the_request(self) -> None:
        h = Harness(
            lambda _: httpx.Response(200, json=_chat_body("ok")),
            base_url="https://api.deepseek.com/v1",
        )
        await h.provider.chat(h.turn())
        assert "api.deepseek.com" in str(h.requests[0].url)

    @pytest.mark.asyncio
    async def test_payload_carries_model_temperature_and_stream_flag(self) -> None:
        h = Harness(
            lambda _: httpx.Response(200, json=_chat_body("ok")), model="deepseek-chat"
        )
        await h.provider.chat(h.turn(), temperature=0.1)
        body = h.sent_body()
        assert body["model"] == "deepseek-chat"
        assert body["temperature"] == 0.1
        assert body["stream"] is False


# --- chat_with_usage --------------------------------------------------------


class TestChatWithUsage:
    @pytest.mark.asyncio
    async def test_reports_text_alongside_token_counts(self) -> None:
        payload = {
            "choices": [{"message": {"role": "assistant", "content": "test reply"}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 7, "total_tokens": 19},
        }
        h = Harness(lambda _: httpx.Response(200, json=payload))
        content, usage = await h.provider.chat_with_usage(h.turn())
        assert content == "test reply"
        assert (usage.prompt_tokens, usage.completion_tokens, usage.total_tokens) == (
            12,
            7,
            19,
        )

    @pytest.mark.asyncio
    async def test_absent_usage_block_defaults_to_zero(self) -> None:
        h = Harness(lambda _: httpx.Response(200, json=_chat_body("ok")))
        content, usage = await h.provider.chat_with_usage(h.turn())
        assert content == "ok"
        assert usage.total_tokens == 0

    @pytest.mark.asyncio
    async def test_chat_still_returns_bare_string(self) -> None:
        """chat() delegates internally but its public return stays a plain str."""
        payload = {
            "choices": [{"message": {"role": "assistant", "content": "hi"}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
        }
        h = Harness(lambda _: httpx.Response(200, json=payload))
        assert await h.provider.chat(h.turn()) == "hi"


# --- chat_stream ------------------------------------------------------------


def _sse(*deltas: dict[str, Any], finish: str = "stop") -> bytes:
    """Build an SSE completion body.

    A real endpoint reports how it stopped, so the fixture does too: a final
    chunk carrying ``finish_reason`` before ``[DONE]``. Streams that omit it
    are cut off mid-reply and now raise, which is why the default is a clean
    ``stop`` rather than nothing.
    """
    frames = [f'data: {json.dumps({"choices": [{"delta": d}]})}' for d in deltas]
    frames.append(
        f'data: {json.dumps({"choices": [{"delta": {}, "finish_reason": finish}]})}'
    )
    frames.append("data: [DONE]")
    return ("\n\n".join(frames) + "\n").encode()


class TestChatStream:
    @pytest.mark.asyncio
    async def test_sse_deltas_concatenate_in_order(self) -> None:
        wire = _sse({"content": "Hello"}, {"content": " World"})
        h = Harness(lambda _: httpx.Response(200, content=wire))
        got = [chunk async for chunk in h.provider.chat_stream(h.turn())]
        assert "".join(got) == "Hello World"

    @pytest.mark.asyncio
    async def test_delta_without_content_is_skipped(self) -> None:
        wire = _sse({}, {"content": "ok"})
        h = Harness(lambda _: httpx.Response(200, content=wire))
        got = [chunk async for chunk in h.provider.chat_stream(h.turn())]
        assert got == ["ok"]


class TestStreamCompleteness:
    """A clipped reply must not be handed back as if it were whole.

    These lock the wire-level distinction the provider used to discard:
    ``finish_reason`` says *why* the stream stopped, and only a clean stop is
    a usable answer. Fragments already yielded stay valid -- the new part is
    that the caller is told the text is partial instead of silently shipping a
    truncated report.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("reason", ["length", "max_tokens", "max_output_tokens"])
    async def test_token_ceiling_raises_after_delivering_the_text(
        self, reason: str
    ) -> None:
        wire = _sse({"content": "半截回答"}, finish=reason)
        h = Harness(lambda _: httpx.Response(200, content=wire))

        seen: list[str] = []
        with pytest.raises(LLMTruncatedError):
            async for chunk in h.provider.chat_stream(h.turn()):
                seen.append(chunk)
        # The partial text still reaches the caller: raising is how it learns
        # the text is incomplete, not a reason to withhold what arrived.
        assert seen == ["半截回答"]

    @pytest.mark.asyncio
    async def test_body_that_just_stops_is_incomplete(self) -> None:
        """No ``[DONE]`` and no reason: the connection went away mid-answer.

        This is the shape a dropped socket has, and it must not be confused
        with the ``[DONE]`` case below.
        """
        wire = (
            f'data: {json.dumps({"choices": [{"delta": {"content": "half"}}]})}\n'
        ).encode()
        h = Harness(lambda _: httpx.Response(200, content=wire))

        seen: list[str] = []
        with pytest.raises(LLMIncompleteStreamError):
            async for chunk in h.provider.chat_stream(h.turn()):
                seen.append(chunk)
        assert seen == ["half"]

    @pytest.mark.asyncio
    async def test_reason_on_the_same_chunk_as_content_is_seen(self) -> None:
        """Some gateways (vLLM, OpenRouter) put ``finish_reason`` on the chunk
        that also carries the last fragment, instead of a separate chunk.

        A parser that only reads the reason from a content-less chunk would
        drop it and misreport a ceiling stop as a clean finish.
        """
        wire = (
            f'data: {json.dumps({"choices": [{"delta": {"content": "cut"}, "finish_reason": "length"}]})}\n'
        ).encode()
        h = Harness(lambda _: httpx.Response(200, content=wire))

        seen: list[str] = []
        with pytest.raises(LLMTruncatedError):
            async for chunk in h.provider.chat_stream(h.turn()):
                seen.append(chunk)
        assert seen == ["cut"]

    @pytest.mark.asyncio
    async def test_done_sentinel_without_a_reason_is_accepted(self) -> None:
        """``[DONE]`` is the server writing its own terminator, so the stream
        was not dropped. It did not label *why* it ended, but a truncated
        completion is labelled ``length`` and is caught before this -- so
        treating an unlabelled-but-terminated stream as a failure would reject
        every answer from a conforming-but-quiet endpoint.
        """
        wire = (
            "\n\n".join(
                [
                    f'data: {json.dumps({"choices": [{"delta": {"content": "whole"}}]})}',
                    "data: [DONE]",
                ]
            )
            + "\n"
        ).encode()
        h = Harness(lambda _: httpx.Response(200, content=wire))
        got = [chunk async for chunk in h.provider.chat_stream(h.turn())]
        assert "".join(got) == "whole"

    @pytest.mark.asyncio
    async def test_clean_stop_does_not_raise(self) -> None:
        wire = _sse({"content": "complete answer"}, finish="stop")
        h = Harness(lambda _: httpx.Response(200, content=wire))
        got = [chunk async for chunk in h.provider.chat_stream(h.turn())]
        assert "".join(got) == "complete answer"

    @pytest.mark.asyncio
    async def test_empty_body_raises_even_with_a_clean_stop(self) -> None:
        wire = _sse(finish="stop")
        h = Harness(lambda _: httpx.Response(200, content=wire))
        with pytest.raises(LLMError):
            async for _ in h.provider.chat_stream(h.turn()):
                pass

    @pytest.mark.asyncio
    async def test_empty_body_that_hit_the_ceiling_reports_empty_not_truncated(
        self,
    ) -> None:
        """A reasoning model can spend its whole budget without emitting any
        answer text. Advising "raise your token budget" then is misleading --
        the caller needs to know nothing came back at all, so the empty check
        must run before the truncation check.
        """
        wire = _sse(finish="length")
        h = Harness(lambda _: httpx.Response(200, content=wire))

        with pytest.raises(LLMError) as caught:
            async for _ in h.provider.chat_stream(h.turn()):
                pass
        assert type(caught.value) is LLMError
        assert "without returning any content" in str(caught.value)
        # The reason still rides along: an empty reply caused by the ceiling
        # is fixed by raising the budget, and only the reason says so.
        assert "finish_reason='length'" in str(caught.value)

    @pytest.mark.asyncio
    async def test_transport_failure_with_a_blank_message_still_says_why(
        self,
    ) -> None:
        """httpx raises ``ReadError('')`` when a body ends early.

        Interpolating it directly produced ``"Stream failed: "`` -- an error
        that names no cause at all, in the one situation where the caller most
        needs to know their answer was cut off. The message must never come out
        blank.
        """

        def responder(_: httpx.Request) -> httpx.Response:
            raise httpx.ReadError("")

        h = Harness(responder)
        with pytest.raises(LLMConnectionError) as caught:
            async for _ in h.provider.chat_stream(h.turn()):
                pass
        message = str(caught.value)
        assert message.rstrip() != "Stream failed:"
        assert "ReadError" in message


# --- generate_structured ----------------------------------------------------


class TestGenerateStructured:
    @pytest.mark.asyncio
    async def test_parses_json_content(self) -> None:
        h = Harness(
            lambda _: httpx.Response(
                200, json=_chat_body('{"symbol": "BTC/USDT", "side": "buy"}')
            )
        )
        out = await h.provider.generate_structured(
            h.turn("trade intent"), output_schema={"type": "object"}
        )
        assert out == {"symbol": "BTC/USDT", "side": "buy"}

    @pytest.mark.asyncio
    async def test_unparseable_content_raises(self) -> None:
        h = Harness(
            lambda _: httpx.Response(200, json=_chat_body("not valid json {{{"))
        )
        with pytest.raises(LLMError, match="Failed to parse"):
            await h.provider.generate_structured(
                h.turn("trade"), output_schema={"type": "object"}
            )

    @pytest.mark.asyncio
    async def test_first_attempt_requests_json_object_mode(self) -> None:
        h = Harness(lambda _: httpx.Response(200, json=_chat_body('{"ok": true}')))
        await h.provider.generate_structured(h.turn(), output_schema={"type": "object"})
        assert h.sent_body()["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_400_triggers_retry_without_response_format(self) -> None:
        attempts: list[dict[str, Any]] = []

        def responder(req: httpx.Request) -> httpx.Response:
            body = json.loads(req.content)
            attempts.append(body)
            if len(attempts) == 1:
                return httpx.Response(400, text="response_format unsupported")
            return httpx.Response(200, json=_chat_body('{"ok": true}'))

        h = Harness(responder)
        out = await h.provider.generate_structured(
            h.turn(), output_schema={"type": "object"}
        )
        assert out == {"ok": True}
        assert attempts[0]["response_format"] == {"type": "json_object"}
        assert "response_format" not in attempts[1]


# --- error classification ---------------------------------------------------


class TestErrorClassification:
    @pytest.mark.asyncio
    async def test_transport_failure_wraps_as_connection_error(self) -> None:
        h = Harness(_refused)
        with pytest.raises(LLMConnectionError):
            await h.provider.chat(h.turn())


# --- dict message tolerance (live-verification regression) -------------------


class TestDictMessageTolerance:
    """react.py / grid/sentinel tools pass raw dicts, not LLMMessage.

    Caught live 2026-09-26: OpenAICompatProvider blew up with
    ``'dict' object has no attribute 'role'`` on the first real ask.
    """

    @pytest.mark.asyncio
    async def test_chat_accepts_plain_dicts(self) -> None:
        h = Harness(lambda _: httpx.Response(200, json=_chat_body("ok")))
        dict_messages: list[Any] = [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]
        reply = await h.provider.chat(dict_messages)
        assert reply == "ok"
        sent = h.sent_body()["messages"]
        assert sent == [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
        ]

    @pytest.mark.asyncio
    async def test_chat_with_tools_accepts_mixed(self) -> None:
        body = {
            "choices": [
                {"message": {"role": "assistant", "content": "done", "tool_calls": []}}
            ]
        }
        h = Harness(lambda _: httpx.Response(200, json=body))
        mixed: list[Any] = [
            LLMMessage(role=LLMRole.USER, content="obj"),
            {"role": "user", "content": "dict"},
        ]
        result = await h.provider.chat_with_tools(mixed)
        assert result.text == "done"
        assert h.sent_body()["messages"] == [
            {"role": "user", "content": "obj"},
            {"role": "user", "content": "dict"},
        ]


# --- bare tool spec wrapping (live-verification regression) ------------------


class TestBareToolSpecWrapping:
    """Tool catalogs emit {name, description, parameters}; the wire wants
    {"type": "function", "function": {...}}.

    Caught live 2026-09-27: api.b.ai rejected ``tools[0].type is invalid
    or missing`` on the first real glm-5.3-flash ask.
    """

    @pytest.mark.asyncio
    async def test_bare_spec_gets_function_wrapper(self) -> None:
        h = Harness(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "done"}}]
                },
            )
        )
        bare: dict[str, Any] = {
            "name": "market_ticker",
            "description": "Get price",
            "parameters": {
                "type": "object",
                "properties": {"symbol": {"type": "string"}},
                "required": ["symbol"],
            },
        }
        await h.provider.chat_with_tools(h.turn(), tools=[bare])
        sent = h.sent_body()["tools"]
        assert sent == [
            {
                "type": "function",
                "function": {
                    "name": "market_ticker",
                    "description": "Get price",
                    "parameters": {
                        "type": "object",
                        "properties": {"symbol": {"type": "string"}},
                        "required": ["symbol"],
                    },
                },
            }
        ]

    @pytest.mark.asyncio
    async def test_openai_shaped_spec_passes_through_untouched(self) -> None:
        h = Harness(
            lambda _: httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "done"}}]
                },
            )
        )
        wrapped = {
            "type": "function",
            "function": {"name": "t", "description": "d", "parameters": {}},
        }
        await h.provider.chat_with_tools(h.turn(), tools=[wrapped])
        assert h.sent_body()["tools"] == [wrapped]
