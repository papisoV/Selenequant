"""Tests for ResearchMissionRepository — B1 state machine + B2 activity stream.

垂直切片:Keltner Mission 落库链路的存储层行为锁。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_missions import (
    PHASE_INVESTIGATION,
    PHASE_NEEDS_INPUT,
    PHASE_PARKED,
    PHASE_RESOLVED,
    PHASE_VALIDATION,
    ResearchMissionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        # strategy 主体存在性门(2026-10-13)要求 subject_id 命中 strategies 表。
        # 种一行真策略,让本文件的合法用例通过门;用假 id 的新用例会因此正确报错。
        await db.execute(
            "INSERT INTO strategies (id, name, type) VALUES (?, ?, ?)",
            ("strat-6395c312", "Keltner V2", "custom"),
        )
        yield ResearchMissionRepository(db)


async def _keltner_mission(repo: ResearchMissionRepository, dedup: bool = True) -> str:
    return str(
        await repo.create(
            title="Keltner V2 配置漂移调查",
            question="SOL 4h 实跑亏损是配置漂移还是框架失效?",
            subject_type="strategy",
            subject_id="strat-6395c312",
            dedup=dedup,
            context={
                "backtest_id": "bt-b62d32ac",
                "declared": {"symbol": "BTC/USDT", "interval": "1h"},
                "as_run": {"symbol": "SOL/USDT", "interval": "4h"},
            },
        )
    )


@pytest.mark.asyncio
async def test_create_returns_id_and_investigation_phase(
    repo: ResearchMissionRepository,
):
    mid = await _keltner_mission(repo)
    assert mid.startswith("mis-")
    mission = await repo.get(mid)
    assert mission is not None
    assert mission["phase"] == PHASE_INVESTIGATION
    assert mission["context"]["backtest_id"] == "bt-b62d32ac"
    # mission_opened 活动已入流
    activity = await repo.list_activity(mid)
    assert any(a["kind"] == "mission_opened" for a in activity)


@pytest.mark.asyncio
async def test_create_dedups_same_open_question(repo: ResearchMissionRepository):
    """同 (subject, question) 且未 resolved/parked → 复用既有 id,不新建。

    2026-10-01 实测:同题 mission 被开了 4 条(去重此前只存在于 pa_mission_sensor,
    绕过它的路径不受保护)。此测试锁住下沉到 create() 后的不变量。
    """
    first = await _keltner_mission(repo)
    second = await _keltner_mission(repo)
    assert second == first, "同题未闭合 mission 必须复用,不得新建"

    rows = await repo._db.execute("SELECT COUNT(*) AS n FROM research_missions")
    assert rows[0]["n"] == 1

    # 闭合后不再复用(resolved 属于 find_open_by_question 的排除集)
    await repo.transition(first, PHASE_VALIDATION)
    await repo.transition(first, PHASE_RESOLVED)
    reopened = await _keltner_mission(repo)
    assert reopened != first


@pytest.mark.asyncio
async def test_create_dedup_can_be_disabled(repo: ResearchMissionRepository):
    """显式 dedup=False 时允许同题并存(调用方自担唯一性)。"""
    first = await _keltner_mission(repo)
    second = await _keltner_mission(repo, dedup=False)
    assert second != first
    rows = await repo._db.execute("SELECT COUNT(*) AS n FROM research_missions")
    assert rows[0]["n"] == 2


@pytest.mark.asyncio
async def test_transition_legal_path(repo: ResearchMissionRepository):
    mid = await _keltner_mission(repo)
    m = await repo.transition(mid, PHASE_NEEDS_INPUT, actor="selene")
    assert m["phase"] == PHASE_NEEDS_INPUT
    m = await repo.transition(
        mid, PHASE_VALIDATION, actor="user", note="approve revalidate"
    )
    assert m["phase"] == PHASE_VALIDATION
    m = await repo.transition(mid, PHASE_RESOLVED, actor="selene")
    assert m["phase"] == PHASE_RESOLVED
    assert m["resolved_at"] is not None


@pytest.mark.asyncio
async def test_transition_rejects_illegal_edge(repo: ResearchMissionRepository):
    mid = await _keltner_mission(repo)
    # investigation 直接 resolved 是非法边(必须先经过 needs_input/user 决策)
    with pytest.raises(ValueError, match="illegal transition"):
        await repo.transition(mid, PHASE_RESOLVED)
    # resolved 是终态
    await repo.transition(mid, PHASE_NEEDS_INPUT)
    await repo.transition(mid, PHASE_VALIDATION)
    await repo.transition(mid, PHASE_RESOLVED)
    with pytest.raises(ValueError, match="illegal transition"):
        await repo.transition(mid, PHASE_INVESTIGATION)


@pytest.mark.asyncio
async def test_transition_rejects_unknown_phase(repo: ResearchMissionRepository):
    mid = await _keltner_mission(repo)
    with pytest.raises(ValueError, match="unknown phase"):
        await repo.transition(mid, "archive")


@pytest.mark.asyncio
async def test_transition_guarded_against_concurrent_phase_change(
    repo: ResearchMissionRepository,
):
    """external-research-project guarded transition 形态: read-then-write 间的并发跳变被拒.

    竞态窗口在 transition() 内部 get() 与 UPDATE 之间: 让第一次 get() 返回
    旧快照(investigation), 并发写者此时直接 SQL 把 phase 推到 resolved;
    guarded UPDATE (WHERE phase=investigation) 不命中变 no-op, transition
    必须报冲突而非静默把 resolved 覆盖成 needs_input(绕过用户决策)。
    """
    mid = await _keltner_mission(repo)
    real_get = repo.get
    snapshot = await real_get(mid)  # T0 读: phase=investigation

    calls = {"n": 0}

    async def stale_then_real(mission_id: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return snapshot  # transition() 内的首读 = 竞态前的旧快照
        return await real_get(mission_id)

    repo.get = stale_then_real  # type: ignore[method-assign]
    try:
        # T1 并发写者绕过 repo 状态机直改 DB(investigation -> resolved)
        await repo._db.execute(
            "UPDATE research_missions SET phase = ? WHERE id = ?",
            (PHASE_RESOLVED, mid),
        )
        # T2 本 repo 尝试 investigation -> needs_input 的合法边;
        # guarded UPDATE 的 WHERE phase='investigation' 不命中, 必须报冲突
        with pytest.raises(ValueError, match="phase changed concurrently"):
            await repo.transition(mid, PHASE_NEEDS_INPUT)
    finally:
        repo.get = real_get  # type: ignore[method-assign]

    # DB 现状未被第二次 transition 破坏(仍是 resolved)
    m = await real_get(mid)
    assert m is not None
    assert m["phase"] == PHASE_RESOLVED
    # 冲突活动行已入流, 记录 attempted/actual 三元组
    activity = await repo.list_activity(mid)
    conflict = [a for a in activity if a["kind"] == "phase_transition_conflict"]
    assert len(conflict) == 1
    detail = conflict[0]["detail"]
    assert detail["attempted_from"] == PHASE_INVESTIGATION
    assert detail["attempted_to"] == PHASE_NEEDS_INPUT
    assert detail["actual"] == PHASE_RESOLVED


@pytest.mark.asyncio
async def test_activity_stream_append_only_and_replay(
    repo: ResearchMissionRepository,
):
    mid = await _keltner_mission(repo)
    await repo.transition(mid, PHASE_NEEDS_INPUT, actor="selene")
    await repo.transition(mid, PHASE_VALIDATION, actor="user", note="approve")
    stream = await repo.list_activity(mid)
    kinds = [a["kind"] for a in stream]
    assert kinds == ["mission_opened", "phase_transition", "phase_transition"]
    # 时间正序
    assert stream == sorted(stream, key=lambda a: (a["occurred_at"], a["id"]))


@pytest.mark.asyncio
async def test_recent_activity_reads_the_tail_not_the_head(
    repo: ResearchMissionRepository,
):
    """``list_recent_activity`` 取最新 N 行;``list_activity`` 取最旧 N 行。

    两者窗口相反,是刻意的:回放要头,消费活动流末尾的调用方要尾。
    把 ``list_activity`` 当"最近"用会在超过 N 行后静默读到过期数据。
    """
    mid = await _keltner_mission(repo)
    for i in range(10):
        await repo.append_activity(mid, actor="system", kind=f"noise_{i}")

    head = await repo.list_activity(mid, limit=3)
    tail = await repo.list_recent_activity(mid, limit=3)

    assert [a["kind"] for a in head] == ["mission_opened", "noise_0", "noise_1"]
    assert [a["kind"] for a in tail] == ["noise_7", "noise_8", "noise_9"]
    # 两侧都仍是时间正序,调用方不必自己反向。
    assert tail == sorted(tail, key=lambda a: (a["occurred_at"], a["id"]))


@pytest.mark.asyncio
async def test_has_activity_detail_is_not_windowed_by_stream_length(
    repo: ResearchMissionRepository,
):
    """存在性检查不受活动流长度影响 —— 幂等的唯一可靠问法。"""
    mid = await _keltner_mission(repo)
    await repo.append_activity(
        mid, actor="system", kind="revisit_due", detail={"key": "q|t"}
    )
    for i in range(700):
        await repo.append_activity(mid, actor="system", kind="noise", detail={"n": i})

    assert await repo.has_activity_detail(
        mid, kind="revisit_due", detail_key="key", detail_value="q|t"
    )
    assert not await repo.has_activity_detail(
        mid, kind="revisit_due", detail_key="key", detail_value="q|other"
    )
    assert not await repo.has_activity_detail(
        mid, kind="user_note", detail_key="key", detail_value="q|t"
    )


@pytest.mark.asyncio
async def test_latest_activity_queries_survive_a_long_stream(
    repo: ResearchMissionRepository,
):
    """ "最近一次 X"必须与流长度无关 —— 否则界面会把"有"显示成"从未"."""
    mid = await _keltner_mission(repo)
    await repo.append_activity(
        mid, actor="user", kind="user_note", detail={"note": "最早的留言"}
    )
    await repo.append_activity(mid, actor="selene", kind="drift_check", detail={"n": 1})
    for i in range(700):
        await repo.append_activity(mid, actor="system", kind="noise", detail={"n": i})
    await repo.append_activity(
        mid, actor="user", kind="user_note", detail={"note": "最新的留言"}
    )
    await repo.append_activity(mid, actor="selene", kind="drift_check", detail={"n": 2})

    drift = await repo.latest_activity_of_kind(mid, "drift_check")
    assert drift is not None and drift["detail"]["n"] == 2
    assert await repo.latest_activity_of_kind(mid, "never_happened") is None

    user = await repo.latest_user_activity(mid)
    assert user is not None and user["detail"]["note"] == "最新的留言"


@pytest.mark.asyncio
async def test_outcome_recorded_writes_json(repo: ResearchMissionRepository):
    mid = await _keltner_mission(repo)
    await repo.transition(mid, PHASE_NEEDS_INPUT)
    await repo.transition(mid, PHASE_VALIDATION)
    await repo.record_outcome(
        mid,
        {
            "verdict": "REVALIDATED",
            "oos_return": 0.12,
            "memory_entry": "Keltner V2 配置修正后 OOS 为正",
        },
    )
    mission = await repo.get(mid)
    assert mission is not None
    assert mission["outcome"]["verdict"] == "REVALIDATED"
    assert any(a["kind"] == "outcome_recorded" for a in await repo.list_activity(mid))


@pytest.mark.asyncio
async def test_outcome_recorded_twice_rejected(repo: ResearchMissionRepository):
    """One-shot 语义早拒: 第二次 record_outcome 报错, 首份 outcome 不被覆写."""
    mid = await _keltner_mission(repo)
    first = {"verdict": "REVALIDATED"}
    await repo.record_outcome(mid, first)
    with pytest.raises(ValueError, match="outcome already recorded"):
        await repo.record_outcome(mid, {"verdict": "FRAMEWORK_SUSPECT"})
    mission = await repo.get(mid)
    assert mission is not None
    assert mission["outcome"] == first


@pytest.mark.asyncio
async def test_outcome_recorded_guarded_against_concurrent_write(
    repo: ResearchMissionRepository,
):
    """Guarded UPDATE: 竞态窗口内已有 outcome 落库时第二写必须报冲突.

    竞态窗口在 record_outcome() 内部 get() 与 UPDATE 之间: 让第一次 get()
    返回旧快照(outcome={}), 并发写者此时直接 SQL 落了首份 outcome;
    guarded UPDATE (WHERE outcome_json='{}') 不命中 → 报冲突而非静默覆写。
    """
    mid = await _keltner_mission(repo)
    real_get = repo.get
    snapshot = await real_get(mid)

    calls = {"n": 0}

    async def stale_then_real(mission_id: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return snapshot
        return await real_get(mission_id)

    repo.get = stale_then_real  # type: ignore[method-assign]
    try:
        await repo._db.execute(
            "UPDATE research_missions SET outcome_json = ? WHERE id = ?",
            ('{"verdict": "PARKED"}', mid),
        )
        with pytest.raises(ValueError, match="outcome already recorded"):
            await repo.record_outcome(mid, {"verdict": "REVALIDATED"})
    finally:
        repo.get = real_get  # type: ignore[method-assign]

    mission = await real_get(mid)
    assert mission is not None
    assert mission["outcome"] == {"verdict": "PARKED"}


@pytest.mark.asyncio
async def test_find_open_by_question_uniqueness(repo: ResearchMissionRepository):
    mid = await _keltner_mission(repo)
    found = await repo.find_open_by_question(
        "strategy", "strat-6395c312", "SOL 4h 实跑亏损是配置漂移还是框架失效?"
    )
    assert found is not None and found["id"] == mid
    # 不同 question 不命中
    other = await repo.find_open_by_question("strategy", "strat-6395c312", "另一个问题")
    assert other is None
    # resolved 后不再算 open
    await repo.transition(mid, PHASE_NEEDS_INPUT)
    await repo.transition(mid, PHASE_VALIDATION)
    await repo.transition(mid, PHASE_RESOLVED)
    gone = await repo.find_open_by_question(
        "strategy", "strat-6395c312", "SOL 4h 实跑亏损是配置漂移还是框架失效?"
    )
    assert gone is None


@pytest.mark.asyncio
async def test_list_missions_filters(repo: ResearchMissionRepository):
    await _keltner_mission(repo)
    await repo.create(
        title="第二条",
        question="q2",
        subject_type="market",
        subject_id="BTC/USDT",
    )
    strategy_missions = await repo.list_missions(subject_type="strategy")
    assert len(strategy_missions) == 1
    assert strategy_missions[0]["subject_id"] == "strat-6395c312"
    parked = await repo.list_missions(phase=PHASE_PARKED)
    assert parked == []


@pytest.mark.asyncio
async def test_update_context_merge(repo: ResearchMissionRepository):
    mid = await _keltner_mission(repo)
    await repo.update_context(mid, {"evidence": ["bt-b62d32ac"]})
    mission = await repo.get(mid)
    assert mission is not None
    assert mission["context"]["backtest_id"] == "bt-b62d32ac"  # 旧键保留
    assert mission["context"]["evidence"] == ["bt-b62d32ac"]  # 新键并入


@pytest.mark.asyncio
async def test_append_activity_rejects_unknown_actor(repo: ResearchMissionRepository):
    mid = await _keltner_mission(repo)
    with pytest.raises(ValueError, match="unknown actor"):
        await repo.append_activity(mid, actor="robot", kind="noop")


# -- 主体存在性门(2026-10-13:治本 —— 探针名/回测 id 静默成为 strategy 主体) -------


@pytest.mark.asyncio
async def test_strategy_subject_must_be_registered(repo: ResearchMissionRepository):
    """strategy 硬门:subject_id 不在 strategies 表 → ValueError,不落库。

    真实事故:``entry-delay-ic-probe``(探针名)、``bt-b62d32ac``(回测 id)曾
    静默成为 strategy 主体,让驾驶舱 State 端点 404。
    """
    with pytest.raises(ValueError, match="不是任何已注册策略"):
        await repo.create(
            title="探针误挂",
            question="q",
            subject_type="strategy",
            subject_id="entry-delay-ic-probe",
        )
    rows = await repo._db.execute("SELECT COUNT(*) AS n FROM research_missions")
    assert rows[0]["n"] == 0, "被拒的 mission 不得有任何残留行"


@pytest.mark.asyncio
async def test_strategy_subject_registered_passes(repo: ResearchMissionRepository):
    """种子策略 id 能过门(证硬门不是无差别拒绝)。"""
    mid = await _keltner_mission(repo)
    mission = await repo.get(mid)
    assert mission is not None
    assert mission["subject_id"] == "strat-6395c312"


@pytest.mark.asyncio
async def test_market_subject_slash_and_bare_forms_pass(
    repo: ResearchMissionRepository,
):
    """market 半门:SYMBOL 与 SYMBOL/QUOTE 两种写法都放行(anchor 侧本就混用)。"""
    await repo.create(
        title="裸 ticker", question="q1", subject_type="market", subject_id="BTC"
    )
    await repo.create(
        title="带报价", question="q2", subject_type="market", subject_id="BTC/USDT"
    )
    rows = await repo._db.execute(
        "SELECT COUNT(*) AS n FROM research_missions WHERE subject_type='market'"
    )
    assert rows[0]["n"] == 2


@pytest.mark.asyncio
async def test_market_subject_rejects_freetext(repo: ResearchMissionRepository):
    """market 半门:含空格/整句的自由文本被拒(明显不是标的)。"""
    for bad in ("", "   ", "not a symbol", "跌破前低怎么办?"):
        with pytest.raises(ValueError, match="不是合法标的形态"):
            await repo.create(
                title="t", question="q", subject_type="market", subject_id=bad
            )


@pytest.mark.asyncio
async def test_event_and_system_subjects_are_free(repo: ResearchMissionRepository):
    """event/system 是显式自由主体,无登记表可查 → 放行任意非空 id。"""
    for st, sid in (("event", "batch3-live-verify"), ("system", "sys-anything-goes")):
        mid = await repo.create(
            title="t", question="q", subject_type=st, subject_id=sid
        )
        mission = await repo.get(mid)
        assert mission is not None
        assert mission["subject_type"] == st
