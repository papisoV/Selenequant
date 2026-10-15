"""Locks for the bundled research methodology skills.

These are content-bearing skills (not fixtures): the tests point the real
SkillRegistry at the real bundled_skills directory so a typo'd frontmatter,
renamed folder, or lost SKILL.md fails here instead of silently emptying
the agent prompt.
"""

from __future__ import annotations

import re
from pathlib import Path

from selene_space_agent.skills.config import BUNDLED_SKILLS_DIR
from selene_space_agent.skills.prompt import format_skills_for_prompt
from selene_space_agent.skills.registry import SkillRegistry
from selene_space_agent.skills.types import SkillSource, SkillsConfig

_EXPECTED = ("research-conventions", "trade-pitch")

# Bulk methodology absorbed from LangAlpha: resident in the prompt only as a
# one-line description; the body is fetched on demand via read_skill.
_LAZY_EXPECTED = (
    "dcf-model",
    "earnings-analysis",
    "earnings-preview",
    "initiating-coverage",
    "morning-note",
    "sector-overview",
)

_BANNED = ("sec ", "edgar", "10-k", "nasdaq", "nyse")
_BANNED_RE = re.compile(r"\bsec\b|edgar|10-[kq]|nasdaq|nyse", re.IGNORECASE)


def _registry() -> SkillRegistry:
    reg = SkillRegistry(bundled_dir=BUNDLED_SKILLS_DIR)
    reg.load()
    return reg


def _production_registry() -> SkillRegistry:
    """Reproduce the main.py:2650-2658 wiring exactly.

    Repo-root ``skills/`` is BUNDLED, the package ``bundled_skills/`` is EXTRA,
    and the user dir is USER — the three-source layout the running app uses.
    """
    repo_root = Path(__file__).resolve().parents[3]
    return SkillRegistry(
        config=SkillsConfig(extra_dirs=[str(BUNDLED_SKILLS_DIR)]),
        bundled_dir=repo_root / "skills",
        user_dir=Path.home() / ".selenespace" / "skills",
    )


class TestBundledSkills:
    def test_dir_constant_points_at_existing_tree(self) -> None:
        assert BUNDLED_SKILLS_DIR.is_dir()
        for slug in _EXPECTED:
            assert (BUNDLED_SKILLS_DIR / slug / "SKILL.md").is_file()

    def test_both_methodology_skills_load(self) -> None:
        names = {s.name for s in _registry().list_skills()}
        assert set(_EXPECTED).issubset(names)

    def test_source_is_bundled_and_content_nonempty(self) -> None:
        reg = _registry()
        for slug in _EXPECTED:
            skill = reg.get(slug)
            assert skill is not None
            assert skill.source is SkillSource.BUNDLED
            assert len(skill.content) > 200

    def test_methodology_content_is_market_agnostic(self) -> None:
        # The absorbed methodology must not hardcode one market's tooling
        # (SEC/EDGAR/10-K were external-research-project equity-specific; selene is
        # multi-market). Asserting absence keeps future edits honest.
        reg = _registry()
        for slug in _EXPECTED:
            skill = reg.get(slug)
            assert skill is not None
            body = (skill.content or "").lower()
            for banned in ("sec ", "edgar", "10-k", "nasdaq", "nyse"):
                assert banned not in body, f"{slug} regressed: contains {banned!r}"

    def test_format_includes_both_in_prompt(self) -> None:
        reg = _registry()
        text = format_skills_for_prompt(reg.list_skills())
        for slug in _EXPECTED:
            assert f"<name>{slug}</name>" in text
        # Key discipline anchors survive rendering
        assert "decision-grade" in text
        assert "falsifiable" in text.lower()

    def test_registry_via_default_config_still_empty(self) -> None:
        # SkillsConfig() alone must stay empty (no bundled dir): bundled
        # skills enter only via explicit bundled_dir, as wired in main.py.
        reg = SkillRegistry(
            config=__import__(
                "selene_space_agent.skills.types", fromlist=["SkillsConfig"]
            ).SkillsConfig()
        )
        assert len(reg.list_skills()) == 0


