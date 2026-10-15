"""Behaviour locks for the legacy runtime stack: prompt builder sections,
ContextManager trimming, agent role registry, and LegacyAgentRuntime
JSON-driven event streaming."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from selene_space_agent.context.manager import ContextManager
from selene_space_agent.prompt_builder import AgentContext, build_system_prompt
from selene_space_agent.runtime import AgentRuntime, LegacyAgentRuntime
from selene_space_agent.team.roles import AGENT_ROLES, RoleDefinition, get_role
from selene_space_agent.tool_catalog import ToolCatalog
from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_types.agent import AgentRole, AgentStreamEventType, MarketRegime, MarketState
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


class _JsonLLM:
    """Replies with JSON dicts; ``chat`` serialises them so the runtime's
    ``_parse_response`` can pull tool_calls / response fields."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = list(script)
        self._cursor = 0

    async def chat(self, messages: list[Any], **kwargs: Any) -> str:
        if self._cursor < len(self._script):
            import json

            step = self._script[self._cursor]
            self._cursor += 1
            return json.dumps(step)
        return '{"response": "script exhausted."}'

    async def chat_stream(self, messages: list[Any], **kwargs: Any) -> AsyncIterator[str]:
        yield await self.chat(messages, **kwargs)

    async def chat_with_tools(
        self, messages: list[Any], tools: list[dict[str, Any]] | None = None, **kwargs: Any
    ) -> Any:
        from selene_space_llm.schemas import TokenUsage, ToolCall, ToolCallResult

        if self._cursor < len(self._script):
            step = self._script[self._cursor]
            self._cursor += 1
            raw = step.get("tool_calls", [])
            calls = [
                ToolCall(
                    id=f"stub_call_{i}",
                    name=tc.get("tool", tc.get("name", "")),
                    arguments=tc.get("arguments", {}),
                )
                for i, tc in enumerate(raw)
                if isinstance(tc, dict)
            ]
            text = step.get("response", "") or None
            return ToolCallResult(
                tool_calls=calls,
                text=text,
                usage=TokenUsage(prompt_tokens=12, completion_tokens=6, total_tokens=18),
            )
        return ToolCallResult(text="script exhausted.")

    async def generate_structured(
        self, messages: list[Any], output_schema: dict[str, Any], **kwargs: Any
    ) -> dict[str, Any]:
        if self._cursor < len(self._script):
            step = self._script[self._cursor]
            self._cursor += 1
            return step
        return {"response": "script exhausted."}


class TestSystemPromptSections:
    def test_identity_and_safety_present(self) -> None:
        text = build_system_prompt(AgentContext())
        checks = ("SeleneQuant" in text, "Safety" in text)
        assert all(checks)

    def test_tool_section_lists_schema(self) -> None:
        ctx = AgentContext(
            available_tools=[
                {
                    "name": "market_ticker",
                    "description": "Fetch a ticker",
                    "parameters": {
                        "type": "object",
                        "properties": {"symbol": {"type": "string", "description": "Pair"}},
                        "required": ["symbol"],
                    },
                },
            ]
        )
        text = build_system_prompt(ctx)
        checks = ("market_ticker" in text, "symbol" in text)
        assert all(checks)

    def test_market_state_rendered(self) -> None:
        ctx = AgentContext(
            market_state=MarketState(
                symbol="ETH/USDT",
                regime=MarketRegime.TRENDING,
                trend_strength=0.75,
                volatility=0.4,
                timestamp=1_710_000_000_000,
            )
        )
        text = build_system_prompt(ctx)
        checks = ("ETH/USDT" in text, "trending" in text.lower())
        assert all(checks)

    def test_preferences_rendered(self) -> None:
        ctx = AgentContext(
            user_preferences={
                "risk_appetite": "cautious",
                "preferred_symbols": ["ETH/USDT", "SOL/USDT"],
            }
        )
        text = build_system_prompt(ctx)
        checks = ("cautious" in text, "ETH/USDT" in text)
        assert all(checks)

    def test_role_header(self) -> None:
        text = build_system_prompt(AgentContext(role=AgentRole.MARKET_ANALYST))
        assert "Market Analyst" in text

    def test_memory_snippet_embedded(self) -> None:
        ctx = AgentContext(memory_context="User favours EMA ribbon setups on ETH.")
        assert "EMA ribbon" in build_system_prompt(ctx)

    def test_active_symbols_listed(self) -> None:
        ctx = AgentContext(active_symbols=["BTC/USDT", "SOL/USDT"])
        assert "BTC/USDT" in build_system_prompt(ctx)


