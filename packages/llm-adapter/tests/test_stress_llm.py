"""Boundary-condition stress tests for llm-adapter (cleanroom hardening).

Covers: extract_structured with truncated/malformed/wrong-typed JSON,
router fallback chain behavior (incl. non-LLMError propagation lock),
stream fallback exhaustion, and concurrent router calls.
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import AsyncIterator
from typing import Any

import pytest

from selene_space_llm.base import (
    LLMConfig,
    LLMConnectionError,
    LLMError,
    LLMMessage,
    LLMProvider,
    LLMRateLimitError,
    LLMRole,
)
from selene_space_llm.router import LLMRouter
from selene_space_llm.schemas import (
    MarketAnalysis,
    TokenUsage,
    ToolCall,
    ToolCallResult,
    extract_structured,
)

_VALID_ANALYSIS = {
    "symbol": "BTC",
    "summary": "s",
    "regime": "r",
    "trend_direction": "up",
    "confidence": 0.5,
}


# ---------------------------------------------------------------------------
# extract_structured malformed inputs
# ---------------------------------------------------------------------------


class TestExtractStructuredMalformed:
    @pytest.mark.parametrize(
        "raw",
        [
            "",
            "   ",
            "{",
            '{"symbol":"BTC","summary":"s","regime":"r",',  # truncation mid-string
            "not json",
            "{}",
            "null",
            "[]",
            '"just a string"',
            "123",
            '{"symbol": 1}',  # wrong type for field
        ],
    )
    def test_malformed_inputs_raise_llm_error(self, raw: str) -> None:
        with pytest.raises(LLMError):
            extract_structured(raw, MarketAnalysis)

    def test_valid_payload_parses(self) -> None:
        result = extract_structured(_VALID_ANALYSIS.__str__().replace("'", '"'), MarketAnalysis)
        assert result.symbol == "BTC"

    def test_extra_fields_tolerated_by_default(self) -> None:
        payload = dict(_VALID_ANALYSIS, unexpected="x")
        import json

        result = extract_structured(json.dumps(payload), MarketAnalysis)
        assert result.confidence == 0.5

    def test_confidence_accepts_any_float_lock_current(self) -> None:
        """MarketAnalysis.confidence has no ge/le constraint — lock that a
        5.0 confidence parses rather than assuming a [0,1] clamp."""
        bad = dict(_VALID_ANALYSIS, confidence=5)
        import json

        result = extract_structured(json.dumps(bad), MarketAnalysis)
        assert result.confidence == 5

    def test_error_message_quotes_raw_head(self) -> None:
        raw = "X" * 500 + " not json"
        with pytest.raises(LLMError, match="XXXX"):
            extract_structured(raw, MarketAnalysis)


class TestSchemaEdges:
    def test_tool_call_arguments_default_empty_dict_not_shared(self) -> None:
        a = ToolCall(name="f")
        b = ToolCall(name="g")
        a.arguments["k"] = 1
        assert b.arguments == {}

    def test_tool_call_string_arguments_rejected(self) -> None:
        with pytest.raises(Exception):
            ToolCall(name="f", arguments="not-a-dict")

    def test_token_usage_negative_allowed_by_model_lock_current(self) -> None:
        usage = TokenUsage(prompt_tokens=-1)
        assert usage.total_tokens == 0

    def test_tool_call_result_defaults(self) -> None:
        res = ToolCallResult()
        assert res.text == ""
        assert res.tool_calls == []
        assert res.model is None


# ---------------------------------------------------------------------------
# Router fallback edges
# ---------------------------------------------------------------------------


class _Scripted(LLMProvider):
    """Returns queued results/exceptions per call."""

    def __init__(self, script: list[Any]) -> None:
        super().__init__(config=LLMConfig(model="scripted"))
        self._script = list(script)
        self.calls = 0

    async def chat(self, messages: list[LLMMessage], **kwargs: Any) -> str:
        self.calls += 1
        item = self._script.pop(0) if self._script else "ok"
        if isinstance(item, Exception):
            raise item
        return str(item)

    def chat_stream(self, messages: list[LLMMessage], **kwargs: Any) -> AsyncIterator[str]:
        raise LLMError("no stream in stub")
        yield ""  # pragma: no cover

    async def generate_structured(
        self, messages: list[LLMMessage], output_schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        item = self._script.pop(0) if self._script else {"ok": True}
        if isinstance(item, Exception):
            raise item
        return item


def _msg() -> list[LLMMessage]:
    return [LLMMessage(role=LLMRole.USER, content="hi")]


class TestRouterFallbackStress:
    def test_empty_provider_list_rejected(self) -> None:
        with pytest.raises(ValueError):
            LLMRouter([])

    def test_fallback_across_llm_errors(self) -> None:
        a = _Scripted([LLMConnectionError("down")])
        b = _Scripted(["recovered"])
        router = LLMRouter([("a", a), ("b", b)])
        assert asyncio.run(router.chat(_msg())) == "recovered"
        assert a.calls == 1 and b.calls == 1

    def test_all_fail_raises_aggregated_llm_error(self) -> None:
        a = _Scripted([LLMConnectionError("x")])
        b = _Scripted([LLMRateLimitError("y", retry_after=1)])
        router = LLMRouter([("a", a), ("b", b)])
        with pytest.raises(LLMError, match="All LLM providers failed"):
            asyncio.run(router.chat(_msg()))

    def test_non_llmerror_propagates_without_fallback(self) -> None:
        """Lock current contract: only LLMError triggers fallback; an
        internal provider bug (RuntimeError) surfaces to the caller."""
        a = _Scripted([RuntimeError("provider bug")])
        b = _Scripted(["never reached"])
        router = LLMRouter([("a", a), ("b", b)])
        with pytest.raises(RuntimeError, match="provider bug"):
            asyncio.run(router.chat(_msg()))
        assert b.calls == 0

    def test_stream_fallback_on_llm_error(self) -> None:
        class StreamFail(LLMProvider):
            def __init__(self) -> None:
                super().__init__(config=LLMConfig(model="sf"))

            async def chat(self, messages, **kwargs):
                return ""

            def chat_stream(self, messages, **kwargs) -> AsyncIterator[str]:
                raise LLMConnectionError("no stream")
                yield ""  # pragma: no cover

            async def generate_structured(self, messages, schema, **kwargs):
                return {}

        class StreamOk(LLMProvider):
            def __init__(self) -> None:
                super().__init__(config=LLMConfig(model="so"))
                self._done = False

            async def chat(self, messages, **kwargs):
                return ""

            def chat_stream(self, messages, **kwargs) -> AsyncIterator[str]:
                return self._gen()

            async def _gen(self) -> AsyncIterator[str]:  # true async generator
                yield "chunk1"
                yield "chunk2"

            async def generate_structured(self, messages, schema, **kwargs):
                return {}

        router = LLMRouter([("bad", StreamFail()), ("good", StreamOk())])

        async def collect() -> list[str]:
            return [chunk async for chunk in router.chat_stream(_msg())]

        assert asyncio.run(collect()) == ["chunk1", "chunk2"]

    def test_stream_all_fail_raises_llm_error(self) -> None:
        class NoStream(LLMProvider):
            def __init__(self) -> None:
                super().__init__(config=LLMConfig(model="ns"))

            async def chat(self, messages, **kwargs):
                return ""

            def chat_stream(self, messages, **kwargs) -> AsyncIterator[str]:
                raise LLMConnectionError("no stream")
                yield ""  # pragma: no cover

            async def generate_structured(self, messages, schema, **kwargs):
                return {}

        router = LLMRouter([("only", NoStream())])

        async def collect() -> list[str]:
            return [chunk async for chunk in router.chat_stream(_msg())]

        with pytest.raises(LLMError):
            asyncio.run(collect())

    def test_health_check_reports_each_backend(self) -> None:
        good = _Scripted(["pong"])
        bad = _Scripted([LLMAuthStub()])
        router = LLMRouter([("good", good), ("bad", bad)])
        health = asyncio.run(router.health_check())
        assert [h.available for h in health] == [True, False]
        assert health[0].name == "good"

    def test_concurrent_chat_calls_independent_fallbacks(self) -> None:
        """Two providers each fail once per call; N concurrent chats must all
        land on the healthy tail without cross-talk."""

        class FailOnce(LLMProvider):
            def __init__(self) -> None:
                super().__init__(config=LLMConfig(model="fo"))
                self._lock = threading.Lock()

            async def chat(self, messages, **kwargs):
                with self._lock:
                    pass  # always fails below
                raise LLMConnectionError("always down")

            def chat_stream(self, messages, **kwargs):
                raise LLMError("none")
                yield ""  # pragma: no cover

            async def generate_structured(self, messages, schema, **kwargs):
                return {}

        ok = _Scripted(["ok"] * 20)
        router = LLMRouter([("down", FailOnce()), ("up", ok)])

        async def run_all() -> list[str]:
            async def run_one() -> str:
                return await router.chat(_msg())

            return await asyncio.gather(*(run_one() for _ in range(20)))

        results = asyncio.run(run_all())
        assert all(r == "ok" for r in results)
        assert ok.calls == 20


def LLMAuthStub():
    return LLMAuthError("denied")


from selene_space_llm.base import LLMAuthError  # noqa: E402  (used above)
