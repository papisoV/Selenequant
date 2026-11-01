"""mission → question 的归属解析(单一实现)。

**为什么要有这个模块。** 同一个问题在库里有两条关联路径,每条都由不同代码
写入、不同代码读取:

- ``research_missions.context_json.question_id`` —— promote 写,worker/verifier读;
- ``research_edges`` 的 ``addresses`` 边 —— promote 也写,``revisit_watcher``
  只认它(见其 ``_open_mission_for_question`` 的注释:"addresses 边是 promote
  写的**权威**关联,context 作兜底")。

三处各自读 ``context.question_id`` 的结果是:手建 mission 在 worker 里没有锚、
在结算时没有决策、在复访 watcher 里却能找到 —— 同一条 mission 在不同环节
有不同的"归属"。集中到一个函数,两边都认,才不会继续分叉。

**只认这两条。** 按 subject/question 文本模糊匹配会把结论记到别人的问题上,
审计链上那比"没记"更糟。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def mission_question_id(db: Any, mission: dict[str, Any]) -> str | None:
    """这条 mission 在回答哪个问题;找不到返回 ``None``(不猜)。

    优先 ``context.question_id``(零额外查询,且 promote 路径一定写了它);
    没有时才查 ``addresses`` 边。
    """
    embedded = (mission.get("context") or {}).get("question_id")
    if embedded:
        return str(embedded)
    mission_id = str(mission.get("id") or "")
    if not mission_id:
        return None

    from selene_space_storage.repositories.research_graph import (
        ResearchEdgeRepository,
    )

    try:
        edges = await ResearchEdgeRepository(db).list_edges(
            mission_id, direction="both", kind="addresses", limit=20
        )
    except Exception:  # noqa: BLE001 — 读不到边只是少了归属,不该让调用方崩
        logger.warning(
            "mission_question_id: addresses lookup failed for %s",
            mission_id,
            exc_info=True,
        )
        return None
    for edge in edges:
        if edge.get("from_type") == "mission":
            return str(edge.get("to_id"))
        if edge.get("to_type") == "mission":
            return str(edge.get("from_id"))
    return None


__all__ = ["mission_question_id"]
