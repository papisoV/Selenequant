"""B0 approval gate — the runtime half.

Locks the behaviour that stops a model from self-signing a state-writing tool:
non-exempt tools block on the gate; exempt (read-only) tools do not; a denied
or timed-out tool never runs but always yields a TOOL_RESULT (so the next LLM
round never sees a dangling tool_call).

The dynamic coverage test at the bottom is the one that matters long-term: it
fails when a tool exists that is neither proven read-only nor covered by the
gate, which is how a future write tool would otherwise slip through.
"""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_agent.react import _APPROVAL_EXEMPT, ReActAgentRuntime
from selene_space_agent.context.manager import ContextManager
from selene_space_agent.prompt_builder import AgentContext
from selene_space_agent.tool_catalog import ToolCatalog
from selene_space_agent.tools.base import (
    CONFIRM_FIELDS,
    BaseTool,
    ToolResult,
    strip_confirm_fields,
)
from selene_space_llm.schemas import ToolCall, ToolCallResult, TokenUsage
from selene_space_types.agent import AgentStreamEventType
from selene_space_types.risk import RiskLevel

# Tools audited to write state (paper orders/accounts, strategy persistence,
# backtest results, python_exec, and strategy_validate's failure-memory log).
# Each must therefore be gated. Keep in sync with the plan's §3.3 table.
_AUDITED_WRITE_TOOLS = frozenset(
    {
        "paper_place_order",
        "paper_create_account",
        "strategy_save_version",
        "strategy_deploy",
        "strategy_stop",
        "backtest_run",
        "python_exec",
        # Writes ~/.selenespace/memory/strategy_failures.json on every failed
        # validation (strategy_failures.py:63-84 _persist), and main.py:4070-4073
        # wires the memory in. No read-only proof exists, so fail-closed puts
        # it on the gated side.
        "strategy_validate",
    }
)

# The 24 tools main.py registers. The coverage test asserts every one is
# classified, so adding a tool to main.py without classifying it fails here.
_REGISTERED_TOOLS = frozenset(
    {
        "market_ticker",
        "market_kline",
        "market_orderbook",
        "market_snapshot",
        "paper_create_account",
        "paper_place_order",
        "paper_positions",
        "paper_pnl",
        "explain_pnl",
        "strategy_get_contract",
        "strategy_validate",
        "backtest_result",
        "strategy_generate",
        "strategy_explain",
        "strategy_save_version",
        "strategy_deploy",
        "strategy_stop",
        "backtest_run",
        "risk_check",
        "risk_report",
        "risk_pressure_check",
        "tg_signal_lookup",
        "tg_event_impact",
        "event_kline_attribution",
        "python_exec",
        "read_skill",
    }
)


class _Stub(BaseTool):
    """Sync-only tool: no ``async_execute``, so the runtime takes the sync path."""

    def __init__(self, slug: str) -> None:
        self.slug = slug
        self.sync_calls = 0
        # Present on the sync-only stub too, so tests can assert the async path
        # was NOT taken (it stays 0).
        self.async_calls = 0
        self.seen_args: dict[str, Any] | None = None

    @property
    def name(self) -> str:
        return self.slug

    @property
    def description(self) -> str:
        return f"stub {self.slug}"

    @property
    def parameters(self) -> dict[str, Any]:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        self.sync_calls += 1
        self.seen_args = args
        return ToolResult(output="sync-ran")


class _AsyncStub(_Stub):
    """Adds an ``async_execute`` coroutine → the runtime dispatches to it."""

    async def async_execute(self, args: dict[str, Any]) -> ToolResult:
        self.async_calls += 1
        self.seen_args = args
        return ToolResult(output="async-ran")


class _ScriptedLLM:
    """Yields the given tool calls, then a final text answer."""

    def __init__(self, calls: list[ToolCall]) -> None:
        self._script = [
            ToolCallResult(tool_calls=calls, usage=TokenUsage(total_tokens=1)),
            ToolCallResult(text="done", usage=TokenUsage(total_tokens=1)),
        ]

    def chat_with_tools(self, messages: Any, tools: Any = None, **kw: Any) -> Any:
        return self._script.pop(0)


def _runtime(tools: list[_Stub], calls: list[ToolCall]) -> ReActAgentRuntime:
    catalog = ToolCatalog()
    for t in tools:
        catalog.register(t)
    return ReActAgentRuntime(
        llm=_ScriptedLLM(calls),
        tool_catalog=catalog,
        context_manager=ContextManager(),
        prompt_context=AgentContext(react_enabled=True),
    )


