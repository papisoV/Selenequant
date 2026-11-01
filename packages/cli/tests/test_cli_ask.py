"""Tests for `selenespace ask` — SSE rendering, session continuation, errors."""

from __future__ import annotations

import json

import httpx
import pytest
from click.testing import CliRunner

from selene_space_cli.commands import ask as ask_mod
from selene_space_cli.commands.ask import ask, _iter_sse


def _sse(event_type: str, data: dict) -> bytes:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event_type}\ndata: {payload}\n\n".encode("utf-8")


class TestIterSse:
    def test_parses_event_and_data_pairs(self) -> None:
        lines = iter(
            [
                b"event: text_delta",
                b'data: {"type": "text_delta", "data": {"text": "hi"}}',
                b"",
                b"event: done",
                b'data: {"data": {"session_id": "s1"}}',
                b"",
            ]
        )
        events = list(_iter_sse(lines))
        assert events[0] == (
            "text_delta",
            {"type": "text_delta", "data": {"text": "hi"}},
        )
        assert events[1][0] == "done"

    def test_malformed_json_yields_empty_dict(self) -> None:
        events = list(_iter_sse(iter([b"event: x", b"data: not-json", b""])))
        assert events == [("x", {})]


class _FakeStreamResponse:
    """Minimal stand-in for httpx streaming response.

    Real httpx ``iter_lines()`` yields str (it decodes internally) — the
    fake mirrors that, which is exactly what caught the bytes/str bug
    during live verification.
    """

    def __init__(self, chunks: list[bytes], headers: dict[str, str] | None = None):
        self._chunks = chunks
        self.headers = headers or {}

    def iter_lines(self):
        for chunk in self._chunks:
            yield from chunk.decode("utf-8").split("\n")

    def raise_for_status(self) -> None:
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass


class _FakeClient:
    def __init__(self, response: _FakeStreamResponse):
        self._response = response
        self.captured: dict = {}
        self.posts: list[tuple[str, dict]] = []

    def stream(self, method: str, url: str, **kwargs):
        self.captured = {"method": method, "url": url, **kwargs}
        return self._response

    def post(self, url: str, **kwargs):
        self.posts.append((url, kwargs.get("json", {})))
        return type("R", (), {"raise_for_status": lambda self: None})()

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        pass


def _patch_client(monkeypatch: pytest.MonkeyPatch, client: _FakeClient) -> None:
    monkeypatch.setattr(ask_mod.httpx, "Client", lambda **_kw: client)
    monkeypatch.setattr(
        ask_mod, "load_config", lambda: type("C", (), {"api_port": 8088})()
    )


