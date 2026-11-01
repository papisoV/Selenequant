"""SkillRegistry — multi-source loading with priority merging and filtering."""

from __future__ import annotations

import math
import re
from pathlib import Path

from selene_space_agent.skills.loader import SkillLoader
from selene_space_agent.skills.types import (
    Skill,
    SkillsConfig,
    SkillSource,
    SkillSummary,
)

_TOKEN_RE = re.compile(r"[a-z0-9_]+")

# Common research-intent verbs → skills whose id/description mention the verb
# get a small relevance boost. Kept intentionally tiny (no embedding model).
_INTENT_BOOST: dict[str, tuple[str, ...]] = {
    "backtest": ("backtest", "strategy"),
    "回测": ("backtest", "strategy"),
    "tune": ("tune", "parameter", "optimize"),
    "调参": ("tune", "parameter", "optimize"),
    "analyze": ("analyze", "analysis"),
    "分析": ("analyze", "analysis"),
    "signal": ("signal", "alert"),
    "risk": ("risk", "guard"),
    "kline": ("kline", "chart"),
    "k线": ("kline", "chart"),
}


def _tokenize(text: str) -> list[str]:
    """Split text into lowercase alphanumeric tokens (unicode-safe)."""
    return _TOKEN_RE.findall(text.lower())


def _weight_factor(weights: "dict[str, float] | None", name: str) -> float:
    """The performance factor for *name*, or 1.0 when there is no usable one.

    1.0 is the identity of the ranking multiplication, so an absent map, an
    absent name, or a value that is not a finite positive number all leave the
    skill's score untouched. Booleans are rejected: ``True`` is an ``int`` in
    Python and must not be read as the factor 1.
    """
    if not weights:
        return 1.0
    factor = weights.get(name)
    if isinstance(factor, bool) or not isinstance(factor, (int, float)):
        return 1.0
    value = float(factor)
    if not math.isfinite(value) or value <= 0:
        return 1.0
    return value


