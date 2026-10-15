"""Skills module for SeleneQuant agent runtime.

Provides skill loading, registry, and prompt formatting for Markdown-based
skills (SKILL.md files with YAML frontmatter).

Public API:
    - Types: ``Skill``, ``SkillFrontmatter``, ``SkillSource``, ``SkillSummary``,
      ``SkillsConfig``, ``SkillsLimits``, ``SkillSnapshot``
    - Registry: ``SkillRegistry``
    - Formatting: ``format_skills_for_prompt``, ``format_skills_compact``
"""

from selene_space_agent.skills.prompt import format_skills_compact, format_skills_for_prompt
from selene_space_agent.skills.registry import SkillRegistry
from selene_space_agent.skills.types import (
    Skill,
    SkillFrontmatter,
    SkillsConfig,
    SkillsLimits,
    SkillSnapshot,
    SkillSource,
    SkillSummary,
)

__all__ = [
    "Skill",
    "SkillFrontmatter",
    "SkillRegistry",
    "SkillSnapshot",
    "SkillSource",
    "SkillsConfig",
    "SkillsLimits",
    "SkillSummary",
    "format_skills_compact",
    "format_skills_for_prompt",
]
