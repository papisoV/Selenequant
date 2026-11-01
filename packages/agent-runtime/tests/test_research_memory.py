"""ResearchFindingsMemory keyword recall behaviour."""

from __future__ import annotations

import json
from pathlib import Path

from selene_space_agent.memory.research_memory import ResearchFindingsMemory


def _seed(tmp_path: Path, entries: list[dict]) -> Path:
    path = tmp_path / "seed.json"
    path.write_text(json.dumps({"entries": entries}), encoding="utf-8")
    return path


TWO_ENTRIES = [
    {
        "id": "a",
        "claim": "claim A",
        "verdict": "NO-GO",
        "date": "2026-09-18",
        "evidence": "evidence A",
        "keywords": ["鲸鱼", "流入", "whale"],
        "findings_path": "a.md",
    },
    {
        "id": "b",
        "claim": "claim B",
        "verdict": "GO",
        "date": "2026-09-19",
        "evidence": "evidence B",
        "keywords": ["RSI", "超买"],
        "findings_path": "b.md",
    },
]


class TestRecall:
    def test_chinese_keyword_hit(self, tmp_path: Path) -> None:
        mem = ResearchFindingsMemory(_seed(tmp_path, TWO_ENTRIES))
        out = mem.recall("鲸鱼大量流入交易所是不是要跌")
        assert "NO-GO" in out
        assert "claim A" in out
        assert "a.md" in out

    def test_english_keyword_hit(self, tmp_path: Path) -> None:
        mem = ResearchFindingsMemory(_seed(tmp_path, TWO_ENTRIES))
        out = mem.recall("is the RSI overbought signal real?")
        assert "claim B" in out
        assert "GO" in out

    def test_no_match_returns_empty(self, tmp_path: Path) -> None:
        mem = ResearchFindingsMemory(_seed(tmp_path, TWO_ENTRIES))
        assert mem.recall("今天天气不错") == ""

    def test_empty_query_returns_empty(self, tmp_path: Path) -> None:
        mem = ResearchFindingsMemory(_seed(tmp_path, TWO_ENTRIES))
        assert mem.recall("") == ""

    def test_hits_ranked_by_keyword_count(self, tmp_path: Path) -> None:
        entries = [
            *TWO_ENTRIES,
            {
                "id": "c",
                "claim": "claim C",
                "verdict": "NO-GO",
                "date": "2026-09-20",
                "evidence": "evidence C",
                "keywords": ["鲸鱼", "流入", "whale", "chain"],
                "findings_path": "c.md",
            },
        ]
        mem = ResearchFindingsMemory(_seed(tmp_path, entries))
        # "c" matches 3 keywords, "a" matches 2 → c first.
        out = mem.recall("鲸鱼流入 whale on-chain")
        assert out.index("claim C") < out.index("claim A")

    def test_recall_capped_at_three(self, tmp_path: Path) -> None:
        entries = [
            {
                "id": f"e{i}",
                "claim": f"claim {i}",
                "verdict": "NO-GO",
                "date": "2026-01-01",
                "evidence": f"evidence {i}",
                "keywords": ["共享词"],
                "findings_path": f"{i}.md",
            }
            for i in range(5)
        ]
        mem = ResearchFindingsMemory(_seed(tmp_path, entries))
        out = mem.recall("共享词")
        assert out.count("claim ") == 3

    def test_missing_seed_file_returns_empty_memory(self, tmp_path: Path) -> None:
        mem = ResearchFindingsMemory(tmp_path / "nope.json")
        assert mem._entries == []
        assert mem.recall("鲸鱼") == ""


class TestRealSeed:
    """The shipped seed must be loadable and recallable."""

    def test_builtin_seed_loads_three_entries(self) -> None:
        mem = ResearchFindingsMemory()
        assert len(mem._entries) == 3

    def test_builtin_seed_recall_whale(self) -> None:
        mem = ResearchFindingsMemory()
        out = mem.recall("鲸鱼向交易所充值是不是马上要跌")
        assert "NO-GO" in out
        assert "波动率代理" in out

    def test_builtin_seed_recall_rsi(self) -> None:
        mem = ResearchFindingsMemory()
        out = mem.recall("RSI 81天窗口最优是真的吗")
        assert "NO-GO" in out

    def test_builtin_seed_recall_vpvr(self) -> None:
        mem = ResearchFindingsMemory()
        out = mem.recall("成交量簇 POC 突破策略胜率 61%")
        assert "NO-GO" in out
