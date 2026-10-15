"""BaseTool ABC and ToolResult — foundation for all agent tools.

Every tool in SeleneQuant implements BaseTool.  Tools are sync unless they
provide ``async_execute``; the react runtime awaits that coroutine directly
when present, and otherwise calls ``execute`` inline.
"""

from __future__ import annotations

import abc
from typing import Any

from pydantic import BaseModel

from selene_space_types.risk import RiskLevel


class ToolResult(BaseModel):
    """Outcome of one tool execution."""

    output: str
    error: str | None = None


_JSON_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "number": (int, float),
    "integer": (int,),
    "boolean": (bool,),
    "array": (list, tuple),
    "object": (dict,),
}


def _is_instance_of(value: Any, types: tuple[type, ...]) -> bool:
    for t in types:
        if t is bool:
            if isinstance(value, bool):
                return True
        elif isinstance(value, bool):
            continue  # bool is not a valid number/str under strict JSON typing
        elif isinstance(value, t):
            return True
    return False


class BaseTool(abc.ABC):
    """Abstract base class for all agent tools."""

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique tool name exposed to the LLM."""

    @property
    @abc.abstractmethod
    def description(self) -> str:
        """Human/LLM readable description of what the tool does."""

    @property
    @abc.abstractmethod
    def parameters(self) -> dict[str, Any]:
        """JSON Schema describing accepted arguments."""

    @property
    @abc.abstractmethod
    def risk_level(self) -> RiskLevel:
        """Risk classification used by policy gating."""

    @abc.abstractmethod
    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Run the tool synchronously."""

    def to_definition(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def validate_args(self, args: dict[str, Any]) -> list[str]:
        schema = self.parameters or {}
        properties: dict[str, Any] = schema.get("properties", {})
        required: list[str] = schema.get("required", [])
        errors: list[str] = []

        for req in required:
            if req not in args:
                errors.append(f"Missing required parameter: {req}")

        for key, value in args.items():
            spec = properties.get(key)
            if not isinstance(spec, dict):
                continue
            expected = spec.get("type")
            if expected and expected in _JSON_TYPE_CHECKS:
                if not _is_instance_of(value, _JSON_TYPE_CHECKS[expected]):
                    errors.append(f"Parameter '{key}' expected type '{expected}'")

        return errors


# Fields the model must never see or fill: the backend injects them after a
# human approves. Stripping them from the exposed schema means the model has
# no field to forge.
CONFIRM_FIELDS: tuple[str, ...] = ("user_confirmed",)


def strip_confirm_fields(definition: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a tool definition without human-confirmation fields.

    Removes each name in ``CONFIRM_FIELDS`` from ``parameters.properties``
    and from ``parameters.required`` in lockstep — a ``required`` entry that
    names a property no longer present is an invalid JSON Schema that some
    providers reject. Immutable: the input definition is never mutated.
    """
    params = definition.get("parameters")
    if not isinstance(params, dict):
        return definition

    props = params.get("properties")
    required = params.get("required")
    present = isinstance(props, dict) and any(f in props for f in CONFIRM_FIELDS)
    listed = isinstance(required, list) and any(f in required for f in CONFIRM_FIELDS)
    if not present and not listed:
        return definition

    new_params = dict(params)
    if isinstance(props, dict):
        new_params["properties"] = {
            k: v for k, v in props.items() if k not in CONFIRM_FIELDS
        }
    if isinstance(required, list):
        new_params["required"] = [r for r in required if r not in CONFIRM_FIELDS]

    return {**definition, "parameters": new_params}
