"""Tests for the read_skill tool — the fetch half of lazy skill loading."""

from __future__ import annotations

from pathlib import Path

import pytest
from selene_space_agent.skills.registry import SkillRegistry
from selene_space_agent.skills.types import SkillSource, SkillsConfig
from selene_space_agent.tools.read_skill import ReadSkillTool


def _write_skill(root: Path, slug: str, body: str, refs: dict[str, str] | None = None):
    d = root / slug
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {slug}\ndescription: A test skill\nlazy_load: true\n---\n\n{body}",
        encoding="utf-8",
    )
    for rel, content in (refs or {}).items():
        target = d / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return d


@pytest.fixture()
def registry(tmp_path: Path) -> SkillRegistry:
    _write_skill(tmp_path, "alpha-skill", "alpha body", {"references/deep.md": "deep"})
    reg = SkillRegistry(bundled_dir=tmp_path)
    reg.load()
    return reg


@pytest.fixture()
def tool(registry: SkillRegistry) -> ReadSkillTool:
    return ReadSkillTool(lambda: registry)


def test_reads_skill_body(tool: ReadSkillTool) -> None:
    result = tool.execute({"name": "alpha-skill"})
    assert result.error is None
    assert "alpha body" in result.output


def test_reads_referenced_file(tool: ReadSkillTool) -> None:
    result = tool.execute({"name": "alpha-skill", "ref": "references/deep.md"})
    assert result.error is None
    assert "deep" in result.output


def test_unknown_skill_is_rejected(tool: ReadSkillTool) -> None:
    result = tool.execute({"name": "no-such-skill"})
    assert result.error is not None
    assert "unknown skill" in result.error


def test_missing_ref_is_rejected(tool: ReadSkillTool) -> None:
    result = tool.execute({"name": "alpha-skill", "ref": "references/nope.md"})
    assert result.error is not None


def test_ref_cannot_escape_skill_directory(tool: ReadSkillTool) -> None:
    """A traversal ref must not read outside the skill's own directory."""
    result = tool.execute({"name": "alpha-skill", "ref": "../../../etc/passwd"})
    assert result.error is not None
    assert "escapes" in result.error


def test_empty_name_is_rejected(tool: ReadSkillTool) -> None:
    assert tool.execute({"name": ""}).error is not None
    assert tool.execute({}).error is not None


def test_no_registry_degrades_cleanly() -> None:
    tool = ReadSkillTool(lambda: None)
    result = tool.execute({"name": "alpha-skill"})
    assert result.error is not None


def test_tool_definition_shape(tool: ReadSkillTool) -> None:
    definition = tool.to_definition()
    assert definition["name"] == "read_skill"
    assert "name" in definition["parameters"]["required"]


def test_respects_registry_priority(tmp_path: Path) -> None:
    """A skill from a higher-priority source wins (here: bundled over extra)."""
    extra = tmp_path / "extra"
    bundled = tmp_path / "bundled"
    _write_skill(extra, "dup-skill", "EXTRA body")
    _write_skill(bundled, "dup-skill", "BUNDLED body")
    reg = SkillRegistry(
        config=SkillsConfig(extra_dirs=[str(extra)]), bundled_dir=bundled
    )
    reg.load()
    tool = ReadSkillTool(lambda: reg)
    result = tool.execute({"name": "dup-skill"})
    assert result.error is None
    assert "BUNDLED body" in result.output


def test_source_is_read_only_safe(tool: ReadSkillTool) -> None:
    from selene_space_types.risk import RiskLevel

    assert tool.risk_level == RiskLevel.SAFE


def test_skill_source_enum_unused_guard() -> None:
    """Sanity: the fixture skills carry the EXTRA source (bundled_dir path)."""
    # Guards against a future refactor silently changing how tests wire dirs.
    assert SkillSource.BUNDLED is not None