async def _drain(runtime: ReActAgentRuntime, gate: Any = None) -> list[Any]:
    events = []
    async for ev in runtime.process_message("go", approval_gate=gate):
        events.append(ev)
    return events


def _types(events: list[Any]) -> list[AgentStreamEventType]:
    return [e.type for e in events]


# ---------------------------------------------------------------------------
# Gate behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_gate_runs_write_tool_ungated() -> None:
    """Callers that pass no gate (CLI, legacy) keep working unchanged."""
    tool = _Stub("strategy_deploy")
    rt = _runtime([tool], [ToolCall(id="c1", name="strategy_deploy", arguments={})])
    events = await _drain(rt)

    assert tool.sync_calls == 1
    assert AgentStreamEventType.TOOL_APPROVAL_REQUEST not in _types(events)


@pytest.mark.asyncio
async def test_gate_approval_runs_tool_and_injects_confirmation() -> None:
    """A model-supplied user_confirmed must be overwritten, and the gate asked."""
    tool = _Stub("paper_place_order")
    rt = _runtime(
        [tool],
        [
            ToolCall(
                id="c1",
                # The model forged consent — it must not survive.
                name="paper_place_order",
                arguments={"user_confirmed": True, "symbol": "SOL"},
            )
        ],
    )
    seen: dict[str, Any] = {}

    async def gate(request_id: str, tool_name: str, args: dict[str, Any]) -> bool:
        seen["request_id"] = request_id
        seen["tool_name"] = tool_name
        return True

    events = await _drain(rt, gate)

    assert seen["tool_name"] == "paper_place_order"
    assert tool.sync_calls == 1
    assert tool.seen_args is not None
    assert tool.seen_args["user_confirmed"] is True

    reqs = [e for e in events if e.type == AgentStreamEventType.TOOL_APPROVAL_REQUEST]
    assert len(reqs) == 1
    assert reqs[0].data["tool_call_id"] == seen["request_id"]
    assert reqs[0].data["tool_name"] == "paper_place_order"

    # The result echoes the request id so the client can clear exactly the
    # card it raised (two calls to one tool in a round must not collide).
    results = [e for e in events if e.type == AgentStreamEventType.TOOL_RESULT]
    assert results[0].data["tool_call_id"] == seen["request_id"]

    types = _types(events)
    assert types.index(AgentStreamEventType.TOOL_APPROVAL_REQUEST) < types.index(
        AgentStreamEventType.TOOL_RESULT
    )


@pytest.mark.asyncio
async def test_gate_denial_does_not_run_tool_but_yields_result() -> None:
    """A denied tool must not run, and must still produce a TOOL_RESULT."""
    tool = _Stub("strategy_deploy")
    rt = _runtime([tool], [ToolCall(id="c1", name="strategy_deploy", arguments={})])

    async def gate(request_id: str, tool_name: str, args: dict[str, Any]) -> str:
        return "user denied"

    events = await _drain(rt, gate)

    assert tool.sync_calls == 0
    results = [e for e in events if e.type == AgentStreamEventType.TOOL_RESULT]
    assert len(results) == 1
    assert "denied" in results[0].data.get("error", "").lower()
    # No dangling tool_call: the loop still finished.
    assert events[-1].type == AgentStreamEventType.DONE


@pytest.mark.asyncio
async def test_exempt_tool_skips_gate() -> None:
    """A read-only tool runs without ever calling the gate."""
    tool = _Stub("market_ticker")
    rt = _runtime([tool], [ToolCall(id="c1", name="market_ticker", arguments={})])

    async def gate(request_id: str, tool_name: str, args: dict[str, Any]) -> bool:
        raise AssertionError("exempt tool must not consult the gate")

    events = await _drain(rt, gate)

    assert tool.sync_calls == 1
    assert AgentStreamEventType.TOOL_APPROVAL_REQUEST not in _types(events)


@pytest.mark.asyncio
async def test_every_call_in_a_batch_is_gated() -> None:
    """A round with two write calls gates both — not just the first."""
    a = _Stub("strategy_deploy")
    b = _Stub("strategy_stop")
    rt = _runtime(
        [a, b],
        [
            ToolCall(id="c1", name="strategy_deploy", arguments={}),
            ToolCall(id="c2", name="strategy_stop", arguments={}),
        ],
    )
    request_ids: list[str] = []

    async def gate(request_id: str, tool_name: str, args: dict[str, Any]) -> bool:
        request_ids.append(request_id)
        return True

    events = await _drain(rt, gate)

    assert len(request_ids) == 2
    assert len(set(request_ids)) == 2, "each call needs a distinct request id"
    reqs = [e for e in events if e.type == AgentStreamEventType.TOOL_APPROVAL_REQUEST]
    assert len(reqs) == 2
    assert a.sync_calls == 1 and b.sync_calls == 1