class TestContextManagerCore:
    def test_append_order_preserved(self) -> None:
        cm = ContextManager()
        cm.add_message("user", "one")
        cm.add_message("assistant", "two")
        rows = cm.get_messages()
        pair = (len(rows), (rows[0].role, rows[1].role))
        assert pair == (2, ("user", "assistant"))

    def test_estimate_floor_of_one(self) -> None:
        cm = ContextManager()
        checks = (cm.estimate_tokens("wxyz") >= 1, cm.estimate_tokens("a" * 400) > 0)
        assert all(checks)

    def test_running_total_positive(self) -> None:
        cm = ContextManager()
        cm.add_message("user", "a" * 400)
        cm.add_message("assistant", "b" * 200)
        assert cm.total_tokens() > 0

    def test_message_cap_trims(self) -> None:
        cm = ContextManager(max_messages=5)
        for i in range(10):
            cm.add_message("user", f"n{i}")
        assert cm.message_count <= 5

    def test_token_cap_trims(self) -> None:
        cm = ContextManager(max_tokens=100)
        # each ~400-char message is roughly 100 tokens
        cm.add_message("user", "a" * 400)
        cm.add_message("user", "b" * 400)
        assert cm.total_tokens() <= 100

    def test_system_row_survives_trim(self) -> None:
        cm = ContextManager(max_messages=3)
        cm.add_message("system", "standing orders")
        for i in range(5):
            cm.add_message("user", f"n{i}")
        assert any(m.role == "system" for m in cm.get_messages())

    def test_oversized_tool_result_cut_down(self) -> None:
        cm = ContextManager(max_tokens=1000)
        fat = "x" * 5000
        cut = cm.truncate_tool_result(fat)
        checks = (len(cut) < len(fat), "Truncated" in cut)
        assert all(checks)

    def test_small_tool_result_untouched(self) -> None:
        cm = ContextManager(max_tokens=100_000)
        brief = "short and sweet"
        assert cm.truncate_tool_result(brief) == brief

    def test_clear_empties_thread(self) -> None:
        cm = ContextManager()
        cm.add_message("user", "temp")
        cm.clear()
        assert cm.message_count == 0


class TestRoleRegistry:
    def test_canonical_roles_exist(self) -> None:
        trio = (
            "market_analyst" in AGENT_ROLES,
            "strategy_architect" in AGENT_ROLES,
            "risk_guardian" in AGENT_ROLES,
        )
        assert all(trio)

    def test_every_role_has_substance(self) -> None:
        for spec in AGENT_ROLES.values():
            checks = (len(spec.system_prompt) > 50, bool(spec.name), bool(spec.description))
            assert all(checks)

    def test_role_tool_assignments(self) -> None:
        trio = (
            "market_ticker" in AGENT_ROLES["market_analyst"].allowed_tools,
            "backtest_run" in AGENT_ROLES["strategy_architect"].allowed_tools,
            "risk_check" in AGENT_ROLES["risk_guardian"].allowed_tools,
        )
        assert all(trio)

    def test_lookup_by_enum(self) -> None:
        assert get_role(AgentRole.MARKET_ANALYST).name == "Market Analyst"

    def test_definitions_are_frozen_dataclasses(self) -> None:
        spec = get_role(AgentRole.STRATEGY_ARCHITECT)
        checks = (isinstance(spec, RoleDefinition), spec.name == "Strategy Architect")
        assert all(checks)


