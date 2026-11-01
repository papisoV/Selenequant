"""US-004 tests: async selene_space_llm backend → sync decision-workflow adapter."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from selene_space_decision_workflow.llm_compat import AsyncBackendToSyncAdapter
from selene_space_decision_workflow.llm_provider import LLMMessage


@dataclass
class _FakeTokenUsage:
    prompt_tokens: int = 12
    completion_tokens: int = 34
    total_tokens: int = 46


@dataclass
class _FakeToolCallResult:
    text: str = ""
    usage: _FakeTokenUsage | None = None


class _FakeAsyncBackend:
    """Duck-typed selene_space_llm provider: async chat_with_tools + pydantic-style enum roles."""

    def __init__(self) -> None:
        self.seen_payloads: list[list[dict]] = []

    async def chat_with_tools(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
        self.seen_payloads.append(messages)
        return _FakeToolCallResult(
            text='{"state": "trend_up", "gate_result": "proceed"}',
            usage=_FakeTokenUsage(),
        )


def test_role_mapping_and_usage_extraction() -> None:
    backend = _FakeAsyncBackend()
    adapter = AsyncBackendToSyncAdapter(backend)
    messages = [
        LLMMessage(role="system", content="you are a PA diagnoser"),
        LLMMessage(role="user", content="diagnose BTC 1h"),
    ]
    resp = adapter.call(messages)

    assert resp.content.startswith('{"state"')
    assert resp.reasoning_content is None
    assert resp.usage == {
        "prompt_tokens": 12,
        "completion_tokens": 34,
        "total_tokens": 46,
    }
    # Roles mapped enum→str values in the payload handed to the backend.
    first, second = backend.seen_payloads[0]
    assert first.role == "system"
    assert second.role == "user"
    assert second.content == "diagnose BTC 1h"


def test_plain_string_result_falls_back() -> None:
    class _StringBackend:
        async def chat_with_tools(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
            return "plain text reply"

    adapter = AsyncBackendToSyncAdapter(_StringBackend())
    resp = adapter.call([LLMMessage(role="user", content="hi")])
    assert resp.content == "plain text reply"
    assert resp.usage == {}


def test_call_from_sync_context_uses_private_loop() -> None:
    adapter = AsyncBackendToSyncAdapter(_FakeAsyncBackend())
    resp = adapter.call([LLMMessage(role="user", content="x")])
    assert "gate_result" in resp.content
    # The private loop is always engaged (never asyncio.run per call): a
    # long-lived AsyncClient backend must stay bound to a single loop.
    assert adapter._loop is not None
    assert adapter._loop.is_running()


def test_multiple_calls_reuse_same_loop() -> None:
    """Second+ calls must not spin a fresh loop — pooled connections would
    crash with 'Event loop is closed'."""
    adapter = AsyncBackendToSyncAdapter(_FakeAsyncBackend())
    adapter.call([LLMMessage(role="user", content="a")])
    first_loop = adapter._loop
    adapter.call([LLMMessage(role="user", content="b")])
    assert adapter._loop is first_loop
    assert adapter.last_usage["total_tokens"] == 46


def test_call_from_running_loop_uses_private_loop() -> None:
    adapter = AsyncBackendToSyncAdapter(_FakeAsyncBackend())

    async def scenario() -> None:
        # Sync blocking call made from inside a coroutine (e.g. legacy sync
        # workflow invoked directly in an async app): asyncio.run would
        # refuse to nest here, so the private-loop path must engage.
        resp = adapter.call([LLMMessage(role="user", content="y")])
        assert "gate_result" in resp.content

    asyncio.run(scenario())
    assert adapter._loop is not None
    assert adapter._loop.is_running()


def test_adapter_satisfies_decision_workflow_protocol() -> None:
    """The adapter instance is accepted wherever the sync LLMProvider Protocol is expected."""
    adapter = AsyncBackendToSyncAdapter(_FakeAsyncBackend())
    # Structural check: has callable `call` with the right input shape.
    assert callable(adapter.call)
    msgs: list[LLMMessage] = [LLMMessage(role="user", content="probe")]
    resp = adapter.call(msgs)
    assert isinstance(resp.content, str)
    assert isinstance(resp.usage, dict)


@pytest.mark.parametrize("bad_usage", [None, "not-an-object", 42])
def test_malformed_usage_yields_empty_dict(bad_usage) -> None:  # noqa: ANN001
    @dataclass
    class _WeirdResult:
        text: str = "ok"
        usage: object = None

    class _WeirdBackend:
        async def chat_with_tools(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
            return _WeirdResult(usage=bad_usage)

    adapter = AsyncBackendToSyncAdapter(_WeirdBackend())
    resp = adapter.call([LLMMessage(role="user", content="z")])
    assert resp.usage == {}
    assert resp.content == "ok"


def test_transient_error_retried_then_succeeds(monkeypatch) -> None:
    class _FlakyBackend:
        def __init__(self) -> None:
            self.calls = 0

        async def chat_with_tools(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError(
                    'Endpoint returned 524: {"error":{"message":'
                    '"Upstream model provider is temporarily unavailable."}}'
                )
            return _FakeToolCallResult(text='{"ok": true}', usage=_FakeTokenUsage())

    monkeypatch.setattr(
        "selene_space_decision_workflow.llm_compat._TRANSIENT_BACKOFF_S", 0.0
    )
    backend = _FlakyBackend()
    adapter = AsyncBackendToSyncAdapter(backend)
    resp = adapter.call([LLMMessage(role="user", content="q")])
    assert backend.calls == 3
    assert resp.content == '{"ok": true}'
    assert resp.usage["total_tokens"] == 46


def test_server_disconnected_is_transient_and_retried(monkeypatch) -> None:
    """httpx RemoteProtocolError mid-flight drops must retry, not abort.

    Measured 2026-09-17 on glm-5.3-flash: reasoning endpoints hold connections
    for minutes and the proxy/gateway intermittently drops them
    ("Server disconnected without sending a response"); two full diagnoses
    died to this before it joined the transient list.
    """

    class _DroppingBackend:
        def __init__(self) -> None:
            self.calls = 0

        async def chat_with_tools(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(
                    "Request failed: Server disconnected without sending a response."
                )
            return _FakeToolCallResult(text='{"ok": true}', usage=_FakeTokenUsage())

    monkeypatch.setattr(
        "selene_space_decision_workflow.llm_compat._TRANSIENT_BACKOFF_S", 0.0
    )
    backend = _DroppingBackend()
    adapter = AsyncBackendToSyncAdapter(backend)
    resp = adapter.call([LLMMessage(role="user", content="q")])
    assert backend.calls == 2
    assert resp.content == '{"ok": true}'


def test_non_transient_error_not_retried() -> None:
    class _HardFailBackend:
        def __init__(self) -> None:
            self.calls = 0

        async def chat_with_tools(self, messages, tools=None, **kwargs):  # noqa: ANN001, ANN202
            self.calls += 1
            raise ValueError("bad request shape")

    adapter = AsyncBackendToSyncAdapter(_HardFailBackend())
    with pytest.raises(ValueError):
        adapter.call([LLMMessage(role="user", content="q")])
    assert adapter._backend.calls == 1
