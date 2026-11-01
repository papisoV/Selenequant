"""Behaviour locks for MCP models: McpServerConfig, McpConfig,
McpToolInfo, McpToolResult, McpServerStatus."""

from __future__ import annotations

import pytest

from selene_space_agent.mcp.types import (
    McpConfig,
    McpServerConfig,
    McpServerStatus,
    McpToolInfo,
    McpToolResult,
    McpTransport,
)
from selene_space_types.risk import RiskLevel


def _recycle(model):
    """model_dump -> model_validate roundtrip."""
    return type(model).model_validate(model.model_dump())


class TestMcpTransport:
    def test_member_values(self) -> None:
        pair = (McpTransport.STDIO.value, McpTransport.SSE.value)
        assert pair == ("stdio", "sse")

    def test_member_count(self) -> None:
        assert len(list(McpTransport)) == 2

    def test_string_equality(self) -> None:
        checks = (McpTransport.STDIO == "stdio", McpTransport.SSE == "sse")
        assert all(checks)


class TestMcpServerConfig:
    def test_factory_defaults(self) -> None:
        cfg = McpServerConfig()
        checks = (
            cfg.command is None,
            cfg.args == [],
            cfg.env == {},
            cfg.cwd is None,
            cfg.url is None,
            cfg.transport == McpTransport.STDIO,
            cfg.enabled is True,
            cfg.risk_level == RiskLevel.RESTRICTED,
        )
        assert all(checks)

    def test_stdio_launch_shape(self) -> None:
        cfg = McpServerConfig(
            command="uvx",
            args=["-y", "some-mcp-server"],
            transport=McpTransport.STDIO,
        )
        checks = (cfg.command == "uvx", cfg.args == ["-y", "some-mcp-server"], cfg.transport == McpTransport.STDIO)
        assert all(checks)

    def test_sse_endpoint_shape(self) -> None:
        endpoint = "https://mcp.internal.example/sse"
        cfg = McpServerConfig(url=endpoint, transport=McpTransport.SSE)
        checks = (cfg.url == endpoint, cfg.transport == McpTransport.SSE)
        assert all(checks)

    def test_env_and_working_dir(self) -> None:
        cfg = McpServerConfig(
            command="python",
            args=["srv.py"],
            env={"TOKEN": "abc123"},
            cwd="/srv/mcp",
        )
        checks = (cfg.env == {"TOKEN": "abc123"}, cfg.cwd == "/srv/mcp")
        assert all(checks)

    def test_toggle_disabled(self) -> None:
        assert McpServerConfig(enabled=False).enabled is False

    def test_risk_level_override(self) -> None:
        assert McpServerConfig(risk_level=RiskLevel.DANGEROUS).risk_level == RiskLevel.DANGEROUS

    # -- validate_config -------------------------------------------------------

    @pytest.mark.parametrize(
        ("transport", "missing_field"),
        [
            (McpTransport.STDIO, "command"),
            (McpTransport.SSE, "url"),
        ],
    )
    def test_missing_required_field_reports_error(self, transport, missing_field) -> None:
        kwargs = {missing_field: None}
        problems = McpServerConfig(transport=transport, **kwargs).validate_config()
        checks = (len(problems) == 1, missing_field in problems[0].lower())
        assert all(checks)

    @pytest.mark.parametrize(
        "cfg",
        [
            McpServerConfig(transport=McpTransport.STDIO, command="uvx"),
            McpServerConfig(transport=McpTransport.SSE, url="https://ok.example/sse"),
        ],
        ids=["stdio-ok", "sse-ok"],
    )
    def test_satisfied_transport_has_no_errors(self, cfg: McpServerConfig) -> None:
        assert cfg.validate_config() == []

    def test_dump_validate_roundtrip(self) -> None:
        cfg = McpServerConfig(
            command="node",
            args=["main.js"],
            env={"PORT": "8080"},
            transport=McpTransport.STDIO,
            risk_level=RiskLevel.SAFE,
        )
        restored = _recycle(cfg)
        checks = (restored.command == "node", restored.risk_level == RiskLevel.SAFE)
        assert all(checks)


