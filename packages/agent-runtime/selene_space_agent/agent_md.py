"""Agent-md loading: canonical system prompts as markdown files.

FS-paradigm adaptation (DESIGN_FS_AGENT_PARADIGM.md F1a/F1b): each role's
canonical prompt lives in ``agents/<slug>.md`` (frontmatter + five-section
body); the Python ``RoleDefinition`` falls back to its inline string when
the file is missing or unparsable, so packaging without the md directory
keeps working.
"""

from __future__ import annotations

from pathlib import Path

import yaml

_AGENTS_DIR = Path(__file__).resolve().parent / "agents"


def load_agent_md(slug: str) -> str | None:
    """Read ``agents/<slug>.md`` and return its body (after frontmatter).

    Returns None when the file is missing, unreadable, or has unparseable
    frontmatter — callers then fall back to the inline prompt.
    """
    path = _AGENTS_DIR / f"{slug}.md"
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    text = raw.lstrip("﻿")
    if not text.startswith("---"):
        return text.strip() or None
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text.strip() or None
    try:
        parsed = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return None
    if not isinstance(parsed, dict):
        return None
    body = parts[2].strip()
    return body or None


def load_base_md() -> str | None:
    """Read ``agents/_base.md`` body; None → caller falls back to inline."""
    return load_agent_md("_base")
