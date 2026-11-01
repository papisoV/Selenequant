"""§9.5 Research Graph Integrity Check —— 把纪律变成能自动验的东西。

这组测试分两半:干净图上必须 ok=True;每种违规各造一次,必须被抓出来。
手动绕过仓储直接打 SQL 来造违规,是因为要模拟"未来某个 agent 图省事
直接写库"的场景 —— 审计必须能抓到仓储背后的脏数据。
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import check_graph_integrity
from selene_space_storage.repositories.research_graph.edges import (
    ResearchEdgeRepository,
)
from selene_space_storage.repositories.research_graph.integrity import _DATED_TABLES
from selene_space_storage.repositories.research_graph.objects import (
    EpisodeRepository,
    EvidenceRepository,
)
from selene_space_storage.repositories.research_graph.questions import (
    ResearchQuestionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


async def _raw(db, sql: str, params: tuple = ()) -> None:
    async with db.connection() as conn:
        await conn.execute(sql, params)
        await conn.commit()


async def _raw_unchecked(db, sql: str, params: tuple = ()) -> None:
    """同上,但**临时关掉 FK**。

    规则 5 检查的是"evidence 指向一个不存在的 episode",而 ``sqlite.py:51``
    开着 ``PRAGMA foreign_keys=ON`` —— 这个状态经仓储和普通 SQL 都写不进去。
    审计的价值恰恰在于它是 FK 之外的**第二道**:它抓的是 FK 拦不住的数据
    (迁移前的存量库、别的副本搬过来的库、将来某次迁移把 REFERENCES 删掉)。
    要证这条规则真的会响,就只能先把那个状态造出来。

    ``PRAGMA foreign_keys`` 在事务内是 no-op,所以每步之间都要 commit。
    """
    async with db.connection() as conn:
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys=OFF")
        await conn.execute(sql, params)
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.commit()


# --- 干净图 ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_graph_is_clean(db):
    report = await check_graph_integrity(db)
    assert report["ok"] is True
    assert report["violations"] == []
    assert report["counts"]["questions"] == 0


@pytest.mark.asyncio
async def test_well_formed_graph_is_clean(db):
    questions = ResearchQuestionRepository(db)
    edges = ResearchEdgeRepository(db)
    episodes = EpisodeRepository(db)
    evidence = EvidenceRepository(db)

    qid = await questions.create(title="SOL 4h 亏损根因")
    ep_id = await episodes.create(
        subject_type="strategy",
        subject_id="strat-x",
        title="实跑段",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T00:00:00+00:00",
    )
    ev_id = await evidence.create(
        episode_id=ep_id, mode="RECORDED", payload={"pnl": -0.07}
    )
    await edges.add_fact_edge(
        from_ref=("evidence", ev_id), to_ref=("question", qid), kind="addresses"
    )
    await questions.add_revision(qid, "初步判断", actor="selene", cited=[ev_id])

    report = await check_graph_integrity(db)
    assert report["ok"] is True, report["violations"]
    assert report["counts"]["edges"] == 1
    assert report["counts"]["questions"] == 1


# --- 九条规则,每条各造一次违规 --------------------------------------------


@pytest.mark.asyncio
async def test_detects_judgment_without_rationale(db):
    await _raw(
        db,
        "INSERT INTO research_edges "
        "(id, from_type, from_id, to_type, to_id, kind, edge_class, rationale, "
        " provenance_json, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "edge-x",
            "evidence",
            "ev-1",
            "hypothesis",
            "hyp-1",
            "supports",
            "judgment",
            None,
            "[]",
            "selene",
            "2026-09-16T00:00:00+00:00",
        ),
    )
    report = await check_graph_integrity(db)
    assert report["ok"] is False
    assert any(v["rule"] == "judgment_without_rationale" for v in report["violations"])


@pytest.mark.asyncio
async def test_detects_inference_recorded_as_fact(db):
    await _raw(
        db,
        "INSERT INTO research_edges "
        "(id, from_type, from_id, to_type, to_id, kind, edge_class, rationale, "
        " provenance_json, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "edge-y",
            "evidence",
            "ev-1",
            "question",
            "q-1",
            "addresses",
            "fact",
            "我觉得这条支持它",
            '["bt-1"]',
            "selene",
            "2026-09-16T00:00:00+00:00",
        ),
    )
    report = await check_graph_integrity(db)
    assert report["ok"] is False
    assert any(v["rule"] == "inference_recorded_as_fact" for v in report["violations"])


@pytest.mark.asyncio
async def test_detects_dangling_edge(db):
    await _raw(
        db,
        "INSERT INTO research_edges "
        "(id, from_type, from_id, to_type, to_id, kind, edge_class, actor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "edge-z",
            "evidence",
            "ev-ghost",
            "question",
            "q-ghost",
            "addresses",
            "fact",
            "system",
            "2026-09-16T00:00:00+00:00",
        ),
    )
    report = await check_graph_integrity(db)
    assert report["ok"] is False
    assert any(v["rule"] == "dangling_edge" for v in report["violations"])


@pytest.mark.asyncio
async def test_detects_recursive_revocation(db):
    # actor 轴只有 ('selene','user','system') 三个词 —— 'director' 是**角色**
    # (那是 research_decisions.decided_by 的取值),不在这里。
    await _raw(
        db,
        "INSERT INTO research_edges "
        "(id, from_type, from_id, to_type, to_id, kind, edge_class, actor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "edge-r1",
            "edge",
            "edge-t1",
            "edge",
            "edge-t1",
            "revokes",
            "fact",
            "system",
            "2026-09-16T00:00:00+00:00",
        ),
    )
    await _raw(
        db,
        "INSERT INTO research_edges "
        "(id, from_type, from_id, to_type, to_id, kind, edge_class, actor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "edge-r2",
            "edge",
            "edge-r1",
            "edge",
            "edge-r1",
            "revokes",
            "fact",
            "system",
            "2026-09-16T00:00:01+00:00",
        ),
    )
    report = await check_graph_integrity(db)
    assert report["ok"] is False
    assert any(v["rule"] == "recursive_revocation" for v in report["violations"])


@pytest.mark.asyncio
async def test_detects_evidence_without_episode(db):
    await _raw_unchecked(
        db,
        "INSERT INTO research_evidence "
        "(id, episode_id, mode, observed_at, created_at) VALUES (?, ?, ?, ?, ?)",
        (
            "ev-orphan",
            "ep-ghost",
            "RECORDED",
            "2026-09-16T00:00:00+00:00",
            "2026-09-16T00:00:00+00:00",
        ),
    )
    report = await check_graph_integrity(db)
    assert report["ok"] is False
    assert any(v["rule"] == "evidence_without_episode" for v in report["violations"])


@pytest.mark.asyncio
async def test_detects_outcome_never_without_window(db):
    # research_decisions.question_id 有 REFERENCES —— 拿一个不存在的问题记
    # 决策会被 FK 挡下,所以先种一个真问题(不是"麻烦",是不能对不存在的问题
    # 做判断)。
    qid = await ResearchQuestionRepository(db).create(title="被记了 never 的问题")
    await _raw(
        db,
        "INSERT INTO research_decisions "
        "(id, question_id, verdict, decided_by, outcome_status, decided_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "dec-bad",
            qid,
            "PARK",
            "director",
            "outcome_never",
            "2026-09-16T00:00:00+00:00",
        ),
    )
    report = await check_graph_integrity(db)
    assert report["ok"] is False
    assert any(
        v["rule"] == "outcome_never_without_window" for v in report["violations"]
    )


@pytest.mark.asyncio
async def test_revokes_edge_is_not_reported_as_dangling(db):
    """**回归**:边的两端可以指向边本身(D-5 的撤销模型)。

    边 id 不在领域对象的前缀表里 —— 不特判这一支,每条 revokes 边都会被
    规则 3 误报成 dangling_edge,审计会当场变成噪声。
    """
    questions = ResearchQuestionRepository(db)
    edges = ResearchEdgeRepository(db)

    parent = await questions.create(title="父问题")
    child = await questions.create(title="子问题")
    edge = await edges.add_fact_edge(
        from_ref=("question", child), to_ref=("question", parent), kind="derived_from"
    )
    await edges.revoke(edge["id"], reason="测试撤销")

    report = await check_graph_integrity(db)
    assert not any(v["rule"] == "dangling_edge" for v in report["violations"]), report[
        "violations"
    ]
    assert report["ok"] is True, report["violations"]


@pytest.mark.asyncio
async def test_mission_endpoint_is_not_reported_as_dangling(db):
    """**回归**:mission 是一等边端点(promote 管线写 ``mission → question``)。

    ``_node_exists`` 按 id 前缀反查表,``mis-`` 若不在 ``_ID_PREFIX_TABLES``
    里就反查不到表 → 规则 3 把**每条** mission 端点的边误报成 dangling_edge。
    2026-10-13 实测:``edge-8a7a7e42a0a3`` from=``mis-b2f9056975c5``(mission
    明明存在,却报 "from endpoint not found")。
    """
    questions = ResearchQuestionRepository(db)
    qid = await questions.create(title="被 mission 指向的问题")

    _raw_mission_ddl = (
        "INSERT INTO research_missions "
        "(id, title, question, subject_type, subject_id, phase, opened_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
    )
    await _raw(
        db,
        _raw_mission_ddl,
        (
            "mis-real",
            "真实 mission",
            "这条 mission 的边不应被误报",
            "system",
            "probe-x",
            "investigation",
            "2026-09-16T00:00:00+00:00",
            "2026-09-16T00:00:00+00:00",
        ),
    )

    edges = ResearchEdgeRepository(db)
    await edges.add_fact_edge(
        from_ref=("mission", "mis-real"),
        to_ref=("question", qid),
        kind="addresses",
    )

    report = await check_graph_integrity(db)
    assert not any(v["rule"] == "dangling_edge" for v in report["violations"]), report[
        "violations"
    ]


@pytest.mark.asyncio
async def test_detects_history_edge_rewritten(db):
    """§9.5 第 3 条:边指向一个比它自己**更晚出生**的对象。

    全是 append-only 表,所以插入永远造不出这种顺序 —— 只有回头改写历史
    才做得到。这条检查把"不许改历史"(C-4)从纪律变成可跑的断言。
    """
    await _raw(
        db,
        "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
        "boundary_start, boundary_end, occurred_at, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "ep-late",
            "strategy",
            "strat-x",
            "晚出生的经历",
            "2026-08-01T00:00:00+00:00",
            "2026-08-31T00:00:00+00:00",
            "2026-09-10T00:00:00+00:00",
            "2026-09-10T00:00:00+00:00",
        ),
    )
    await _raw(
        db,
        "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, kind, "
        "edge_class, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "edge-early",
            "episode",
            "ep-late",
            "question",
            "q-ghost",
            "addresses",
            "fact",
            "system",
            "2026-09-01T00:00:00+00:00",
        ),
    )
    report = await check_graph_integrity(db)
    assert any(v["rule"] == "history_edge_rewritten" for v in report["violations"])


@pytest.mark.asyncio
async def test_dated_table_columns_really_exist(db):
    """规则 7 的 ``_DATED_TABLES`` 里,每张表的"出生时间列"必须真的存在。

    九条规则里只有它会在**没被触发**时安静地坏掉:`node_id` 的前缀对不上
    就跳过,对上了才去查那一列。所以某一行写错列名,平时完全看不出来 ——
    直到某天真的有一条边指向那个前缀的节点,审计当场抛 ``no such column``。
    """
    for _prefix, table, column in _DATED_TABLES:
        cols = {
            dict(r)["name"] for r in await db.execute(f"PRAGMA table_info({table})")
        }
        assert column in cols, f"{table} 没有 {column} 列 —— 实际有 {sorted(cols)}"


@pytest.mark.asyncio
async def test_detects_memory_without_evidence_chain(db):
    """§9.5 第 4 条 / §7.4:**是否存在无证据 Memory**。

    可执行形态是"被引用过、但证据链走不到 Episode / Evidence" ——
    它已经被当作既有知识消费了,而它没有这个资格。
    """
    questions = ResearchQuestionRepository(db)
    qid = await questions.create(title="无证据的结论")
    await _raw(
        db,
        "INSERT INTO research_hypotheses (id, statement, status, created_at, updated_at) "
        "VALUES (?, ?, 'proposed', ?, ?)",
        (
            "hyp-bare",
            "完全没有证据支撑的断言",
            "2026-09-01T00:00:00+00:00",
            "2026-09-01T00:00:00+00:00",
        ),
    )
    await _raw(
        db,
        "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, kind, "
        "edge_class, actor, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "edge-hb",
            "hypothesis",
            "hyp-bare",
            "question",
            qid,
            "addresses",
            "fact",
            "system",
            "2026-09-01T00:00:00+00:00",
        ),
    )
    # 一条引用了这个无证据假设的回答 —— 于是它已经被消费了
    await questions.add_revision(
        qid, "基于该假设的判断", actor="selene", cited=["hyp-bare"]
    )

    report = await check_graph_integrity(db)
    assert report["ok"] is False
    assert any(
        v["rule"] == "memory_without_evidence_chain" for v in report["violations"]
    )


@pytest.mark.asyncio
async def test_scope_pressure_is_not_a_violation(db):
    """D-6:锚外引边是**扩锚信号,不是错误** —— 它不该让 ok 变 False。

    把它算进 violations 就等于让图自己去矛盾,那是 D-4 禁止的。
    它该触发 PROPOSE EXPAND,由 Director 拍。
    """
    questions = ResearchQuestionRepository(db)
    edges = ResearchEdgeRepository(db)
    episodes = EpisodeRepository(db)
    evidence = EvidenceRepository(db)

    qid = await questions.create(
        title="只关心 strat-a 的问题", anchor={"strategy_ids": ["strat-a"]}
    )
    ep_id = await episodes.create(
        subject_type="strategy",
        subject_id="strat-OTHER",
        title="别的主体的经历",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T00:00:00+00:00",
    )
    ev_id = await evidence.create(episode_id=ep_id, mode="RECORDED", payload={"x": 1})
    await edges.add_fact_edge(
        from_ref=("evidence", ev_id), to_ref=("question", qid), kind="addresses"
    )

    report = await check_graph_integrity(db)
    assert report["violations"] == [], report["violations"]
    assert report["ok"] is True
    assert [p["object_id"] for p in report["scope_pressure"]] == [ev_id]


@pytest.mark.asyncio
async def test_revision_readable_via_column_is_clean(db):
    """规则 10:revision 经 question_id **列**可达 —— 不需要边。

    这正是"列能承载的关系不写边"约定的机械保证:若规则 10 写成
    "必须有 revision_of 边",全库 17 条 revision 会全部误报。
    """
    questions = ResearchQuestionRepository(db)
    qid = await questions.create(title="Q")
    await questions.add_revision(qid, "回答", actor="selene")

    report = await check_graph_integrity(db)
    assert not any(
        v["rule"] == "revision_relation_unreadable" for v in report["violations"]
    ), report["violations"]


@pytest.mark.asyncio
async def test_unreadable_revision_is_caught(db):
    """规则 10:列被清空且无边 → revision 从任何问题都读不到。"""
    qid = await ResearchQuestionRepository(db).create(title="Q")
    await ResearchQuestionRepository(db).add_revision(qid, "回答", actor="selene")
    # 模拟"绕过仓储直接改库"的脏数据:与 FK 冲突,故临时关 FK
    async with db.connection() as conn:
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys=OFF")
        await conn.execute("UPDATE research_revisions SET question_id = ''")
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.commit()

    report = await check_graph_integrity(db)
    assert report["ok"] is False
    assert any(
        v["rule"] == "revision_relation_unreadable" for v in report["violations"]
    ), report["violations"]


@pytest.mark.asyncio
async def test_unreadable_evidence_is_caught(db):
    """规则 10:evidence 的 episode_id 被清空且无边 → 从任何 episode 读不到。"""
    ep_id = await EpisodeRepository(db).create(
        subject_type="strategy",
        subject_id="strat-x",
        title="实跑段",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T00:00:00+00:00",
    )
    await EvidenceRepository(db).create(
        episode_id=ep_id, mode="RECORDED", payload={"x": 1}
    )
    async with db.connection() as conn:
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys=OFF")
        await conn.execute("UPDATE research_evidence SET episode_id = ''")
        await conn.commit()
        await conn.execute("PRAGMA foreign_keys=ON")
        await conn.commit()

    report = await check_graph_integrity(db)
    assert report["ok"] is False
    assert any(
        v["rule"] == "evidence_relation_unreadable" for v in report["violations"]
    ), report["violations"]


# --- 规则 11:mission 主体完整性(2026-10-13) ---------------------------------


async def _raw_mission(db, mid: str, subject_type: str, subject_id: str) -> None:
    """绕过仓储直插 mission —— 模拟门上线前落库的存量脏数据。"""
    await _raw(
        db,
        "INSERT INTO research_missions "
        "(id, title, question, subject_type, subject_id, phase, stage, "
        " created_by, opened_at, updated_at) "
        "VALUES (?, 't', 'q', ?, ?, 'investigation', 'opened', "
        " 'selene', '2026-10-01T00:00:00+00:00', '2026-10-01T00:00:00+00:00')",
        (mid, subject_type, subject_id),
    )


@pytest.mark.asyncio
async def test_dangling_strategy_mission_subject_is_caught(db):
    """规则 11:strategy mission 的 subject_id 不在 strategies 表 → 违规。

    真实事故:探针名 entry-delay-ic-probe / 回测 id bt-b62d32ac 曾静默成为
    strategy 主体,驾驶舱 State 端点 404。写入门(L1)挡新数据,本规则抓存量。
    """
    await _raw_mission(db, "mis-ghost", "strategy", "entry-delay-ic-probe")

    report = await check_graph_integrity(db)
    assert report["ok"] is False
    violations = [
        v for v in report["violations"] if v["rule"] == "mission_subject_dangling"
    ]
    assert len(violations) == 1
    assert violations[0]["object_id"] == "mis-ghost"
    assert "entry-delay-ic-probe" in violations[0]["detail"]


@pytest.mark.asyncio
async def test_registered_strategy_mission_subject_is_clean(db):
    """命中 strategies 表的 strategy mission 不报违规(规则不是无差别开火)。"""
    await _raw(
        db,
        "INSERT INTO strategies (id, name, type) VALUES ('strat-6395c312', 'Keltner V2', 'custom')",
    )
    await _raw_mission(db, "mis-ok", "strategy", "strat-6395c312")

    report = await check_graph_integrity(db)
    assert not any(
        v["rule"] == "mission_subject_dangling" for v in report["violations"]
    ), report["violations"]


@pytest.mark.asyncio
async def test_non_strategy_mission_subjects_out_of_scope(db):
    """market/event/system 主体无登记表 → 不在规则 11 范围(不是遗漏)。"""
    await _raw_mission(db, "mis-mkt", "market", "BTC/USDT")
    await _raw_mission(db, "mis-sys", "system", "batch3-live-verify")

    report = await check_graph_integrity(db)
    assert not any(
        v["rule"] == "mission_subject_dangling" for v in report["violations"]
    ), report["violations"]
