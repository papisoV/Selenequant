"""Behaviour locks for skills models: SkillFrontmatter, Skill,
SkillsConfig, SkillsLimits, SkillSummary, SkillSnapshot."""

from __future__ import annotations

import pytest

from selene_space_agent.skills.types import (
    Skill,
    SkillFrontmatter,
    SkillsConfig,
    SkillsLimits,
    SkillSnapshot,
    SkillSource,
    SkillSummary,
)


def _recycle(model):
    """model_dump -> model_validate roundtrip."""
    return type(model).model_validate(model.model_dump())


class TestSkillSource:
    def test_member_values(self) -> None:
        quartet = (
            SkillSource.BUNDLED.value,
            SkillSource.USER.value,
            SkillSource.WORKSPACE.value,
            SkillSource.EXTRA.value,
        )
        assert quartet == ("bundled", "user", "workspace", "extra")

    def test_member_count(self) -> None:
        assert len(list(SkillSource)) == 4

    def test_string_equality(self) -> None:
        pair = (SkillSource.BUNDLED == "bundled", SkillSource.WORKSPACE == "workspace")
        assert all(pair)


class TestSkillFrontmatter:
    def test_name_only_construction(self) -> None:
        fm = SkillFrontmatter(name="bare-skill")
        assert fm.name == "bare-skill"

    def test_name_is_mandatory(self) -> None:
        with pytest.raises(Exception):
            SkillFrontmatter()  # type: ignore[call-arg]

    def test_field_defaults(self) -> None:
        fm = SkillFrontmatter(name="defaults-probe")
        checks = (
            fm.description == "",
            fm.version == "0.1.0",
            fm.author == "",
            fm.tags == [],
            fm.user_invocable is True,
            fm.model_invocable is True,
            fm.always_load is False,
            fm.requires_tools == [],
            fm.requires_env == [],
        )
        assert all(checks)

    def test_every_field_set_explicitly(self) -> None:
        fm = SkillFrontmatter(
            name="risk-audit",
            description="Audits portfolio risk",
            version="2.3.1",
            author="Community Maintainers",
            tags=["risk", "audit"],
            user_invocable=False,
            model_invocable=True,
            always_load=True,
            requires_tools=["risk_report", "market_history"],
            requires_env=["BROKER_TOKEN"],
        )
        checks = (
            fm.name == "risk-audit",
            fm.description == "Audits portfolio risk",
            fm.version == "2.3.1",
            fm.author == "Community Maintainers",
            fm.tags == ["risk", "audit"],
            fm.user_invocable is False,
            fm.always_load is True,
            fm.requires_tools == ["risk_report", "market_history"],
            fm.requires_env == ["BROKER_TOKEN"],
        )
        assert all(checks)

    def test_dump_validate_roundtrip(self) -> None:
        fm = SkillFrontmatter(
            name="roundtrip-skill", description="Travels through dicts", tags=["travel"]
        )
        data = fm.model_dump()
        checks = (data["name"] == "roundtrip-skill", data["tags"] == ["travel"])
        assert all(checks)

        restored = _recycle(fm)
        trio = (restored.name, restored.description, restored.tags)
        assert trio == (fm.name, fm.description, fm.tags)

    def test_json_roundtrip(self) -> None:
        fm = SkillFrontmatter(name="json-traveller", tags=["x", "y"])
        restored = SkillFrontmatter.model_validate_json(fm.model_dump_json())
        pair = (restored.name, restored.tags)
        assert pair == ("json-traveller", ["x", "y"])


class TestSkillsLimits:
    def test_factory_defaults(self) -> None:
        limits = SkillsLimits()
        checks = (
            limits.max_skills_in_prompt == 50,
            limits.max_prompt_chars == 40_000,
            limits.max_skill_file_bytes == 256_000,
        )
        assert all(checks)

    def test_custom_values(self) -> None:
        limits = SkillsLimits(
            max_skills_in_prompt=8, max_prompt_chars=4000, max_skill_file_bytes=2048
        )
        checks = (
            limits.max_skills_in_prompt == 8,
            limits.max_prompt_chars == 4000,
            limits.max_skill_file_bytes == 2048,
        )
        assert all(checks)

    def test_roundtrip(self) -> None:
        limits = SkillsLimits(max_skills_in_prompt=33)
        assert _recycle(limits).max_skills_in_prompt == 33


class TestSkillsConfig:
    def test_factory_defaults(self) -> None:
        cfg = SkillsConfig()
        checks = (
            cfg.extra_dirs == [],
            cfg.enabled == {},
            isinstance(cfg.limits, SkillsLimits),
        )
        assert all(checks)

    def test_custom_values(self) -> None:
        cfg = SkillsConfig(
            extra_dirs=["/srv/skills-a", "/srv/skills-b"],
            enabled={"on-skill": True, "off-skill": False},
            limits=SkillsLimits(max_skills_in_prompt=7),
        )
        checks = (
            len(cfg.extra_dirs) == 2,
            cfg.enabled["on-skill"] is True,
            cfg.enabled["off-skill"] is False,
            cfg.limits.max_skills_in_prompt == 7,
        )
        assert all(checks)

    def test_roundtrip(self) -> None:
        cfg = SkillsConfig(extra_dirs=["/p", "/q"], enabled={"z": True})
        restored = _recycle(cfg)
        pair = (restored.extra_dirs, restored.enabled)
        assert pair == (cfg.extra_dirs, cfg.enabled)

    def test_nested_limits_get_defaults(self) -> None:
        assert SkillsConfig().limits.max_prompt_chars == 40_000


