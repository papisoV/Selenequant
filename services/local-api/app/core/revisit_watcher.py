"""复访消费者:把到期的 research question 浮出成待办(只提示,不开调查)。

背景:``ResearchQuestionRepository.due()`` 与 ``GET /revisit/due`` 早已存在,
但没有任何调度器去读 —— 用户设的复访时点永远不会自己冒出来。

本模块只做一件事:**到期就提醒**。它不推进 mission 相位、不创建 mission、
不调 LLM。真正的调查是用户的按钮(与 promote 的"问题结不结案是用户的
另一颗按钮"同一条红线)。

**``resolved`` 的 mission 仍然接收复访 —— 它是结论要接受检验的地方。**
结案时 ``settle_mission`` 会在原问题上设复访时点,而那条 mission 恰好在此
刻被推成 ``resolved``;把 resolved 与 parked 一起当"终态"过滤,每一条结案
留下的复访都会静默落空。``parked`` 相反:停放就是"别再打扰我",跳过它。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from selene_space_storage.repositories.research_graph import (
    ResearchEdgeRepository,
    ResearchQuestionRepository,
)
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)

logger = logging.getLogger(__name__)

# 单轮最多提醒多少条,防止积压时一次刷屏。
_DUE_LIMIT = 20
# 每页取多少行。必须大于 _DUE_LIMIT,否则一页里全是已提醒过的行时,本轮
# 一条新的也轮不到。
_PAGE = 50
# 单轮最多翻几页。"已提醒"不会让问题脱离到期集合(提醒不改问题本身),
# 所以积压里的旧行会一直排在队首;翻页深度必须有上限,否则积压一多,
# 一轮要扫全表。
_MAX_PAGES = 4
# 幂等键前缀:同一 (question_id, next_revisit_at) 只提醒一次。
_ACTIVITY_KIND = "revisit_due"


def _utc_now() -> str:
    """ISO-8601 UTC,与仓储层 ``_now()`` 同格式(字典序即时间序)。"""
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def _notified_key(question_id: str, next_revisit_at: str) -> str:
    return f"{question_id}|{next_revisit_at}"


async def _open_mission_for_question(
    db: Any, question_id: str
) -> dict[str, Any] | None:
    """问题 → 该收提醒的 mission(经 addresses 边)。

    版本一按 mission.context.question_id 找,但手建 mission 不带该字段;
    addresses 边是 promote 写的**权威**关联,优先用它,context 作兜底。

    只排除 ``parked``。``resolved`` 必须保留 —— 见模块 docstring:结案后
    的复访正是结论的检验点,过滤掉它等于把循环的最后一环剪断。
    """
    edge_repo = ResearchEdgeRepository(db)
    mission_repo = ResearchMissionRepository(db)
    for edge in await edge_repo.list_edges(question_id, direction="both", limit=50):
        if edge.get("kind") != "addresses":
            continue
        if edge.get("from_type") == "mission":
            mission_id = edge.get("from_id")
        elif edge.get("to_type") == "mission":
            mission_id = edge.get("to_id")
        else:
            continue
        found: dict[str, Any] | None = await mission_repo.get(str(mission_id))
        if found is not None and found.get("phase") != "parked":
            return found
    # 兜底:promote 之外的路径建的 mission 没有 addresses 边
    fallback: list[dict[str, Any]] = await mission_repo.list_missions(limit=200)
    for mission in fallback:
        if mission.get("phase") == "parked":
            continue
        if (mission.get("context") or {}).get("question_id") == question_id:
            return mission
    return None


async def revisit_watch_round(
    db: Any,
    now: str | None = None,
    *,
    limit: int = _DUE_LIMIT,
    after: tuple[str, str] | None = None,
) -> dict[str, Any]:
    """扫描到期问题并在其开放 mission 上追加 ``revisit_due`` 活动。

    返回 ``{"due": n, "notified": n, "skipped": n, "next": cursor|None}``。
    幂等:同一 ``(question_id, next_revisit_at)`` 第二次调用不再追加(计入
    skipped)。

    ``next`` 是下一轮该从哪继续的 keyset 游标。为什么必须滚:提醒**不改
    问题本身**,所以"已提醒"的行仍留在到期集合里、仍排在队首;只从队首
    取一页的扫描在积压超过一页后永远停在同一批已提醒的行上,后面的问题
    一辈子浮不出来。``None`` 表示本轮已扫到该时刻的末尾,下一轮从头开始。
    """
    question_repo = ResearchQuestionRepository(db)
    mission_repo = ResearchMissionRepository(db)
    stamp = now or _utc_now()

    notified = 0
    skipped = 0
    due = 0
    cursor = after
    next_cursor: tuple[str, str] | None = None
    for _ in range(_MAX_PAGES):
        page = await question_repo.due(stamp, limit=_PAGE, after=cursor)
        if not page:
            next_cursor = None
            break
        due += len(page)
        # 游标必须落在**最后处理过的那一行**,不是页尾 —— 名额在中途用完时,
        # 页尾之前还有没看的行,跳到页尾会把它们永久跳过。
        last_seen: tuple[str, str] | None = None
        for question in page:
            if notified >= limit:
                break
            question_id = str(question.get("id") or "")
            next_revisit_at = str(question.get("next_revisit_at") or "")
            last_seen = (next_revisit_at, question_id)
            if not question_id or not next_revisit_at:
                skipped += 1
                continue
            mission = await _open_mission_for_question(db, question_id)
            if mission is None:
                # 没有开放 mission = 没有可挂的待办;不新建(新建是用户的决定)。
                skipped += 1
                continue
            mission_id = str(mission["id"])
            key = _notified_key(question_id, next_revisit_at)
            if await mission_repo.has_activity_detail(
                mission_id, kind=_ACTIVITY_KIND, detail_key="key", detail_value=key
            ):
                skipped += 1
                continue
            await mission_repo.append_activity(
                mission_id,
                actor="system",
                kind=_ACTIVITY_KIND,
                detail={
                    "key": key,
                    "question_id": question_id,
                    "question_title": question.get("title"),
                    "next_revisit_at": next_revisit_at,
                    "phase": mission.get("phase"),
                    "hint": "复访到期:请复核该问题,决定是否补证据/改判/结案",
                },
            )
            notified += 1
        if last_seen is not None:
            cursor = last_seen
        if notified >= limit:
            # 名额用完:下一轮从最后处理过的下一行继续。
            next_cursor = cursor
            break
        if len(page) < _PAGE:
            # 这一页就是队尾:下一轮从头开始(新到期的会更晚,排在后面)。
            next_cursor = None
            break
        next_cursor = cursor

    return {"due": due, "notified": notified, "skipped": skipped, "next": next_cursor}


async def revisit_watch_loop(state: dict, *, interval: float) -> None:
    """周期扫描;单轮异常不连坐(记 warning 继续下一轮)。

    游标跨轮保留:积压超过一轮名额时,下一轮从上一轮停下的地方继续,
    否则每轮都从队首那一批已提醒过的行开始 —— 剩下的永远轮不到。
    扫到末尾时游标归 None,下一轮从头开始,所以持续新增的到期问题
    最多等 (积压 / 每轮名额) 轮就会轮到。
    """
    cursor: tuple[str, str] | None = None
    while True:
        try:
            db = state.get("research_db") or state.get("db_manager")
            if db is None:
                await asyncio.sleep(interval)
                continue
            result = await revisit_watch_round(db, after=cursor)
            cursor = result["next"]
            if result["notified"]:
                logger.info(
                    "revisit watcher: %d due, %d notified, %d skipped",
                    result["due"],
                    result["notified"],
                    result["skipped"],
                )
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.warning("revisit watch round failed", exc_info=True)
            cursor = None  # 出错后从头扫,避免卡在坏游标上
        await asyncio.sleep(interval)


__all__ = ["revisit_watch_loop", "revisit_watch_round"]
