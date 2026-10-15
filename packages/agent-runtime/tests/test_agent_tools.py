"""Behaviour locks for BaseTool ABC and ToolResult."""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_types.risk import RiskLevel


class _Mirror(BaseTool):
    """Minimal concrete tool: repeats the given text back."""

    @property
    def name(self) -> str:
        return "mirror"

    @property
    def description(self) -> str:
        return "Repeats the supplied text."

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to repeat"},
            },
            "required": ["text"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def execute(self, args: dict[str, Any]) -> ToolResult:
        text = args.get("text", "")
        return ToolResult(output=f"Mirror: {text}")


class TestToolResult:
    def test_success_shape(self) -> None:
        res = ToolResult(output="fine")
        pair = (res.output, res.error)
        assert pair == ("fine", None)

    def test_error_shape(self) -> None:
        res = ToolResult(output="", error="detonation")
        assert res.error == "detonation"

    def test_exact_dict_contract(self) -> None:
        data = ToolResult(output="payload", error=None).model_dump()
        checks = (data == {"output": "payload", "error": None},)
        assert all(checks)

        restored = ToolResult.model_validate(data)
        assert restored.output == "payload"


class TestBaseToolSurface:
    def test_metadata_properties(self) -> None:
        tool = _Mirror()
        trio = (tool.name, tool.description, tool.risk_level)
        assert trio == ("mirror", "Repeats the supplied text.", RiskLevel.SAFE)

    def test_execute_roundtrip(self) -> None:
        out = _Mirror().execute({"text": "ping"})
        checks = (out.output == "Mirror: ping", out.error is None)
        assert all(checks)

    def test_definition_keys(self) -> None:
        defn = _Mirror().to_definition()
        checks = (
            defn["name"] == "mirror",
            defn["description"] == "Repeats the supplied text.",
            "properties" in defn["parameters"],
        )
        assert all(checks)


class TestValidateArgs:
    def test_satisfied_call_has_no_errors(self) -> None:
        errors = _Mirror().validate_args({"text": "hello"})
        assert errors == []

    def test_absent_required_flagged(self) -> None:
        errors = _Mirror().validate_args({})
        checks = (len(errors) == 1, "Missing required parameter: text" in errors[0])
        assert all(checks)

    def test_wrong_type_flagged(self) -> None:
        errors = _Mirror().validate_args({"text": 123})
        checks = (len(errors) == 1, "expected type 'string'" in errors[0])
        assert all(checks)

    def test_extra_keys_tolerated(self) -> None:
        errors = _Mirror().validate_args({"text": "hi", "bonus": True})
        assert errors == []


def test_abstract_base_cannot_construct() -> None:
    with pytest.raises(TypeError):
        BaseTool()  # type: ignore[abstract]
