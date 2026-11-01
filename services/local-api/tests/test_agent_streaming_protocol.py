"""Agent SSE + WS streaming protocol (Sprint 1.3).

A scripted runtime replays one full ReAct turn; the assertions pin down what
the SSE wire format must look like when it crosses /agent/chat.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from typing import Any

import pytest
from app.core.dependencies import get_agent_runtime, get_settings_service
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_types.agent import AgentStreamEvent, AgentStreamEventType

_NOW = int(time.time() * 1000)


def _evt(kind: AgentStreamEventType, data: dict, offset_ms: int) -> AgentStreamEvent:
    return AgentStreamEvent(type=kind, data=data, timestamp=_NOW + offset_ms)


class _ScriptedRuntime:
    """Replays a canonical ReAct turn for protocol assertions."""

    async def process_message(
        self, message: str, **kwargs: Any
    ) -> AsyncIterator[AgentStreamEvent]:
        # **kwargs absorbs approval_gate/model, which the producer always
        # passes. Accepting-and-ignoring is fine for a *read-only* scripted
        # turn: the gate only matters for state-writing tools, and this
        # script emits none.
        yield _evt(
            AgentStreamEventType.THINKING,
            {"content": "Let me check the market price...", "round": 1},
            0,
        )
        yield _evt(
            AgentStreamEventType.TOOL_CALL,
            {"tool": "market_ticker", "arguments": {"symbol": "BTC/USDT"}},
            1,
        )
        yield _evt(
            AgentStreamEventType.TOOL_RESULT,
            {"tool": "market_ticker", "output": '{"price": 67234.5}'},
            2,
        )
        yield _evt(
            AgentStreamEventType.REFLECTION,
            {"content": "Got price data, sufficient to answer.", "round": 1},
            3,
        )
        yield _evt(
            AgentStreamEventType.TEXT_DELTA, {"text": "BTC is currently $67,234.50."}, 4
        )
        yield _evt(AgentStreamEventType.DONE, {}, 5)


def _app_with_script():
    app = create_app()
    app.dependency_overrides[get_agent_runtime] = lambda: _ScriptedRuntime()
    app.dependency_overrides[get_settings_service] = lambda: None
    return app


def _parse_sse_events(body: str) -> list[dict[str, Any]]:
    """Split raw SSE text into [{event_type, data}, ...] pairs."""
    events: list[dict[str, Any]] = []
    pending_event: str | None = None
    pending_data: str | None = None

    for line in body.split("\n"):
        if line.startswith("event: "):
            pending_event = line[7:].strip()
        elif line.startswith("data: "):
            pending_data = line[6:].strip()
        elif not line and pending_event is not None and pending_data is not None:
            try:
                payload = json.loads(pending_data)
            except json.JSONDecodeError:
                payload = pending_data
            events.append({"event_type": pending_event, "data": payload})
            pending_event = None
            pending_data = None

    return events


async def _chat(message: str) -> Any:
    transport = ASGITransport(app=_app_with_script(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post("/api/v1/agent/chat", json={"message": message})


async def _chat_events(message: str) -> list[dict[str, Any]]:
    return _parse_sse_events((await _chat(message)).text)


def _names(events: list[dict[str, Any]]) -> list[str]:
    return [e["event_type"] for e in events]


@pytest.mark.asyncio
async def test_stream_carries_thinking_and_reflection():
    names = _names(await _chat_events("BTC price?"))
    assert "thinking" in names
    assert "reflection" in names


@pytest.mark.asyncio
async def test_react_phase_ordering_holds():
    """thinking < tool_call < tool_result < reflection < text_delta."""
    at = {n: idx for idx, n in enumerate(_names(await _chat_events("BTC price?")))}
    wanted = ("thinking", "tool_call", "tool_result", "reflection", "text_delta")
    positions = [at[w] for w in wanted]
    assert positions == sorted(positions)
    assert at["thinking"] < at["tool_call"]
    assert at["tool_result"] < at["reflection"]
    assert at["reflection"] < at["text_delta"]


@pytest.mark.asyncio
async def test_done_is_always_terminal():
    """Backward compat: done must close every stream."""
    events = await _chat_events("test")
    assert len(events) > 0
    assert events[-1]["event_type"] == "done"


@pytest.mark.asyncio
async def test_every_payload_is_typed_json():
    for ev in await _chat_events("test"):
        assert isinstance(ev["data"], dict), f"Event data is not dict: {ev}"
        assert "type" in ev["data"], f"Missing type in event data: {ev}"
        assert "timestamp" in ev["data"], f"Missing timestamp in event data: {ev}"


@pytest.mark.asyncio
async def test_stream_alias_matches_chat_output():
    """/agent/stream and /agent/chat must emit identical event sequences."""
    transport = ASGITransport(app=_app_with_script(), raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        chat_resp = await c.post("/api/v1/agent/chat", json={"message": "test"})
        stream_resp = await c.post("/api/v1/agent/stream", json={"message": "test"})
    assert chat_resp.status_code == 200
    assert stream_resp.status_code == 200
    chat_names = _names(_parse_sse_events(chat_resp.text))
    stream_names = _names(_parse_sse_events(stream_resp.text))
    assert chat_names == stream_names


@pytest.mark.asyncio
async def test_thinking_payload_has_content_and_round():
    thinking = [e for e in await _chat_events("test") if e["event_type"] == "thinking"]
    assert len(thinking) >= 1
    inner = thinking[0]["data"]["data"]
    assert "content" in inner
    assert "round" in inner
