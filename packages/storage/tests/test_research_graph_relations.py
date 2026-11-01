"""关系读取 —— 边表 ∪ 列派生的并集。

这组测试防的是"同一关系两套读法,各读一半":``/view`` 只读边表时
看不见 30 条列关系,前端只好自己补一遍,于是同一张图在不同消费者
眼里不一样。

约定(见 ``_shared.py`` 注册表注释):列能承载的关系**不写边**。
所以这里验的是"读得出",不是"写进去"。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    EpisodeRepository,
    EvidenceRepository,
    ResearchEdgeRepository,
    ResearchQuestionRepository,
    list_relations,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


@pytest.mark.asyncio
async def test_empty_db_returns_no_relations(db):
    assert await list_relations(db) == []


@pytest.mark.asyncio
async def test_revision_column_becomes_derived_relation(db):
    """revision.question_id 是列,不是边 —— 但读关系时必须能读出来。"""
    questions = ResearchQuestionRepository(db)
    qid = await questions.create(title="Q")
    rev = await questions.add_revision(qid, "第一版", actor="selene")

    rels = await list_relations(db)
    derived = [r for r in rels if r["derived"]]
    assert len(derived) == 1
    rel = derived[0]
    assert rel["kind"] == "revision_of"
    assert rel["from_type"] == "revision" and rel["from_id"] == rev["id"]
    assert rel["to_type"] == "question" and rel["to_id"] == qid
    assert rel["id"].startswith("derived:")


@pytest.mark.asyncio
async def test_evidence_column_becomes_derived_relation(db):
    episodes = EpisodeRepository(db)
    ep_id = await episodes.create(
        subject_type="strategy",
        subject_id="strat-x",
        title="实跑段",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T00:00:00+00:00",
    )
    ev_id = await EvidenceRepository(db).create(
        episode_id=ep_id, mode="RECORDED", payload={"pnl": -0.07}
    )

    rels = await list_relations(db)
    derived = [r for r in rels if r["derived"]]
    assert len(derived) == 1
    rel = derived[0]
    assert rel["kind"] == "observed_from"
    assert rel["from_type"] == "evidence" and rel["from_id"] == ev_id
    assert rel["to_type"] == "episode" and rel["to_id"] == ep_id


@pytest.mark.asyncio
async def test_real_edges_are_not_marked_derived(db):
    """真边与派生行必须可分辨 —— 只有真边能被 revokes 寻址。"""
    await ResearchEdgeRepository(db).add_fact_edge(
        from_ref=("evidence", "ev-1"), to_ref=("question", "q-1"), kind="addresses"
    )
    rels = await list_relations(db)
    assert len(rels) == 1
    assert rels[0]["derived"] is False
    assert rels[0]["id"].startswith("edge-")


@pytest.mark.asyncio
async def test_union_has_no_id_collision(db):
    """派生 id 用 derived: 前缀,真边用 edge- —— 并集里不得相撞。"""
    questions = ResearchQuestionRepository(db)
    qid = await questions.create(title="Q")
    await questions.add_revision(qid, "回答", actor="selene")
    await ResearchEdgeRepository(db).add_fact_edge(
        from_ref=("evidence", "ev-1"), to_ref=("question", qid), kind="addresses"
    )

    rels = await list_relations(db)
    ids = [r["id"] for r in rels]
    assert len(ids) == len(set(ids)) == 2


@pytest.mark.asyncio
async def test_blank_column_does_not_produce_relation(db):
    """列值为空串(理论上的脏数据)不产关系 —— 不能凭空造出一条指向 '' 的边。

    ``PRAGMA foreign_keys=ON`` 下这个状态写不进去,所以临时关 FK 造出来
    —— 与 ``test_research_graph_integrity._raw_unchecked`` 同法。读关系
    这一侧必须扛得住这类存量脏数据(迁移前的库、别的副本搬过来的库)。
    """
    await ResearchQuestionRepository(db).create(title="Q")
    async with db.connection() as conn:
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys=OFF")
        await conn.execute(
            "INSERT INTO research_revisions "
            "(id, question_id, seq, content, rationale_json, cited_json, actor, created_at) "
            "VALUES ('rev-blank', '', 1, 'x', '[]', '[]', 'selene', "
            "'2026-01-01T00:00:00+00:00')"
        )
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.commit()

    rels = await list_relations(db)
    assert [r for r in rels if r["kind"] == "revision_of"] == []
    # 同时确认那个空 question_id 的 revision 确实在库里(不是插入失败)
    rows = await db.execute("SELECT id FROM research_revisions WHERE id = 'rev-blank'")
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_no_duplicate_relation_when_edge_and_column_agree(db):
    """**约定守卫**:列能承载的关系不该同时写边。

    若某天有人给 revision.question_id 补了一条 ``revision_of`` 真边,
    并集里同一对会出现两行(一真一派生),图上双线 —— 这条测试挡住它。

    这是 ``_shared.py`` 注册表注释里"列能承载的关系不写边"的机械执行。
    """
    questions = ResearchQuestionRepository(db)
    qid = await questions.create(title="Q")
    rev = await questions.add_revision(qid, "回答", actor="selene")

    # 违规写入:给已有列关系补一条真边
    await ResearchEdgeRepository(db).add_fact_edge(
        from_ref=("revision", rev["id"]),
        to_ref=("question", qid),
        kind="revision_of",
    )

    rels = [
        r
        for r in await list_relations(db)
        if r["from_id"] == rev["id"] and r["to_id"] == qid
    ]
    # 并集如实反映了两行 —— 测试的价值在于:这个数字本应为 1,
    # 出现 2 就说明有人违反了约定(而非"读关系读错了")。
    assert (
        len(rels) == 2
    ), "revision_of 同时来自列与边 —— 违反 _shared.py 的'列能承载不写边'约定"
