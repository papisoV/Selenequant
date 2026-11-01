"""Behaviour locks for McpToolWrapper: name sanitisation, description
composition, parameter schema fallbacks, risk level, and execution."""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_agent.mcp.tool_wrapper import McpToolWrapper, _sanitize_name
from selene_space_agent.mcp.types import McpToolInfo, McpToolResult
from selene_space_types.risk import RiskLevel


class _StubSession:
    """call_tool stand-in returning a canned result or raising."""

    def __init__(self, result: McpToolResult | None = None, error: Exception | None = None) -> None:
        self._result = result or McpToolResult(content="stub payload")
        self._error = error

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> McpToolResult:
        if self._error:
            raise self._error
        return self._result


def _wrapper(
    *,
    server: str = "probe-srv",
    tool: str = "probe_tool",
    blurb: str = "Probe tool",
    schema: dict[str, Any] | None = None,
    risk: RiskLevel | None = None,
    session: _StubSession | None = None,
) -> McpToolWrapper:
    info = McpToolInfo(server_name=server, tool_name=tool, description=blurb, input_schema=schema or {})
    kwargs: dict[str, Any] = {"session": session or _StubSession(), "tool_info": info}
    if risk is not None:
        kwargs["risk_level"] = risk
    return McpToolWrapper(**kwargs)


class TestSanitizeName:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("plain_slug", "plain_slug"),
            ("MixedCase", "mixedcase"),
            ("dotted.name-v2", "dotted_name_v2"),
            ("__bracketed__", "bracketed"),
            ("a@b#c$d", "a_b_c_d"),
            ("has space", "has_space"),
            ("Host-01.V2", "host_01_v2"),
        ],
    )
    def test_rewrites(self, raw: str, expected: str) -> None:
        assert _sanitize_name(raw) == expected

    def test_all_special_collapses_to_empty(self) -> None:
        assert _sanitize_name("---") == ""


class TestNameProperty:
    @pytest.mark.parametrize(
        ("server", "tool", "expected"),
        [
            ("docs", "open_doc", "mcp_docs_open_doc"),
            ("my-srv.v1", "get-row", "mcp_my_srv_v1_get_row"),
            ("Upper", "Lower", "mcp_upper_lower"),
            ("srv9", "tool9", "mcp_srv9_tool9"),
            ("tidy", "neat", "mcp_tidy_neat"),
        ],
        ids=["basic", "sanitized", "lowered", "digits", "clean"],
    )
    def test_name_pattern(self, server: str, tool: str, expected: str) -> None:
        assert _wrapper(server=server, tool=tool).name == expected


class TestDescriptionProperty:
    def test_server_tag_plus_blurb(self) -> None:
        desc = _wrapper(server="docs", tool="open_doc", blurb="Opens a stored doc").description
        checks = ("[MCP: docs]" in desc, "Opens a stored doc" in desc)
        assert all(checks)

    def test_blank_blurb_gets_generated_text(self) -> None:
        desc = _wrapper(server="alpha", tool="beta", blurb="").description
        trio = ("[MCP: alpha]" in desc, "beta" in desc, "alpha" in desc)
        assert all(trio)


class TestParametersProperty:
    def test_schema_passthrough(self) -> None:
        given = {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "mode": {"type": "string", "default": "fast"},
            },
            "required": ["target"],
        }
        assert _wrapper(schema=given).parameters == given

    def test_empty_schema_becomes_object_stub(self) -> None:
        assert _wrapper(schema={}).parameters == {"type": "object", "properties": {}}

    def test_default_schema_has_type_key(self) -> None:
        # input_schema omitted -> Field default_factory supplies {}
        assert "type" in _wrapper(blurb="any").parameters


class TestRiskLevel:
    def test_defaults_to_restricted(self) -> None:
        assert _wrapper().risk_level == RiskLevel.RESTRICTED

    def test_dangerous_override(self) -> None:
        assert _wrapper(risk=RiskLevel.DANGEROUS).risk_level == RiskLevel.DANGEROUS

    def test_safe_override(self) -> None:
        assert _wrapper(risk=RiskLevel.SAFE).risk_level == RiskLevel.SAFE


class TestExecute:
    def test_happy_path_returns_output(self) -> None:
        sess = _StubSession(result=McpToolResult(content="ran fine"))
        out = _wrapper(session=sess).execute({"k": "v"})
        checks = (out.output == "ran fine", out.error is None)
        assert all(checks)

    def test_mcp_error_flag_maps_to_error_field(self) -> None:
        sess = _StubSession(result=McpToolResult(content="boom upstream", is_error=True))
        out = _wrapper(session=sess).execute({})
        pair = (out.error, out.output)
        assert pair == ("boom upstream", "boom upstream")

    def test_raised_exception_becomes_error_string(self) -> None:
        sess = _StubSession(error=RuntimeError("socket died"))
        out = _wrapper(session=sess).execute({})
        checks = (out.error is not None, "MCP tool execution failed" in out.error)
        assert all(checks)

    def test_empty_args_accepted(self) -> None:
        sess = _StubSession(result=McpToolResult(content="no-arg path"))
        assert _wrapper(session=sess).execute({}).output == "no-arg path"


class TestToDefinition:
    def test_definition_fields(self) -> None:
        defn = _wrapper(
            server="docs",
            tool="open_doc",
            blurb="Opens a doc",
            schema={"type": "object", "properties": {"doc_id": {"type": "string"}}},
        ).to_definition()

        checks = (
            defn["name"] == "mcp_docs_open_doc",
            "Opens a doc" in defn["description"],
            "doc_id" in defn["parameters"]["properties"],
        )
        assert all(checks)
