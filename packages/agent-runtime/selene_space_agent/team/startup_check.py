"""Startup tool-reference audit — surface dangling references before they bite silently.

roles.py hardcodes ``allowed_tools`` tuples and skills declare
``requires_tools`` frontmatter, but nothing at runtime verifies those names
exist in the ToolCatalog. A renamed tool leaves roles with dead entries and
skills silently hidden by ``SkillRegistry.list_eligible``. This module
aggregates both checks into one problem list (external-research-project startup_validation
pattern: collect everything, report once).
"""

from __future__ import annotations

from typing import Iterable

from selene_space_agent.team.roles import AGENT_ROLES


def check_tool_references(
    tool_names: set[str],
    skills: Iterable[object] = (),
) -> list[str]:
    """Return every dangling tool reference across roles and skills.

    *tool_names* is the set of names registered in the ToolCatalog.
    *skills* are Skill objects (anything with ``name`` and
    ``frontmatter.requires_tools``); they are duck-typed so tests can pass
    lightweight fakes.
    """
    problems: list[str] = []

    for role_value, role in AGENT_ROLES.items():
        for tool in role.allowed_tools:
            if tool not in tool_names:
                problems.append(f"role '{role_value}' references unknown tool '{tool}'")

    for skill in skills:
        requires = getattr(getattr(skill, "frontmatter", None), "requires_tools", None)
        if not requires:
            continue
        name = getattr(skill, "name", "<unnamed>")
        for tool in requires:
            if tool not in tool_names:
                problems.append(
                    f"skill '{name}' requires unknown tool '{tool}' "
                    "(skill silently hidden from eligible list)"
                )

    return problems
