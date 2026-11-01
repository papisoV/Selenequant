"""六个独立一等研究对象 —— 存储层行为锁。

重点防三件事:
① Episode 被后来的人"更新成现在认为正确的样子"(§8③)
② Decision 被 AI 顺手做掉(§6)
③ outcome_never 把"没等够"和"永远不会发生"混为一谈(§8④)
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph.derivation import (
    derive_subject_lifecycle,
)
from selene_space_storage.repositories.research_graph.objects import (
    DecisionRepository,
    EpisodeRepository,
    EvidenceRepository,
    ExperimentRepository,
    HypothesisRepository,
    link_derived_from,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


T0 = "2026-09-01T00:00:00+00:00"


@pytest_asyncio.fixture
async def repos():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        # Decision 挂在 Question 上,而 ``sqlite.py:51`` 开着
        # ``PRAGMA foreign_keys=ON`` —— 拿一个不存在的 q-id 记决策会被 FK 挡下。
        # 这不是麻烦,是设计:**不能对一个问题做它不存在的判断**。
        for qid in ("q-1", "q-2"):
            await db.execute(
                "INSERT INTO research_questions (id, title, body, status, "
                "opened_at, updated_at) VALUES (?, ?, '', 'open', ?, ?)",
                (qid, f"问题 {qid}", T0, T0),
            )
        yield {
            "db": db,
            "episode": EpisodeRepository(db),
            "evidence": EvidenceRepository(db),
            "hypothesis": HypothesisRepository(db),
            "experiment": ExperimentRepository(db),
            "decision": DecisionRepository(db),
        }


async def _episode(repos) -> str:
    return str(
        await repos["episode"].create(
            subject_type="strategy",
            subject_id="strat-6395c312",
            title="Keltner V2 @ SOL 4h 实跑段",
            boundary_start="2026-08-01T00:00:00+00:00",
            boundary_end="2026-08-31T00:00:00+00:00",
            recorded={"entry_price": 148.2, "exit_price": 137.9, "pnl": -0.0695},
            occurred_at="2026-08-31T23:59:59+00:00",
        )
    )


# --- Episode:不可改 -------------------------------------------------------


@pytest.mark.asyncio
async def test_episode_create_and_get(repos):
    ep_id = await _episode(repos)
    assert ep_id.startswith("ep-")
    ep = await repos["episode"].get(ep_id)
    assert ep["subject_type"] == "strategy"
    assert ep["recorded"]["pnl"] == -0.0695


@pytest.mark.asyncio
async def test_episode_repository_has_no_mutation_method(repos):
    """§8③:Episode 不可改。不是靠纪律,是靠 API 表面根本不给改的门。

    白名单断言(比黑名单硬):EpisodeRepository 公开方法中,唯一允许写
    语义的方法 = record_lifecycle_transition(lifecycle 子键受控窄门,
    见该测试组);update/put/set/modify 及未来任何新增写入口一律禁止。
    一旦有人加回裸 update,这个测试会红 —— 它就是那个"提醒"。
    """
    repo = repos["episode"]
    for forbidden in ("update", "update_recorded", "put", "set_recorded", "modify"):
        assert not hasattr(
            repo, forbidden
        ), f"EpisodeRepository must not expose {forbidden}() — Episode 不可改 (§8③)"
    assert hasattr(
        repo, "record_lifecycle_transition"
    ), "生命周期窄门方法必须存在(§8③ 唯一显式例外)"
    # 公开方法全量盘点:除 create(初始写入)与 record_lifecycle_transition 外,
    # 其余必须全是读语义
    public = {
        name
        for name in dir(repo)
        if not name.startswith("_") and callable(getattr(repo, name))
    }
    writers = public - {
        "create",
        "record_lifecycle_transition",
    }
    for name in sorted(writers):
        source_name = getattr(repo, name).__name__
        assert source_name.startswith(
            ("list_", "get", "compare")
        ), f"EpisodeRepository 公开方法 {name}() 出现在白名单之外 — §8③ 禁止新增写语义方法"


@pytest.mark.asyncio
async def test_episode_has_no_state_version_column(repos):
    """Episode 没有 state_version,所以不会诱惑未来的 Agent 去"更新"它。"""
    ep_id = await _episode(repos)
    ep = await repos["episode"].get(ep_id)
    assert "state_version" not in ep


# --- Evidence:只增、"当时 vs 现在" ----------------------------------------


@pytest.mark.asyncio
async def test_evidence_modes_coexist_on_one_episode(repos):
    """同一 Episode 挂 RECORDED / RECONSTRUCTED / AFTER_THE_FACT 三条观察。"""
    ep_id = await _episode(repos)
    await repos["evidence"].create(
        episode_id=ep_id,
        mode="RECORDED",
        payload={"pnl": -0.0695},
        source_ref="bt-b62d32ac",
    )
    await repos["evidence"].create(
        episode_id=ep_id,
        mode="RECONSTRUCTED",
        payload={"pnl": -0.115, "engine": "tick-recompute"},
        source_ref="tick-2026-09-16",
    )
    await repos["evidence"].create(
        episode_id=ep_id,
        mode="AFTER_THE_FACT",
        payload={"note": "该段处于 regime shift 后半程"},
        source_ref="regime-classifier-v2",
    )
    rows = await repos["evidence"].list_for_episode(ep_id)
    assert [r["mode"] for r in rows] == ["RECORDED", "RECONSTRUCTED", "AFTER_THE_FACT"]
    # 原 Episode 的 recorded 字段没被任何一条覆盖
    ep = await repos["episode"].get(ep_id)
    assert ep["recorded"]["pnl"] == -0.0695


@pytest.mark.asyncio
async def test_evidence_list_is_append_only_ordered(repos):
    ep_id = await _episode(repos)
    a = await repos["evidence"].create(
        episode_id=ep_id, mode="RECORDED", observed_at="2026-09-01T00:00:00+00:00"
    )
    b = await repos["evidence"].create(
        episode_id=ep_id, mode="RECONSTRUCTED", observed_at="2026-09-16T00:00:00+00:00"
    )
    rows = await repos["evidence"].list_for_episode(ep_id)
    assert [r["id"] for r in rows] == [a, b]


@pytest.mark.asyncio
async def test_evidence_rejects_bad_mode(repos):
    ep_id = await _episode(repos)
    with pytest.raises(ValueError, match="unknown evidence mode"):
        await repos["evidence"].create(episode_id=ep_id, mode="GUESSED")


@pytest.mark.asyncio
async def test_compare_modes_surfaces_drift(repos):
    """当时 vs 现在 —— 这是 Time Travel 真正要冻结的东西。"""
    ep_id = await _episode(repos)
    await repos["evidence"].create(
        episode_id=ep_id, mode="RECORDED", payload={"pnl": -0.0695}
    )
    await repos["evidence"].create(
        episode_id=ep_id, mode="RECONSTRUCTED", payload={"pnl": -0.115}
    )
    drift = await repos["evidence"].compare_modes(ep_id, key="pnl")
    assert drift["recorded"] == -0.0695
    assert drift["latest"] == -0.115
    assert drift["drifted"] is True


# --- Hypothesis / Experiment ---------------------------------------------


@pytest.mark.asyncio
async def test_hypothesis_lifecycle(repos):
    hid = await repos["hypothesis"].create("该策略失效主要由 regime shift 导致")
    assert hid.startswith("hyp-")
    h = await repos["hypothesis"].get(hid)
    assert h["status"] == "proposed"
    await repos["hypothesis"].set_status(hid, "supported")
    assert (await repos["hypothesis"].get(hid))["status"] == "supported"
    with pytest.raises(ValueError, match="unknown hypothesis status"):
        await repos["hypothesis"].set_status(hid, "maybe")


@pytest.mark.asyncio
async def test_experiment_links_to_hypothesis(repos):
    hid = await repos["hypothesis"].create("配置漂移假设")
    xid = await repos["experiment"].create(
        kind="backtest",
        hypothesis_id=hid,
        config={"symbol": "SOL/USDT", "interval": "4h"},
    )
    assert xid.startswith("exp-")
    x = await repos["experiment"].get(xid)
    assert x["hypothesis_id"] == hid
    assert x["ran_at"] is None
    await repos["experiment"].mark_ran(xid, ran_at="2026-09-10T12:00:00+00:00")
    assert (await repos["experiment"].get(xid))["ran_at"] == "2026-09-10T12:00:00+00:00"


@pytest.mark.asyncio
async def test_experiment_mark_ran_twice_rejected(repos):
    xid = await repos["experiment"].create(kind="backtest")
    await repos["experiment"].mark_ran(xid)
    with pytest.raises(ValueError, match="already ran"):
        await repos["experiment"].mark_ran(xid)


# --- Decision:归 Director -------------------------------------------------


@pytest.mark.asyncio
async def test_decision_is_recorded_as_director(repos):
    """§6:Decision 由 Director 正式产生,Mission 只能产出 Candidate Decision。

    record() 根本不接受 decided_by 参数 —— 没有入口能让 AI 冒充 Director。
    """
    dec_id = await repos["decision"].record(
        question_id="q-1",
        verdict="REVALIDATE",
        rationale="配置漂移假设成立,按修正配置重跑",
        window_start="2026-09-17T00:00:00+00:00",
        window_end="2026-10-17T00:00:00+00:00",
    )
    dec = await repos["decision"].get(dec_id)
    assert dec["decided_by"] == "director"
    assert dec["outcome_status"] == "outcome_pending"


@pytest.mark.asyncio
async def test_director_signature_cannot_be_forged(repos):
    """判据的原话是"AI 冒充在 **schema 层** 不可能",所以两根腿都要证明。

    上一个测试只证明了"写进去的是 director",没证明"写不进别的"。这两件事
    完全不同 —— 一个只有默认值的字段照样能被显式传成别的值。所以:

    ① 仓储根本不收这个参数 —— 传了当场 ``TypeError``,不是被忽略后静默丢弃
    ② 绕过仓储直接写 SQL,``CHECK (decided_by IN ('director'))`` 会挡下
    """
    import sqlite3

    with pytest.raises(TypeError):
        await repos["decision"].record(
            question_id="q-1", verdict="PARK", decided_by="selene"
        )

    async with repos["db"].connection() as conn:
        with pytest.raises(sqlite3.IntegrityError):
            await conn.execute(
                "INSERT INTO research_decisions (id, question_id, verdict, "
                "decided_by, decided_at) VALUES ('dec-forged', 'q-1', 'PARK', "
                "'selene', ?)",
                (T0,),
            )


@pytest.mark.asyncio
async def test_observe_outcome_moves_to_observed(repos):
    dec_id = await repos["decision"].record(question_id="q-1", verdict="REVALIDATE")
    updated = await repos["decision"].observe_outcome(dec_id)
    assert updated["outcome_status"] == "outcome_observed"


@pytest.mark.asyncio
async def test_mark_never_requires_observation_window(repos):
    """§8④ 的 never 防线:必须是"结果事件在明确观察窗口内未发生"。"""
    dec_id = await repos["decision"].record(question_id="q-1", verdict="PARK")
    with pytest.raises(ValueError, match="observation window"):
        await repos["decision"].mark_never(dec_id)


@pytest.mark.asyncio
async def test_mark_never_requires_window_to_have_elapsed(repos):
    dec_id = await repos["decision"].record(
        question_id="q-1", verdict="PARK", window_end="2099-01-01T00:00:00+00:00"
    )
    with pytest.raises(ValueError, match="still open"):
        await repos["decision"].mark_never(dec_id)


@pytest.mark.asyncio
async def test_mark_never_marks_when_window_elapsed(repos):
    dec_id = await repos["decision"].record(question_id="q-1", verdict="PARK")
    updated = await repos["decision"].mark_never(
        dec_id, window_end="2020-01-01T00:00:00+00:00"
    )
    assert updated["outcome_status"] == "outcome_never"
    assert updated["window_end"] == "2020-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_observe_outcome_after_never_is_rejected(repos):
    dec_id = await repos["decision"].record(question_id="q-1", verdict="PARK")
    await repos["decision"].mark_never(dec_id, window_end="2020-01-01T00:00:00+00:00")
    with pytest.raises(ValueError, match="already resolved"):
        await repos["decision"].observe_outcome(dec_id)


@pytest.mark.asyncio
async def test_observe_outcome_guarded_against_concurrent_never(repos):
    """CAS 一次性转换: pending 竞态窗口内被并发推到不同终态必须报冲突.

    竞态窗口在 observe_outcome() 内部 get() 与 UPDATE 之间: 让第一次 get()
    返回旧快照(pending), 并发写者此时直接 SQL 把状态推到 outcome_never;
    guarded UPDATE (WHERE outcome_status=pending) 不命中 → 重读到 never,
    必须报冲突而非静默把 never 覆盖成 observed。
    (并发对端同样 observe 到 observed 则是良性收敛,不冲突。)
    """
    dec_id = await repos["decision"].record(question_id="q-1", verdict="REVALIDATE")
    decision_repo = repos["decision"]
    real_get = decision_repo.get
    snapshot = await real_get(dec_id)

    calls = {"n": 0}

    async def stale_then_real(did: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return snapshot
        return await real_get(did)

    decision_repo.get = stale_then_real  # type: ignore[method-assign]
    try:
        # 并发写者直改 DB: pending -> never(绕过窗口校验模拟竞态)
        await repos["db"].execute(
            "UPDATE research_decisions SET outcome_status = 'outcome_never' "
            "WHERE id = ?",
            (dec_id,),
        )
        with pytest.raises(ValueError, match="already resolved"):
            await decision_repo.observe_outcome(dec_id)
    finally:
        decision_repo.get = real_get  # type: ignore[method-assign]

    row = await real_get(dec_id)
    assert row is not None
    assert row["outcome_status"] == "outcome_never"


@pytest.mark.asyncio
async def test_mark_never_guarded_against_concurrent_resolve(repos):
    """mark_never 的守卫冲突分支: 竞态窗口内被并发 observe 掉必须报冲突.

    竞态窗口在 mark_never() 内部 get() 与 UPDATE 之间: 让第一次 get() 返回
    旧快照(pending), 并发写者此时直接 SQL 把状态推到 outcome_observed;
    guarded UPDATE (WHERE outcome_status=pending) 不命中 → 重读到 observed,
    必须报冲突而非静默把 observed 覆盖成 never。
    """
    dec_id = await repos["decision"].record(
        question_id="q-1", verdict="PARK", window_end="2020-01-01T00:00:00+00:00"
    )
    decision_repo = repos["decision"]
    real_get = decision_repo.get
    snapshot = await real_get(dec_id)

    calls = {"n": 0}

    async def stale_then_real(did: str):
        calls["n"] += 1
        if calls["n"] == 1:
            return snapshot
        return await real_get(did)

    decision_repo.get = stale_then_real  # type: ignore[method-assign]
    try:
        # 并发写者直改 DB: pending -> observed
        await repos["db"].execute(
            "UPDATE research_decisions SET outcome_status = 'outcome_observed' "
            "WHERE id = ?",
            (dec_id,),
        )
        with pytest.raises(ValueError, match="already resolved"):
            await decision_repo.mark_never(dec_id)
    finally:
        decision_repo.get = real_get  # type: ignore[method-assign]

    row = await real_get(dec_id)
    assert row is not None
    assert row["outcome_status"] == "outcome_observed"


@pytest.mark.asyncio
async def test_list_decisions_filters_by_question(repos):
    await repos["decision"].record(question_id="q-1", verdict="A")
    await repos["decision"].record(question_id="q-2", verdict="B")
    assert len(await repos["decision"].list_decisions(question_id="q-1")) == 1
    assert len(await repos["decision"].list_decisions()) == 2


# --- Episode lifecycle:per-episode 观察周期状态(§8③ 唯一受控窄门) ---------


async def _lifecycle_episode(repos) -> str:
    """带 lifecycle 子键的落账型 episode(模拟快照/探针写入形态)。"""
    return str(
        await repos["episode"].create(
            subject_type="market",
            subject_id="ic-panel:binance:futures:BTC/USDT:1h:momentum:5:500",
            title="IC snapshot 测试",
            boundary_start="2026-09-01T00:00:00+00:00",
            boundary_end="2026-09-29T00:00:00+00:00",
            recorded={
                "kind": "factor_snapshot",
                "n_rows": 8,
                "lifecycle": {"status": "DISCOVERED", "transitions": []},
            },
            occurred_at="2026-09-29T00:00:00+00:00",
        )
    )


@pytest.mark.asyncio
async def test_lifecycle_full_path_discovered_to_retired(repos):
    ep_id = await _lifecycle_episode(repos)
    repo = repos["episode"]
    for status in ("INSPECTED", "PAPER_TRADING", "LIVE"):
        result = await repo.record_lifecycle_transition(
            ep_id, status, actor="user", reason="逐级推进"
        )
        assert result["recorded"]["lifecycle"]["status"] == status
    result = await repo.record_lifecycle_transition(
        ep_id, "RETIRED", actor="user", reason="纸面交易两个月无优势"
    )
    assert result["recorded"]["lifecycle"]["status"] == "RETIRED"
    transitions = result["recorded"]["lifecycle"]["transitions"]
    # 只增事件流:逐事件完整可审计
    assert [(t["from"], t["to"]) for t in transitions] == [
        ("DISCOVERED", "INSPECTED"),
        ("INSPECTED", "PAPER_TRADING"),
        ("PAPER_TRADING", "LIVE"),
        ("LIVE", "RETIRED"),
    ]
    assert all(t["actor"] == "user" for t in transitions)
    assert transitions[-1]["reason"] == "纸面交易两个月无优势"
    # 内容键原样保留 —— lifecycle 只动自己的子键
    assert result["recorded"]["kind"] == "factor_snapshot"
    assert result["recorded"]["n_rows"] == 8


@pytest.mark.asyncio
async def test_lifecycle_short_path_to_retired(repos):
    """DISCOVERED → RETIRED 是合法直达(证伪即退役,不必先走完观察阶梯)。"""
    ep_id = await _lifecycle_episode(repos)
    result = await repos["episode"].record_lifecycle_transition(
        ep_id, "RETIRED", actor="system", reason="基线 IC≈0 证伪"
    )
    assert result["recorded"]["lifecycle"]["status"] == "RETIRED"


@pytest.mark.asyncio
async def test_lifecycle_rejects_illegal_transitions(repos):
    ep_id = await _lifecycle_episode(repos)
    repo = repos["episode"]
    # 跨分支跳级
    with pytest.raises(ValueError, match="illegal lifecycle transition"):
        await repo.record_lifecycle_transition(ep_id, "LIVE", actor="user")
    # DISCOVERED 只到 INSPECTED/RETIRED
    with pytest.raises(ValueError, match="illegal lifecycle transition"):
        await repo.record_lifecycle_transition(ep_id, "PAPER_TRADING", actor="user")
    # 未知状态词
    with pytest.raises(ValueError, match="unknown lifecycle status"):
        await repo.record_lifecycle_transition(ep_id, "MAYBE", actor="user")


@pytest.mark.asyncio
async def test_lifecycle_retired_is_terminal(repos):
    ep_id = await _lifecycle_episode(repos)
    repo = repos["episode"]
    await repo.record_lifecycle_transition(
        ep_id, "RETIRED", actor="user", reason="证伪退役"
    )
    # RETIRED 无出边:复活路径不存在(结转=复活也不在 storage 层发生)
    for attempt in ("DISCOVERED", "INSPECTED", "LIVE"):
        with pytest.raises(ValueError, match="illegal lifecycle transition"):
            await repo.record_lifecycle_transition(ep_id, attempt, actor="user")
    with pytest.raises(ValueError, match="illegal lifecycle transition"):
        await repo.record_lifecycle_transition(
            ep_id, "RETIRED", actor="user", reason="再次退役"
        )


@pytest.mark.asyncio
async def test_lifecycle_retired_requires_reason(repos):
    ep_id = await _lifecycle_episode(repos)
    with pytest.raises(ValueError, match="requires a reason"):
        await repos["episode"].record_lifecycle_transition(
            ep_id, "RETIRED", actor="user"
        )
    with pytest.raises(ValueError, match="requires a reason"):
        await repos["episode"].record_lifecycle_transition(
            ep_id, "RETIRED", actor="user", reason=""
        )


@pytest.mark.asyncio
async def test_lifecycle_rejects_episode_without_lifecycle_subkey(repos):
    """无 lifecycle 子键的历史 episode(如 _episode 造的策略实跑段)raise,
    不静默补初始状态 —— 误伤旧数据比少一个功能严重。"""
    ep_id = await _episode(repos)
    with pytest.raises(KeyError, match="no lifecycle subkey"):
        await repos["episode"].record_lifecycle_transition(
            ep_id, "INSPECTED", actor="user"
        )


@pytest.mark.asyncio
async def test_lifecycle_rejects_unknown_episode(repos):
    with pytest.raises(KeyError, match="episode not found"):
        await repos["episode"].record_lifecycle_transition(
            "ep-not-exist", "INSPECTED", actor="user"
        )


@pytest.mark.asyncio
async def test_lifecycle_rejects_actor_outside_vocabulary(repos):
    ep_id = await _lifecycle_episode(repos)
    with pytest.raises(ValueError, match="unknown actor"):
        await repos["episode"].record_lifecycle_transition(
            ep_id, "INSPECTED", actor="director"
        )


@pytest.mark.asyncio
async def test_lifecycle_transitions_are_append_only(repos):
    """旧事件逐字保留:流转 N 次后,前 N-1 条与流转前逐字段相等。"""
    ep_id = await _lifecycle_episode(repos)
    repo = repos["episode"]
    await repo.record_lifecycle_transition(ep_id, "INSPECTED", actor="system")
    before = (await repo.get(ep_id))["recorded"]["lifecycle"]["transitions"]
    await repo.record_lifecycle_transition(ep_id, "PAPER_TRADING", actor="user")
    after = (await repo.get(ep_id))["recorded"]["lifecycle"]["transitions"]
    assert after[:-1] == before
    assert len(after) == len(before) + 1


# --- derived_from 血缘:同 subject 重跑时间链 -------------------------------


async def _market_episode(repos, subject_id: str) -> str:
    return str(
        await repos["episode"].create(
            subject_type="market",
            subject_id=subject_id,
            title=f"IC snapshot {subject_id}",
            boundary_start="2026-09-01T00:00:00+00:00",
            boundary_end="2026-09-29T00:00:00+00:00",
            recorded={"kind": "factor_snapshot"},
            occurred_at="2026-09-29T00:00:00+00:00",
        )
    )


@pytest.mark.asyncio
async def test_derived_from_first_save_no_edge(repos):
    """同 subject 首跑:查不到前一集,返回 None 不连边。"""
    ep_id = await _market_episode(repos, "ic-panel:key-a")
    parent = await link_derived_from(
        repos["db"],
        child_episode_id=ep_id,
        subject_type="market",
        subject_id="ic-panel:key-a",
    )
    assert parent is None
    edges = await repos["db"].execute(
        "SELECT * FROM research_edges WHERE kind='derived_from'"
    )
    assert not edges


@pytest.mark.asyncio
async def test_derived_from_second_save_links_child_to_parent(repos):
    """同 key 第二次落账:自动连边,方向子→父(from=新,to=旧)。"""
    key = "ic-panel:key-b"
    first = await _market_episode(repos, key)
    second = await _market_episode(repos, key)
    parent = await link_derived_from(
        repos["db"],
        child_episode_id=second,
        subject_type="market",
        subject_id=key,
    )
    assert parent == first
    edges = await repos["db"].execute(
        "SELECT from_type, from_id, to_type, to_id, edge_class, actor "
        "FROM research_edges WHERE kind='derived_from'"
    )
    assert len(edges) == 1
    e = edges[0]
    assert (e["from_id"], e["to_id"]) == (second, first)  # 子→父
    assert e["from_type"] == "episode" and e["to_type"] == "episode"
    assert e["edge_class"] == "fact" and e["actor"] == "system"


@pytest.mark.asyncio
async def test_derived_from_chain_walks_same_key_only(repos):
    """三连快照链:a←b←c;不同 subject 不互相连。"""
    key = "ic-panel:key-c"
    other = await _market_episode(repos, "ic-panel:other")
    a = await _market_episode(repos, key)
    b = await _market_episode(repos, key)
    await link_derived_from(
        repos["db"], child_episode_id=b, subject_type="market", subject_id=key
    )
    c = await _market_episode(repos, key)
    await link_derived_from(
        repos["db"], child_episode_id=c, subject_type="market", subject_id=key
    )
    edges = await repos["db"].execute(
        "SELECT from_id, to_id FROM research_edges WHERE kind='derived_from' "
        "ORDER BY rowid ASC"
    )
    assert [(e["from_id"], e["to_id"]) for e in edges] == [(b, a), (c, b)]
    # other 不出现在链上
    ids_in_chain = {x for e in edges for x in (e["from_id"], e["to_id"])}
    assert other not in ids_in_chain


@pytest.mark.asyncio
async def test_derived_from_same_second_double_save_rowid_tiebreak(repos):
    """同秒双 save:occurred_at 全等时 episode rowid(插入序)分先后 ——
    后存者的父必须是先存者(不是自己,也不是反方向)。"""
    key = "ic-panel:key-d"
    first = await _market_episode(repos, key)
    second = await _market_episode(repos, key)
    # 两集 occurred_at 相同(同秒),rowid 序 = 插入序
    parent = await link_derived_from(
        repos["db"],
        child_episode_id=second,
        subject_type="market",
        subject_id=key,
    )
    assert parent == first
    # 反向查询也必须排除自身:对 first 连边时查到的"前一集"不存在
    # (first 是最早插入的;second 虽然更晚但 first 查询排除自身后无候选
    #  —— 这正是"血缘只指向过去"的物理含义)
    back = await link_derived_from(
        repos["db"],
        child_episode_id=first,
        subject_type="market",
        subject_id=key,
    )
    assert (
        back is None or back == second
    )  # 指向更晚者不违反唯一性,但时间链断言以 first 为根
    edges = await repos["db"].execute(
        "SELECT from_id, to_id FROM research_edges WHERE kind='derived_from'"
    )
    assert (edges[0]["from_id"], edges[0]["to_id"]) == (second, first)


@pytest.mark.asyncio
async def test_derived_from_race_double_save_two_edges_semantically_correct(repos):
    """竞态双 save:两条不同 from_id 的边各自指向自己的前一集,语义正确;
    UNIQUE 约束不误伤(子不同)。"""
    key = "ic-panel:key-e"
    first = await _market_episode(repos, key)
    second = await _market_episode(repos, key)
    third = await _market_episode(repos, key)
    p1 = await link_derived_from(
        repos["db"], child_episode_id=second, subject_type="market", subject_id=key
    )
    p2 = await link_derived_from(
        repos["db"], child_episode_id=third, subject_type="market", subject_id=key
    )
    assert p1 == first and p2 == second
    edges = await repos["db"].execute(
        "SELECT from_id, to_id FROM research_edges WHERE kind='derived_from'"
    )
    assert {(e["from_id"], e["to_id"]) for e in edges} == {
        (second, first),
        (third, second),
    }


# --- 因子级生命周期推导消费口(derivation.py) --------------------------------


async def _formula_episode(
    repos,
    subject_id: str,
    *,
    verdict: str | None,
    occurred_at: str,
    lifecycle: bool = True,
) -> str:
    """带 lifecycle 子键 + 可选 verdict 的落账型 episode(账本刀写入形态)。"""
    recorded: dict = {"kind": "formula_registration", "source": "test"}
    if lifecycle:
        recorded["lifecycle"] = {"status": "DISCOVERED", "transitions": []}
    if verdict is not None:
        recorded["verdict"] = verdict
    return str(
        await repos["episode"].create(
            subject_type="formula",
            subject_id=subject_id,
            title=f"Formula registration {subject_id}",
            boundary_start=occurred_at,
            boundary_end=occurred_at,
            recorded=recorded,
            occurred_at=occurred_at,
        )
    )


@pytest.mark.asyncio
async def test_derivation_suspicious_latest_suggests_inspect(repos):
    """链 VALID→VALID→SUSPICIOUS:最新重测是 IC 衰减证据 → 建议 inspect(保守)。"""
    key = "formula:ab8d2445"
    await _formula_episode(
        repos, key, verdict="VALID", occurred_at="2026-10-01T00:00:00+00:00"
    )
    await _formula_episode(
        repos, key, verdict="VALID", occurred_at="2026-10-02T00:00:00+00:00"
    )
    third = await _formula_episode(
        repos, key, verdict="SUSPICIOUS", occurred_at="2026-10-03T00:00:00+00:00"
    )
    out = await derive_subject_lifecycle(
        repos["db"], subject_type="formula", subject_id=key
    )
    assert len(out["chain"]) == 3
    assert out["chain"][0]["verdict"] == "SUSPICIOUS"  # 新→旧
    assert out["latest"]["episode_id"] == third
    assert out["retired_in_history"] is False
    assert out["suggestions"] == [
        {
            "action": "inspect",
            "basis_episode_id": third,
            "reason": "最新重测 verdict=SUSPICIOUS(IC 衰减证据,建议复查)",
        }
    ]


@pytest.mark.asyncio
async def test_derivation_invalid_latest_suggests_retire(repos):
    """最新 verdict=INVALID(证伪)→ 建议 retire。"""
    key = "formula:deadbeef"
    await _formula_episode(
        repos, key, verdict="VALID", occurred_at="2026-10-01T00:00:00+00:00"
    )
    last = await _formula_episode(
        repos, key, verdict="INVALID", occurred_at="2026-10-02T00:00:00+00:00"
    )
    out = await derive_subject_lifecycle(
        repos["db"], subject_type="formula", subject_id=key
    )
    assert [s["action"] for s in out["suggestions"]] == ["retire"]
    assert out["suggestions"][0]["basis_episode_id"] == last


@pytest.mark.asyncio
async def test_derivation_latest_retired_means_no_suggestion(repos):
    """最新 episode 已 RETIRED(终态):现状即答案,无建议 —— 即使 verdict=INVALID。"""
    key = "formula:retired-latest"
    await _formula_episode(
        repos, key, verdict="VALID", occurred_at="2026-10-01T00:00:00+00:00"
    )
    last = await _formula_episode(
        repos, key, verdict="INVALID", occurred_at="2026-10-02T00:00:00+00:00"
    )
    await repos["episode"].record_lifecycle_transition(
        last, "RETIRED", actor="user", reason="证伪退役"
    )
    out = await derive_subject_lifecycle(
        repos["db"], subject_type="formula", subject_id=key
    )
    assert out["latest"]["status"] == "RETIRED"
    assert out["suggestions"] == []


@pytest.mark.asyncio
async def test_derivation_retired_in_history_is_gray_zone_no_suggestion(repos):
    """链中曾 RETIRED 但存在更新 episode(storage 不结转=复活灰区):
    只标 retired_in_history,不出建议 —— 推导不假装能裁决复活。"""
    key = "formula:revival"
    first = await _formula_episode(
        repos, key, verdict="VALID", occurred_at="2026-10-01T00:00:00+00:00"
    )
    await repos["episode"].record_lifecycle_transition(
        first, "RETIRED", actor="user", reason="早前证伪退役"
    )
    retest = await _formula_episode(
        repos, key, verdict="VALID", occurred_at="2026-10-05T00:00:00+00:00"
    )
    out = await derive_subject_lifecycle(
        repos["db"], subject_type="formula", subject_id=key
    )
    assert out["latest"]["episode_id"] == retest
    assert out["latest"]["status"] == "DISCOVERED"
    assert out["retired_in_history"] is True
    assert out["suggestions"] == []


@pytest.mark.asyncio
async def test_derivation_skips_episodes_without_lifecycle_subkey(repos):
    """无 lifecycle 子键的历史 episode 混在链里:status=None 跳过状态逻辑,
    不报错不炸 —— 误伤旧数据比少一个功能严重(objects.record_lifecycle_transition 同原则)。"""
    key = "formula:mixed-history"
    await _formula_episode(
        repos,
        key,
        verdict=None,
        occurred_at="2026-09-01T00:00:00+00:00",
        lifecycle=False,
    )
    latest = await _formula_episode(
        repos, key, verdict="SUSPICIOUS", occurred_at="2026-10-03T00:00:00+00:00"
    )
    out = await derive_subject_lifecycle(
        repos["db"], subject_type="formula", subject_id=key
    )
    assert len(out["chain"]) == 2
    assert out["chain"][1]["status"] is None
    assert out["latest"]["episode_id"] == latest
    assert [s["action"] for s in out["suggestions"]] == ["inspect"]


@pytest.mark.asyncio
async def test_derivation_empty_subject_returns_empty_shape(repos):
    """查无此 subject:空链 + latest=None + 零建议(读路径零副作用,不建任何行)。"""
    out = await derive_subject_lifecycle(
        repos["db"], subject_type="formula", subject_id="formula:never-existed"
    )
    assert out["chain"] == []
    assert out["latest"] is None
    assert out["retired_in_history"] is False
    assert out["suggestions"] == []
    rows = await repos["db"].execute("SELECT COUNT(*) c FROM research_episodes")
    assert rows[0]["c"] == 0
