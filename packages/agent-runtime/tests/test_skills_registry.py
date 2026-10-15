"""Behaviour locks for SkillRegistry: source precedence, lazy load,
eligibility filtering, refresh, and enable/disable config."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from selene_space_agent.skills.registry import SkillRegistry
from selene_space_agent.skills.types import SkillsConfig, SkillSource


def _seed(root: Path, slug: str, blurb: str = "") -> Path:
    """Drop one SKILL.md folder under *root*."""
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    blurb_line = f"description: {blurb}\n" if blurb else ""
    (folder / "SKILL.md").write_text(
        f"---\nname: {slug}\n{blurb_line}---\nBody of {slug}.",
        encoding="utf-8",
    )
    return folder


def _seed_with_tools(root: Path, slug: str, needed: list[str]) -> Path:
    """SKILL.md folder declaring requires_tools frontmatter."""
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    listed = "\n".join(f"  - {t}" for t in needed)
    (folder / "SKILL.md").write_text(
        f"---\nname: {slug}\nrequires_tools:\n{listed}\n---\nBody.",
        encoding="utf-8",
    )
    return folder


def _registry(tmp_path: Path, **kw: Any) -> SkillRegistry:
    """Registry factory; accepts bundled/user/workspace/config kwargs."""
    kw.setdefault("user_dir", tmp_path / "vacant-user")
    return SkillRegistry(**kw)


class TestLoadingBasics:
    def test_bundled_folder_populates(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "aurora", "Aurora skill")
        _seed(pack, "borealis", "Borealis skill")

        reg = _registry(tmp_path, bundled_dir=pack)
        reg.load()

        assert reg.is_loaded
        assert len(reg) == 2
        assert all(name in reg for name in ("aurora", "borealis"))

    def test_get_triggers_lazy_load(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "dormant")

        reg = _registry(tmp_path, bundled_dir=pack)
        assert not reg.is_loaded

        found = reg.get("dormant")
        assert reg.is_loaded
        assert found is not None
        assert found.name == "dormant"

    def test_get_missing_yields_none(self, tmp_path: Path) -> None:
        reg = _registry(tmp_path, bundled_dir=tmp_path / "void")
        assert reg.get("never-seeded") is None

    def test_no_sources_means_empty(self, tmp_path: Path) -> None:
        reg = _registry(
            tmp_path,
            bundled_dir=tmp_path / "b",
            workspace_dir=tmp_path / "w",
        )
        reg.load()
        checks = (len(reg) == 0, reg.list_skills() == [])
        assert all(checks)


class TestSourcePrecedence:
    @pytest.mark.parametrize(
        ("winner_source", "winner_blurb"),
        [
            (SkillSource.WORKSPACE, "Workspace edition"),
            (SkillSource.USER, "User edition"),
        ],
    )
    def test_higher_layer_shadows_bundled(self, tmp_path: Path, winner_source, winner_blurb) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "shared-skill", "Bundled edition")

        top = tmp_path / str(winner_source.value)  # 'workspace' or 'user'
        top.mkdir()
        _seed(top, "shared-skill", winner_blurb)

        kwargs: dict[str, Any] = {"bundled_dir": pack}
        if winner_source is SkillSource.WORKSPACE:
            kwargs["workspace_dir"] = top
        else:
            kwargs["user_dir"] = top
        reg = _registry(tmp_path, **kwargs)
        reg.load()

        got = reg.get("shared-skill")
        assert got is not None
        assert got.source == winner_source
        assert got.description == winner_blurb

    def test_workspace_beats_user(self, tmp_path: Path) -> None:
        udir = tmp_path / "u"
        udir.mkdir()
        _seed(udir, "crown", "User edition")

        wdir = tmp_path / "w"
        wdir.mkdir()
        _seed(wdir, "crown", "Workspace edition")

        reg = _registry(tmp_path, bundled_dir=tmp_path / "void", user_dir=udir, workspace_dir=wdir)
        reg.load()

        got = reg.get("crown")
        assert got is not None
        assert got.source == SkillSource.WORKSPACE

    def test_extra_dir_ranks_below_bundled(self, tmp_path: Path) -> None:
        annex = tmp_path / "annex"
        annex.mkdir()
        _seed(annex, "shadowed-one", "Extra edition")

        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "shadowed-one", "Bundled edition")

        cfg = SkillsConfig(extra_dirs=[str(annex)])
        reg = _registry(tmp_path, config=cfg, bundled_dir=pack)
        reg.load()

        got = reg.get("shadowed-one")
        assert got is not None
        assert got.source == SkillSource.BUNDLED

    def test_disjoint_sources_merge(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "pack-exclusive")

        udir = tmp_path / "u"
        udir.mkdir()
        _seed(udir, "user-exclusive")

        wdir = tmp_path / "w"
        wdir.mkdir()
        _seed(wdir, "ws-exclusive")

        reg = _registry(tmp_path, bundled_dir=pack, user_dir=udir, workspace_dir=wdir)
        reg.load()

        trio = ("pack-exclusive", "user-exclusive", "ws-exclusive")
        assert all(name in reg for name in trio)
        assert len(reg) == 3


class TestListing:
    def test_list_skills_is_sorted(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        for slug in ("yak", "ibis", "koala"):
            _seed(pack, slug)

        reg = _registry(tmp_path, bundled_dir=pack)
        ordered = [s.name for s in reg.list_skills()]
        assert ordered == ["ibis", "koala", "yak"]

    def test_summaries_carry_metadata(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "digest-me", "Digest target skill")

        reg = _registry(tmp_path, bundled_dir=pack)
        rows = reg.list_summaries()

        checks = (
            len(rows) == 1,
            rows[0].name == "digest-me",
            rows[0].description == "Digest target skill",
            rows[0].source == SkillSource.BUNDLED,
        )
        assert all(checks)

    def test_skill_names_sorted_pair(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "z-skill")
        _seed(pack, "a-skill")

        reg = _registry(tmp_path, bundled_dir=pack)
        pair = tuple(reg.skill_names())
        assert pair == ("a-skill", "z-skill")


class TestEligibility:
    def test_no_filter_keeps_everything(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed_with_tools(pack, "hungry", ["market_ticker"])
        _seed(pack, "self-served")

        reg = _registry(tmp_path, bundled_dir=pack)
        assert len(reg.list_eligible(available_tools=None)) == 2

    def test_filter_drops_unmet_requirements(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed_with_tools(pack, "ticker-hungry", ["market_ticker"])
        _seed_with_tools(pack, "backtest-hungry", ["backtest_run"])
        _seed(pack, "requirement-free")

        reg = _registry(tmp_path, bundled_dir=pack)
        names = {s.name for s in reg.list_eligible(available_tools=["market_ticker"])}

        checks = (
            "ticker-hungry" in names,
            "requirement-free" in names,
            "backtest-hungry" not in names,
        )
        assert all(checks)

    def test_multi_tool_skill_needs_full_set(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed_with_tools(pack, "combo", ["tool_a", "tool_b"])

        reg = _registry(tmp_path, bundled_dir=pack)

        partial = reg.list_eligible(available_tools=["tool_a"])
        assert partial == []

        complete = reg.list_eligible(available_tools=["tool_a", "tool_b"])
        (only,) = complete
        assert only.name == "combo"

    def test_zero_available_tools_blocks_hungry_only(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed_with_tools(pack, "needs-something", ["market_ticker"])
        _seed(pack, "needs-nothing")

        reg = _registry(tmp_path, bundled_dir=pack)
        survivors = reg.list_eligible(available_tools=[])

        (only,) = survivors
        assert only.name == "needs-nothing"


class TestRefresh:
    def test_refresh_picks_up_additions(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "firstborn")

        reg = _registry(tmp_path, bundled_dir=pack)
        reg.load()
        assert len(reg) == 1

        _seed(pack, "latecomer")
        reg.refresh()

        checks = (len(reg) == 2, "latecomer" in reg)
        assert all(checks)

    def test_refresh_picks_up_removals(self, tmp_path: Path) -> None:
        import shutil

        pack = tmp_path / "pack"
        pack.mkdir()
        doomed = _seed(pack, "evicted")

        reg = _registry(tmp_path, bundled_dir=pack)
        reg.load()
        assert "evicted" in reg

        shutil.rmtree(doomed)
        reg.refresh()

        assert "evicted" not in reg


class TestEnabledConfig:
    def test_disabled_entry_excluded(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "kept")
        _seed(pack, "switched-off")

        cfg = SkillsConfig(enabled={"switched-off": False})
        reg = _registry(tmp_path, config=cfg, bundled_dir=pack)
        reg.load()

        checks = ("kept" in reg, "switched-off" not in reg, len(reg) == 1)
        assert all(checks)

    def test_explicit_true_stays_loaded(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "flagged-on")

        cfg = SkillsConfig(enabled={"flagged-on": True})
        reg = _registry(tmp_path, config=cfg, bundled_dir=pack)
        reg.load()

        assert "flagged-on" in reg

    def test_unlisted_defaults_to_enabled(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "silent-entry")

        cfg = SkillsConfig(enabled={"some-other": False})
        reg = _registry(tmp_path, config=cfg, bundled_dir=pack)
        reg.load()

        assert "silent-entry" in reg


class TestDunders:
    def test_contains_membership(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "present")

        reg = _registry(tmp_path, bundled_dir=pack)
        checks = ("present" in reg, "absent" not in reg)
        assert all(checks)

    def test_len_counts_entries(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "uno")
        _seed(pack, "dos")

        reg = _registry(tmp_path, bundled_dir=pack)
        assert len(reg) == 2
