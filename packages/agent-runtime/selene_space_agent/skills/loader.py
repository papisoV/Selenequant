"""SKILL.md discovery and parsing with safety guards."""

from __future__ import annotations

from pathlib import Path

import yaml

from selene_space_agent.skills.types import (
    Skill,
    SkillFrontmatter,
    SkillsLimits,
    SkillSource,
)

_SKILL_FILE = "SKILL.md"
_DEFAULT_LIMITS = SkillsLimits()


def _parse_frontmatter(raw: str) -> tuple[dict, str]:
    """Split ``---\\nyaml\\n---\\nbody`` into (metadata, body).

    Any malformed frontmatter — missing delimiters, unparseable YAML, or a
    non-mapping root — yields ``({}, raw)`` so callers can fall back to
    treating the whole file as body text.
    """
    text = raw.lstrip("﻿")
    if not text.startswith("---"):
        return {}, raw

    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, raw

    _, yaml_block, body = parts
    try:
        parsed = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        return {}, raw
    if not isinstance(parsed, dict):
        return {}, raw
    return parsed, body.strip()


class SkillLoader:
    """Scan one source directory and materialize Skill objects."""

    def __init__(
        self,
        root_dir: Path,
        source: SkillSource,
        limits: SkillsLimits | None = None,
    ) -> None:
        self._root = Path(root_dir).resolve()
        self._source = source
        self._limits = limits or _DEFAULT_LIMITS

    @property
    def root_dir(self) -> Path:
        return self._root

    @property
    def source(self) -> SkillSource:
        return self._source

    # ------------------------------------------------------------------
    # Scanning
    # ------------------------------------------------------------------

    def scan(self) -> list[Skill]:
        if not self._root.is_dir():
            return []

        skills: list[Skill] = []
        for entry in sorted(self._root.iterdir()):
            if not entry.is_dir():
                continue
            if not self._within_root(entry.resolve()):
                continue
            skill = self.load_single(entry)
            if skill is not None:
                skills.append(skill)
        return sorted(skills, key=lambda s: s.name)

    def load_single(self, skill_dir: Path) -> Skill | None:
        skill_md = skill_dir / _SKILL_FILE
        if not skill_md.is_file():
            return None

        try:
            size = skill_md.stat().st_size
            if size > self._limits.max_skill_file_bytes:
                return None
            raw = skill_md.read_text(encoding="utf-8")
        except OSError:
            return None

        metadata, body = _parse_frontmatter(raw)
        dir_name = skill_dir.name
        name = str(metadata.get("name") or dir_name)
        description = str(metadata.get("description") or "")

        try:
            frontmatter = SkillFrontmatter.model_validate({**metadata, "name": name})
            description = frontmatter.description or description
        except Exception:
            frontmatter = SkillFrontmatter(name=name, description=description)

        return Skill(
            name=name,
            description=description,
            file_path=str(skill_md),
            base_dir=str(skill_dir),
            source=self._source,
            frontmatter=frontmatter,
            content=body.strip(),
        )

    def _within_root(self, resolved: Path) -> bool:
        return self._root == resolved or self._root in resolved.parents
