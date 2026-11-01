"""per-skill 权重因子对 ``top_n_for_prompt`` 排序的作用(📋3)。

锁三条契约:
1. 无权重(或权重不可用)→ 排序与从前**逐位相同**(纯新增,不回归)。
2. 权重**调制**相关性,绝不**制造**相关性 —— 0 分的 skill 无论因子多大都不注入。
3. 因子只在"都命中查询"的 skill 之间改变次序。
"""

from __future__ import annotations

from pathlib import Path

from selene_space_agent.skills.registry import SkillRegistry


def _seed(root: Path, slug: str, tags: list[str], blurb: str = "") -> Path:
    folder = root / slug
    folder.mkdir(parents=True, exist_ok=True)
    tags_str = "[" + ", ".join(tags) + "]"
    blurb_line = f"description: {blurb}\n" if blurb else ""
    (folder / "SKILL.md").write_text(
        f"---\nname: {slug}\n{blurb_line}tags: {tags_str}\n---\nBody.",
        encoding="utf-8",
    )
    return folder


def _registry(tmp_path: Path, pack: Path) -> SkillRegistry:
    reg = SkillRegistry(bundled_dir=pack, user_dir=tmp_path / "vacant-user")
    reg.load()
    return reg


def _names(skills) -> list[str]:
    return [s.name for s in skills]


class TestWeightFactorRanking:
    def test_absent_weights_leave_order_unchanged(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "skill-a", ["research"], "Research skill A")
        _seed(pack, "skill-b", ["research"], "Research skill B")
        reg = _registry(tmp_path, pack)

        baseline = _names(reg.top_n_for_prompt("research", n=6))
        # 空 map 与 None 都必须是恒等变换。
        assert _names(reg.top_n_for_prompt("research", n=6, weights={})) == baseline
        assert _names(reg.top_n_for_prompt("research", n=6, weights=None)) == baseline

    def test_higher_factor_ranks_earlier(self, tmp_path: Path) -> None:
        # 两个同分 skill:a 的因子高 → a 排前。
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "skill-a", ["research"], "Research skill A")
        _seed(pack, "skill-b", ["research"], "Research skill B")
        reg = _registry(tmp_path, pack)

        # 默认按名字升序:skill-a 本就在前。给 b 高的因子翻转它。
        out = reg.top_n_for_prompt(
            "research", n=6, weights={"skill-a": 0.8, "skill-b": 1.2}
        )
        assert _names(out)[:2] == ["skill-b", "skill-a"]

    def test_weight_cannot_resurrect_a_zero_score_skill(self, tmp_path: Path) -> None:
        # 核心契约:因子只乘"已有的相关性",不能让不相关的 skill 冒出来。
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "kline-view", ["kline"], "View klines")
        reg = _registry(tmp_path, pack)

        out = reg.top_n_for_prompt("kline", n=6, weights={"some-other-skill": 1.2})
        assert _names(out) == ["kline-view"]
        # 给 kline-view 灌一个巨大因子也不会改变"它本来就在结果里"这一事实。
        out2 = reg.top_n_for_prompt("kline", n=6, weights={"kline-view": 100.0})
        assert _names(out2) == ["kline-view"]

    def test_unrelated_skill_stays_excluded_regardless_of_weight(
        self, tmp_path: Path
    ) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "kline-view", ["kline"], "View klines")
        _seed(pack, "tg-signals", ["signal", "telegram"], "Telegram signals")
        reg = _registry(tmp_path, pack)

        # 给一个与查询无关的 skill 灌满分因子 —— 它仍然不得出现。
        out = reg.top_n_for_prompt("kline", n=6, weights={"tg-signals": 1.2})
        assert "tg-signals" not in _names(out)

    def test_malformed_factors_are_neutral(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "skill-a", ["research"], "Research skill A")
        _seed(pack, "skill-b", ["research"], "Research skill B")
        reg = _registry(tmp_path, pack)

        baseline = _names(reg.top_n_for_prompt("research", n=6))
        for bad in (True, 0, -1.5, float("nan"), float("inf"), "1.2", None):
            got = _names(
                reg.top_n_for_prompt("research", n=6, weights={"skill-a": bad})
            )
            assert got == baseline, f"factor {bad!r} should be neutral"

    def test_limit_still_applies_after_weighting(self, tmp_path: Path) -> None:
        pack = tmp_path / "pack"
        pack.mkdir()
        for i in range(5):
            _seed(pack, f"skill-{i}", ["research"], f"Research skill {i}")
        reg = _registry(tmp_path, pack)

        out = reg.top_n_for_prompt(
            "research", n=2, weights={f"skill-{i}": 1.2 for i in range(5)}
        )
        assert len(out) == 2

    def test_bool_factor_is_not_read_as_one(self, tmp_path: Path) -> None:
        # True 在 Python 里是 int,不能被当成因子 1.0 而"恰好像没给"——
        # 语义上它是非法输入,应按中性处理(结果相同,但理由不同)。
        pack = tmp_path / "pack"
        pack.mkdir()
        _seed(pack, "skill-a", ["research"], "Research skill A")
        reg = _registry(tmp_path, pack)
        assert _names(reg.top_n_for_prompt("research", weights={"skill-a": True})) == [
            "skill-a"
        ]
