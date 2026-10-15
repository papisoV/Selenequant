"""Pydantic models for the skills subsystem."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class SkillSource(str, Enum):
    """Where a skill was loaded from; higher members win name conflicts."""

    EXTRA = "extra"
    BUNDLED = "bundled"
    USER = "user"
    WORKSPACE = "workspace"


class SkillFrontmatter(BaseModel):
    """YAML frontmatter block at the top of a SKILL.md file."""

    name: str
    description: str = ""
    version: str = "0.1.0"
    author: str = ""
    tags: list[str] = Field(default_factory=list)
    user_invocable: bool = True
    model_invocable: bool = True
    always_load: bool = False
    # Lazy skills expose only their description in the system prompt; the body
    # is fetched on demand via the read_skill tool.  Keeps bulk methodology
    # (research skills) out of the always-resident prompt budget.
    lazy_load: bool = False
    requires_tools: list[str] = Field(default_factory=list)
    requires_env: list[str] = Field(default_factory=list)
    # Declared safety metadata (defaults are the safe values so legacy skills
    # with no risk frontmatter parse as low-risk / read-only).
    risk_level: str = (
        "low"  # low | medium | high — registered at declare-time, not runtime-inferred
    )
    read_only: bool = True  # whether the capability only reads state
    requires_auth: bool = (
        False  # whether the capability needs explicit user confirmation
    )


class SkillsLimits(BaseModel):
    """Guardrails bounding how many/which skills enter a prompt."""

    max_skills_in_prompt: int = 50
    # 40k, not 30k: the resident set (10 full skills + 6 lazy descriptions)
    # lands at ~30.0k. At a 30k cap it fits with one character to spare, so the
    # next skill or description edit would silently evict skills alphabetically.
    # Lazy bodies stay on disk; this only covers descriptions + XML scaffolding.
    max_prompt_chars: int = 40_000
    max_skill_file_bytes: int = 256_000


class SkillsConfig(BaseModel):
    """User-facing configuration for the skills subsystem."""

    extra_dirs: list[str] = Field(default_factory=list)
    enabled: dict[str, bool] = Field(default_factory=dict)
    limits: SkillsLimits = Field(default_factory=SkillsLimits)


class Skill(BaseModel):
    """A fully-loaded skill: metadata plus markdown body."""

    name: str
    description: str
    file_path: str
    base_dir: str
    source: SkillSource
    frontmatter: SkillFrontmatter
    content: str


class SkillSummary(BaseModel):
    """Lightweight skill descriptor for listings and snapshots."""

    name: str
    description: str
    source: SkillSource
    tags: list[str] = Field(default_factory=list)
    user_invocable: bool = True
    model_invocable: bool = True
    risk_level: str = "low"
    read_only: bool = True
    requires_auth: bool = False


class SkillSnapshot(BaseModel):
    """Point-in-time capture of the rendered skills prompt."""

    prompt: str
    skills: list[SkillSummary] = Field(default_factory=list)
    version: int = 1
