"""Behaviour locks for SkillLoader: frontmatter parsing, directory scan,
size limits, path-traversal guard, and load_single."""

from __future__ import annotations

from pathlib import Path

import pytest

from selene_space_agent.skills.loader import SkillLoader, _parse_frontmatter
from selene_space_agent.skills.types import SkillsLimits, SkillSource


def _drop_skill(root: Path, slug: str, body_or_doc: str, *, filename: str = "SKILL.md") -> Path:
    """Create folder *slug* under root with one file inside."""
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_text(body_or_doc, encoding="utf-8")
    return folder


class TestFrontmatterParsing:
    def test_wellformed_pair(self) -> None:
        raw = "---\nname: craft-skill\ndescription: Makes things\n---\nBody lives here."
        meta, body = _parse_frontmatter(raw)
        checks = (
            meta["name"] == "craft-skill",
            meta["description"] == "Makes things",
            body == "Body lives here.",
        )
        assert all(checks)

    def test_plain_markdown_passthrough(self) -> None:
        raw = "# Heading\n\nNo delimiters at all."
        meta, body = _parse_frontmatter(raw)
        checks = (meta == {}, body == raw)
        assert all(checks)

    def test_unclosed_delimiter_rejected(self) -> None:
        raw = "---\nname: torn\nNever closed."
        meta, body = _parse_frontmatter(raw)
        checks = (meta == {}, "torn" in body)
        assert all(checks)

    def test_bom_is_stripped(self) -> None:
        raw = "﻿---\nname: bom-carrying\n---\nPayload."
        meta, body = _parse_frontmatter(raw)
        checks = (meta["name"] == "bom-carrying", body == "Payload.")
        assert all(checks)

    @pytest.mark.parametrize(
        "raw",
        [
            "---\n: : : malformed yaml\n---\nBody.",  # broken YAML
            "---\n- bare sequence\n---\nBody.",  # parses to non-dict
        ],
    )
    def test_unparseable_yaml_yields_empty_meta(self, raw: str) -> None:
        meta, _body = _parse_frontmatter(raw)
        assert meta == {}

    def test_multiline_body_kept_intact(self) -> None:
        raw = "---\nname: lines\n---\nFirst row.\nSecond row.\nThird row."
        meta, body = _parse_frontmatter(raw)
        checks = (meta["name"] == "lines", "First row." in body, "Third row." in body)
        assert all(checks)

    def test_nested_lists_in_frontmatter(self) -> None:
        raw = (
            "---\nname: rich\n"
            "tags:\n  - one\n  - two\n"
            "requires_tools:\n  - market_ticker\n---\nBody."
        )
        meta, _body = _parse_frontmatter(raw)
        checks = (
            meta["name"] == "rich",
            meta["tags"] == ["one", "two"],
            meta["requires_tools"] == ["market_ticker"],
        )
        assert all(checks)


class TestScan:
    def test_single_valid_folder(self, tmp_path: Path) -> None:
        _drop_skill(tmp_path, "lone", "---\nname: lone\ndescription: Probe skill\n---\n## Steps\n1. Act")

        got = SkillLoader(tmp_path, SkillSource.BUNDLED).scan()

        checks = (
            len(got) == 1,
            got[0].name == "lone",
            got[0].description == "Probe skill",
            got[0].source == SkillSource.BUNDLED,
            "Act" in got[0].content,
        )
        assert all(checks)

    def test_multiple_folders_sorted(self, tmp_path: Path) -> None:
        for slug in ("yak", "ant", "owl"):
            _drop_skill(tmp_path, slug, f"---\nname: {slug}\ndescription: {slug} skill\n---\nDoc.")

        got = SkillLoader(tmp_path, SkillSource.USER).scan()

        names = [s.name for s in got]
        checks = (len(got) == 3, names == ["ant", "owl", "yak"])
        assert all(checks)

    def test_folder_without_skill_md_ignored(self, tmp_path: Path) -> None:
        _drop_skill(tmp_path, "real-one", "---\nname: real-one\n---\nBody.")
        _drop_skill(tmp_path, "impostor", "Just a readme.", filename="README.md")

        got = SkillLoader(tmp_path, SkillSource.BUNDLED).scan()
        checks = (len(got) == 1, got[0].name == "real-one")
        assert all(checks)

    def test_missing_root_gives_empty_list(self, tmp_path: Path) -> None:
        assert SkillLoader(tmp_path / "ghost", SkillSource.BUNDLED).scan() == []

    def test_empty_root_gives_empty_list(self, tmp_path: Path) -> None:
        assert SkillLoader(tmp_path, SkillSource.BUNDLED).scan() == []

    def test_root_level_file_not_a_skill(self, tmp_path: Path) -> None:
        (tmp_path / "SKILL.md").write_text("---\nname: top\n---\nBody.", encoding="utf-8")
        assert SkillLoader(tmp_path, SkillSource.BUNDLED).scan() == []


