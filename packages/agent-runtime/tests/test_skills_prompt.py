"""Behaviour locks for skill prompt formatting: XML-tagged full format,
compact bullet format, and limits-driven truncation."""

from __future__ import annotations

from selene_space_agent.skills.prompt import (
    format_skills_compact,
    format_skills_for_prompt,
)
from selene_space_agent.skills.types import (
    Skill,
    SkillFrontmatter,
    SkillsLimits,
    SkillSource,
)


def _skill(slug: str, blurb: str = "", body: str = "Skill text.") -> Skill:
    return Skill(
        name=slug,
        description=blurb,
        file_path=f"/vault/{slug}/SKILL.md",
        base_dir=f"/vault/{slug}",
        source=SkillSource.BUNDLED,
        frontmatter=SkillFrontmatter(name=slug, description=blurb),
        content=body,
    )


class TestFullFormat:
    def test_no_skills_no_output(self) -> None:
        assert format_skills_for_prompt([]) == ""

    def test_single_skill_xml_skeleton(self) -> None:
        out = format_skills_for_prompt([_skill("aurora", "Aurora skill", "## Steps\n1. Wake lights")])

        for tag in ("<available_skills>", "</available_skills>", "<skill>", "</skill>", "<content>"):
            assert tag in out
        checks = (
            "<name>aurora</name>" in out,
            "<description>Aurora skill</description>" in out,
            "Wake lights" in out,
        )
        assert all(checks)

    def test_two_skills_two_blocks(self) -> None:
        out = format_skills_for_prompt([_skill("aurora", "Aurora"), _skill("borealis", "Borealis")])

        pair = (out.count("<skill>"), out.count("</skill>"))
        assert pair == (2, 2)
        checks = ("<name>aurora</name>" in out, "<name>borealis</name>" in out)
        assert all(checks)

    def test_blank_description_gets_placeholder(self) -> None:
        out = format_skills_for_prompt([_skill("wordless", "", "Content.")])
        assert "<description>No description</description>" in out

    def test_header_present(self) -> None:
        out = format_skills_for_prompt([_skill("titled", "Titled")])
        assert "## Available Skills" in out

    def test_content_lines_survive(self) -> None:
        out = format_skills_for_prompt([_skill("twoliner", "Probe", "Row one\nRow two")])
        matching = [ln for ln in out.split("\n") if "Row one" in ln or "Row two" in ln]
        assert len(matching) == 2


class TestCompactFormat:
    def test_no_skills_no_output(self) -> None:
        assert format_skills_compact([]) == ""

    def test_single_bullet_line(self) -> None:
        out = format_skills_compact([_skill("aurora", "Aurora description")])
        checks = ("## Available Skills" in out, "- **aurora**: Aurora description" in out)
        assert all(checks)

    def test_blank_description_gets_placeholder(self) -> None:
        out = format_skills_compact([_skill("wordless", "")])
        assert "- **wordless**: No description" in out

    def test_two_bullets(self) -> None:
        out = format_skills_compact([_skill("aurora", "Aurora"), _skill("borealis", "Borealis")])
        checks = ("- **aurora**: Aurora" in out, "- **borealis**: Borealis" in out)
        assert all(checks)

    def test_xml_tags_absent(self) -> None:
        out = format_skills_compact([_skill("plain", "Plain skill")])
        checks = ("<skill>" not in out, "<available_skills>" not in out)
        assert all(checks)


class TestLimits:
    def test_cap_on_skill_count(self) -> None:
        many = [_skill(f"entry-{i:02d}", f"Entry {i}") for i in range(20)]
        caps = SkillsLimits(max_skills_in_prompt=5, max_prompt_chars=100_000)

        out = format_skills_for_prompt(many, caps)
        assert out.count("<skill>") == 5

    def test_char_budget_enforced(self) -> None:
        fat_body = "A" * 5000
        many = [_skill(f"hefty-{i}", f"Desc {i}", fat_body) for i in range(10)]
        tight = SkillsLimits(max_skills_in_prompt=50, max_prompt_chars=500)

        assert len(format_skills_for_prompt(many, tight)) <= 500

    def test_partial_fit_before_compact_fallback(self) -> None:
        mid_body = "B" * 200
        many = [_skill(f"s{i}", f"Desc {i}", mid_body) for i in range(10)]
        # limit fits some but not all in full XML form
        partial = SkillsLimits(max_skills_in_prompt=50, max_prompt_chars=800)

        assert len(format_skills_for_prompt(many, partial)) <= 800

    def test_single_huge_skill_stays_within_budget(self) -> None:
        giant = "C" * 10_000
        one = [_skill("colossus", "Colossal skill", giant)]
        tight = SkillsLimits(max_skills_in_prompt=50, max_prompt_chars=500)

        # compact/truncated output must respect the budget
        assert len(format_skills_for_prompt(one, tight)) <= 500

    def test_defaults_leave_small_set_untouched(self) -> None:
        trio = [_skill(f"s{i}", f"Desc {i}", "Brief.") for i in range(3)]

        out = format_skills_for_prompt(trio)
        assert out.count("<skill>") == 3

    def test_zero_cap_yields_no_blocks(self) -> None:
        lone = [_skill("a", "A")]
        zeroed = SkillsLimits(max_skills_in_prompt=0, max_prompt_chars=100_000)

        out = format_skills_for_prompt(lone, zeroed)
        # truncated list is empty; no <skill> block may appear
        assert "<skill>" not in out


class TestEdgeCases:
    def test_multiline_body_rendered(self) -> None:
        steps = "Phase 1: Prepare\nPhase 2: Execute\nPhase 3: Wrap"
        out = format_skills_for_prompt([_skill("phased", "Phased plan", steps)])

        checks = ("Phase 1" in out, "Phase 3" in out)
        assert all(checks)

    def test_empty_body_still_named(self) -> None:
        out = format_skills_for_prompt([_skill("hollow", "Has blurb", "")])
        assert "<name>hollow</name>" in out

    def test_angle_brackets_in_name_not_escaped(self) -> None:
        out = format_skills_for_prompt([_skill("odd<>&name", "Special chars")])
        assert "odd<>&name" in out
