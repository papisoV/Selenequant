"""read_skill — fetch a skill's full body (or a referenced file) on demand.

Lazy skills (``lazy_load: true`` in frontmatter) contribute only their
description to the system prompt; the methodology body — potentially tens of
KB across a SKILL.md plus ``references/*.md`` — stays on disk until the model
actually needs it.  This tool is the fetch half of that contract.

Guards: the skill name must resolve in the live registry, and any ``ref`` must
resolve *inside* that skill's own directory.  Both checks are mistake-
prevention, not an adversarial sandbox (same posture as code_exec).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from selene_space_types.risk import RiskLevel

from selene_space_agent.skills.types import Skill
from selene_space_agent.tools.base import BaseTool, ToolResult

_MAX_OUTPUT_CHARS = 40_000
_MAX_REF_BYTES = 256_000


class ReadSkillTool(BaseTool):
    """Read a loaded skill's body, or a file referenced from within it."""

    def __init__(self, registry_getter: Callable[[], Any | None]) -> None:
        self._get_registry = registry_getter

    @property
    def name(self) -> str:
        return "read_skill"

    @property
    def description(self) -> str:
        return (
            "Load the full text of a skill listed in the system prompt as "
            'lazy="true". Omit "ref" for the skill body; pass "ref" (a path '
            "relative to the skill directory, e.g. "
            '"references/workbook-patterns.md") for a referenced file. Skills '
            'without lazy="true" are already fully present — do not re-read '
            "them."
        )

    @property
    def parameters(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Skill name as shown in the system prompt.",
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Optional path relative to the skill's own directory. "
                        "Omit to read SKILL.md."
                    ),
                },
            },
            "required": ["name"],
        }

    @property
    def risk_level(self) -> RiskLevel:
        return RiskLevel.SAFE

    def _resolve_skill(self, name: str) -> Skill | None:
        registry = self._get_registry()
        if registry is None:
            return None
        getter = getattr(registry, "get", None)
        if not callable(getter):
            return None
        skill: Skill | None = getter(name)
        return skill

    def _read_ref(self, skill: Skill, ref: str) -> tuple[str | None, str | None]:
        """Return (content, error); resolves ref strictly inside base_dir."""
        base = Path(skill.base_dir).resolve()
        candidate = (base / ref).resolve()
        if base != candidate and base not in candidate.parents:
            return None, f"ref {ref!r} escapes the skill directory"
        if not candidate.is_file():
            return None, f"no such file in skill {skill.name!r}: {ref}"
        try:
            if candidate.stat().st_size > _MAX_REF_BYTES:
                return None, f"ref {ref!r} exceeds {_MAX_REF_BYTES} bytes"
            return candidate.read_text(encoding="utf-8"), None
        except OSError as exc:
            return None, f"could not read {ref!r}: {exc}"

    def execute(self, args: dict[str, Any]) -> ToolResult:
        name = str(args.get("name", "")).strip()
        if not name:
            return ToolResult(output="", error="name is required")

        skill = self._resolve_skill(name)
        if skill is None:
            return ToolResult(output="", error=f"unknown skill {name!r}")

        ref = str(args.get("ref", "") or "").strip()
        if ref:
            content, error = self._read_ref(skill, ref)
            if error:
                return ToolResult(output="", error=error)
            text = content or ""
            header = f"# {skill.name} — {ref}"
        else:
            text = skill.content or ""
            header = f"# {skill.name}"

        truncated = False
        if len(text) > _MAX_OUTPUT_CHARS:
            text = text[:_MAX_OUTPUT_CHARS]
            truncated = True

        body = f"{header}\n\n{text}"
        if truncated:
            body += f"\n\n[truncated at {_MAX_OUTPUT_CHARS} chars]"
        return ToolResult(output=body)
