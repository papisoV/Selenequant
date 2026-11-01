"""Behaviour locks for skill safety metadata: frontmatter defaults,
filter_by_risk, and top_n_for_prompt ranking."""

from __future__ import annotations

from pathlib import Path
from typing import Any


from selene_space_agent.skills.registry import SkillRegistry
from selene_space_agent.skills.types import SkillFrontmatter


def _seed(root: Path, slug: str, extra: str = "", blurb: str = "") -> Path:
    """Drop one SKILL.md folder under *root* with optional frontmatter extras."""
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    blurb_line = f"description: {blurb}\n" if blurb else ""
    (folder / "SKILL.md").write_text(
        f"---\nname: {slug}\n{blurb_line}{extra}---\nBody of {slug}.",
        encoding="utf-8",
    )
    return folder


def _registry(tmp_path: Path, **kw: Any) -> SkillRegistry:
    kw.setdefault("user_dir", tmp_path / "vacant-user")
    return SkillRegistry(**kw)


# ---------------------------------------------------------------------------
# SkillFrontmatter defaults (acceptance 1)
# ---------------------------------------------------------------------------


class TestRiskFrontmatterDefaults:
    def test_legacy_frontmatter_defaults_safe(self) -> None:
        """Old skills with no risk fields parse to low / read-only / no-auth."""
        fm = SkillFrontmatter(name="legacy")
        checks = (
            fm.risk_level == "low",
            fm.read_only is True,
            fm.requires_auth is False,
        )
        assert all(checks)

    def test_explicit_risk_fields_parsed(self) -> None:
        fm = SkillFrontmatter(
            name="danger-zone",
            risk_level="high",
            read_only=False,
            requires_auth=True,
        )
        checks = (
            fm.risk_level == "high",
            fm.read_only is False,
            fm.requires_auth is True,
        )
        assert all(checks)


# ---------------------------------------------------------------------------
# filter_by_risk (acceptance 2)
# ---------------------------------------------------------------------------


class TestFilterByRisk:
    def test_high_skill_filtered_by_medium(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "safe-one")
        _seed(pack, "risky-one", "risk_level: high\n")

        reg = _registry(tmp_path, bundled_dir=pack)
        reg.load()

        names = {s.name for s in reg.filter_by_risk(max_risk="medium")}
        checks = ("safe-one" in names, "risky-one" not in names)
        assert all(checks)

    def test_low_skill_survives_medium_filter(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "quiet")
        _seed(pack, "moderate", "risk_level: medium\n")

        reg = _registry(tmp_path, bundled_dir=pack)
        reg.load()

        names = {s.name for s in reg.filter_by_risk(max_risk="medium")}
        assert {"quiet", "moderate"} == names


# ---------------------------------------------------------------------------
# top_n_for_prompt (acceptance 3)
# ---------------------------------------------------------------------------


def _tagged_seed(root: Path, slug: str, tags: list[str], blurb: str = "") -> Path:
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    tags_str = "[" + ", ".join(tags) + "]"
    blurb_line = f"description: {blurb}\n" if blurb else ""
    (folder / "SKILL.md").write_text(
        f"---\nname: {slug}\n{blurb_line}tags: {tags_str}\n---\nBody.",
        encoding="utf-8",
    )
    return folder


class TestTopNForPrompt:
    def test_query_hits_rank_top_n(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _tagged_seed(pack, "backtest-pro", ["backtest", "strategy"], "Run backtests")
        _tagged_seed(pack, "kline-view", ["kline", "chart"], "View klines")
        _tagged_seed(pack, "tg-signals", ["signal", "telegram"], "Telegram signals")

        reg = _registry(tmp_path, bundled_dir=pack)
        reg.load()

        top = reg.top_n_for_prompt("backtest strategy kline", n=6)
        names = [s.name for s in top]
        assert names[0] == "backtest-pro"
        assert "kline-view" in names
        assert "tg-signals" not in names

    def test_no_match_returns_empty(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _tagged_seed(pack, "kline-view", ["kline", "chart"], "View klines")

        reg = _registry(tmp_path, bundled_dir=pack)
        reg.load()

        assert reg.top_n_for_prompt("zzz-totally-unrelated") == []

    def test_limit_truncates(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        for i in range(5):
            _tagged_seed(pack, f"skill-{i}", ["research"], f"Research skill {i}")

        reg = _registry(tmp_path, bundled_dir=pack)
        reg.load()

        top = reg.top_n_for_prompt("research", n=2)
        assert len(top) == 2


# ---------------------------------------------------------------------------
# list_summaries carries safety metadata (acceptance 1 auxiliary)
# ---------------------------------------------------------------------------


class TestSummariesCarryRisk:
    def test_summary_exposes_risk_fields(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "audited", "risk_level: medium\nread_only: false\n")

        reg = _registry(tmp_path, bundled_dir=pack)
        rows = reg.list_summaries()

        (row,) = rows
        checks = (
            row.risk_level == "medium",
            row.read_only is False,
            row.requires_auth is False,
        )
        assert all(checks)