class TestAskCommand:
    def test_renders_text_and_session_hint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chunks = [
            _sse("text_delta", {"data": {"text": "你好"}}),
            _sse("tool_call", {"data": {"name": "python_exec"}}),
            _sse("tool_result", {"data": {"output": "2 rows"}}),
            _sse("text_delta", {"data": {"text": "，结论是 X"}}),
            _sse("done", {"data": {"session_id": "sess-abc"}}),
        ]
        client = _FakeClient(
            _FakeStreamResponse(chunks, headers={"X-Session-ID": "sess-abc"})
        )
        _patch_client(monkeypatch, client)

        outcome = CliRunner().invoke(ask, ["bb_v3 回撤为什么深"])

        assert outcome.exit_code == 0, outcome.output
        assert "你好" in outcome.output
        assert "，结论是 X" in outcome.output
        assert "python_exec" in outcome.output
        assert "2 rows" in outcome.output
        assert "sess-abc" in outcome.output
        assert "--session sess-abc" in outcome.output

    def test_request_body_carries_message_and_session(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(
            _FakeStreamResponse(
                [_sse("done", {"data": {"session_id": "s9"}})],
                headers={"X-Session-ID": "s9"},
            )
        )
        _patch_client(monkeypatch, client)

        outcome = CliRunner().invoke(ask, ["继续查", "--session", "s9"])

        assert outcome.exit_code == 0, outcome.output
        body = client.captured["json"]
        assert body == {"message": "继续查", "session_id": "s9"}
        assert client.captured["url"].endswith("/api/v1/agent/stream")

    def test_connection_error_becomes_click_exception(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _raise(**_kw):
            raise httpx.ConnectError("refused")

        monkeypatch.setattr(ask_mod.httpx, "Client", _raise)
        monkeypatch.setattr(
            ask_mod, "load_config", lambda: type("C", (), {"api_port": 8088})()
        )

        outcome = CliRunner().invoke(ask, ["hi"])

        assert outcome.exit_code != 0
        assert "cannot reach agent" in outcome.output
        assert "8088" in outcome.output

    def test_no_text_prints_placeholder(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(
            _FakeStreamResponse([_sse("done", {"data": {}})], headers={})
        )
        _patch_client(monkeypatch, client)

        outcome = CliRunner().invoke(ask, ["hi"])

        assert outcome.exit_code == 0, outcome.output
        assert "no answer text" in outcome.output

    def test_provenance_line_counts_tool_uses(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        chunks = [
            _sse("tool_call", {"data": {"name": "python_exec"}}),
            _sse("tool_call", {"data": {"name": "market_ticker"}}),
            _sse("tool_call", {"data": {"name": "python_exec"}}),
            _sse("done", {"data": {"session_id": "s1"}}),
        ]
        client = _FakeClient(
            _FakeStreamResponse(chunks, headers={"X-Session-ID": "s1"})
        )
        _patch_client(monkeypatch, client)

        outcome = CliRunner().invoke(ask, ["查一下"])

        assert outcome.exit_code == 0, outcome.output
        assert "provenance: python_exec ×2 · market_ticker ×1" in outcome.output

    def test_no_tools_no_provenance_line(self, monkeypatch: pytest.MonkeyPatch) -> None:
        chunks = [
            _sse("text_delta", {"data": {"text": "纯回答"}}),
            _sse("done", {"data": {"session_id": "s1"}}),
        ]
        client = _FakeClient(
            _FakeStreamResponse(chunks, headers={"X-Session-ID": "s1"})
        )
        _patch_client(monkeypatch, client)

        outcome = CliRunner().invoke(ask, ["hi"])

        assert outcome.exit_code == 0, outcome.output
        assert "provenance:" not in outcome.output


class TestAskToolApproval:
    """B0 确认门:写工具在 tool_call 与 tool_result 之间插入 tool_approval_request,
    后端回合在此暂停等待人手裁决。CLI 若不理它,流会一直卡到超时 ——
    ask 须把它渲染成 y/n 提示并 POST /agent/approve。"""

    def _chunks(self):
        return [
            _sse("text_delta", {"data": {"text": "准备下单"}}),
            _sse("tool_call", {"data": {"name": "paper_place_order"}}),
            _sse(
                "tool_approval_request",
                {
                    "data": {
                        "tool_call_id": "tc-1",
                        "tool_name": "paper_place_order",
                        "arguments": {"side": "buy", "qty": 1},
                    }
                },
            ),
            _sse("tool_result", {"data": {"output": "已下单", "tool_call_id": "tc-1"}}),
            _sse("done", {"data": {"session_id": "s7"}}),
        ]

    def test_prompts_and_posts_approval_true_on_yes(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _FakeClient(
            _FakeStreamResponse(self._chunks(), headers={"X-Session-ID": "s7"})
        )
        _patch_client(monkeypatch, client)

        outcome = CliRunner().invoke(ask, ["帮我买一手"], input="y\n")

        assert outcome.exit_code == 0, outcome.output
        # 提示里点出工具名,人才能判断
        assert "paper_place_order" in outcome.output
        # 批准端点被调用,携带 session/tool_call_id/approve=true
        assert client.posts, "expected a POST to /agent/approve"
        url, body = client.posts[-1]
        assert url.endswith("/api/v1/agent/approve")
        assert body == {"session_id": "s7", "tool_call_id": "tc-1", "approve": True}
        # 后续事件仍被渲染
        assert "已下单" in outcome.output

    def test_posts_approval_false_on_no(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = _FakeClient(
            _FakeStreamResponse(self._chunks(), headers={"X-Session-ID": "s7"})
        )
        _patch_client(monkeypatch, client)

        outcome = CliRunner().invoke(ask, ["帮我买一手"], input="n\n")

        assert outcome.exit_code == 0, outcome.output
        assert client.posts
        _url, body = client.posts[-1]
        assert body == {"session_id": "s7", "tool_call_id": "tc-1", "approve": False}

    def test_default_is_deny_on_bare_enter(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # click.confirm(default=False):直接回车 = 拒绝(安全默认,与 B0 fail-closed 同族)
        client = _FakeClient(
            _FakeStreamResponse(self._chunks(), headers={"X-Session-ID": "s7"})
        )
        _patch_client(monkeypatch, client)

        outcome = CliRunner().invoke(ask, ["帮我买一手"], input="\n")

        assert outcome.exit_code == 0, outcome.output
        _url, body = client.posts[-1]
        assert body["approve"] is False