class TestDefaultConfigKwargs:
    def test_wires_user_skills_dir(self) -> None:
        from selene_space_agent.skills.config import (
            USER_SKILLS_DIR,
            default_config_kwargs,
        )

        kwargs = default_config_kwargs()
        cfg = SkillsConfig(**kwargs)
        assert cfg.extra_dirs == [str(USER_SKILLS_DIR)]

    def test_registry_loads_with_missing_user_dir(self) -> None:
        # The user dir (~/.selenespace/skills) rarely exists; the registry
        # must not crash — loader skips non-existent roots.
        from selene_space_agent.skills import SkillRegistry
        from selene_space_agent.skills.config import default_config_kwargs

        registry = SkillRegistry(config=SkillsConfig(**default_config_kwargs()))
        registry.load()
        assert registry.is_loaded


def _build_skill(**overrides: object) -> Skill:
    """Construct a Skill from explicit fields, with optional overrides.

    Built field-by-field rather than ``Skill(**base)``: a heterogeneous dict
    widens to ``dict[str, object]`` and mypy cannot check the splat against
    the model's per-field types.
    """
    fields: dict[str, object] = {
        "name": "probe-skill",
        "description": "Probe description",
        "file_path": "/vault/probe-skill/SKILL.md",
        "base_dir": "/vault/probe-skill",
        "source": SkillSource.BUNDLED,
        "frontmatter": SkillFrontmatter(
            name="probe-skill", description="Probe description"
        ),
        "content": "## Recipe\n1. Proceed",
    }
    fields.update(overrides)
    return Skill.model_validate(fields)


class TestSkill:
    def test_core_fields(self) -> None:
        skill = _build_skill()
        checks = (
            skill.name == "probe-skill",
            skill.description == "Probe description",
            skill.source == SkillSource.BUNDLED,
            skill.content == "## Recipe\n1. Proceed",
        )
        assert all(checks)

    def test_required_fields_enforced(self) -> None:
        with pytest.raises(Exception):
            Skill()  # type: ignore[call-arg]

    def test_every_source_accepted(self) -> None:
        for src in SkillSource:
            assert _build_skill(source=src).source == src

    def test_dump_validate_roundtrip(self) -> None:
        skill = _build_skill()
        data = skill.model_dump()
        checks = (data["name"] == "probe-skill", data["source"] == "bundled")
        assert all(checks)

        restored = _recycle(skill)
        pair = (restored.name, restored.frontmatter.name)
        assert pair == (skill.name, skill.frontmatter.name)

    def test_json_roundtrip(self) -> None:
        skill = _build_skill()
        restored = Skill.model_validate_json(skill.model_dump_json())
        pair = (restored.name, restored.source)
        assert pair == ("probe-skill", SkillSource.BUNDLED)


class TestSkillSummary:
    def test_minimal_summary(self) -> None:
        row = SkillSummary(
            name="digest-a", description="Handles digesting", source=SkillSource.USER
        )
        checks = (
            row.name == "digest-a",
            row.description == "Handles digesting",
            row.source == SkillSource.USER,
            row.tags == [],
            row.user_invocable is True,
            row.model_invocable is True,
        )
        assert all(checks)

    def test_flags_overridable(self) -> None:
        row = SkillSummary(
            name="quiet-one",
            description="hush",
            source=SkillSource.WORKSPACE,
            tags=["silent"],
            user_invocable=False,
            model_invocable=False,
        )
        checks = (
            row.user_invocable is False,
            row.model_invocable is False,
            row.tags == ["silent"],
        )
        assert all(checks)


class TestSkillSnapshot:
    def test_versioned_empty_snapshot(self) -> None:
        snap = SkillSnapshot(prompt="## Skills\n...", skills=[], version=1)
        checks = (snap.prompt == "## Skills\n...", snap.skills == [], snap.version == 1)
        assert all(checks)

    def test_snapshot_holds_summaries(self) -> None:
        rows = [
            SkillSummary(
                name="first", description="First skill", source=SkillSource.BUNDLED
            ),
            SkillSummary(
                name="second", description="Second skill", source=SkillSource.USER
            ),
        ]
        snap = SkillSnapshot(prompt="prompt body", skills=rows)
        checks = (len(snap.skills) == 2, snap.skills[0].name == "first")
        assert all(checks)

    def test_roundtrip_restores_default_version(self) -> None:
        snap = SkillSnapshot(prompt="kept", skills=[])
        restored = _recycle(snap)
        pair = (restored.prompt, restored.version)
        assert pair == ("kept", 1)
