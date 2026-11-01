"""playbook 路由：按 market_regime 叠加去重。

模式参考 selene 自有 MIT decision-workflow 的 routing.py（按状态匹配→收集→去重，
保持首现顺序），市场侧改名与独立撰写。路由纯函数不触碰 LLM / 存储，可单测。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class PlaybookRoutingEntry:
    """一条路由规则：matcher(diagnosis) 为真则纳入该组 playbook。"""

    matcher: Callable[[dict], bool]
    playbooks: tuple[str, ...]


@dataclass(frozen=True)
class PlaybookRoutingTable:
    """有序路由规则集合。"""

    entries: tuple[PlaybookRoutingEntry, ...]


def route_playbooks(diagnosis: dict, table: PlaybookRoutingTable) -> list[str]:
    """纯路由函数：按序命中 matcher，收集 playbook 并去重（首现顺序）。

    - 未命中任何规则时返回空表（如 entry_gate 短路场景由编排层跳过路由）。
    - 同一 playbook 被多条规则命中时只保留首次出现（叠加去重）。
    """
    seen: set[str] = set()
    out: list[str] = []
    for entry in table.entries:
        if not entry.matcher(diagnosis):
            continue
        for playbook in entry.playbooks:
            if playbook not in seen:
                out.append(playbook)
                seen.add(playbook)
    return out


# 默认市场 playbook 表：基础价格行为常驻，regime 命中叠加，proceed 追加执行册。
# playbook 文件见 packages/market-analysis/selene_space_market_analysis/playbooks/，
# 来源均为 Al Brooks 公开出版物（文件头标 provenance）。
MARKET_PLAYBOOK_TABLE = PlaybookRoutingTable(
    entries=(
        PlaybookRoutingEntry(
            matcher=lambda d: True,
            playbooks=("al-brooks-foundations",),
        ),
        PlaybookRoutingEntry(
            matcher=lambda d: d.get("market_regime") in ("trend_up", "trend_down"),
            playbooks=("al-brooks-trend",),
        ),
        PlaybookRoutingEntry(
            matcher=lambda d: d.get("market_regime") == "range",
            playbooks=("al-brooks-range",),
        ),
        PlaybookRoutingEntry(
            matcher=lambda d: d.get("market_regime") == "spike",
            playbooks=("al-brooks-spike", "al-brooks-range"),
        ),
        PlaybookRoutingEntry(
            matcher=lambda d: d.get("entry_gate") == "proceed",
            playbooks=("al-brooks-execution",),
        ),
    )
)


__all__ = [
    "MARKET_PLAYBOOK_TABLE",
    "PlaybookRoutingEntry",
    "PlaybookRoutingTable",
    "route_playbooks",
]
