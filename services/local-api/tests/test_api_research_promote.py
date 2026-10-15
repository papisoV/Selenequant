"""question → pillar 桥的 draft 端点 —— integration(内存库)。

锁 Phase 1 的契约:**纯读**(调用前后三表行数不变)+ existing_pillars 投影
+ override 参数 + 错误路径。

计划: internal-plan
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
    ResearchQuestionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.asyncio

# 真 revision 片段(足够触发三段映射;完整文本见 storage 层测试)
FIVE_SECTION = """【时间线】
固定 BTC/USDT 1h,d0=-0.0058/-0.0059,d1=-0.0026/-0.0028。

【观点整合与矛盾】
机械定义下 half_delay=1。

【深层分析】
曲线非单调半衰。

【反向检验】
新 episode 或滚动重跑后 |d1|/|d0|≥0.5,或置信区间跨 0.5,则 half_delay≠1。

【新问题】
1. d1 与 0.5×|IC0| 的差异是否统计显著?block bootstrap 分布是什么?
2. 若改为"持续≤半值",答案是什么?
"""

TITLE = "BTC 1h funding 延迟衰减:half_delay 在哪一档?"

# 真策略 id(2026-10-13 起 strategy 主体必须命中 strategies 表)。
STRATEGY_ID = "strat-6395c312"


@pytest_asyncio.fixture
async def ctx():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        # 种一行真策略:promote 的 strategy 主体门要求 subject_id 存在。
        await db.execute(
            "INSERT INTO strategies (id, name, type) VALUES (?, ?, ?)",
            (STRATEGY_ID, "Keltner V2", "custom"),
        )
        app = create_app()
        app.dependency_overrides[rg.get_graph_db] = lambda: db
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, db


async def _seed_question_with_revision(db, title: str = TITLE) -> tuple[str, str]:
    """造 问题 + 一条五段 revision。返回 (qid, rev_id)。"""
    repo = ResearchQuestionRepository(db)
    qid = await repo.create(
        title=title,
        body="背景",
        anchor={"intervals": ["1h"], "strategy_ids": [STRATEGY_ID]},
        created_by="user",
    )
    rev = await repo.add_revision(qid, FIVE_SECTION, actor="selene")
    return qid, str(rev["id"])


async def _table_counts(db) -> dict[str, int]:
    out = {}
    for t in ("research_missions", "mission_pillars", "research_edges"):
        rows = await db.execute(f"SELECT COUNT(*) AS n FROM {t}")
        out[t] = int(rows[0]["n"])
    return out


async def _make_mission_with_pillar(
    db, *, question_title: str, claim: str = "既有支柱"
) -> tuple[str, str]:
    """造一个开放 mission + 一根活跃 pillar(模拟此前已 promote 过)。"""
    from selene_space_storage.repositories.mission_thesis import MissionThesisRepository
    from selene_space_storage.repositories.research_missions import (
        ResearchMissionRepository,
    )

    mid = await ResearchMissionRepository(db).create(
        title="既有 mission",
        question=question_title,
        subject_type="strategy",
        subject_id=STRATEGY_ID,
        created_by="user",
    )
    pid = await MissionThesisRepository(db).add_pillar(mid, claim, "core")
    return str(mid), str(pid)


# --- happy path --------------------------------------------------------------


async def test_draft_returns_pillar_fields_from_revision(ctx):
    client, db = ctx
    qid, rev_id = await _seed_question_with_revision(db)

    resp = await client.get(f"/api/v1/research/graph/questions/{qid}/promote-draft")
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]

    assert d["revision_id"] == rev_id
    assert d["revision_seq"] == 1
    # 三段映射(提取而非发明)
    assert "0.0058" in (d["pillar"]["baseline"] or "")
    assert "0.5" in (d["pillar"]["break_threshold"] or "")
    assert "block bootstrap" in (d["pillar"]["next_proof_point"] or "")
    assert d["pillar"]["model_line"] == f"council v1 {rev_id}"
    # subject 建议(anchor 有 strategy_ids)
    assert d["subject_suggestion"] == {
        "subject_type": "strategy",
        "subject_id": STRATEGY_ID,
    }
    # claim 脚手架 + 恒含改写提醒
    assert d["pillar"]["claim"]
    assert any("claim" in w for w in d["warnings"])
    # 首次:无既有 mission
    assert d["existing_mission_id"] is None
    assert d["existing_pillars"] == []


async def test_draft_is_zero_write(ctx):
    """纯读的铁证:调用前后三表行数完全不变。"""
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    before = await _table_counts(db)

    resp = await client.get(f"/api/v1/research/graph/questions/{qid}/promote-draft")
    assert resp.status_code == 200

    after = await _table_counts(db)
    assert before == after


async def test_draft_seq_selects_specific_revision(ctx):
    client, db = ctx
    repo = ResearchQuestionRepository(db)
    qid = await repo.create(
        title=TITLE, anchor={"strategy_ids": ["s1"]}, created_by="user"
    )
    await repo.add_revision(qid, "第一版,无五段", actor="selene")
    await repo.add_revision(qid, FIVE_SECTION, actor="selene")

    # 缺省 = latest
    latest = await client.get(f"/api/v1/research/graph/questions/{qid}/promote-draft")
    assert latest.json()["data"]["revision_seq"] == 2
    assert latest.json()["data"]["pillar"]["break_threshold"]

    # 显式 seq=1 → 手写版,无段
    first = await client.get(
        f"/api/v1/research/graph/questions/{qid}/promote-draft", params={"seq": 1}
    )
    assert first.status_code == 200
    assert first.json()["data"]["segments_found"] == []
    assert first.json()["data"]["pillar"]["break_threshold"] is None


# --- existing_pillars 投影(Architect 必修①) ---------------------------------


async def test_draft_surfaces_existing_active_pillars(ctx):
    """已有开放 mission + 活跃 pillar → draft 必须报出来(否则旧败支柱静默拖累 verdict)。"""
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    mid, pid = await _make_mission_with_pillar(db, question_title=TITLE, claim="旧结论")

    resp = await client.get(f"/api/v1/research/graph/questions/{qid}/promote-draft")
    assert resp.status_code == 200
    d = resp.json()["data"]
    assert d["existing_mission_id"] == mid
    assert len(d["existing_pillars"]) == 1
    assert d["existing_pillars"][0]["id"] == pid
    assert d["existing_pillars"][0]["claim"] == "旧结论"
    assert d["existing_pillars"][0]["status"] == "untested"


async def test_draft_excludes_retired_pillars(ctx):
    """retired 的支柱不算活跃 —— 不该出现在警告里(谓词与 aggregate_status 同源)。"""
    from selene_space_storage.repositories.mission_thesis import MissionThesisRepository

    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    mid, pid = await _make_mission_with_pillar(db, question_title=TITLE)
    await MissionThesisRepository(db).set_pillar_status(pid, "retired", actor="user")

    d = (
        await client.get(f"/api/v1/research/graph/questions/{qid}/promote-draft")
    ).json()["data"]
    assert d["existing_mission_id"] == mid
    assert d["existing_pillars"] == []


# --- override 参数 -----------------------------------------------------------


async def test_override_subject_refreshes_existing_lookup(ctx):
    """用户改主体后重发 → existing 查询按 override 值走(警告不失真)。"""
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    # 既有 mission 挂在 market 下,而 draft 建议是 strategy
    mid, _ = await _make_mission_with_pillar(db, question_title=TITLE)
    await db.execute(
        "UPDATE research_missions SET subject_type='market', subject_id='BTC/USDT' "
        "WHERE id = ?",
        (mid,),
    )

    # 不带 override → 按 draft 建议(strategy/STRATEGY_ID)查 → 不相交
    plain = (
        await client.get(f"/api/v1/research/graph/questions/{qid}/promote-draft")
    ).json()["data"]
    assert plain["existing_mission_id"] is None

    # 带 override 指向 market → 命中
    resp = await client.get(
        f"/api/v1/research/graph/questions/{qid}/promote-draft",
        params={"subject_type": "market", "subject_id": "BTC/USDT"},
    )
    d = resp.json()["data"]
    assert d["mission"]["subject_type"] == "market"
    assert d["existing_mission_id"] == mid


async def test_override_invalid_subject_type_422(ctx):
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    resp = await client.get(
        f"/api/v1/research/graph/questions/{qid}/promote-draft",
        params={"subject_type": "bogus", "subject_id": "x"},
    )
    assert resp.status_code == 422
    assert "subject_type" in resp.json()["detail"]


async def test_override_empty_subject_id_422(ctx):
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    resp = await client.get(
        f"/api/v1/research/graph/questions/{qid}/promote-draft",
        params={"subject_type": "strategy", "subject_id": ""},
    )
    assert resp.status_code == 422


# --- 错误路径 ----------------------------------------------------------------


async def test_unknown_question_404(ctx):
    client, _ = ctx
    resp = await client.get("/api/v1/research/graph/questions/q-nope/promote-draft")
    assert resp.status_code == 404


async def test_question_without_revision_409(ctx):
    """有问无答 → 409(先跑调查会),不是 500。"""
    client, db = ctx
    repo = ResearchQuestionRepository(db)
    qid = await repo.create(
        title="还没查过?", anchor={"strategy_ids": ["s1"]}, created_by="user"
    )
    resp = await client.get(f"/api/v1/research/graph/questions/{qid}/promote-draft")
    assert resp.status_code == 409
    assert "revision" in resp.json()["detail"]


async def test_unknown_seq_404(ctx):
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    resp = await client.get(
        f"/api/v1/research/graph/questions/{qid}/promote-draft", params={"seq": 99}
    )
    assert resp.status_code == 404


# --- Phase 2: promote 写入端点 ------------------------------------------------


def _promote_body(
    *,
    claim: str,
    subject_type: str = "strategy",
    subject_id: str = STRATEGY_ID,
    seq: int = 1,
) -> dict:
    return {
        "mission": {
            "title": "BTC 1h funding 延迟衰减",
            "question": TITLE,
            "subject_type": subject_type,
            "subject_id": subject_id,
        },
        "pillar": {"claim": claim, "priority": "core"},
        "revision_seq": seq,
    }


async def test_promote_happy_path_writes_three_tables(ctx):
    """happy path:mission + pillar + addresses 边三样都落。"""
    client, db = ctx
    qid, rev_id = await _seed_question_with_revision(db)
    before = await _table_counts(db)

    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote",
        json=_promote_body(claim="BTC 1h 上 half_delay=1,但仅是条件性机械点估计。"),
    )
    assert resp.status_code == 200, resp.text
    d = resp.json()["data"]
    assert d["mission_id"].startswith("mis-")
    assert d["pillar_id"].startswith("pil-")
    assert d["reused"] is False
    assert d["edge_written"] is True
    assert d["revision_id"] == rev_id

    after = await _table_counts(db)
    assert after["research_missions"] == before["research_missions"] + 1
    assert after["mission_pillars"] == before["mission_pillars"] + 1
    assert after["research_edges"] == before["research_edges"] + 1


async def test_promote_does_not_touch_question_status_or_events(ctx):
    """红线:promote 不改 question 状态、不写 question 事件流。"""
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    q_before = (
        await db.execute("SELECT status FROM research_questions WHERE id=?", (qid,))
    )[0]["status"]
    ev_before = (
        await db.execute(
            "SELECT COUNT(*) n FROM research_question_events WHERE question_id=?",
            (qid,),
        )
    )[0]["n"]

    await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote",
        json=_promote_body(claim="一个真正改写过的可证伪陈述。"),
    )

    q_after = (
        await db.execute("SELECT status FROM research_questions WHERE id=?", (qid,))
    )[0]["status"]
    ev_after = (
        await db.execute(
            "SELECT COUNT(*) n FROM research_question_events WHERE question_id=?",
            (qid,),
        )
    )[0]["n"]
    assert q_before == q_after
    assert ev_before == ev_after


async def test_promote_enforces_server_side_model_line(ctx):
    """必修③:客户端传 model_line 一律忽略,落库值由服务端写。"""
    client, db = ctx
    qid, rev_id = await _seed_question_with_revision(db)
    body = _promote_body(claim="改写过的陈述句,不再是问句。")
    body["pillar"]["model_line"] = "用户伪造的前缀 v99 fake"  # 应被忽略

    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote", json=body
    )
    assert resp.status_code == 200, resp.text
    pid = resp.json()["data"]["pillar_id"]
    row = (
        await db.execute("SELECT model_line FROM mission_pillars WHERE id=?", (pid,))
    )[0]
    assert row["model_line"] == f"council v1 {rev_id}"


async def test_promote_second_time_reuses_mission(ctx):
    """1:1 mission 不变量:同问题二次 promote → reused=true,且 pillar 数 2、边不重复。"""
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    first = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote",
        json=_promote_body(claim="第一版结论:条件性机械点估计。"),
    )
    assert first.status_code == 200
    mid = first.json()["data"]["mission_id"]

    edges_after_first = (await db.execute("SELECT COUNT(*) n FROM research_edges"))[0][
        "n"
    ]

    second = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote",
        json=_promote_body(claim="第二版结论:加入 bootstrap 后仍不稳健。"),
    )
    assert second.status_code == 200
    d2 = second.json()["data"]
    assert d2["reused"] is True
    assert d2["mission_id"] == mid
    assert d2["edge_written"] is False  # UNIQUE 幂等

    n_pillars = (
        await db.execute(
            "SELECT COUNT(*) n FROM mission_pillars WHERE mission_id=?", (mid,)
        )
    )[0]["n"]
    assert n_pillars == 2
    edges_after_second = (await db.execute("SELECT COUNT(*) n FROM research_edges"))[0][
        "n"
    ]
    assert edges_after_second == edges_after_first  # 边没重复


async def test_promote_rejects_claim_equal_to_title(ctx):
    """必修②:claim 硬门 —— 问号剥离后的 title 原样提交 → 422。"""
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote",
        json=_promote_body(claim="BTC 1h funding 延迟衰减:half_delay 在哪一档"),
    )
    assert resp.status_code == 422
    assert "可证伪陈述" in resp.json()["detail"]


async def test_promote_accepts_one_word_change(ctx):
    """硬门不过度:改一个词即过(脚手架不该变成摩擦)。"""
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote",
        json=_promote_body(claim="BTC 1h funding 延迟衰减:half_delay 在 delay=1 档"),
    )
    assert resp.status_code == 200, resp.text


async def test_promote_invalid_subject_type_422(ctx):
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote",
        json=_promote_body(claim="一个改写过的陈述。", subject_type="bogus"),
    )
    assert resp.status_code == 422


async def test_promote_invalid_provenance_422(ctx):
    """封闭词表:provenance 填自由文本 → 422(本会话踩过的 422 原型)。"""
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    body = _promote_body(claim="一个改写过的陈述。")
    body["pillar"]["provenance"] = "probe:entry_delay_ic 实测"
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote", json=body
    )
    assert resp.status_code == 422
    assert "provenance" in resp.json()["detail"]


async def test_promote_unknown_question_404(ctx):
    client, _ = ctx
    resp = await client.post(
        "/api/v1/research/graph/questions/q-nope/promote",
        json=_promote_body(claim="改写过的陈述。"),
    )
    assert resp.status_code == 404


async def test_promote_unknown_seq_404(ctx):
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote",
        json=_promote_body(claim="改写过的陈述。", seq=99),
    )
    assert resp.status_code == 404


async def test_promote_edge_is_visible_via_relations(ctx):
    """桥的图语义:promote 后 addresses 边经 list_relations 可查。"""
    from selene_space_storage.repositories.research_graph import list_relations

    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote",
        json=_promote_body(claim="改写过的陈述句。"),
    )
    mid = resp.json()["data"]["mission_id"]

    rels = await list_relations(db)
    hit = [
        r
        for r in rels
        if r["from_id"] == mid and r["to_id"] == qid and r["kind"] == "addresses"
    ]
    assert len(hit) == 1


# --- Phase 3: /view 投影(桥可见性) ------------------------------------------


async def test_view_shows_mission_node_and_edge_after_promote(ctx):
    """桥的可见性承重件:promote 后 /view 必须含 mission 节点 + question↔mission 边。

    没有这一条,addresses 边会被 valid_ids 过滤静默丢弃 = 桥在图谱上不可见。
    """
    client, db = ctx
    qid, _ = await _seed_question_with_revision(db)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/promote",
        json=_promote_body(claim="改写过的可证伪陈述,用于验证图可见性。"),
    )
    assert resp.status_code == 200
    mid = resp.json()["data"]["mission_id"]

    view = await client.get("/api/v1/research/graph/view")
    assert view.status_code == 200
    data = view.json()["data"]

    mission_nodes = [n for n in data["nodes"] if n["id"] == mid]
    assert len(mission_nodes) == 1, "mission 节点必须在 /view 里"
    assert mission_nodes[0]["category"] == "mission"
    assert mission_nodes[0]["subject_id"] == STRATEGY_ID

    bridge_links = [
        link
        for link in data["links"]
        if link["source"] == mid
        and link["target"] == qid
        and link["kind"] == "addresses"
    ]
    assert len(bridge_links) == 1, "question↔mission 的 addresses 边必须出现在 /view"


async def test_view_without_promote_has_no_mission_nodes(ctx):
    """无 promote 的库 → 视图无 mission 节点(不回退、不臆造)。"""
    client, db = ctx
    await _seed_question_with_revision(db)
    view = await client.get("/api/v1/research/graph/view")
    assert view.status_code == 200
    assert [n for n in view.json()["data"]["nodes"] if n["category"] == "mission"] == []