class SkillRegistry:
    """Aggregate skills from extra/bundled/user/workspace directories.

    Name conflicts resolve by source priority (workspace > user > bundled >
    extra). The ``enabled`` map in the config can veto individual skills.
    """

    def __init__(
        self,
        config: SkillsConfig | None = None,
        *,
        bundled_dir: Path | None = None,
        user_dir: Path | None = None,
        workspace_dir: Path | None = None,
    ) -> None:
        self._config = config or SkillsConfig()

        self._loaders: list[tuple[SkillSource, Path]] = []
        for extra in self._config.extra_dirs:
            self._loaders.append((SkillSource.EXTRA, Path(extra)))
        if bundled_dir is not None:
            self._loaders.append((SkillSource.BUNDLED, Path(bundled_dir)))
        if user_dir is not None:
            self._loaders.append((SkillSource.USER, Path(user_dir)))
        if workspace_dir is not None:
            self._loaders.append((SkillSource.WORKSPACE, Path(workspace_dir)))

        self._skills: dict[str, Skill] = {}
        #: Every scanned skill, enabled or not. ``_skills`` is the enabled view;
        #: this one exists so the management API can still see a switched-off
        #: skill and offer to switch it back on.
        self._all_skills: dict[str, Skill] = {}
        self._loaded = False

    # ------------------------------------------------------------------
    # Loading
    # ------------------------------------------------------------------

    def load(self) -> None:
        merged: dict[str, Skill] = {}
        for source, root in self._loaders:
            loader = SkillLoader(root, source, self._config.limits)
            for skill in loader.scan():
                merged[skill.name] = skill
        self._all_skills = merged
        self._skills = {
            name: skill for name, skill in merged.items() if self._is_enabled(name)
        }
        self._loaded = True

    def refresh(self) -> None:
        self.load()

    def _is_enabled(self, name: str) -> bool:
        return self._config.enabled.get(name, True)

    @property
    def is_loaded(self) -> bool:
        return self._loaded

    # ------------------------------------------------------------------
    # Lookup / listing
    # ------------------------------------------------------------------

    def get(self, name: str) -> Skill | None:
        if not self._loaded:
            self.load()
        return self._skills.get(name)

    def get_any(self, name: str) -> Skill | None:
        """Look up a skill whether or not it is currently enabled."""
        if not self._loaded:
            self.load()
        return self._all_skills.get(name)

    def list_skills(self) -> list[Skill]:
        if not self._loaded:
            self.load()
        return sorted(self._skills.values(), key=lambda s: s.name)

    def list_all_skills(self) -> list[Skill]:
        """Every scanned skill, disabled ones included.

        For management surfaces only — prompt injection and tool dispatch keep
        using :meth:`list_skills`, so a disabled skill stays invisible to the
        model while remaining reachable from the settings page.
        """
        if not self._loaded:
            self.load()
        return sorted(self._all_skills.values(), key=lambda s: s.name)

    def list_summaries(self) -> list[SkillSummary]:
        summaries = [
            SkillSummary(
                name=s.name,
                description=s.description,
                source=s.source,
                tags=list(s.frontmatter.tags),
                user_invocable=s.frontmatter.user_invocable,
                model_invocable=s.frontmatter.model_invocable,
                risk_level=s.frontmatter.risk_level,
                read_only=s.frontmatter.read_only,
                requires_auth=s.frontmatter.requires_auth,
            )
            for s in self.list_skills()
        ]
        return sorted(summaries, key=lambda x: x.name)

    def skill_names(self) -> list[str]:
        return [s.name for s in self.list_skills()]

    def list_eligible(self, available_tools: list[str] | None) -> list[Skill]:
        skills = self.list_skills()
        if available_tools is None:
            return skills
        tool_set = set(available_tools)
        return [
            s
            for s in skills
            if not s.frontmatter.requires_tools
            or set(s.frontmatter.requires_tools).issubset(tool_set)
        ]

    def filter_by_risk(self, max_risk: str) -> list[Skill]:
        """Return skills whose declared risk_level is at or below *max_risk*.

        Ordering is low < medium < high. A skill declaring an unknown level is
        treated as highest risk so a typo in frontmatter fails closed rather
        than opening a capability beyond the caller's authorization.
        """
        rank = {"low": 0, "medium": 1, "high": 2}
        max_rank = rank.get(max_risk, 2)
        return [
            s
            for s in self.list_skills()
            if rank.get(s.frontmatter.risk_level, 3) <= max_rank
        ]

    def top_n_for_prompt(
        self,
        query: str,
        n: int = 6,
        *,
        weights: "dict[str, float] | None" = None,
    ) -> list[Skill]:
        """Rank skills by weighted keyword relevance and return the top *n*.

        Scoring (QD match_skills idea, no embeddings): +20 when the query hits
        the skill id or its category, +10 per matching keyword, plus a small
        intent boost for common research verbs. Skills that score 0 are never
        injected, so an unrelated query yields an empty list instead of noise.

        ``weights`` is an optional precomputed ``skill name -> factor`` map (see
        :func:`selene_space_backtest.skill_weight.compute_skill_weight_factors`)
        that nudges skills a Skill has historically been right about. It is
        injected rather than computed here so this class stays synchronous and
        pure — it does no I/O and holds no database handle.

        Two deliberate properties of the weight's effect:

        - **It modulates relevance, it never manufactures it.** A skill whose raw
          score is 0 stays excluded no matter how large its factor: a good
          track record cannot make an unrelated skill relevant to this query.
        - **A missing or unusable factor is neutral (1.0).** Names absent from
          the map, and non-finite / non-positive values, leave the score exactly
          as it was.
        """
        if not query:
            return []
        tokens = {t.lower() for t in _tokenize(query)}
        if not tokens:
            return []

        ranked: list[tuple[float, Skill]] = []
        for skill in self.list_skills():
            score = 0
            if skill.name.lower() in tokens:
                score += 20
            if skill.description and tokens & set(_tokenize(skill.description)):
                score += 20
            if tokens & {k.lower() for k in skill.frontmatter.tags}:
                score += 10
            for token in tokens:
                for intent in _INTENT_BOOST.get(token, ()):
                    if intent in skill.name or intent in skill.description.lower():
                        score += 5
            if score > 0:
                ranked.append((score * _weight_factor(weights, skill.name), skill))

        ranked.sort(key=lambda pair: (-pair[0], pair[1].name))
        return [skill for _, skill in ranked[: max(0, n)]]

    # ------------------------------------------------------------------
    # Dunders
    # ------------------------------------------------------------------

    def __contains__(self, name: object) -> bool:
        if not isinstance(name, str):
            return False
        return self.get(name) is not None

    def __len__(self) -> int:
        return len(self.list_skills())
