"""B0 approval gate — the HTTP half (POST /agent/approve) and the round trip.

The runtime test (``packages/agent-runtime/tests/test_approval_gate.py``)
proves the gate blocks a write tool. This file proves the *whole* path a real
browser takes: a scripted LLM asks for a state-writing tool, the SSE stream
pauses on TOOL_APPROVAL_REQUEST, an HTTP POST resolves it, and the turn then
resumes to TOOL_RESULT and DONE. It also pins the endpoint's authorization and
the three "no pending approval" refusals (unknown id, dead turn, replay).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, cast

import pytest

import app.api.v1.agent as agent_mod
from app.core.dependencies import (
    AuthenticatedUser,
    get_agent_runtime,
    get_settings_service,
    require_user,
)
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_agent.context.manager import ContextManager
from selene_space_agent.prompt_builder import AgentContext
from selene_space_agent.react import ReActAgentRuntime
from selene_space_agent.tool_catalog import ToolCatalog
from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_llm.schemas import ToolCall, ToolCallResult
from selene_space_types.risk import RiskLevel

_SESSION_ID = "sess-approve-test"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _wipe_turns():
    """Every test starts and ends with no live turns / cached runtimes."""
    agent_mod._active_turns.clear()
    agent_mod._user_runtimes.clear()
    yield
    for turn in list(agent_mod._active_turns.values()):
        if turn.producer_task and not turn.producer_task.done():
            turn.producer_task.cancel()
    agent_mod._active_turns.clear()
    agent_mod._user_runtimes.clear()


class _FakeRepo:
    """Minimal ChatSessionRepository stand-in for the ownership check."""

    def __init__(self, owner: str = "local") -> None:
        self._owner = owner

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        return {"user_id": self._owner, "session_id": session_id}


@pytest.fixture
def local_owner(monkeypatch):
    """Ownership check passes for the test session."""

    def _get() -> Any:
        return _FakeRepo(owner="local")

    monkeypatch.setattr("app.core.dependencies.get_chat_session_repo", _get)


def _app(*, runtime: Any = None) -> Any:
    app = create_app()
    app.dependency_overrides[get_agent_runtime] = lambda: runtime
    if runtime is None:
        app.dependency_overrides[get_settings_service] = lambda: None
    return app


async def _approve(
    *,
    session_id: str = _SESSION_ID,
    tool_call_id: str = "req-1",
    approve: bool = True,
    app: Any = None,
    client_host: tuple[str, int] = ("127.0.0.1", 123),
    user: AuthenticatedUser | None = None,
) -> Any:
    app = app or _app()
    if user is not None:
        app.dependency_overrides[require_user] = lambda: user
    transport = ASGITransport(app=app, client=client_host)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post(
            "/api/v1/agent/approve",
            json={
                "session_id": session_id,
                "tool_call_id": tool_call_id,
                "approve": approve,
            },
        )


def _park_turn(
    request_id: str = "req-1", *, status: str = "running"
) -> tuple[Any, Any]:
    """Register a running turn blocked on one pending approval."""
    turn = agent_mod.TurnState(
        session_id=_SESSION_ID, user_id="local", user_message="go"
    )
    turn.status = status
    fut: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
    turn.pending_approval[request_id] = fut
    agent_mod._active_turns[("local", _SESSION_ID)] = turn
    return turn, fut


# ---------------------------------------------------------------------------
# Endpoint authorization
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_rejects_remote_unauthenticated():
    """A non-localhost caller with no token must get 401."""
    app = create_app()
    app.dependency_overrides[get_settings_service] = lambda: None
    resp = await _approve(app=app, client_host=("8.8.8.8", 4000))
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_approve_forbids_non_owner(monkeypatch):
    """A different user cannot resolve someone else's pending approval."""

    def _get() -> Any:
        return _FakeRepo(owner="someone-else")

    monkeypatch.setattr("app.core.dependencies.get_chat_session_repo", _get)
    turn, fut = _park_turn()
    resp = await _approve(user=AuthenticatedUser(id="intruder", role="user"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"]["code"] == "forbidden"
    assert body["data"] is None
    # The pending future must be untouched by a rejected caller.
    assert not fut.done()


# ---------------------------------------------------------------------------
# The three "no pending approval" refusals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_unknown_request_id(local_owner):
    turn, fut = _park_turn()
    resp = await _approve(tool_call_id="does-not-exist")
    body = resp.json()
    assert body["error"]["code"] == "no_pending_approval"
    assert not fut.done()


@pytest.mark.asyncio
async def test_approve_dead_turn_is_refused(local_owner):
    """A2: a finished/failed turn reports nothing-pending, not a false success."""
    turn, fut = _park_turn(status="completed")
    resp = await _approve()
    assert resp.json()["error"]["code"] == "no_pending_approval"
    assert not fut.done()


@pytest.mark.asyncio
async def test_approve_replay_after_resolution(local_owner):
    """A second click on the same id is a safe no-op, not a second approval."""
    turn, fut = _park_turn()

    first = await _approve(approve=True)
    assert first.json()["error"] is None
    assert first.json()["data"] == {"resolved": True}
    assert fut.result() is True

    second = await _approve(approve=True)
    assert second.json()["error"]["code"] == "no_pending_approval"


@pytest.mark.asyncio
async def test_approve_denial_resolves_with_reason(local_owner):
    turn, fut = _park_turn()
    resp = await _approve(approve=False)
    assert resp.json()["error"] is None
    # Denial resolves the future to a falsy reason string, never True.
    assert fut.result() is not True


# ---------------------------------------------------------------------------
# The gate itself: timeout + abandon
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gate_timeout_pops_stale_future(monkeypatch):
    """A3: on TTL expiry the gate denies AND removes the stale future."""
    monkeypatch.setattr(agent_mod, "_TURN_TTL_SECONDS", 0.01)
    turn = agent_mod.TurnState(
        session_id=_SESSION_ID, user_id="local", user_message="go"
    )
    gate = agent_mod._make_approval_gate(turn)
    verdict = await gate("req-timeout", "paper_place_order", {})
    assert verdict is not True
    assert "timed out" in verdict
    assert turn.pending_approval == {}


@pytest.mark.asyncio
async def test_abandon_resolves_pending_as_denied():
    turn, fut = _park_turn()
    agent_mod._abandon_pending_approvals(turn, "superseded")
    assert fut.done() and fut.result() is not True
    assert turn.pending_approval == {}
    assert turn.status == "failed"


# ---------------------------------------------------------------------------
# Round trip: SSE pause → HTTP approve → SSE resume
# ---------------------------------------------------------------------------


class _WriteTool(BaseTool):
    """A stand-in state-writing tool that records what it was called with."""

    def __init__(self) -> None:
        self.seen: dict[str, Any] | None = None
        self.calls = 0

    @property
    def name(self) -> str:
        return "paper_place_order"

    @property
    def description(self) -> str:
        return "place an order"

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        self.calls += 1
        self.seen = args
        return ToolResult(output="order placed")


class _ScriptedLLM:
    """Round 1 asks for the write tool; round 2 answers in plain text."""

    async def chat_with_tools(
        self, messages: list[Any], tools: list[dict[str, Any]] | None = None, **k: Any
    ) -> ToolCallResult:
        asked = any(
            m.get("role") == "user" and "returned" in str(m.get("content", ""))
            for m in messages
        )
        if not asked:
            return ToolCallResult(
                tool_calls=[
                    ToolCall(name="paper_place_order", arguments={"symbol": "BTC/USDT"})
                ]
            )
        return ToolCallResult(text="Done placing your order.")

    async def chat(self, messages: list[Any], **k: Any) -> str:
        return ""


def _runtime_with(tool: BaseTool) -> ReActAgentRuntime:
    catalog = ToolCatalog()
    catalog.register(tool)
    return ReActAgentRuntime(
        llm=_ScriptedLLM(),
        tool_catalog=catalog,
        context_manager=ContextManager(),
        prompt_context=AgentContext(react_enabled=True, max_tool_rounds=5),
        max_tool_rounds=5,
    )


def _parse(sse_str: str) -> dict[str, Any]:
    for line in sse_str.splitlines():
        if line.startswith("data: "):
            return cast(dict[str, Any], json.loads(line[6:]))
    return {}


async def _drain_paused_turn(turn: Any, *, approve: bool) -> list[dict[str, Any]]:
    """Consume the turn's SSE queue, clicking approve when it pauses."""
    events: list[dict[str, Any]] = []
    while True:
        item = await asyncio.wait_for(turn.queue.get(), timeout=5)
        if item is None:
            break
        payload = _parse(item)
        events.append(payload)
        if payload.get("type") == "tool_approval_request":
            data = payload["data"]
            resp = await _approve(tool_call_id=data["tool_call_id"], approve=approve)
            assert resp.json()["error"] is None, resp.json()
    return events


@pytest.mark.asyncio
async def test_round_trip_approval_resumes_execution(local_owner):
    """Full path: pause on TOOL_APPROVAL_REQUEST → approve → tool runs → DONE."""
    tool = _WriteTool()
    turn = agent_mod._begin_turn(
        _runtime_with(tool), "buy BTC", _SESSION_ID, None, user_id="local"
    )
    events = await _drain_paused_turn(turn, approve=True)
    names = [e.get("type") for e in events]

    assert "tool_approval_request" in names
    assert names.index("tool_call") < names.index("tool_approval_request")
    assert names.index("tool_approval_request") < names.index("tool_result")
    assert names[-1] == "done"

    # The tool ran exactly once, and the backend injected consent itself.
    assert tool.calls == 1
    assert tool.seen is not None and tool.seen.get("user_confirmed") is True


@pytest.mark.asyncio
async def test_round_trip_denial_blocks_tool_but_finishes(local_owner):
    """Denial: the tool never runs, but the turn still reaches DONE."""
    tool = _WriteTool()
    turn = agent_mod._begin_turn(
        _runtime_with(tool), "buy BTC", _SESSION_ID, None, user_id="local"
    )
    events = await _drain_paused_turn(turn, approve=False)
    names = [e.get("type") for e in events]

    assert tool.calls == 0
    results = [e for e in events if e.get("type") == "tool_result"]
    assert results and "approval denied" in json.dumps(results[0]).lower()
    assert names[-1] == "done"


@pytest.mark.asyncio
async def test_model_forged_confirmation_is_not_consent(local_owner):
    """A model that writes user_confirmed:true in its own args still gets gated.

    This is the whole point of B0: the field is stripped from the schema, and
    even if a model hallucinates it, the runtime overwrites it only after a
    human approves. Here the human denies — execution must not happen.
    """
    tool = _WriteTool()
    runtime = _runtime_with(tool)

    class _Forger(_ScriptedLLM):
        async def chat_with_tools(self, messages, tools=None, **k):  # type: ignore[override]
            asked = any(
                m.get("role") == "user" and "returned" in str(m.get("content", ""))
                for m in messages
            )
            if not asked:
                return ToolCallResult(
                    tool_calls=[
                        ToolCall(
                            name="paper_place_order",
                            arguments={"symbol": "BTC/USDT", "user_confirmed": True},
                        )
                    ]
                )
            return ToolCallResult(text="ok")

    runtime._llm = _Forger()
    turn = agent_mod._begin_turn(runtime, "buy BTC", _SESSION_ID, None, user_id="local")
    events = await _drain_paused_turn(turn, approve=False)

    assert tool.calls == 0
    assert any(e.get("type") == "tool_approval_request" for e in events)
