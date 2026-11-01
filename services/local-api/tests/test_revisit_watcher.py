"""复访消费者测试:到期问题必须浮出成待办,且只浮出不动作。

覆盖:到期→通知、幂等、非到期不通知、无开放 mission 不新建 mission、
以及"绝不推进相位"这条红线。
"""

from __future__ import annotations

from typing import Any

import pytest
import pytest_asyncio

from app.core.revisit_watcher import revisit_watch_round

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    ResearchEdgeRepository,
    ResearchQuestionRepository,
)
from selene_space_storage.repositories.research_missions import (
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

_NOW = "2026-10-21T12:00:00.000000+00:00"
_PAST = "2026-10-20T00:00:00.000000+00:00"
_FUTURE = "2026-10-30T00:00:00.000000+00:00"


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


async def _question_with_mission(db, *, revisit_at: str) -> tuple[str, str]:
    qrepo = ResearchQuestionRepository(db)
    question_id = await qrepo.create("BTC 资金费率是否已反映降息预期", body="")
    await qrepo.set_revisit(question_id, revisit_at, actor="user")
    mrepo = ResearchMissionRepository(db)
    question = await qrepo.get(question_id)
    assert question is not None
    mission_id = await mrepo.create(
        title="资金费率定价检验",
        question=str(question["title"]),
        subject_type="market",
        subject_id="BTC/USDT",
        created_by="user",
    )
    await ResearchEdgeRepository(db).add_fact_edge(
        ("mission", mission_id), ("question", question_id), "addresses", actor="user"
    )
    return question_id, mission_id


async def _fetch(db, mission_id: str) -> dict[str, Any]:
    mission: dict[str, Any] | None = await ResearchMissionRepository(db).get(mission_id)
    assert mission is not None
    return mission


@pytest.mark.asyncio
async def test_due_question_emits_activity(db) -> None:
    question_id, mission_id = await _question_with_mission(db, revisit_at=_PAST)

    result = await revisit_watch_round(db, now=_NOW)
    assert result["due"] == 1
    assert result["notified"] == 1
    assert result["skipped"] == 0
    assert result["next"] is None

    activity = await ResearchMissionRepository(db).list_activity(mission_id)
    due_rows = [a for a in activity if a["kind"] == "revisit_due"]
    assert len(due_rows) == 1
    detail = due_rows[0]["detail"]
    assert detail["question_id"] == question_id
    assert detail["next_revisit_at"] == _PAST


@pytest.mark.asyncio
async def test_second_round_is_idempotent(db) -> None:
    _, mission_id = await _question_with_mission(db, revisit_at=_PAST)

    first = await revisit_watch_round(db, now=_NOW)
    second = await revisit_watch_round(db, now=_NOW)
    assert first["notified"] == 1
    assert second["due"] == 1
    assert second["notified"] == 0
    assert second["skipped"] == 1

    activity = await ResearchMissionRepository(db).list_activity(mission_id)
    assert len([a for a in activity if a["kind"] == "revisit_due"]) == 1


@pytest.mark.asyncio
async def test_idempotency_survives_a_long_activity_stream(db) -> None:
    """活动流超过幂等窗口时,同一复访时点仍只能提醒一次。

    回归:幂等检查用 ``list_activity``(截最旧 N 行),流一长就看不到自己
    上一轮写的那条 ``revisit_due``,于是每轮扫描都再刷一条 —— 用户会被
    同一个到期问题反复提醒。
    """
    _, mission_id = await _question_with_mission(db, revisit_at=_PAST)
    mrepo = ResearchMissionRepository(db)
    for i in range(600):
        await mrepo.append_activity(
            mission_id, actor="selene", kind="worker_analysis", detail={"n": i}
        )

    first = await revisit_watch_round(db, now=_NOW)
    second = await revisit_watch_round(db, now=_NOW)

    assert first["notified"] == 1
    assert second["due"] == 1
    assert second["notified"] == 0
    assert second["skipped"] == 1
    activity = await mrepo.list_activity(mission_id, limit=2000)
    assert len([a for a in activity if a["kind"] == "revisit_due"]) == 1


@pytest.mark.asyncio
async def test_not_due_yet_is_silent(db) -> None:
    _, mission_id = await _question_with_mission(db, revisit_at=_FUTURE)
    result = await revisit_watch_round(db, now=_NOW)
    assert result["due"] == 0
    assert result["notified"] == 0
    assert result["skipped"] == 0
    assert result["next"] is None
    activity = await ResearchMissionRepository(db).list_activity(mission_id)
    assert not [a for a in activity if a["kind"] == "revisit_due"]


@pytest.mark.asyncio
async def test_no_open_mission_does_not_create_one(db) -> None:
    """没有开放 mission 时只跳过 —— 新建 mission 是用户的决定。"""
    qrepo = ResearchQuestionRepository(db)
    question_id = await qrepo.create("无人跟进的问题", body="")
    await qrepo.set_revisit(question_id, _PAST, actor="user")

    mrepo = ResearchMissionRepository(db)
    before = await mrepo.list_missions(limit=100)
    result = await revisit_watch_round(db, now=_NOW)
    after = await mrepo.list_missions(limit=100)

    assert result["notified"] == 0
    assert result["skipped"] == 1
    assert len(before) == len(after) == 0


@pytest.mark.asyncio
async def test_never_advances_mission_phase(db) -> None:
    """红线:复访提醒不得推进相位。"""
    _, mission_id = await _question_with_mission(db, revisit_at=_PAST)
    phase_before = (await _fetch(db, mission_id))["phase"]

    await revisit_watch_round(db, now=_NOW)

    assert (await _fetch(db, mission_id))["phase"] == phase_before == "investigation"


@pytest.mark.asyncio
async def test_resolved_mission_is_skipped(db) -> None:
    """已结算 mission 不接收复访待办(它已不是开放 mission)。"""
    question_id, mission_id = await _question_with_mission(db, revisit_at=_PAST)
    mrepo = ResearchMissionRepository(db)
    await mrepo.transition(mission_id, "parked", actor="user", note="暂缓")

    result = await revisit_watch_round(db, now=_NOW)
    assert result["notified"] == 0
    activity = await mrepo.list_activity(mission_id)
    assert not [a for a in activity if a["kind"] == "revisit_due"]


@pytest.mark.asyncio
async def test_backlog_beyond_one_round_is_not_starved(db) -> None:
    """积压超过一轮名额时,下一轮必须从上次停下的地方继续。

    回归:``due()`` 的头是稳定排序,而"已提醒"不会让问题脱离到期集合
    (提醒不改问题本身)。只从队首取一页的扫描,积压一多就永远停在同一批
    已提醒的行上,第 21 条起永远浮不出来 —— 与"没接消费者"同形的黑洞。
    """
    question_ids, mission_ids = await _backlog(db, 25)

    first = await revisit_watch_round(db, now=_NOW)
    assert first["notified"] == 20
    assert first["next"] is not None

    second = await revisit_watch_round(db, now=_NOW, after=first["next"])
    assert second["notified"] == 5
    assert second["next"] is None

    mrepo = ResearchMissionRepository(db)
    for mission_id in mission_ids:
        rows = await mrepo.list_activity(mission_id)
        assert [r for r in rows if r["kind"] == "revisit_due"], mission_id
    assert len(question_ids) == 25


@pytest.mark.asyncio
async def test_quota_filling_mid_page_continues_at_last_processed_row(
    db, monkeypatch
) -> None:
    """名额在一页中途用完时,游标必须停在**最后处理过的那一行**。

    回归:游标若跳到页尾,页尾之前没看一眼的行会被整段跳过(直到游标绕回
    队首才补上)—— 用户看到的是复访提醒莫名其妙地晚了好几轮。
    """
    from app.core import revisit_watcher

    monkeypatch.setattr(revisit_watcher, "_PAGE", 50)
    monkeypatch.setattr(revisit_watcher, "_MAX_PAGES", 4)
    _, mission_ids = await _backlog(db, 60)
    mrepo = ResearchMissionRepository(db)

    async def _count() -> int:
        total = 0
        for mission_id in mission_ids:
            rows = await mrepo.list_activity(mission_id)
            total += len([r for r in rows if r["kind"] == "revisit_due"])
        return total

    first = await revisit_watch_round(db, now=_NOW, limit=20)
    assert (first["notified"], await _count()) == (20, 20)
    assert first["next"] is not None

    second = await revisit_watch_round(db, now=_NOW, limit=20, after=first["next"])
    # 游标落在第 20 行 → 下一轮从第 21 行起,正好再提醒 20 条。
    # 若游标跳到页尾(第 50 行),这一轮只能提醒 10 条。
    assert (second["notified"], await _count()) == (20, 40)


@pytest.mark.asyncio
async def test_backlog_larger_than_one_page_progresses(db) -> None:
    """积压超过单页大小时,一轮内也要翻页,不能停在页尾。"""
    _, mission_ids = await _backlog(db, 60)

    result = await revisit_watch_round(db, now=_NOW, limit=60)
    assert result["notified"] == 60
    mrepo = ResearchMissionRepository(db)
    notified = 0
    for mission_id in mission_ids:
        rows = await mrepo.list_activity(mission_id)
        notified += len([r for r in rows if r["kind"] == "revisit_due"])
    assert notified == 60


@pytest.mark.asyncio
async def test_loop_carries_cursor_across_rounds(db, monkeypatch) -> None:
    """循环本身必须把游标带到下一轮,否则积压永远不会被消化。

    这里把单页压到 5 行、每轮只翻一页:游标若被丢掉,每轮都会重扫
    队首那 5 条已提醒过的行,第 6 条起永远轮不到(积压大于
    单轮扫描窗口时的真实形状,只是用更小的数字重现)。
    """
    import asyncio

    from app.core import revisit_watcher

    monkeypatch.setattr(revisit_watcher, "_PAGE", 5)
    monkeypatch.setattr(revisit_watcher, "_MAX_PAGES", 1)
    _, mission_ids = await _backlog(db, 12)
    mrepo = ResearchMissionRepository(db)

    async def _count() -> int:
        total = 0
        for mission_id in mission_ids:
            rows = await mrepo.list_activity(mission_id)
            total += len([r for r in rows if r["kind"] == "revisit_due"])
        return total

    task = asyncio.create_task(
        revisit_watcher.revisit_watch_loop({"research_db": db}, interval=0.01)
    )
    try:
        for _ in range(600):
            if await _count() == 12:
                break
            await asyncio.sleep(0.01)
        assert await _count() == 12
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


async def _backlog(db, n: int) -> tuple[list[str], list[str]]:
    """n 个到期问题,各自挂一条开放 mission。"""
    qrepo = ResearchQuestionRepository(db)
    mrepo = ResearchMissionRepository(db)
    edge = ResearchEdgeRepository(db)
    question_ids: list[str] = []
    mission_ids: list[str] = []
    for i in range(n):
        question_id = await qrepo.create(f"到期问题 {i}", body="")
        await qrepo.set_revisit(question_id, _PAST, actor="user")
        question = await qrepo.get(question_id)
        assert question is not None
        mission_id = await mrepo.create(
            title=f"调查 {i}",
            question=str(question["title"]),
            subject_type="market",
            subject_id="BTC/USDT",
            created_by="user",
        )
        await edge.add_fact_edge(
            ("mission", mission_id),
            ("question", question_id),
            "addresses",
            actor="user",
        )
        question_ids.append(question_id)
        mission_ids.append(mission_id)
    return question_ids, mission_ids


@pytest.mark.asyncio
async def test_loop_notifies_then_sleeps_and_is_cancellable(db) -> None:
    """循环是 lifespan 真正跑的东西:先跑一轮,再睡觉,关停时干净退出。"""
    import asyncio

    from app.core.revisit_watcher import revisit_watch_loop

    question_id, mission_id = await _question_with_mission(db, revisit_at=_PAST)
    state = {"research_db": db}

    task = asyncio.create_task(revisit_watch_loop(state, interval=3600.0))
    try:
        for _ in range(200):
            activity = await ResearchMissionRepository(db).list_activity(mission_id)
            if any(row["kind"] == "revisit_due" for row in activity):
                break
            await asyncio.sleep(0.01)
        assert any(row["kind"] == "revisit_due" for row in activity)
        assert not task.done(), "loop must keep sleeping between rounds"
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.asyncio
async def test_resolved_mission_still_receives_revisit(db) -> None:
    """结案的 mission 必须能收到复访 —— 它是结论要接受检验的地方。

    ``settle_mission`` 给原问题设 ``next_revisit_at`` 时,那条 mission 刚好
    被推成 ``resolved``。若把 resolved 与 parked 一起过滤,每一条结案留下
    的复访都会静默落空:问题列表页仍会渲染(``due()`` 只看问题),但
    mission 活动流里永远没有那条 "revisit_due",研究面看起来像"结了就完了"。

    ``parked`` 不同 —— 停放是"别再打扰我",它才该被跳过。
    """
    question_id, mission_id = await _question_with_mission(db, revisit_at=_PAST)
    mrepo = ResearchMissionRepository(db)
    await mrepo.transition(mission_id, "validation", actor="user", note="批准")
    await mrepo.transition(mission_id, "resolved", actor="user", note="结案")
    assert (await _fetch(db, mission_id))["phase"] == "resolved"

    result = await revisit_watch_round(db, now=_NOW)
    assert result["notified"] == 1
    activity = await mrepo.list_activity(mission_id)
    rows = [a for a in activity if a["kind"] == "revisit_due"]
    assert len(rows) == 1
    # 红线:提醒不改相位
    assert (await _fetch(db, mission_id))["phase"] == "resolved"

    # 幂等:同一 (question, revisit_at) 第二轮不再追加
    again = await revisit_watch_round(db, now=_NOW)
    assert again["notified"] == 0
