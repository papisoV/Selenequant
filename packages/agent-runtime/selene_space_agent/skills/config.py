"""Default directory layout for the skills subsystem."""

from __future__ import annotations

from pathlib import Path
from typing import TypedDict

BUNDLED_SKILLS_DIR = Path(__file__).resolve().parent / "bundled_skills"
USER_SKILLS_DIR = Path.home() / ".selenespace" / "skills"


class DefaultConfigKwargs(TypedDict):
    """Shape of :func:`default_config_kwargs` — precise so ``SkillsConfig(**...)``
    type-checks at the call site rather than widening to ``dict[str, object]``."""

    extra_dirs: list[str]


def default_config_kwargs() -> DefaultConfigKwargs:
    """Standard constructor kwargs wiring up the default skill directories."""
    return {
        "extra_dirs": [str(USER_SKILLS_DIR)],
    }
