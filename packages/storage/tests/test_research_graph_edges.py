"""边语义 —— §7 的全部约束,以及 D-3 / D-4 / D-5 的机械检查。

这组测试防的三件事:
① 把认识论判断伪装成事实历史(D-3)
② 图自动去矛盾,静默改写历史(D-4)
③ 撤销套撤销,provenance 递归到没人看得懂(D-5)
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph.edges import (
    ResearchEdgeRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

Q1 = ("question", "q-1")
Q2 = ("question", "q-2")
E1 = ("evidence", "ev-1")
E2 = ("evidence", "ev-2")
H1 = ("hypothesis", "hyp-1")
H2 = ("hypothesis", "hyp-2")


@pytest_asyncio.fixture
async def edges():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield ResearchEdgeRepository(db)


@pytest.mark.asyncio
async def test_fact_edge_is_recorded(edges):
    edge = await edges.add_fact_edge(from_ref=E1, to_ref=Q1, kind="addresses")
    assert edge["id"].startswith("edge-")
    assert edge["edge_class"] == "fact"
    assert edge["rationale"] is None


@pytest.mark.asyncio
async def test_fact_edge_rejects_judgment_kind(edges):
    """系统不得用事实通道写入判断 —— 这就是 D-3 的机械执行。"""
    with pytest.raises(ValueError, match="judgment kind"):
        await edges.add_fact_edge(from_ref=E1, to_ref=H1, kind="supports")


@pytest.mark.asyncio
async def test_fact_edge_rejects_unregistered_kind(edges):
    with pytest.raises(ValueError, match="unregistered"):
        await edges.add_fact_edge(from_ref=E1, to_ref=H1, kind="tested_by")


@pytest.mark.asyncio
async def test_judgment_edge_requires_rationale_and_provenance(edges):
    with pytest.raises(ValueError, match="rationale"):
        await edges.add_judgment_edge(
            from_ref=E1, to_ref=H1, kind="supports", rationale="", provenance=["bt-1"]
        )
    with pytest.raises(ValueError, match="provenance"):
        await edges.add_judgment_edge(
            from_ref=E1, to_ref=H1, kind="supports", rationale="OOS 一致", provenance=[]
        )


@pytest.mark.asyncio
async def test_judgment_edge_records_actor_and_provenance(edges):
    edge = await edges.add_judgment_edge(
        from_ref=E1,
        to_ref=H1,
        kind="contradicts",
        rationale="该证据的 regime 与假设所述不同",
        provenance=["bt-b62d32ac", "tick-recompute-2026-09-16"],
        actor="selene",
    )
    assert edge["edge_class"] == "judgment"
    assert edge["provenance"] == ["bt-b62d32ac", "tick-recompute-2026-09-16"]
    assert edge["actor"] == "selene"
    assert edge["rationale"].startswith("该证据")


@pytest.mark.asyncio
async def test_judgment_edge_rejects_actor_outside_vocabulary(edges):
    """D-3 的第三根腿:谁做的这一笔,必须落在三词词表里。

    ``'director'`` 故意不在词表里 —— 它是**角色**(记在
    ``research_decisions.decided_by``),不是 ``actor`` 这一轴。放它进来,
    等于允许 AI 给自己签 Director 的名。
    """
    with pytest.raises(ValueError, match="unknown actor"):
        await edges.add_judgment_edge(
            from_ref=E1,
            to_ref=H1,
            kind="supports",
            rationale="看着一致",
            provenance=["bt-1"],
            actor="director",
        )


@pytest.mark.asyncio
async def test_judgment_edge_rejects_fact_kind(edges):
    with pytest.raises(ValueError, match="not a judgment kind"):
        await edges.add_judgment_edge(
            from_ref=E1, to_ref=Q1, kind="addresses", rationale="x", provenance=["y"]
        )


@pytest.mark.asyncio
async def test_graph_does_not_auto_reconcile_contradiction(edges):
    """D-4:同一条 Evidence 同时对 H1 supports、对 H2 contradicts 是**正常状态**。

    图不得因为"看起来矛盾"就去掉一边。矛盾由 Revision/Decision 解释。
    """
    await edges.add_judgment_edge(
        from_ref=E1,
        to_ref=H1,
        kind="supports",
        rationale="方向一致",
        provenance=["bt-1"],
    )
    await edges.add_judgment_edge(
        from_ref=E1,
        to_ref=H2,
        kind="contradicts",
        rationale="方向相反",
        provenance=["bt-1"],
    )
    assert len(await edges.list_edges(E1[1])) == 2
    kinds = {e["kind"] for e in await edges.list_edges(E1[1])}
    assert kinds == {"supports", "contradicts"}


@pytest.mark.asyncio
async def test_duplicate_edge_is_rejected(edges):
    import sqlite3

    await edges.add_fact_edge(from_ref=E1, to_ref=Q1, kind="addresses")
    with pytest.raises(sqlite3.IntegrityError):
        await edges.add_fact_edge(from_ref=E1, to_ref=Q1, kind="addresses")


@pytest.mark.asyncio
async def test_relates_to_stored_once_canonically(edges):
    """对称边只存一份,否则 §4 的引用迁移统计会算重。"""
    await edges.relate(("hypothesis", "hyp-b"), ("hypothesis", "hyp-a"))
    # 反向再调一次 —— 因为会命中 UNIQUE,必须抛 IntegrityError 而不是存第二条
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError):
        await edges.relate(("hypothesis", "hyp-a"), ("hypothesis", "hyp-b"))
    found = await edges.list_edges("hyp-a")
    assert len(found) == 1
    assert found[0]["from_id"] == "hyp-a"  # 规范化后小的一端在前


@pytest.mark.asyncio
async def test_relate_rejects_self_loop(edges):
    with pytest.raises(ValueError, match="self"):
        await edges.relate(("hypothesis", "hyp-a"), ("hypothesis", "hyp-a"))


@pytest.mark.asyncio
async def test_revoke_appends_edge_and_hides_original(edges):
    """D-5:撤销 = 追加撤销边,不是删边。原边仍在历史里。"""
    original = await edges.add_judgment_edge(
        from_ref=E1,
        to_ref=H1,
        kind="supports",
        rationale="当时看着一致",
        provenance=["bt-1"],
    )
    assert not await edges.is_revoked(original["id"])
    revocation = await edges.revoke(
        original["id"], actor="user", reason="regime 判断错了"
    )
    assert revocation["kind"] == "revokes"
    assert await edges.is_revoked(original["id"])
    # 原边没被删
    rows = await edges.list_edges(H1[1], include_revoked=True)
    assert any(r["id"] == original["id"] for r in rows)
    # 默认视图看不到被撤销的边
    visible = await edges.list_edges(H1[1])
    assert all(r["id"] != original["id"] for r in visible)


@pytest.mark.asyncio
async def test_revoke_is_not_recursive(edges):
    """D-5:最多一层。不允许撤销一条撤销边 —— 深审计另有独立结构,不靠边套边。"""
    original = await edges.add_fact_edge(from_ref=E1, to_ref=Q1, kind="addresses")
    revocation = await edges.revoke(original["id"], actor="user", reason="错挂")
    with pytest.raises(ValueError, match="not recursive"):
        await edges.revoke(revocation["id"], actor="user", reason="再撤销")


@pytest.mark.asyncio
async def test_revoke_twice_is_rejected(edges):
    original = await edges.add_fact_edge(from_ref=E1, to_ref=Q1, kind="addresses")
    await edges.revoke(original["id"], actor="user", reason="第一次")
    with pytest.raises(ValueError, match="already revoked"):
        await edges.revoke(original["id"], actor="user", reason="第二次")


@pytest.mark.asyncio
async def test_revoke_missing_edge_raises(edges):
    with pytest.raises(KeyError):
        await edges.revoke("edge-nope", actor="user", reason="x")


@pytest.mark.asyncio
async def test_exists_between_is_direction_agnostic(edges):
    await edges.add_fact_edge(from_ref=E1, to_ref=Q1, kind="addresses")
    assert await edges.exists_between("q-1", "ev-1")
    assert await edges.exists_between("ev-1", "q-1")


@pytest.mark.asyncio
async def test_exists_between_ignores_revoked(edges):
    edge = await edges.add_fact_edge(from_ref=E1, to_ref=Q1, kind="addresses")
    await edges.revoke(edge["id"], actor="user", reason="错挂")
    assert not await edges.exists_between("q-1", "ev-1")


@pytest.mark.asyncio
async def test_neighbors_and_kind_filter(edges):
    await edges.add_fact_edge(from_ref=E1, to_ref=Q1, kind="addresses")
    await edges.add_judgment_edge(
        from_ref=E1, to_ref=H1, kind="supports", rationale="一致", provenance=["bt-1"]
    )
    assert set(await edges.neighbors("ev-1")) == {"q-1", "hyp-1"}
    assert await edges.neighbors("ev-1", kind="supports") == ["hyp-1"]
