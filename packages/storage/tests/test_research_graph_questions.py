"""Question 身份 / 状态轴 / 回答轴 + Revision 引用验证。

最重的一组测试是引用验证:如果 add_revision 收下一个悬空引用,
§11 的整条验收判据(第二次回答必须真的利用第一次留下的东西)就没了载体。

本文件只用 raw SQL 造 Episode/Evidence/Edge —— 那两个仓储是后续 task 的
交付物,这里验证的是 Question 自己的逻辑,不该依赖它们的正确性。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph.questions import (
    ResearchQuestionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def repos():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield ResearchQuestionRepository(db), db


async def _seed_episode_with_evidence(db, *, connected_to: str | None = None) -> str:
    """造一条 Episode + 一条 Evidence,可选地把它接到某个 Question 上。

    ``connected_to`` 传 question_id 时同时插一条 addresses 边 —— 这就是
    "这个对象进入了这个问题的研究世界"的凭证。
    """
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
            "boundary_start, boundary_end, occurred_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "ep-1",
                "strategy",
                "strat-x",
                "实跑段",
                "2026-08-01T00:00:00+00:00",
                "2026-08-31T00:00:00+00:00",
                "2026-08-31T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ),
        )
        await conn.execute(
            "INSERT INTO research_evidence (id, episode_id, mode, observed_at, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                "ev-1",
                "ep-1",
                "RECORDED",
                "2026-09-01T00:00:00+00:00",
                "2026-09-01T00:00:00+00:00",
            ),
        )
        if connected_to is not None:
            await conn.execute(
                "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
                "kind, edge_class, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    "edge-1",
                    "evidence",
                    "ev-1",
                    "question",
                    connected_to,
                    "addresses",
                    "fact",
                    "system",
                    "2026-09-01T00:00:00+00:00",
                ),
            )
        await conn.commit()
    return "ev-1"


@pytest.mark.asyncio
async def test_create_returns_id_in_open_status(repos):
    questions, _ = repos
    qid = await questions.create(
        title="SOL 4h 实跑亏损是配置漂移还是框架失效?",
        body="Keltner V2 在 SOL 4h 上实盘亏损,需判断根因",
        anchor={"strategy_ids": ["strat-6395c312"], "intervals": ["4h"]},
    )
    assert qid.startswith("q-")
    q = await questions.get(qid)
    assert q is not None
    assert q["status"] == "open"
    assert q["anchor"]["strategy_ids"] == ["strat-6395c312"]
    assert q["revision_count"] == 0
    assert q["current_revision"] is None


@pytest.mark.asyncio
async def test_first_revision_becomes_current(repos):
    questions, _ = repos
    qid = await questions.create(title="Q")
    rev = await questions.add_revision(
        qid,
        "初步判断:配置漂移",
        actor="selene",
        rationale=["declared vs as_run 不一致"],
    )
    assert rev["seq"] == 1
    q = await questions.get(qid)
    assert q["current_revision"]["content"] == "初步判断:配置漂移"
    assert q["revision_count"] == 1


@pytest.mark.asyncio
async def test_revision_seq_is_monotonic(repos):
    questions, _ = repos
    qid = await questions.create(title="Q")
    await questions.add_revision(qid, "v1", actor="selene")
    await questions.add_revision(qid, "v2", actor="selene")
    revs = await questions.list_revisions(qid)
    assert [r["seq"] for r in revs] == [1, 2]
    assert [r["content"] for r in revs] == ["v1", "v2"]


@pytest.mark.asyncio
async def test_add_revision_rejects_unknown_citation(repos):
    """悬空引用必须被拒绝 —— 这是 §11 验收判据的下限。"""
    questions, _ = repos
    qid = await questions.create(title="Q")
    with pytest.raises(ValueError, match="unknown cited"):
        await questions.add_revision(
            qid, "回答", actor="selene", cited=["ev-does-not-exist"]
        )


@pytest.mark.asyncio
async def test_add_revision_rejects_unconnected_citation(repos):
    """对象存在但与本问题没有边 —— 同样必须被拒绝。

    否则"引用"可以退化成"贴一个别处对象的 id",引用迁移就失去意义。
    """
    questions, db = repos
    qid = await questions.create(title="Q")
    ev_id = await _seed_episode_with_evidence(db)  # 故意不接边
    with pytest.raises(ValueError, match="not connected"):
        await questions.add_revision(qid, "回答", actor="selene", cited=[ev_id])


@pytest.mark.asyncio
async def test_add_revision_rejects_citation_with_unknown_prefix(repos):
    """前缀不在注册表里 —— 既不能当成存在,也不能拿去查表。"""
    questions, _ = repos
    qid = await questions.create(title="Q")
    with pytest.raises(ValueError, match="unknown cited"):
        await questions.add_revision(qid, "回答", actor="selene", cited=["zzz-1"])


@pytest.mark.asyncio
async def test_add_revision_rejects_revision_of_another_question(repos):
    """引用别的问题的 revision —— "self-revision 免验证"只对自己问题成立。

    掉进这个口子的话,引用就退化成"贴任意一个别人问题里的 id",
    §4 的引用迁移读数立刻失去意义。
    """
    questions, _ = repos
    other = await questions.create(title="别的问题")
    other_rev = await questions.add_revision(other, "别的问题的回答", actor="selene")
    qid = await questions.create(title="Q")
    with pytest.raises(ValueError, match="not connected"):
        await questions.add_revision(
            qid, "回答", actor="selene", cited=[other_rev["id"]]
        )


@pytest.mark.asyncio
async def test_add_revision_accepts_connected_citation(repos):
    questions, db = repos
    qid = await questions.create(title="Q")
    ev_id = await _seed_episode_with_evidence(db, connected_to=qid)
    rev = await questions.add_revision(qid, "回答", actor="selene", cited=[ev_id])
    assert rev["cited"] == [ev_id]


@pytest.mark.asyncio
async def test_add_revision_accepts_earlier_revision_of_same_question(repos):
    """第二轮回答引用第一轮回答 —— 永远合法,不需要额外建边。"""
    questions, _ = repos
    qid = await questions.create(title="Q")
    r1 = await questions.add_revision(qid, "第一版回答", actor="selene")
    r2 = await questions.add_revision(
        qid, "第二版回答", actor="selene", cited=[r1["id"]]
    )
    assert r1["id"] in r2["cited"]


@pytest.mark.asyncio
async def test_citation_migration_reports_new_and_dropped(repos):
    """§4 引用迁移的可观测形式:新引用了谁、不再引用谁。

    单版无从比较(from_revision_id 为 None);两版起每一对相邻版本都要能报出
    新增与掉落 —— 这正是"第二次回答有没有真的用上第一次的成果"的读数。
    """
    questions, _ = repos
    qid = await questions.create(title="Q")
    r1 = await questions.add_revision(qid, "v1", actor="selene")

    only_one = await questions.citation_migration(qid)
    assert only_one["from_revision_id"] is None
    assert only_one["to_revision_id"] == r1["id"]
    assert only_one["added"] == []

    r2 = await questions.add_revision(qid, "v2", actor="selene", cited=[r1["id"]])
    into_r2 = await questions.citation_migration(qid)
    assert into_r2["from_revision_id"] == r1["id"]
    assert into_r2["to_revision_id"] == r2["id"]
    assert into_r2["added"] == [r1["id"]]  # 第二版开始引用第一版
    assert into_r2["dropped"] == []

    r3 = await questions.add_revision(qid, "v3", actor="selene", cited=[])
    into_r3 = await questions.citation_migration(qid)
    assert into_r3["from_revision_id"] == r2["id"]
    assert into_r3["dropped"] == [r1["id"]]  # 不再引用第一版
    assert into_r3["retained"] == []
    assert r3["cited"] == []


@pytest.mark.asyncio
async def test_close_requires_terminal_status(repos):
    questions, _ = repos
    qid = await questions.create(title="Q")
    with pytest.raises(ValueError, match="terminal status"):
        await questions.close(qid, "open", actor="user")
    closed = await questions.close(qid, "falsified", actor="user", note="OOS 归零")
    assert closed["status"] == "falsified"
    assert closed["closed_at"] is not None


@pytest.mark.asyncio
async def test_reopen_makes_status_reversible(repos):
    """状态轴可逆:终态可以回到 open,且历史留下事件。"""
    questions, _ = repos
    qid = await questions.create(title="Q")
    await questions.close(qid, "parked", actor="user")
    reopened = await questions.reopen(qid, actor="user", reason="新证据出现")
    assert reopened["status"] == "open"
    assert reopened["closed_at"] is None
    kinds = [e["kind"] for e in await questions.list_events(qid)]
    assert kinds == ["created", "closed", "reopened"]


@pytest.mark.asyncio
async def test_reopen_rejects_open_question(repos):
    questions, _ = repos
    qid = await questions.create(title="Q")
    with pytest.raises(ValueError, match="not closed"):
        await questions.reopen(qid, actor="user", reason="无意义")


@pytest.mark.asyncio
async def test_close_guarded_against_concurrent_status_change(repos):
    """Guarded update 形态: read-then-write 间的并发跳变必须报冲突.

    竞态窗口在 close() 内部 get() 与 UPDATE 之间: 让第一次 get() 返回旧
    快照(open), 并发写者此时直接 SQL 把 status 推到 parked; guarded UPDATE
    (WHERE status=open) 不命中 → 必须报冲突, 事件流的 "from" 值绝不写已
    过期的旧状态。
    """
    questions, db = repos
    qid = await questions.create(title="Q")
    real_get = questions.get
    snapshot = await real_get(qid)

    calls = {"n": 0}

    async def stale_then_real(question_id: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return snapshot
        return await real_get(question_id)

    questions.get = stale_then_real  # type: ignore[method-assign]
    try:
        await db.execute(
            "UPDATE research_questions SET status = 'parked' WHERE id = ?", (qid,)
        )
        with pytest.raises(ValueError, match="changed concurrently"):
            await questions.close(qid, "falsified", actor="user")
    finally:
        questions.get = real_get  # type: ignore[method-assign]

    row = await real_get(qid)
    assert row is not None
    assert row["status"] == "parked"
    kinds = [e["kind"] for e in await questions.list_events(qid)]
    assert "closed" not in kinds


@pytest.mark.asyncio
async def test_reopen_guarded_against_concurrent_status_change(repos):
    """同 close(): reopen 竞态窗口内状态被并发改走必须报冲突."""
    questions, db = repos
    qid = await questions.create(title="Q")
    await questions.close(qid, "parked", actor="user")
    real_get = questions.get
    snapshot = await real_get(qid)

    calls = {"n": 0}

    async def stale_then_real(question_id: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return snapshot
        return await real_get(question_id)

    questions.get = stale_then_real  # type: ignore[method-assign]
    try:
        # 并发写者把终态改成 falsified(另一终态间迁移)
        await db.execute(
            "UPDATE research_questions SET status = 'falsified' WHERE id = ?", (qid,)
        )
        with pytest.raises(ValueError, match="changed concurrently"):
            await questions.reopen(qid, actor="user", reason="无意义")
    finally:
        questions.get = real_get  # type: ignore[method-assign]

    row = await real_get(qid)
    assert row is not None
    assert row["status"] == "falsified"
    kinds = [e["kind"] for e in await questions.list_events(qid)]
    assert "reopened" not in kinds


@pytest.mark.asyncio
async def test_fork_keeps_original_alive(repos):
    questions, _ = repos
    qid = await questions.create(title="原问题")
    child_id = await questions.fork(qid, title="子问题", actor="user")
    parent = await questions.get(qid)
    child = await questions.get(child_id)
    assert parent["status"] == "open"  # 分叉:原问题继续活着
    assert child["parent_question_id"] == qid
    assert child["status"] == "open"


@pytest.mark.asyncio
async def test_succeed_ends_original(repos):
    questions, _ = repos
    qid = await questions.create(title="原问题")
    successor_id = await questions.succeed(qid, title="后继问题", actor="user")
    parent = await questions.get(qid)
    successor = await questions.get(successor_id)
    assert parent["status"] == "superseded"  # 取代:原问题结束
    assert parent["closed_at"] is not None
    assert successor["status"] == "open"
    assert successor["parent_question_id"] == qid


@pytest.mark.asyncio
async def test_list_questions_filters_by_status(repos):
    questions, _ = repos
    await questions.create(title="A")
    q2 = await questions.create(title="B")
    await questions.close(q2, "parked", actor="user")
    assert len(await questions.list_questions(status="open")) == 1
    assert len(await questions.list_questions(status="parked")) == 1
    all_q = await questions.list_questions()
    assert len(all_q) == 2
    # 列表契约:revision_count 随行(列表页显示版数,不容 N+1 或缺字段)
    a = next(q for q in all_q if q["title"] == "A")
    b = next(q for q in all_q if q["title"] == "B")
    assert a["revision_count"] == 0
    assert b["revision_count"] == 0
    await questions.add_revision(q2, "第一版", actor="user", rationale=[], cited=[])
    b2 = next(q for q in await questions.list_questions() if q["title"] == "B")
    assert b2["revision_count"] == 1


@pytest.mark.asyncio
async def test_list_questions_filters_by_parent(repos):
    """归来条数据源:按 parent 过滤出 Selene 提议的子问题(拍板后子问题离开 open,条自然消失)。"""
    questions, _ = repos
    parent_id = await questions.create("母问题")
    other_id = await questions.create("别家问题")
    child_a = await questions.create(
        "提议A", created_by="selene", parent_question_id=parent_id
    )
    child_b = await questions.create(
        "提议B", created_by="selene", parent_question_id=parent_id
    )

    kids = await questions.list_questions(parent=parent_id)
    assert {q["id"] for q in kids} == {child_a, child_b}

    # parent + status 组合过滤:拍板(parked)一个后,open 视角只剩一个
    await questions.close(child_a, "parked", actor="user")
    open_kids = await questions.list_questions(parent=parent_id, status="open")
    assert [q["id"] for q in open_kids] == [child_b]

    assert await questions.list_questions(parent=other_id) == []
    assert (
        len(await questions.list_questions()) == 4
    )  # parent=None 行为不变(母+别家+两提议)


@pytest.mark.asyncio
async def test_get_missing_returns_none(repos):
    questions, _ = repos
    assert await questions.get("q-nope") is None


# ---------------------------------------------------------------------------
# A1:系统提议问题的受保护不变量 —— created_by='selene' 组合 + actor 看守
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fork_with_selene_actor_raises(repos):
    """actor='selene' 会被 _spawn 透传进 created_by,撞坏系统提议组合 —— fail fast。"""
    questions, _ = repos
    qid = await questions.create("母问题")
    with pytest.raises(ValueError, match="reserved for system-proposed"):
        await questions.fork(qid, "分叉", actor="selene")


@pytest.mark.asyncio
async def test_succeed_with_selene_actor_raises(repos):
    questions, _ = repos
    qid = await questions.create("母问题")
    with pytest.raises(ValueError, match="reserved for system-proposed"):
        await questions.succeed(qid, "继任", actor="selene")


@pytest.mark.asyncio
async def test_create_with_selene_and_parent_is_proposed(repos):
    """create(created_by='selene', parent_question_id=...) 是系统提议的唯一入口,
    is_proposed_question 谓词识别它;用户 fork 的子问题不是提议。"""
    from selene_space_storage.repositories.research_graph import is_proposed_question

    questions, _ = repos
    parent_id = await questions.create("母问题")
    proposed_id = await questions.create(
        "主持人提议", created_by="selene", parent_question_id=parent_id
    )
    user_fork_id = await questions.fork(parent_id, "用户分叉", actor="user")

    proposed = await questions.get(proposed_id)
    user_fork = await questions.get(user_fork_id)
    parent = await questions.get(parent_id)

    assert is_proposed_question(proposed) is True
    assert is_proposed_question(user_fork) is False, "created_by='user' 不撞组合"
    assert is_proposed_question(parent) is False, "parent_question_id 为空不撞组合"


@pytest.mark.asyncio
async def test_list_events_kind_filter(repos):
    """R2:kind 过滤只返回该类事件;kind=None 行为不变(全量 ASC 回放)。"""
    questions, _ = repos
    qid = await questions.create("母问题")
    repo = questions
    await repo.append_event(
        qid,
        actor="selene",
        kind="investigate_perspective",
        detail={"perspective": "empiricist"},
    )
    await repo.append_event(qid, actor="user", kind="closed", detail={})
    await repo.append_event(
        qid,
        actor="selene",
        kind="investigate_perspective",
        detail={"perspective": "recordist"},
    )

    all_events = await repo.list_events(qid)
    assert [e["kind"] for e in all_events] == [
        "created",
        "investigate_perspective",
        "closed",
        "investigate_perspective",
    ]

    only_perspectives = await repo.list_events(qid, kind="investigate_perspective")
    assert len(only_perspectives) == 2
    assert all(e["kind"] == "investigate_perspective" for e in only_perspectives)

    assert await repo.list_events(qid, kind="no_such_kind") == []


# ---- v025 主动复访:set_revisit / due ----------------------------------------


@pytest.mark.asyncio
async def test_set_revisit_schedules_and_clears(repos):
    repo, _ = repos
    qid = await repo.create("复访测试")

    updated = await repo.set_revisit(qid, "2026-10-10T00:00:00+00:00", actor="user")
    assert updated["next_revisit_at"] == "2026-10-10T00:00:00+00:00"

    events = await repo.list_events(qid)
    assert events[-1]["kind"] == "revisit_scheduled"
    assert events[-1]["detail"]["next_revisit_at"] == "2026-10-10T00:00:00+00:00"

    cleared = await repo.set_revisit(qid, None, actor="user")
    assert cleared["next_revisit_at"] is None
    events = await repo.list_events(qid)
    assert events[-1]["kind"] == "revisit_cleared"


@pytest.mark.asyncio
async def test_set_revisit_rejects_terminal_status(repos):
    repo, _ = repos
    qid = await repo.create("终态问题")
    await repo.close(qid, "falsified")
    with pytest.raises(ValueError, match="falsified"):
        await repo.set_revisit(qid, "2026-10-10T00:00:00+00:00")


@pytest.mark.asyncio
async def test_set_revisit_missing_question_raises(repos):
    repo, _ = repos
    with pytest.raises(KeyError):
        await repo.set_revisit("q-missing", "2026-10-10T00:00:00+00:00")


@pytest.mark.asyncio
async def test_due_returns_only_due_open_questions(repos):
    repo, _ = repos
    q_due = await repo.create("到期问题")
    q_future = await repo.create("未到期问题")
    q_plain = await repo.create("无计划问题")
    await repo.set_revisit(q_due, "2026-10-01T00:00:00+00:00")
    await repo.set_revisit(q_future, "2026-12-01T00:00:00+00:00")

    due = await repo.due("2026-10-03T00:00:00+00:00")
    ids = [q["id"] for q in due]
    assert q_due in ids
    assert q_future not in ids
    assert q_plain not in ids


@pytest.mark.asyncio
async def test_due_excludes_terminal_and_parked_included(repos):
    repo, _ = repos
    q_dead = await repo.create("已关闭问题")
    await repo.set_revisit(q_dead, "2026-10-01T00:00:00+00:00")
    await repo.close(q_dead, "superseded")

    q_parked = await repo.create("搁置问题")
    await repo.set_revisit(q_parked, "2026-10-01T00:00:00+00:00")

    due = await repo.due("2026-10-03T00:00:00+00:00")
    ids = [q["id"] for q in due]
    assert q_dead not in ids
    assert q_parked in ids


@pytest.mark.asyncio
async def test_due_limit_and_order(repos):
    repo, _ = repos
    for i in range(5):
        qid = await repo.create(f"批量{i}")
        await repo.set_revisit(qid, f"2026-10-0{i + 1}T00:00:00+00:00")
    due = await repo.due("2026-10-03T00:00:00+00:00", limit=2)
    assert len(due) == 2
    assert due[0]["next_revisit_at"] <= due[1]["next_revisit_at"]