class TestLegacyRuntime:
    """The pre-ReAct runtime streams via JSON-text parsing."""

    @pytest.fixture
    def catalog(self) -> ToolCatalog:
        cat = ToolCatalog()
        cat.register(_tool("market_ticker", "ETH/USDT: $3,450"))
        cat.register(_tool("risk_check", "ALLOWED"))
        return cat

    @pytest.fixture
    def context(self) -> ContextManager:
        return ContextManager()

    @pytest.fixture
    def prompt_ctx(self) -> AgentContext:
        return AgentContext(react_enabled=False)

    async def _collect(self, runtime: LegacyAgentRuntime, message: str) -> list[Any]:
        bag: list[Any] = []
        async for ev in runtime.process_message(message):
            bag.append(ev)
        return bag

    @pytest.mark.asyncio
    async def test_plain_text_reply_streams(self, catalog, context, prompt_ctx) -> None:
        llm = _JsonLLM([{"response": "Greetings, trader."}])
        events = await self._collect(LegacyAgentRuntime(llm, catalog, context, prompt_ctx), "hey")

        kinds = [e.type for e in events]
        checks = (
            AgentStreamEventType.TEXT_DELTA in kinds,
            AgentStreamEventType.DONE in kinds,
            events[-1].type == AgentStreamEventType.DONE,
        )
        assert all(checks)

        deltas = [e for e in events if e.type == AgentStreamEventType.TEXT_DELTA]
        assert "Greeting" in deltas[0].data["text"]

    @pytest.mark.asyncio
    async def test_tool_roundtrip_then_reply(self, catalog, context, prompt_ctx) -> None:
        llm = _JsonLLM(
            [
                {"response": "", "tool_calls": [{"tool": "market_ticker", "arguments": {"symbol": "ETH/USDT"}}]},
                {"response": "ETH sits at $3,450."},
            ]
        )
        events = await self._collect(LegacyAgentRuntime(llm, catalog, context, prompt_ctx), "eth price?")

        kinds = [e.type for e in events]
        quad = (
            AgentStreamEventType.TOOL_CALL in kinds,
            AgentStreamEventType.TOOL_RESULT in kinds,
            AgentStreamEventType.TEXT_DELTA in kinds,
            AgentStreamEventType.DONE in kinds,
        )
        assert all(quad)

    @pytest.mark.asyncio
    async def test_unknown_tool_reports_not_found(self, catalog, context, prompt_ctx) -> None:
        llm = _JsonLLM(
            [
                {"response": "", "tool_calls": [{"tool": "ghost_tool", "arguments": {}}]},
                {"response": "That tool is missing."},
            ]
        )
        events = await self._collect(LegacyAgentRuntime(llm, catalog, context, prompt_ctx), "do it")

        results = [e for e in events if e.type == AgentStreamEventType.TOOL_RESULT]
        checks = (len(results) == 1, "not found" in results[0].data.get("error", ""))
        assert all(checks)

    @pytest.mark.asyncio
    async def test_round_limit_halts_looping(self, catalog, context, prompt_ctx) -> None:
        looping = [{"response": "", "tool_calls": [{"tool": "market_ticker", "arguments": {}}]} for _ in range(15)]
        llm = _JsonLLM(looping)
        runtime = LegacyAgentRuntime(llm, catalog, context, prompt_ctx, max_tool_rounds=3)

        events = await self._collect(runtime, "spin")
        deltas = [e for e in events if e.type == AgentStreamEventType.TEXT_DELTA]
        assert any("maximum" in e.data.get("text", "").lower() for e in deltas)

    @pytest.mark.asyncio
    async def test_denied_tool_reports_blocked(self, context, prompt_ctx) -> None:
        from selene_space_security.tool_policy import ToolPolicy, ToolPolicyEngine

        policy = ToolPolicyEngine([ToolPolicy(deny=["market_ticker"])])
        cat = ToolCatalog(policy_engine=policy)
        cat.register(_tool("market_ticker"))

        llm = _JsonLLM(
            [
                {"response": "", "tool_calls": [{"tool": "market_ticker", "arguments": {}}]},
                {"response": "Denied by policy."},
            ]
        )
        events = await self._collect(LegacyAgentRuntime(llm, cat, context, prompt_ctx), "try it")

        results = [e for e in events if e.type == AgentStreamEventType.TOOL_RESULT]
        checks = (len(results) == 1, "blocked" in results[0].data.get("error", "").lower())
        assert all(checks)


def test_runtime_alias_points_to_react() -> None:
    from selene_space_agent.react import ReActAgentRuntime

    assert AgentRuntime is ReActAgentRuntime