class TestNameFallback:
    def test_no_frontmatter_falls_back_to_dir_name(self, tmp_path: Path) -> None:
        _drop_skill(tmp_path, "folder-wins", "# Plain doc\nNo metadata here.")

        got = SkillLoader(tmp_path, SkillSource.BUNDLED).scan()
        checks = (len(got) == 1, got[0].name == "folder-wins")
        assert all(checks)

    def test_missing_name_key_falls_back_to_dir_name(self, tmp_path: Path) -> None:
        _drop_skill(tmp_path, "unnamed-dir", "---\ndescription: Has blurb only\n---\nBody.")

        got = SkillLoader(tmp_path, SkillSource.BUNDLED).scan()
        checks = (
            len(got) == 1,
            got[0].name == "unnamed-dir",
            got[0].description == "Has blurb only",
        )
        assert all(checks)


class TestFileSizeLimit:
    def test_small_file_passes(self, tmp_path: Path) -> None:
        _drop_skill(tmp_path, "tiny", "---\nname: tiny\n---\nShort.")

        limits = SkillsLimits(max_skill_file_bytes=10_000)
        got = SkillLoader(tmp_path, SkillSource.BUNDLED, limits).scan()
        assert len(got) == 1

    def test_huge_file_skipped(self, tmp_path: Path) -> None:
        padded = "---\nname: whale\n---\n" + "x" * 500
        _drop_skill(tmp_path, "whale", padded)

        limits = SkillsLimits(max_skill_file_bytes=100)
        got = SkillLoader(tmp_path, SkillSource.BUNDLED, limits).scan()
        assert len(got) == 0

    def test_boundary_exact_size_passes(self, tmp_path: Path) -> None:
        folder = _drop_skill(tmp_path, "edge", "---\nname: edge\n---\nBody.")
        measured = (folder / "SKILL.md").stat().st_size

        limits = SkillsLimits(max_skill_file_bytes=measured)
        got = SkillLoader(tmp_path, SkillSource.BUNDLED, limits).scan()
        assert len(got) == 1


class TestPathTraversalGuard:
    def test_symlink_escaping_root_blocked(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "SKILL.md").write_text("---\nname: smuggled\n---\nContraband.", encoding="utf-8")

        vault = tmp_path / "vault"
        vault.mkdir()

        try:
            (vault / "escape-hatch").symlink_to(outside)
        except OSError:
            pytest.skip("symlinks unavailable on this system")

        got = SkillLoader(vault, SkillSource.BUNDLED).scan()
        assert [s.name for s in got if s.name == "smuggled"] == []

    def test_regular_subdir_loads_normally(self, tmp_path: Path) -> None:
        _drop_skill(tmp_path, "lawful", "---\nname: lawful\n---\nFine content.")

        got = SkillLoader(tmp_path, SkillSource.BUNDLED).scan()
        checks = (len(got) == 1, got[0].name == "lawful")
        assert all(checks)


class TestBrokenYamlResilience:
    def test_malformed_yaml_does_not_crash_scan(self, tmp_path: Path) -> None:
        _drop_skill(tmp_path, "garbled", "---\n: : : junk\n---\nBody content.")

        got = SkillLoader(tmp_path, SkillSource.BUNDLED).scan()
        assert isinstance(got, list)

    def test_wrong_field_types_do_not_crash_scan(self, tmp_path: Path) -> None:
        # tags should be a list; loader must survive pydantic coercion outcomes
        _drop_skill(tmp_path, "mistyped", "---\nname: mistyped\ntags: not-a-list\n---\nBody.")

        got = SkillLoader(tmp_path, SkillSource.BUNDLED).scan()
        assert isinstance(got, list)


class TestLoadSingle:
    def test_valid_folder_returns_skill(self, tmp_path: Path) -> None:
        target = _drop_skill(tmp_path, "solo", "---\nname: solo\n---\nContent.")

        skill = SkillLoader(tmp_path, SkillSource.WORKSPACE).load_single(target)

        checks = (skill is not None, skill.name == "solo", skill.source == SkillSource.WORKSPACE)
        assert all(checks)

    def test_folder_without_file_returns_none(self, tmp_path: Path) -> None:
        hollow = tmp_path / "hollow"
        hollow.mkdir()

        assert SkillLoader(tmp_path, SkillSource.WORKSPACE).load_single(hollow) is None

    def test_absent_folder_returns_none(self, tmp_path: Path) -> None:
        assert SkillLoader(tmp_path, SkillSource.WORKSPACE).load_single(tmp_path / "nope") is None

    def test_body_whitespace_trimmed(self, tmp_path: Path) -> None:
        target = _drop_skill(tmp_path, "padded", "---\nname: padded\n---\n\n  Inner text  \n\n")

        skill = SkillLoader(tmp_path, SkillSource.BUNDLED).load_single(target)
        assert skill is not None
        assert skill.content == "Inner text"


class TestLoaderProperties:
    def test_root_dir_resolved(self, tmp_path: Path) -> None:
        assert SkillLoader(tmp_path, SkillSource.BUNDLED).root_dir == tmp_path.resolve()

    def test_source_roundtrips(self, tmp_path: Path) -> None:
        assert SkillLoader(tmp_path, SkillSource.USER).source == SkillSource.USER

    def test_default_limits_usable(self, tmp_path: Path) -> None:
        # defaults must be the SkillsLimits defaults; verify via crash-free scan
        assert SkillLoader(tmp_path, SkillSource.BUNDLED).scan() == []