# ---------------------------------------------------------------------------
# async_execute dispatch (fixes the latent bug where async-only tools never ran)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_async_only_tool_dispatches_to_async_execute() -> None:
    tool = _AsyncStub("backtest_run")
    rt = _runtime([tool], [ToolCall(id="c1", name="backtest_run", arguments={})])
    events = await _drain(rt)

    assert tool.async_calls == 1
    results = [e for e in events if e.type == AgentStreamEventType.TOOL_RESULT]
    assert results[0].data.get("output") == "async-ran"


@pytest.mark.asyncio
async def test_sync_tool_still_uses_execute() -> None:
    tool = _Stub("strategy_deploy")
    rt = _runtime([tool], [ToolCall(id="c1", name="strategy_deploy", arguments={})])
    await _drain(rt)

    assert tool.sync_calls == 1
    assert tool.async_calls == 0


# ---------------------------------------------------------------------------
# Dynamic coverage — the anti-rot oracle
# ---------------------------------------------------------------------------


def test_every_registered_tool_is_classified() -> None:
    """Every tool main.py registers is either exempt or audited as a writer.

    This is the test that fails when someone adds a tool and forgets to
    classify it — which is exactly how a fail-open gate would rot.
    """
    unclassified = _REGISTERED_TOOLS - _APPROVAL_EXEMPT - _AUDITED_WRITE_TOOLS
    assert not unclassified, (
        f"Unclassified tool(s): {sorted(unclassified)}. Add each to "
        f"_APPROVAL_EXEMPT (with a read-only file:line proof) if it only "
        f"reads, or to _AUDITED_WRITE_TOOLS if it writes."
    )


def test_exempt_and_write_lists_are_disjoint() -> None:
    assert not (_APPROVAL_EXEMPT & _AUDITED_WRITE_TOOLS)


def test_exempt_list_only_contains_known_tools() -> None:
    """A typo in the exempt list would silently exempt nothing (or worse)."""
    unknown = _APPROVAL_EXEMPT - _REGISTERED_TOOLS
    assert not unknown, f"Unknown names in _APPROVAL_EXEMPT: {sorted(unknown)}"


# ---------------------------------------------------------------------------
# Exposure layer — the model must never see the confirmation field
# ---------------------------------------------------------------------------


def _definition_with_confirm() -> dict[str, Any]:
    return {
        "name": "paper_place_order",
        "description": "place an order",
        "parameters": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "user_confirmed": {"type": "boolean"},
            },
            "required": ["symbol", "user_confirmed"],
        },
    }


def test_strip_removes_field_from_properties_and_required() -> None:
    """Both halves must go together — a required entry naming a vanished
    property is an invalid schema some providers reject."""
    out = strip_confirm_fields(_definition_with_confirm())
    params = out["parameters"]
    assert "user_confirmed" not in params["properties"]
    assert "user_confirmed" not in params["required"]
    assert "symbol" in params["properties"]


def test_strip_is_immutable() -> None:
    """The input definition is never mutated — callers may hold a reference."""
    src = _definition_with_confirm()
    strip_confirm_fields(src)
    assert "user_confirmed" in src["parameters"]["properties"]
    assert "user_confirmed" in src["parameters"]["required"]


def test_strip_is_noop_without_confirm_fields() -> None:
    src = {
        "name": "market_ticker",
        "parameters": {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    }
    out = strip_confirm_fields(src)
    assert out["parameters"]["properties"] == {"symbol": {"type": "string"}}
    assert out["parameters"]["required"] == ["symbol"]


class _ConfirmFieldStub(_Stub):
    """Declares user_confirmed in its schema, like the real write tools."""

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "symbol": {"type": "string"},
                "user_confirmed": {"type": "boolean"},
            },
            "required": ["symbol", "user_confirmed"],
        }


def test_catalog_definitions_never_expose_confirm_field() -> None:
    """The catalog every consumer reads strips the field — so the model has
    no property to fill, and a hallucinated one is still overwritten."""
    catalog = ToolCatalog()
    catalog.register(_ConfirmFieldStub("paper_place_order"))
    defs = catalog.get_tool_definitions()
    assert len(defs) == 1
    params = defs[0]["parameters"]
    assert "user_confirmed" not in params["properties"]
    assert "user_confirmed" not in params.get("required", [])


def test_confirm_fields_constant_names_the_backend_field() -> None:
    """The strip list must match the field the execution layer injects."""
    assert "user_confirmed" in CONFIRM_FIELDS
