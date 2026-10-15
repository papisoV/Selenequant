"""GET /api/v1/research/graph/memory —— MEMORY 正典页投影端点测试。

分层判据:证据链走到 Episode/Evidence 的条目 = 既有知识(knowledge),
走不到的 = 待检验线索(clues)—— 复用 recall 的机械版判据。
行为后果 = cited_json 反向索引("已改变 N 次回答")。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.api.v1 import research_graph as rg
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    EpisodeRepository,
    ResearchEdgeRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def ctx():
    """(client, db) —— 内存库 + 指向它的 app,fresh per test。"""
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        app = create_app()
        app.dependency_overrides[rg.get_graph_db] = lambda: db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, db


async def _make_question(client: AsyncClient, title: str = "SOL 4h 亏损根因") -> str:
    resp = await client.post(
        "/api/v1/research/graph/questions",
        json={"title": title, "anchor": {"strategy_ids": ["strat-x"]}},
    )
    assert resp.status_code == 200, resp.text
    qid: str = resp.json()["data"]["question_id"]
    return qid


async def _make_episode_with_evidence(db, qid: str) -> tuple[str, str]:
    """Episode + Evidence + addresses 边挂到 question → hypothesis 链可走到。"""
    ep_id = await EpisodeRepository(db).create(
        subject_type="strategy",
        subject_id="strat-x",
        title="实跑段",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T00:00:00+00:00",
    )
    await ResearchEdgeRepository(db).add_fact_edge(
        from_ref=("episode", ep_id), to_ref=("question", qid), kind="addresses"
    )
    return ep_id, ""


async def test_memory_endpoint_empty_db(ctx):
    """空库:两池皆空,counts 为 0,recently_used 为 null。"""
    client, _ = ctx
    resp = await client.get("/api/v1/research/graph/memory")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["knowledge"] == []
    assert body["clues"] == []
    assert body["recently_used"] is None
    assert body["counts"] == {"knowledge": 0, "clues": 0}


async def test_memory_layering_knowledge_vs_clues(ctx):
    """分层:链走到 Episode/Evidence 的条目进 knowledge,走不到的进 clues。

    判据机械版(recall.py::_chain_seeds):hypothesis 的证据链只走它身上
    supports/contradicts/qualifies 的 **Evidence** 边,不经过 question。
    """
    client, db = ctx
    qid = await _make_question(client)
    ep_id, _ = await _make_episode_with_evidence(db, qid)

    from selene_space_storage.repositories.research_graph import (
        EvidenceRepository,
        HypothesisRepository,
    )

    # hypothesis 直接 supports 一条 Evidence(链终点=evidence)
    ev_id = await EvidenceRepository(db).create(
        episode_id=ep_id, mode="RECORDED", payload={"pnl": -0.07}
    )
    hyp_id = await HypothesisRepository(db).create(statement="配置漂移导致亏损")
    await ResearchEdgeRepository(db).add_judgment_edge(
        from_ref=("hypothesis", hyp_id),
        to_ref=("evidence", ev_id),
        kind="supports",
        rationale="回撤曲线吻合",
        provenance=["bt-f4e357b6"],
    )

    resp = await client.get("/api/v1/research/graph/memory")
    body = resp.json()["data"]

    hyp_entries = [e for e in body["knowledge"] if e["id"] == hyp_id]
    assert len(hyp_entries) == 1
    entry = hyp_entries[0]
    assert entry["kind"] == "hypothesis"
    assert entry["text"] == "配置漂移导致亏损"
    assert entry["falsified"] is False
    # chain 内联了 evidence 明细(链终点)
    ev_rows = [c for c in entry["chain"] if c["kind"] == "evidence"]
    assert len(ev_rows) == 1
    assert ev_rows[0]["id"] == ev_id
    assert ev_rows[0]["payload"] == {"pnl": -0.07}
    # 未被任何 revision 引用过 → 0 次回答
    assert entry["used_count"] == 0
    assert entry["last_used_at"] is None


async def test_memory_used_by_reverse_index(ctx):
    """行为后果:revision 的 cited_json 引用了谁,谁的 used_by 就有这条 revision。"""
    client, db = ctx
    qid = await _make_question(client)
    await _make_episode_with_evidence(db, qid)

    # 第一版 revision(无引用)
    r1 = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "第一版:怀疑配置漂移"},
    )
    rev1_id = r1.json()["data"]["revision"]["id"]
    # 第二版引用第一版 → 第一版的 used_count=1
    r2 = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "第二版:确认配置漂移", "cited": [rev1_id]},
    )
    assert r2.status_code == 200, r2.text

    resp = await client.get("/api/v1/research/graph/memory")
    body = resp.json()["data"]

    pool = {e["id"]: e for e in body["knowledge"] + body["clues"]}
    rev1 = pool[rev1_id]
    assert rev1["used_count"] == 1
    assert rev1["used_by"][0]["revision_id"] != rev1_id
    assert rev1["last_used_at"] is not None
    # recently_used = 第二版 revision(rev2 引用了 rev1,rev2 自己没被引用)
    # 注意:recently_used 取的是"引用时间线里最近被引用动作"的 meta,
    # 即 used_by_map 里 created_at 最大的 revision_meta = rev2 自己?
    # 不 —— used_by_map 值是"引用者 revision"的 meta,recently_used 取全部
    # 引用者中 created_at 最大的 = rev2(它做了引用这个动作)。
    assert body["recently_used"]["revision_id"] is not None
    assert body["recently_used"]["content"] == "第二版:确认配置漂移"


async def test_memory_falsified_hypothesis_flag(ctx):
    """证伪态:hypothesis.status ∈ {refuted, retired} → falsified=true,仍在池中。"""
    client, db = ctx
    from selene_space_storage.repositories.research_graph import HypothesisRepository

    hyp_id = await HypothesisRepository(db).create(statement="均值回归假设")
    repo = HypothesisRepository(db)
    await repo.set_status(hyp_id, "refuted")

    resp = await client.get("/api/v1/research/graph/memory")
    body = resp.json()["data"]
    pool = {e["id"]: e for e in body["knowledge"] + body["clues"]}
    entry = pool[hyp_id]
    assert entry["falsified"] is True
    assert entry["status"] == "refuted"
    # 无链 → clues
    assert entry in body["clues"] or any(c["id"] == hyp_id for c in body["clues"])


async def test_memory_origin_and_used_by_actor(ctx):
    """origin 透出:revisions 条目带 origin=actor;hypotheses 无 actor 列 → origin=None。

    actor='user' 行用裸 INSERT 构造(CHECK 限 'selene'/'user'/'system';
    API POST 通道固定 actor='selene',无法经端点造 user 行)。
    """
    client, db = ctx
    from selene_space_storage.repositories.research_graph import HypothesisRepository

    qid = await _make_question(client)
    hyp_id = await HypothesisRepository(db).create(statement="人为拍板假设")
    await db.execute(
        "INSERT INTO research_revisions "
        "(id, question_id, seq, content, rationale_json, cited_json, actor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "rev-user-1",
            qid,
            1,
            "人工改写结论",
            "[]",
            "[]",
            "user",
            "2026-09-27T00:00:00+00:00",
        ),
    )
    # 一条 API 常规(selene)revision 引用 user revision → used_by 带 actor
    r2 = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "第二版引用人工结论", "cited": ["rev-user-1"]},
    )
    assert r2.status_code == 200, r2.text

    resp = await client.get("/api/v1/research/graph/memory")
    body = resp.json()["data"]

    pool = {e["id"]: e for e in body["knowledge"] + body["clues"]}
    user_rev = pool["rev-user-1"]
    assert user_rev["origin"] == "user"
    assert len(user_rev["used_by"]) == 1
    assert user_rev["used_by"][0]["actor"] == "selene"

    hyp_entry = pool[hyp_id]
    assert hyp_entry["origin"] is None


async def test_memory_counts_consistent(ctx):
    """counts 与两池长度一致。"""
    client, db = ctx
    qid = await _make_question(client)
    await _make_episode_with_evidence(db, qid)

    resp = await client.get("/api/v1/research/graph/memory")
    body = resp.json()["data"]
    assert body["counts"]["knowledge"] == len(body["knowledge"])
    assert body["counts"]["clues"] == len(body["clues"])