class TestLazyResearchSkills:
    """The 6 absorbed external-research-project research skills load lazily and stay neutral."""

    def test_all_lazy_skills_present(self) -> None:
        reg = _registry()
        names = {s.name for s in reg.list_skills()}
        for slug in _LAZY_EXPECTED:
            assert slug in names, f"missing bundled skill: {slug}"

    def test_all_lazy_skills_marked_lazy(self) -> None:
        reg = _registry()
        for slug in _LAZY_EXPECTED:
            skill = reg.get(slug)
            assert skill is not None, slug
            assert skill.frontmatter.lazy_load is True, (
                f"{slug} must set lazy_load: true — otherwise its body enters "
                "the resident prompt and evicts other skills"
            )

    def test_lazy_bodies_do_not_enter_the_prompt(self) -> None:
        """Only descriptions are resident; the methodology stays on disk."""
        reg = _registry()
        text = format_skills_for_prompt(reg.list_skills())
        for slug in _LAZY_EXPECTED:
            skill = reg.get(slug)
            assert skill is not None
            assert f"<name>{slug}</name>" in text
            # A lazy skill's own body must NOT be inlined into the prompt.
            probe = (skill.content or "")[:80].strip()
            if probe:
                assert probe not in text, f"{slug} body leaked into the prompt"

    def test_every_skill_directory_is_market_agnostic(self) -> None:
        """No US-specific tooling anywhere in the skill tree.

        Scans every file (references/, assets/, TROUBLESHOOTING.md included),
        not just SKILL.md — a dangling term in a referenced file is just as
        much a contract violation as one in the body.
        """
        for slug in (*_EXPECTED, *_LAZY_EXPECTED):
            skill_dir = BUNDLED_SKILLS_DIR / slug
            for path in skill_dir.rglob("*"):
                if not path.is_file():
                    continue
                text = path.read_text(encoding="utf-8", errors="ignore")
                hit = _BANNED_RE.search(text)
                assert hit is None, (
                    f"{path.relative_to(BUNDLED_SKILLS_DIR)} contains "
                    f"{hit.group(0)!r}: absorbed methodology must not hardcode "
                    "one market's tooling"
                )

    def test_no_dangling_internal_references(self) -> None:
        """No skill may point at the foreign `.agents/skills/` layout."""
        for slug in (*_EXPECTED, *_LAZY_EXPECTED):
            skill_dir = BUNDLED_SKILLS_DIR / slug
            for path in skill_dir.rglob("*.md"):
                text = path.read_text(encoding="utf-8", errors="ignore")
                assert ".agents/skills" not in text, (
                    f"{path.relative_to(BUNDLED_SKILLS_DIR)} still references "
                    "the foreign .agents/skills/ layout"
                )

    def test_read_skill_name_refs_resolve(self) -> None:
        """Every read_skill(...) must name a loaded skill, and any ref a real file."""
        reg = _registry()
        # both forms: whole-skill read, and skill+ref read
        whole = re.compile(r'read_skill\(\s*name=["\']([a-z0-9-]+)["\']\s*\)')
        with_ref = re.compile(
            r'read_skill\(\s*name=["\']([a-z0-9-]+)["\']\s*,\s*ref=["\']([^"\']+)["\']\s*\)'
        )
        whole_count = 0
        ref_count = 0
        for slug in (*_EXPECTED, *_LAZY_EXPECTED):
            for path in (BUNDLED_SKILLS_DIR / slug).rglob("*.md"):
                text = path.read_text(encoding="utf-8", errors="ignore")
                for target in whole.findall(text):
                    assert reg.get(target) is not None, (
                        f"{path.name} calls read_skill(name={target!r}) but no "
                        "such skill is loaded"
                    )
                    whole_count += 1
                for target, ref in with_ref.findall(text):
                    target_skill = reg.get(target)
                    assert target_skill is not None, (
                        f"{path.name} calls read_skill(name={target!r}) but no "
                        "such skill is loaded"
                    )
                    resolved = Path(target_skill.base_dir) / ref
                    assert resolved.is_file(), (
                        f"{path.name} calls read_skill({target!r}, {ref!r}) but "
                        f"{resolved} does not exist"
                    )
                    ref_count += 1
        assert whole_count + ref_count > 0, "expected cross-skill read_skill() refs"


class TestPromptBudget:
    """The whole skill set must fit the prompt — no silent eviction."""

    def test_no_skill_is_silently_evicted(self) -> None:
        """Every loaded skill must appear in the rendered prompt.

        The renderer drops whole skills (alphabetically) to fit
        ``max_prompt_chars``. Before lazy loading, adding the 6 research
        skills evicted all but one skill from the prompt — silently, since
        main.py logs the *loaded* count, not the injected one. This test is
        the regression lock for that.
        """
        reg = _production_registry()
        reg.load()
        skills = reg.list_skills()
        assert len(skills) >= 16, f"expected repo+bundled skills, got {len(skills)}"

        text = format_skills_for_prompt(skills)
        present = set(re.findall(r"<name>([^<]+)</name>", text))
        missing = {s.name for s in skills} - present
        assert not missing, (
            f"skills evicted from the prompt: {sorted(missing)} — raise "
            "max_prompt_chars or mark them lazy_load"
        )
