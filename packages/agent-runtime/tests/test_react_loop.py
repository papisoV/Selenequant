"""Behaviour locks for the ReAct runtime loop: think/act/reflect/answer
cycle, multi-round tool use, loop detection, timeout fallback, tool-error
recovery, no-progress abort, and event ordering."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from selene_space_agent.context.manager import ContextManager
from selene_space_agent.prompt_builder import AgentContext
from selene_space_agent.react import ReActAgentRuntime
from selene_space_agent.tool_catalog import ToolCatalog
from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_llm.schemas import TokenUsage, ToolCall, ToolCallResult
from selene_space_types.agent import AgentStreamEventType
from selene_space_types.risk import RiskLevel


def _tool(slug: str, reply: str = "fine") -> BaseTool:
    class _T(BaseTool):
        @property
        def name(self) -> str:
            return slug

        @property
        def description(self) -> str:
            return f"stub {slug}"

        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}, "required": []}

        @property
        def risk_level(self) -> RiskLevel:
            return RiskLevel.SAFE

        def execute(self, args: dict[str, Any]) -> ToolResult:
            return ToolResult(output=reply)

    return _T()


class _ScriptedLLM:
    """Replays a queue of ToolCallResults for chat_with_tools."""

    def __init__(self, script: list[ToolCallResult]) -> None:
        self._script = list(script)
        self._cursor = 0

    async def chat(self, messages: list[Any], **kwargs: Any) -> str:
        return "plain chat"

    async def chat_stream(
        self, messages: list[Any], **kwargs: Any
    ) -> AsyncIterator[str]:
        yield "stub"

    async def chat_with_tools(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolCallResult:
        if self._cursor < len(self._script):
            step = self._script[self._cursor]
            self._cursor += 1
            return step
        return ToolCallResult(text="script exhausted.")

    async def generate_structured(
        self, messages: list[Any], output_schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        return {}


def _runtime(
    llm: Any,
    slugs: list[str] | None = None,
    max_rounds: int = 10,
    react_enabled: bool = True,
    memory_recall: Any = None,
) -> ReActAgentRuntime:
    catalog = ToolCatalog()
    for slug in slugs or ["market_ticker"]:
        catalog.register(_tool(slug, f"{slug} answered"))
    ctx = AgentContext(react_enabled=react_enabled, max_tool_rounds=max_rounds)
    return ReActAgentRuntime(
        llm=llm,
        tool_catalog=catalog,
        context_manager=ContextManager(),
        prompt_context=ctx,
        max_tool_rounds=max_rounds,
        memory_recall=memory_recall,
    )


async def _drain(runtime: ReActAgentRuntime, message: str) -> list[Any]:
    bag: list[Any] = []
    async for event in runtime.process_message(message):
        bag.append(event)
    return bag


async def _drain_model(
    runtime: ReActAgentRuntime, message: str, model: str | None
) -> list[Any]:
    bag: list[Any] = []
    async for event in runtime.process_message(message, model=model):
        bag.append(event)
    return bag


class _RecordingLLM(_ScriptedLLM):
    """Scripted LLM that records the kwargs of every chat_with_tools call.

    ``config`` mimics the real provider's shared ``LLMConfig`` surface so a
    test can assert the override never mutates it.
    """

    def __init__(self, script: list[ToolCallResult], *, config_model: str = "base") -> None:
        super().__init__(script)
        self.seen_kwargs: list[dict[str, Any]] = []
        self.config = SimpleNamespace(model=config_model)

    async def chat_with_tools(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolCallResult:
        self.seen_kwargs.append(dict(kwargs))
        return await super().chat_with_tools(messages, tools=tools, **kwargs)


def _ticker_call(cid: str = "c1", symbol: str = "SOL") -> ToolCallResult:
    return ToolCallResult(
        tool_calls=[
            ToolCall(id=cid, name="market_ticker", arguments={"symbol": symbol})
        ],
        usage=TokenUsage(total_tokens=20),
    )


class TestFullCycle:
    @pytest.mark.asyncio
    async def test_think_act_reflect_answer_sequence(self) -> None:
        llm = _ScriptedLLM(
            [
                _ticker_call(),
                ToolCallResult(
                    text="SOL trades near $153.", usage=TokenUsage(total_tokens=15)
                ),
            ]
        )
        events = await _drain(_runtime(llm), "SOL quote?")

        kinds = [e.type for e in events]
        for expected in (
            AgentStreamEventType.THINKING,
            AgentStreamEventType.TOOL_CALL,
            AgentStreamEventType.TOOL_RESULT,
            AgentStreamEventType.REFLECTION,
            AgentStreamEventType.TEXT_DELTA,
            AgentStreamEventType.DONE,
        ):
            assert expected in kinds
        assert events[-1].type == AgentStreamEventType.DONE

        deltas = [e for e in events if e.type == AgentStreamEventType.TEXT_DELTA]
        assert any("153" in e.data.get("text", "") for e in deltas)


class TestMultiRoundTools:
    @pytest.mark.asyncio
    async def test_two_tool_calls_then_answer(self) -> None:
        llm = _ScriptedLLM(
            [
                _ticker_call("r1"),
                _ticker_call("r2"),
                ToolCallResult(
                    text="Quotes gathered.", usage=TokenUsage(total_tokens=15)
                ),
            ]
        )
        events = await _drain(_runtime(llm), "quote again")

        calls = [e for e in events if e.type == AgentStreamEventType.TOOL_CALL]
        checks = (len(calls) == 2, events[-1].type == AgentStreamEventType.DONE)
        assert all(checks)


class TestLoopDetection:
    @pytest.mark.asyncio
    async def test_identical_call_thrice_trips_guard(self) -> None:
        repeated = _ticker_call()
        llm = _ScriptedLLM([repeated, repeated, repeated])
        events = await _drain(_runtime(llm), "stuck?")

        deltas = [e for e in events if e.type == AgentStreamEventType.TEXT_DELTA]
        checks = (
            any("loop detected" in e.data.get("text", "").lower() for e in deltas),
            events[-1].type == AgentStreamEventType.DONE,
        )
        assert all(checks)


class TestTimeoutFallback:
    @pytest.mark.asyncio
    async def test_llm_timeout_degrades_gracefully(self) -> None:
        class _HangingLLM:
            async def chat(self, messages: list[Any], **kwargs: Any) -> str:
                return "degraded"

            async def chat_stream(
                self, messages: list[Any], **kwargs: Any
            ) -> AsyncIterator[str]:
                yield "stub"

            async def chat_with_tools(
                self, messages: list[Any], **kwargs: Any
            ) -> ToolCallResult:
                raise TimeoutError("upstream stall")

            async def generate_structured(
                self, messages: list[Any], **kwargs: Any
            ) -> dict:
                return {}

        events = await _drain(_runtime(_HangingLLM()), "anyone there?")

        checks = (
            events[-1].type == AgentStreamEventType.DONE,
            len([e for e in events if e.type == AgentStreamEventType.TEXT_DELTA]) >= 1,
        )
        assert all(checks)


class TestTextOnlyFallback:
    @pytest.mark.asyncio
    async def test_non_tool_llm_still_answers(self) -> None:
        class _ChatOnlyLLM:
            turns = 0

            async def chat(self, messages: list[Any], **kwargs: Any) -> str:
                type(self).turns += 1
                return "garbage {{{" if type(self).turns == 1 else "Here you go"

            async def chat_stream(
                self, messages: list[Any], **kwargs: Any
            ) -> AsyncIterator[str]:
                yield "stub"

            async def generate_structured(
                self, messages: list[Any], **kwargs: Any
            ) -> dict:
                return {}

        events = await _drain(_runtime(_ChatOnlyLLM()), "help")

        deltas = [e for e in events if e.type == AgentStreamEventType.TEXT_DELTA]
        checks = (len(deltas) >= 1, events[-1].type == AgentStreamEventType.DONE)
        assert all(checks)


class TestToolErrorResilience:
    @pytest.mark.asyncio
    async def test_crashing_tool_surfaces_error_and_recovers(self) -> None:
        class _Detonator(BaseTool):
            @property
            def name(self) -> str:
                return "detonator"

            @property
            def description(self) -> str:
                return "always explodes"

            @property
            def parameters(self) -> dict[str, Any]:
                return {"type": "object", "properties": {}, "required": []}

            @property
            def risk_level(self) -> RiskLevel:
                return RiskLevel.SAFE

            def execute(self, args: dict[str, Any]) -> ToolResult:
                raise RuntimeError("tool blew up")

        catalog = ToolCatalog()
        catalog.register(_Detonator())
        llm = _ScriptedLLM(
            [
                ToolCallResult(
                    tool_calls=[ToolCall(id="c1", name="detonator", arguments={})],
                    usage=TokenUsage(total_tokens=10),
                ),
                ToolCallResult(
                    text="Recovered anyway.", usage=TokenUsage(total_tokens=10)
                ),
            ]
        )
        runtime = ReActAgentRuntime(
            llm=llm,
            tool_catalog=catalog,
            context_manager=ContextManager(),
            prompt_context=AgentContext(react_enabled=True),
        )
        events = await _drain(runtime, "use it")

        results = [e for e in events if e.type == AgentStreamEventType.TOOL_RESULT]
        checks = (
            len(results) == 1,
            "error" in results[0].data.get("error", "").lower(),
            events[-1].type == AgentStreamEventType.DONE,
        )
        assert all(checks)


class TestNoProgressAbort:
    @pytest.mark.asyncio
    async def test_empty_replies_eventually_abort(self) -> None:
        blanks = [
            ToolCallResult(text="", usage=TokenUsage(total_tokens=5)) for _ in range(5)
        ]
        events = await _drain(_runtime(_ScriptedLLM(blanks)), "echo chamber")

        deltas = [e for e in events if e.type == AgentStreamEventType.TEXT_DELTA]
        checks = (
            any("progress" in e.data.get("text", "").lower() for e in deltas),
            events[-1].type == AgentStreamEventType.DONE,
        )
        assert all(checks)


class TestMaxRoundsWrapUp:
    """§2.5 (open-source-reference 吸收): 工具轮数用尽时不是硬停,而是注入一条
    "工具轮已用尽,立即据上文写最终报告"的指令再问一次(工具已解绑),
    让模型体面交出答案并说明哪些数据没取到 —— 对齐 TA `setup.py:76-89`
    的 wrap_up 节点。"""

    @pytest.mark.asyncio
    async def test_exhausted_rounds_asks_for_final_report(self) -> None:
        # 三轮全是工具调用(参数各异,避开 loop 守卫),永不给出最终文本 → 触发 wrap-up
        calls = [_ticker_call(f"r{i}", f"SYM{i}") for i in range(3)]
        final = ToolCallResult(
            text="Final report from gathered data.", usage=TokenUsage(total_tokens=15)
        )
        llm = _ToolCapturingLLM([*calls, final])
        events = await _drain(_runtime(llm, max_rounds=3), "deep dive")

        deltas = [
            e.data.get("text", "")
            for e in events
            if e.type == AgentStreamEventType.TEXT_DELTA
        ]
        assert any("Final report from gathered data." in t for t in deltas), deltas

    @pytest.mark.asyncio
    async def test_wrap_up_round_is_tools_unbound(self) -> None:
        calls = [_ticker_call(f"r{i}", f"SYM{i}") for i in range(3)]
        final = ToolCallResult(text="Wrapped up.", usage=TokenUsage(total_tokens=15))
        llm = _ToolCapturingLLM([*calls, final])
        await _drain(_runtime(llm, max_rounds=3), "deep dive")

        # 常规轮绑定了工具;最后那轮(wrap-up)必须 tools=None
        bound = [tools for _msgs, tools in llm.calls]
        checks = (
            len(llm.calls) == 4,  # 3 常规 + 1 wrap-up
            bound[0] is not None  # 常规轮:有工具
            and bound[-1] is None,  # wrap-up 轮:无工具
        )
        assert all(checks), bound

    @pytest.mark.asyncio
    async def test_wrap_up_instruction_present_in_final_messages(self) -> None:
        calls = [_ticker_call(f"r{i}", f"SYM{i}") for i in range(3)]
        final = ToolCallResult(text="Done.", usage=TokenUsage(total_tokens=15))
        llm = _ToolCapturingLLM([*calls, final])
        await _drain(_runtime(llm, max_rounds=3), "deep dive")

        final_msgs = llm.calls[-1][0]
        blob = " ".join(str(m.get("content", "")) for m in final_msgs).lower()
        checks = (
            "tool round" in blob or "报告" in blob or "report" in blob,
            "could not" in blob or "未取到" in blob or "unable" in blob or "缺" in blob,
        )
        assert all(checks), blob[-500:] if isinstance(blob, str) else blob

    @pytest.mark.asyncio
    async def test_wrap_up_failure_falls_back_to_notice(self) -> None:
        # wrap-up 那一问也失败(超时)→ 不能吞掉,必须给用户可见的降级提示,
        # 且仍以 DONE 收尾(不 hang、不抛)。用 max_rounds 用尽后 script 返空文本模拟。
        class _WrapUpExplodes(_ScriptedLLM):
            def __init__(self) -> None:
                super().__init__([_ticker_call("r0")])

            async def chat_with_tools(
                self,
                messages: list[Any],
                tools: list[dict[str, Any]] | None = None,
                **kwargs: Any,
            ) -> ToolCallResult:
                # 只有在 wrap-up 轮(工具解绑)才炸;常规轮正常返回工具调用
                if tools is None:
                    raise RuntimeError("wrap-up provider blew up")
                return await super().chat_with_tools(messages, tools=tools, **kwargs)

        events = await _drain(_runtime(_WrapUpExplodes(), max_rounds=1), "go")

        deltas = [
            e.data.get("text", "")
            for e in events
            if e.type == AgentStreamEventType.TEXT_DELTA
        ]
        checks = (
            len(deltas) >= 1,
            any("maximum" in t.lower() or "round" in t.lower() for t in deltas),
            events[-1].type == AgentStreamEventType.DONE,
        )
        assert all(checks), (deltas, events[-1].type)

    @pytest.mark.asyncio
    async def test_no_wrap_up_when_model_answers_in_budget(self) -> None:
        # 在预算内就给出最终文本 → 不该多打一次 LLM(wrap-up 只在用尽时发生)
        llm = _ToolCapturingLLM(
            [
                _ticker_call(),
                ToolCallResult(text="Answered.", usage=TokenUsage(total_tokens=5)),
            ]
        )
        await _drain(_runtime(llm, max_rounds=10), "quick")

        assert len(llm.calls) == 2, len(llm.calls)


class TestReactDisabled:
    @pytest.mark.asyncio
    async def test_plain_mode_still_streams_answer(self) -> None:
        # react_enabled=False strips the reasoning protocol from the prompt;
        # THINKING internals may still fire but text + DONE must arrive.
        llm = _ScriptedLLM(
            [ToolCallResult(text="Plain reply.", usage=TokenUsage(total_tokens=10))]
        )
        events = await _drain(_runtime(llm, react_enabled=False), "hi")

        deltas = [e for e in events if e.type == AgentStreamEventType.TEXT_DELTA]
        checks = (len(deltas) >= 1, events[-1].type == AgentStreamEventType.DONE)
        assert all(checks)


class TestEventOrdering:
    @pytest.mark.asyncio
    async def test_thinking_precedes_first_tool_call(self) -> None:
        llm = _ScriptedLLM(
            [
                _ticker_call(),
                ToolCallResult(text="done", usage=TokenUsage(total_tokens=5)),
            ]
        )
        kinds = [e.type for e in await _drain(_runtime(llm), "go")]
        assert kinds.index(AgentStreamEventType.THINKING) < kinds.index(
            AgentStreamEventType.TOOL_CALL
        )

    @pytest.mark.asyncio
    async def test_reflection_follows_tool_result(self) -> None:
        llm = _ScriptedLLM(
            [
                _ticker_call(),
                ToolCallResult(text="answer", usage=TokenUsage(total_tokens=5)),
            ]
        )
        kinds = [e.type for e in await _drain(_runtime(llm), "go")]

        result_idx = kinds.index(AgentStreamEventType.TOOL_RESULT)
        reflections = [
            i for i, k in enumerate(kinds) if k == AgentStreamEventType.REFLECTION
        ]
        assert any(ri > result_idx for ri in reflections)


class _CapturingLLM(_ScriptedLLM):
    """Records the message lists passed to chat_with_tools."""

    def __init__(self, script: list[ToolCallResult]) -> None:
        super().__init__(script)
        self.seen_messages: list[list[Any]] = []

    async def chat_with_tools(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolCallResult:
        self.seen_messages.append(list(messages))
        return await super().chat_with_tools(messages, tools=tools, **kwargs)


class _ToolCapturingLLM:
    """Replays a script and records ``(messages, tools)`` for every call.

    ``tools`` is what the runtime bound this round: the catalog specs on an
    ordinary round, ``None`` on the tools-free wrap-up round."""

    def __init__(self, script: list[ToolCallResult]) -> None:
        self._script = list(script)
        self._cursor = 0
        self.calls: list[tuple[list[Any], Any]] = []

    async def chat(self, messages: list[Any], **kwargs: Any) -> str:
        return "plain chat"

    async def chat_stream(
        self, messages: list[Any], **kwargs: Any
    ) -> AsyncIterator[str]:
        yield "stub"

    async def chat_with_tools(
        self,
        messages: list[Any],
        tools: list[dict[str, Any]] | None = None,
        **kwargs: Any,
    ) -> ToolCallResult:
        self.calls.append((list(messages), tools))
        if self._cursor < len(self._script):
            step = self._script[self._cursor]
            self._cursor += 1
            return step
        return ToolCallResult(text="")

    async def generate_structured(
        self, messages: list[Any], output_schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        return {}


class TestMemoryRecallHook:
    @pytest.mark.asyncio
    async def test_matching_message_injects_memory_context(self) -> None:
        llm = _CapturingLLM(
            [ToolCallResult(text="answer", usage=TokenUsage(total_tokens=5))]
        )
        runtime = _runtime(
            llm,
            memory_recall=lambda q: (
                "- [NO-GO 2026-09-18] whale claim" if "鲸鱼" in q else ""
            ),
        )
        await _drain(runtime, "鲸鱼流入要跌吗")

        system = llm.seen_messages[0][0]["content"]
        assert "## Memory Context" in system
        assert "NO-GO" in system

    @pytest.mark.asyncio
    async def test_non_matching_message_has_no_memory_section(self) -> None:
        llm = _CapturingLLM(
            [ToolCallResult(text="answer", usage=TokenUsage(total_tokens=5))]
        )
        runtime = _runtime(llm, memory_recall=lambda q: "")
        await _drain(runtime, "hello")

        system = llm.seen_messages[0][0]["content"]
        assert "## Memory Context" not in system

    @pytest.mark.asyncio
    async def test_no_hook_behaves_as_before(self) -> None:
        llm = _CapturingLLM(
            [ToolCallResult(text="answer", usage=TokenUsage(total_tokens=5))]
        )
        await _drain(_runtime(llm), "hello")

        system = llm.seen_messages[0][0]["content"]
        assert "## Memory Context" not in system

    @pytest.mark.asyncio
    async def test_shared_prompt_context_not_mutated(self) -> None:
        llm = _CapturingLLM(
            [ToolCallResult(text="answer", usage=TokenUsage(total_tokens=5))]
        )
        shared = AgentContext(react_enabled=True)
        catalog = ToolCatalog()
        catalog.register(_tool("market_ticker"))
        runtime = ReActAgentRuntime(
            llm=llm,
            tool_catalog=catalog,
            context_manager=ContextManager(),
            prompt_context=shared,
            memory_recall=lambda q: "recalled text",
        )
        await _drain(runtime, "hello")

        assert shared.memory_context == ""


class TestMemoryUsedProvenance:
    @pytest.mark.asyncio
    async def test_memory_used_count_in_done_event(self) -> None:
        # 透明性批2-9: recall 命中条数("- " 行)随 DONE 带出
        recall_text = "- [confirmed 2026-09] a\n- [weakening 2026-09] b\n说明行"
        llm = _ScriptedLLM(
            [ToolCallResult(text="ans", usage=TokenUsage(total_tokens=5))]
        )
        events = await _drain(
            _runtime(llm, memory_recall=lambda q: recall_text), "query"
        )
        done = events[-1]
        assert done.type == AgentStreamEventType.DONE
        assert done.data.get("memory_used") == 2

    @pytest.mark.asyncio
    async def test_no_memory_hit_done_has_no_memory_used(self) -> None:
        llm = _ScriptedLLM(
            [ToolCallResult(text="ans", usage=TokenUsage(total_tokens=5))]
        )
        events = await _drain(_runtime(llm, memory_recall=lambda q: ""), "query")
        done = events[-1]
        assert done.type == AgentStreamEventType.DONE
        assert not done.data.get("memory_used")


class TestPerTurnModelOverride:
    """Smart-model routing must be per-turn and must NOT mutate provider state.

    Regression lock for the bug where ``agent.py`` mutated
    ``runtime._llm._config.model`` — an attribute that never existed, so the
    override silently never applied (and would have raced had it worked).
    """

    @pytest.mark.asyncio
    async def test_model_forwarded_to_chat_with_tools_only_when_set(self) -> None:
        llm = _RecordingLLM(
            [ToolCallResult(text="done", usage=TokenUsage(total_tokens=5))]
        )
        await _drain_model(_runtime(llm), "hi", "smart-model-x")

        assert llm.seen_kwargs, "chat_with_tools was never called"
        assert llm.seen_kwargs[0].get("model") == "smart-model-x"

    @pytest.mark.asyncio
    async def test_no_model_kwarg_when_override_absent(self) -> None:
        llm = _RecordingLLM(
            [ToolCallResult(text="done", usage=TokenUsage(total_tokens=5))]
        )
        await _drain_model(_runtime(llm), "hi", None)

        assert llm.seen_kwargs, "chat_with_tools was never called"
        # A present-but-None key would send ``model: null`` to the provider.
        assert "model" not in llm.seen_kwargs[0]

    @pytest.mark.asyncio
    async def test_override_does_not_mutate_shared_provider_config(self) -> None:
        llm = _RecordingLLM(
            [ToolCallResult(text="done", usage=TokenUsage(total_tokens=5))],
            config_model="base-model",
        )
        await _drain_model(_runtime(llm), "hi", "smart-model-x")

        assert llm.config.model == "base-model"

    @pytest.mark.asyncio
    async def test_model_override_reaches_every_round(self) -> None:
        llm = _RecordingLLM(
            [
                _ticker_call(),
                ToolCallResult(text="ans", usage=TokenUsage(total_tokens=5)),
            ]
        )
        await _drain_model(_runtime(llm), "quote", "smart-model-x")

        assert len(llm.seen_kwargs) == 2
        assert all(k.get("model") == "smart-model-x" for k in llm.seen_kwargs)

    @pytest.mark.asyncio
    async def test_model_override_reaches_the_wrap_up_round(self) -> None:
        """The max-rounds wrap-up round must keep the per-turn model override.

        Regression lock for the merge of two independent features: the wrap-up
        round (tools=None) and the per-turn model override were added on
        separate branches. Unioning the signatures without threading model
        into the wrap-up call would silently drop the override on the one round
        that produces the *final report* -- the round where it matters most.
        """
        llm = _RecordingLLM([_ticker_call(), _ticker_call("c2")])
        # max_rounds=2 with a tool call each round exhausts the budget, so the
        # wrap-up round fires; it is the LAST chat_with_tools call recorded.
        await _drain_model(_runtime(llm, max_rounds=2), "quote", "smart-model-x")

        assert len(llm.seen_kwargs) == 3, llm.seen_kwargs  # 2 tool rounds + wrap-up
        assert llm.seen_kwargs[-1].get("model") == "smart-model-x", llm.seen_kwargs

    @pytest.mark.asyncio
    async def test_override_lands_on_the_wire_payload(self) -> None:
        """Full chain: process_message → real provider → HTTP body.

        Uses a real OpenAICompatProvider over a mock transport so the
        assertion is on the actual ``model`` field the server would see.
        """
        import json

        import httpx

        from selene_space_llm.base import LLMConfig
        from selene_space_llm.openai_compat import OpenAICompatProvider

        seen_bodies: list[dict[str, Any]] = []

        def record(req: httpx.Request) -> httpx.Response:
            seen_bodies.append(json.loads(req.content))
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"role": "assistant", "content": "ok"}}],
                    "model": "resp-model",
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
                },
            )

        provider = OpenAICompatProvider(
            config=LLMConfig(model="base-model", api_key="sk-test"),
            client=httpx.AsyncClient(transport=httpx.MockTransport(record)),
        )
        runtime = ReActAgentRuntime(
            llm=provider,
            tool_catalog=ToolCatalog(),
            context_manager=ContextManager(),
            prompt_context=AgentContext(),
        )

        async for _ in runtime.process_message("hi", model="smart-model-x"):
            pass

        assert seen_bodies, "no request reached the transport"
        assert seen_bodies[0]["model"] == "smart-model-x"
        # The shared config is untouched.
        assert provider.config.model == "base-model"
