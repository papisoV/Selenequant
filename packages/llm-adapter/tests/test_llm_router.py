"""Behavior locks for selene_space_llm.router: failover chain semantics
across chat / stream / structured generation plus health reporting."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from selene_space_llm.base import (
    LLMAuthError,
    LLMConfig,
    LLMConnectionError,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRateLimitError,
    LLMRole,
    LLMStreamInterruptedError,
    LLMTruncatedError,
)
from selene_space_llm.router import LLMRouter


class Steady(LLMProvider):
    """Always answers with its configured reply (stream splits on spaces)."""

    def __init__(self, reply: str = "ok") -> None:
        super().__init__(config=LLMConfig(model="steady"))
        self._reply = reply

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        return self._reply

    async def chat_stream(
        self, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        for word in self._reply.split():
            yield word

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        return {"result": self._reply}


class Broken(LLMProvider):
    """Always raises the injected LLM failure on every primitive."""

    def __init__(self, failure: LLMError) -> None:
        super().__init__(config=LLMConfig(model="broken"))
        self._failure = failure

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        raise self._failure

    async def chat_stream(
        self, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        raise self._failure
        yield ""  # noqa: B901  # unreachable: makes this an async generator

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise self._failure


USER_TURN = [LLMMessage(role=LLMRole.USER, content="test")]

_PRIMARY_FAILURES = [
    LLMConnectionError("down"),
    LLMRateLimitError("429"),
    LLMAuthError("bad key"),
]


# --- construction -----------------------------------------------------------


class TestConstruction:
    def test_empty_chain_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least one"):
            LLMRouter([])

    def test_registration_order_is_preserved(self) -> None:
        router = LLMRouter(
            [
                ("primary", Steady("a")),
                ("fallback", Steady("b")),
            ]
        )
        assert router.provider_names == ["primary", "fallback"]


# --- chat failover ----------------------------------------------------------


class TestChatFailover:
    @pytest.mark.asyncio
    async def test_healthy_primary_answers_first(self) -> None:
        router = LLMRouter(
            [
                ("primary", Steady("primary_response")),
                ("fallback", Steady("fallback_response")),
            ]
        )
        assert await router.chat(USER_TURN) == "primary_response"

    @pytest.mark.parametrize(
        "failure", _PRIMARY_FAILURES, ids=["conn-refused", "rate-limit", "auth"]
    )
    @pytest.mark.asyncio
    async def test_any_llm_failure_hands_off_to_backup(self, failure: LLMError) -> None:
        router = LLMRouter(
            [
                ("primary", Broken(failure)),
                ("fallback", Steady("fallback_response")),
            ]
        )
        assert await router.chat(USER_TURN) == "fallback_response"

    @pytest.mark.asyncio
    async def test_total_outage_raises_aggregate_error(self) -> None:
        router = LLMRouter(
            [
                ("primary", Broken(LLMConnectionError("down"))),
                ("fallback", Broken(LLMAuthError("bad key"))),
            ]
        )
        with pytest.raises(LLMError, match="All LLM providers failed"):
            await router.chat(USER_TURN)

    @pytest.mark.asyncio
    async def test_three_tier_chain_lands_on_healthy_tail(self) -> None:
        router = LLMRouter(
            [
                ("openai", Broken(LLMConnectionError("timeout"))),
                ("deepseek", Broken(LLMRateLimitError("429"))),
                ("ollama", Steady("local_response")),
            ]
        )
        assert await router.chat(USER_TURN) == "local_response"


# --- stream failover --------------------------------------------------------


class TestStreamFailover:
    @pytest.mark.asyncio
    async def test_primary_streams_word_split(self) -> None:
        router = LLMRouter([("primary", Steady("hello world"))])
        got = [chunk async for chunk in router.chat_stream(USER_TURN)]
        assert got == ["hello", "world"]

    @pytest.mark.asyncio
    async def test_dead_primary_switches_stream_source(self) -> None:
        router = LLMRouter(
            [
                ("primary", Broken(LLMConnectionError("down"))),
                ("fallback", Steady("fallback ok")),
            ]
        )
        got = [chunk async for chunk in router.chat_stream(USER_TURN)]
        assert got == ["fallback", "ok"]


class HalfThenFails(LLMProvider):
    """Emits some text, then blows up -- the shape a mid-stream disconnect has
    once the provider layers have already handed fragments to the caller."""

    def __init__(self, prefix: list[str], failure: LLMError) -> None:
        super().__init__(config=LLMConfig(model="half"))
        self._prefix = prefix
        self._failure = failure

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        raise self._failure

    async def chat_stream(
        self, messages: list[LLMMessage], **kwargs: Any
    ) -> AsyncIterator[str]:
        for piece in self._prefix:
            yield piece
        raise self._failure

    async def generate_structured(
        self,
        messages: list[LLMMessage],
        output_schema: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        raise self._failure


class TestStreamSplice:
    """Falling back *after* text has been emitted would hand the caller one
    string stitched from two models' answers with nothing marking the seam.
    The router must refuse, so the failure surfaces as an error the caller can
    act on instead of a silently wrong reply.
    """

    @pytest.mark.asyncio
    async def test_mid_stream_failure_does_not_splice_the_fallback_answer(
        self,
    ) -> None:
        router = LLMRouter(
            [
                (
                    "primary",
                    HalfThenFails(["AI 复盘前半段"], LLMConnectionError("reset")),
                ),
                ("fallback", Steady("完整答案从头开始")),
            ]
        )

        seen: list[str] = []
        with pytest.raises(LLMStreamInterruptedError):
            async for chunk in router.chat_stream(USER_TURN):
                seen.append(chunk)

        # What was already delivered stays delivered; the fallback's words must
        # never appear, or a reader cannot tell where one answer ends.
        assert seen == ["AI 复盘前半段"]
        assert "完整答案从头开始" not in "".join(seen)

    @pytest.mark.asyncio
    async def test_mid_stream_truncation_also_refuses_to_splice(self) -> None:
        router = LLMRouter(
            [
                ("primary", HalfThenFails(["half"], LLMTruncatedError("ceiling"))),
                ("fallback", Steady("second model answer")),
            ]
        )
        with pytest.raises(LLMStreamInterruptedError):
            async for _ in router.chat_stream(USER_TURN):
                pass

    @pytest.mark.asyncio
    async def test_failure_before_any_text_still_falls_back(self) -> None:
        """The refusal above must not turn every failover into an error: a
        provider that dies before emitting anything has produced nothing to
        protect, so the next one may answer.
        """

        router = LLMRouter(
            [
                ("primary", HalfThenFails([], LLMConnectionError("down"))),
                ("fallback", Steady("fallback ok")),
            ]
        )
        got = [chunk async for chunk in router.chat_stream(USER_TURN)]
        assert got == ["fallback", "ok"]

    @pytest.mark.asyncio
    async def test_empty_fragment_does_not_block_fallback(self) -> None:
        """An empty string carries no text to protect.

        Treating it as "already delivered" would refuse the fallback over
        nothing at all, stranding a request that another backend could have
        answered.
        """
        router = LLMRouter(
            [
                ("primary", HalfThenFails(["", ""], LLMConnectionError("down"))),
                ("fallback", Steady("fallback ok")),
            ]
        )
        got = [chunk async for chunk in router.chat_stream(USER_TURN)]
        # The empties still pass through -- the router forwards what a provider
        # gives it -- but they must not count as "already delivered". Had they
        # counted, chat_stream would have raised instead of reaching this line,
        # and only the fallback's words may appear in the result.
        assert [chunk for chunk in got if chunk] == ["fallback", "ok"]


# --- structured failover ----------------------------------------------------


class TestStructuredFailover:
    @pytest.mark.asyncio
    async def test_primary_serves_structured(self) -> None:
        router = LLMRouter([("primary", Steady("ok"))])
        assert await router.generate_structured(USER_TURN, {"type": "object"}) == {
            "result": "ok"
        }

    @pytest.mark.asyncio
    async def test_failed_primary_defers_to_backup(self) -> None:
        router = LLMRouter(
            [
                ("primary", Broken(LLMConnectionError("down"))),
                ("fallback", Steady("fallback")),
            ]
        )
        assert await router.generate_structured(USER_TURN, {"type": "object"}) == {
            "result": "fallback"
        }


# --- health -----------------------------------------------------------------


class TestHealthReporting:
    @pytest.mark.asyncio
    async def test_every_member_reported_available(self) -> None:
        router = LLMRouter(
            [
                ("primary", Steady("ok")),
                ("fallback", Steady("ok")),
            ]
        )
        report = await router.health_check()
        assert len(report) == 2
        assert all(entry.available for entry in report)

    @pytest.mark.asyncio
    async def test_partial_outage_marks_only_broken_member(self) -> None:
        router = LLMRouter(
            [
                ("primary", Broken(LLMConnectionError("down"))),
                ("fallback", Steady("ok")),
            ]
        )
        report = await router.health_check()
        assert report[0].available is False
        assert report[0].error is not None
        assert report[1].available is True
        assert report[1].error is None
