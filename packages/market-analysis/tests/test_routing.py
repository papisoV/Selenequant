"""US-006 — playbook 路由单测（4 regime × 组合叠加去重）。

覆盖验收标准：
- 路由按 market_regime 叠加（基础册常驻 + regime 命中叠加 + proceed 追加执行册）
- 同一 playbook 被多条规则命中时去重，首现顺序保留
- 真实表 + 自定义表均可路由；playbook 知识库文件存在于 playbooks/ 且有 provenance 头
"""
from __future__ import annotations

from pathlib import Path

from selene_space_market_analysis.routing import (
    MARKET_PLAYBOOK_TABLE,
    PlaybookRoutingEntry,
    PlaybookRoutingTable,
    route_playbooks,
)


def _diagnosis(regime: str, entry_gate: str = "proceed") -> dict:
    return {"market_regime": regime, "entry_gate": entry_gate}


# ---------------------------------------------------------------------------
# 4 regime × 路由
# ---------------------------------------------------------------------------


def test_route_trend_up_stacks_trend_and_execution() -> None:
    assert route_playbooks(_diagnosis("trend_up"), MARKET_PLAYBOOK_TABLE) == [
        "al-brooks-foundations",
        "al-brooks-trend",
        "al-brooks-execution",
    ]


def test_route_trend_down_shares_trend_playbook() -> None:
    """trend_down 与 trend_up 共享同一 trend 册。"""
    assert route_playbooks(_diagnosis("trend_down"), MARKET_PLAYBOOK_TABLE) == [
        "al-brooks-foundations",
        "al-brooks-trend",
        "al-brooks-execution",
    ]


def test_route_range_stacks_range_playbook() -> None:
    assert route_playbooks(_diagnosis("range"), MARKET_PLAYBOOK_TABLE) == [
        "al-brooks-foundations",
        "al-brooks-range",
        "al-brooks-execution",
    ]


def test_route_spike_stacks_spike_plus_range() -> None:
    """spike 叠加 range 册（通道段具有区间回归属性）。"""
    assert route_playbooks(_diagnosis("spike"), MARKET_PLAYBOOK_TABLE) == [
        "al-brooks-foundations",
        "al-brooks-spike",
        "al-brooks-range",
        "al-brooks-execution",
    ]


def test_route_short_circuit_gate_skips_execution() -> None:
    """entry_gate=wait（短路）不追加执行册，但 regime 册照常叠加。"""
    assert route_playbooks(_diagnosis("spike", entry_gate="wait"), MARKET_PLAYBOOK_TABLE) == [
        "al-brooks-foundations",
        "al-brooks-spike",
        "al-brooks-range",
    ]


# ---------------------------------------------------------------------------
# 叠加去重
# ---------------------------------------------------------------------------


def test_route_dedup_preserves_first_occurrence() -> None:
    """两条规则命中同一 playbook → 只保留首现，顺序稳定。"""
    table = PlaybookRoutingTable(
        entries=(
            PlaybookRoutingEntry(matcher=lambda d: d.get("a") is True, playbooks=("common", "x")),
            PlaybookRoutingEntry(matcher=lambda d: d.get("b") is True, playbooks=("common", "y")),
        )
    )
    assert route_playbooks({"a": True, "b": True}, table) == ["common", "x", "y"]


def test_route_no_match_returns_empty() -> None:
    """无命中规则 → 空表。"""
    table = PlaybookRoutingTable(entries=())
    assert route_playbooks(_diagnosis("trend_up"), table) == []


def test_route_missing_regime_only_foundations() -> None:
    """market_regime 缺失 → 仅基础册常驻。"""
    out = route_playbooks({"entry_gate": "proceed"}, MARKET_PLAYBOOK_TABLE)
    assert out == ["al-brooks-foundations", "al-brooks-execution"]


# ---------------------------------------------------------------------------
# playbook 知识库文件存在 + provenance 头
# ---------------------------------------------------------------------------


def test_playbook_files_exist_with_provenance_header() -> None:
    """每文件头标 provenance 来源章节（Al Brooks 公开出版物）。"""
    playbooks_dir = (
        Path(__file__).resolve().parent.parent
        / "selene_space_market_analysis"
        / "playbooks"
    )
    files = sorted(playbooks_dir.glob("*.txt"))
    assert len(files) >= 5
    for f in files:
        first_lines = f.read_text(encoding="utf-8").splitlines()
        assert any("来源:" in line for line in first_lines[:3]), f"{f.name} 缺 provenance 头"
        assert any("Al Brooks" in line for line in first_lines[:3]), f"{f.name} 缺来源署名"