class TestMcpConfig:
    def test_no_servers_by_default(self) -> None:
        assert McpConfig().servers == {}

    def test_two_named_servers(self) -> None:
        cfg = McpConfig(
            servers={
                "docs": McpServerConfig(command="npx", transport=McpTransport.STDIO),
                "cloud": McpServerConfig(
                    url="https://mcp.example.net/sse",
                    transport=McpTransport.SSE,
                    enabled=False,
                ),
            }
        )
        checks = (
            set(cfg.servers) == {"docs", "cloud"},
            cfg.servers["cloud"].enabled is False,
        )
        assert all(checks)

    def test_serialization_roundtrip(self) -> None:
        cfg = McpConfig(servers={"probe": McpServerConfig(command="echo")})
        restored = _recycle(cfg)
        assert restored.servers["probe"].command == "echo"


class TestMcpToolInfo:
    def test_full_construction(self) -> None:
        info = McpToolInfo(
            server_name="docs",
            tool_name="open_doc",
            description="Opens a stored document",
            input_schema={"type": "object", "properties": {"doc_id": {"type": "string"}}},
        )
        checks = (
            info.server_name == "docs",
            info.tool_name == "open_doc",
            info.description == "Opens a stored document",
            "doc_id" in info.input_schema["properties"],
        )
        assert all(checks)

    def test_optional_fields_default(self) -> None:
        bare = McpToolInfo(server_name="x", tool_name="y")
        checks = (bare.description == "", bare.input_schema == {})
        assert all(checks)

    def test_roundtrip(self) -> None:
        info = McpToolInfo(server_name="s9", tool_name="t9", description="d")
        restored = _recycle(info)
        pair = (restored.server_name, restored.tool_name)
        assert pair == ("s9", "t9")


class TestMcpToolResult:
    def test_success_payload(self) -> None:
        res = McpToolResult(content="All good here")
        checks = (res.content == "All good here", res.is_error is False)
        assert all(checks)

    def test_failure_payload(self) -> None:
        res = McpToolResult(content="Doc not found", is_error=True)
        checks = (res.content == "Doc not found", res.is_error is True)
        assert all(checks)

    def test_blank_defaults(self) -> None:
        res = McpToolResult()
        checks = (res.content == "", res.is_error is False)
        assert all(checks)

    def test_dict_roundtrip(self) -> None:
        res = McpToolResult(content="payload", is_error=False)
        data = res.model_dump()
        checks = (data["content"] == "payload", data["is_error"] is False)
        assert all(checks)
        assert McpToolResult.model_validate(data).content == "payload"


class TestMcpServerStatus:
    def test_fresh_status_defaults(self) -> None:
        st = McpServerStatus(name="fresh", config=McpServerConfig())
        checks = (
            st.name == "fresh",
            st.connected is False,
            st.tool_count == 0,
            st.error is None,
            st.tools == [],
        )
        assert all(checks)

    def test_online_with_tool_listing(self) -> None:
        tools = [
            McpToolInfo(server_name="live", tool_name="first", description="First"),
            McpToolInfo(server_name="live", tool_name="second", description="Second"),
        ]
        st = McpServerStatus(
            name="live",
            config=McpServerConfig(command="node"),
            connected=True,
            tool_count=2,
            tools=tools,
        )
        checks = (st.connected is True, st.tool_count == 2, len(st.tools) == 2)
        assert all(checks)

    def test_failure_reason_recorded(self) -> None:
        st = McpServerStatus(
            name="down",
            config=McpServerConfig(),
            connected=False,
            error="Connection refused",
        )
        checks = (st.error == "Connection refused", st.connected is False)
        assert all(checks)

    def test_roundtrip(self) -> None:
        st = McpServerStatus(
            name="round",
            config=McpServerConfig(command="echo"),
            connected=True,
            tool_count=1,
        )
        restored = _recycle(st)
        checks = (restored.name == "round", restored.connected is True)
        assert all(checks)
