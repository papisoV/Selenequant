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
    async def test_any_llm_failure_hands_off_to_backup(
        self, failure: LLMError
    ) -> None:
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
