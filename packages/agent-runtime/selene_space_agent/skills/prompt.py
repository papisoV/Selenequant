"""Render loaded skills into system-prompt text."""

from __future__ import annotations

import logging

from selene_space_agent.skills.types import Skill, SkillsLimits

logger = logging.getLogger(__name__)

_DEFAULT_LIMITS = SkillsLimits()
_NO_DESCRIPTION = "No description"
_HEADER = "## Available Skills"
_LAZY_NOTE = (
    'Skills marked lazy="true" show only their description. Call '
    'read_skill(name="<skill>") to load the full methodology, or '
    'read_skill(name="<skill>", ref="<relative path>") for a referenced file.'
)


def _summary_lines(skill: Skill) -> list[str]:
    description = skill.description or _NO_DESCRIPTION
    return [f"- **{skill.name}**: {description}"]


def format_skills_compact(skills: list[Skill]) -> str:
    """One-line-per-skill listing; used when the full format overflows."""
    if not skills:
        return ""
    lines = [_HEADER, ""]
    for skill in skills:
        lines.extend(_summary_lines(skill))
    return "\n".join(lines)


def _render_one(skill: Skill, lines: list[str]) -> None:
    """Append one skill's XML block; lazy skills omit their body."""
    description = skill.description or _NO_DESCRIPTION
    if skill.frontmatter.lazy_load:
        lines.extend(
            [
                '<skill lazy="true">',
                f"<name>{skill.name}</name>",
                f"<description>{description}</description>",
                "</skill>",
                "",
            ]
        )
        return

    content = skill.content or ""
    indented = "\n".join(f"  {line}" if line else "" for line in content.split("\n"))
    lines.extend(
        [
            "<skill>",
            f"<name>{skill.name}</name>",
            f"<description>{description}</description>",
            "<content>",
            indented,
            "</content>",
            "</skill>",
            "",
        ]
    )


def _build_full_format(skills: list[Skill]) -> str:
    lines = [_HEADER, ""]
    if any(s.frontmatter.lazy_load for s in skills):
        lines.extend([_LAZY_NOTE, ""])
    lines.extend(["<available_skills>", ""])
    for skill in skills:
        _render_one(skill, lines)
    lines.append("</available_skills>")
    return "\n".join(lines)


def format_skills_for_prompt(
    skills: list[Skill],
    limits: SkillsLimits | None = None,
) -> str:
    """Full XML-tagged prompt block, degrading gracefully under limits.

    Strategy: start with all (capped) skills in full format; shrink the set
    until it fits ``max_prompt_chars``; fall back to the compact listing if
    even one full-format skill does not fit.

    Lazy skills (``lazy_load: true``) contribute only their description here;
    their body is fetched on demand via the read_skill tool.  Any skill
    dropped to fit the budget is logged — a silent drop would read as "every
    skill is resident" when it is not.
    """
    if not skills:
        return ""

    active = limits or _DEFAULT_LIMITS
    ordered = sorted(skills, key=lambda s: s.name)
    candidates = ordered[: active.max_skills_in_prompt]

    while candidates:
        rendered = _build_full_format(candidates)
        if len(rendered) <= active.max_prompt_chars:
            dropped = [
                s.name for s in ordered if s.name not in {c.name for c in candidates}
            ]
            if dropped:
                logger.warning(
                    "Skills prompt over budget: dropped %d skill(s): %s "
                    "(raise max_prompt_chars or mark them lazy_load)",
                    len(dropped),
                    dropped,
                )
            return rendered
        candidates = candidates[:-1]

    logger.warning(
        "Skills prompt over budget even for a single skill; falling back to "
        "the compact listing for %d skill(s)",
        len(ordered),
    )
    compact = format_skills_compact(ordered[: active.max_skills_in_prompt])
    if len(compact) > active.max_prompt_chars:
        return compact[: active.max_prompt_chars]
    return compact
