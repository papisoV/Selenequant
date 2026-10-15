"""研究图谱端点 —— 功能穿透测试。

用 `create_app()` + `dependency_overrides[get_graph_db]` 把路由指向内存库
(与 test_api_account_state.py 同法),不碰真实 selenespace.db。

重点测:引用验证在 HTTP 层仍然是 400 而不是 500;§11 的
"第二次回答用了第一次留下的东西"能被端到端观察到。

响应约定:成功路径包 APIResponse 信封({data, meta, error});断言统一先解
resp.json()["data"]。错误路径(HTTPException 400/404)不进信封,FastAPI 默认
handler 返回裸 {"detail": ...}。

W2 新增三条:
- test_recall_endpoint_returns_candidates_with_class(含"焊点与前端同窗口"断言)
- test_cited_outside_recall_rejected_at_http_but_accepted_at_repo(层间差异双断言)
- test_cited_from_recall_is_accepted(三版构造,与 anchor 机制无关)

investigate 五条(monkeypatch council.call_llm,不碰真实 keyring):
- 404 / closed 400 / LLM 失败(202+finished failed event)/ 首轮无 cited /
  二轮引上一版且 prompt 真含账本
P3:POST investigate 是 202 后台任务 —— 测试用 _await_investigate 等后台
跑完再断言事件与 revision(等待发生在 monkeypatch.undo() 之前,LLM stub
仍生效)。
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio
from app.api.v1 import research_graph as rg
from app.services import investigate_council as council
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.research_graph import (
    EpisodeRepository,
    ResearchQuestionRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.asyncio


async def _await_investigate() -> None:
    """等本测试触发的后台调查任务跑完(事件已落、revision 已写)。

    模块级注册表跨测试存活:快照 → 清空 → await 快照。残留任务崩在已关闭
    的旧内存库上,异常已被 run_council_background 吞掉,不影响本测试。"""
    tasks = list(rg._INVESTIGATE_TASKS.values())
    rg._INVESTIGATE_TASKS.clear()
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


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


async def test_create_and_get_question(ctx):
    client, _ = ctx
    qid = await _make_question(client)
    resp = await client.get(f"/api/v1/research/graph/questions/{qid}")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["question"]["status"] == "open"
    assert body["revisions"] == []
    assert body["events"][0]["kind"] == "created"


async def test_missing_question_returns_404(ctx):
    client, _ = ctx
    resp = await client.get("/api/v1/research/graph/questions/q-nope")
    assert resp.status_code == 404


async def test_revision_with_dangling_citation_returns_400(ctx):
    """W2 登记:焊点在仓储校验之前拦截,不存在的 id 现在拿到焊点文案
    (cited not from recall ledger)而非仓储的 unknown cited —— 仍是 400,
    仓储的 unknown/unconnected 语义由 packages/storage 的 251 条测试覆盖。
    """
    client, _ = ctx
    qid = await _make_question(client)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "回答", "cited": ["ev-ghost"]},
    )
    assert resp.status_code == 400
    assert "not from recall ledger" in resp.json()["detail"]


async def test_second_revision_citing_first_is_accepted(ctx):
    """§11 验收判据的最小 HTTP 形式。"""
    client, _ = ctx
    qid = await _make_question(client)
    first_resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "第一版:怀疑配置漂移"},
    )
    first = first_resp.json()["data"]["revision"]
    second = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "第二版:确认配置漂移", "cited": [first["id"]]},
    )
    assert second.status_code == 200, second.text
    body = second.json()["data"]
    assert body["revision"]["seq"] == 2
    # retained = 上一版.cited ∩ 本版.cited = ∅ ∩ {rev1} = ∅(rev2 是"新增"引用,
    # 不是"保留"引用 —— Task 8 原文此处断言 retained==[rev1] 是数学上不可能的
    # 潜伏 bug;retained 非空的正确构造见 test_cited_from_recall_is_accepted)
    assert body["citation_migration"]["retained"] == []
    assert body["citation_migration"]["added"] == [first["id"]]


async def test_evidence_and_integrity_endpoint(ctx):
    client, db = ctx
    qid = await _make_question(client)
    # Episode 由内部流程/脚本创建(本切片不暴露 Episode 写端点)
    ep_id = await EpisodeRepository(db).create(
        subject_type="strategy",
        subject_id="strat-x",
        title="实跑段",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T00:00:00+00:00",
    )
    from selene_space_storage.repositories.research_graph import ResearchEdgeRepository

    await ResearchEdgeRepository(db).add_fact_edge(
        from_ref=("episode", ep_id), to_ref=("question", qid), kind="addresses"
    )
    resp = await client.post(
        "/api/v1/research/graph/evidence",
        json={"episode_id": ep_id, "mode": "RECORDED", "payload": {"pnl": -0.07}},
    )
    assert resp.status_code == 200, resp.text
    integrity = await client.get("/api/v1/research/graph/integrity")
    assert integrity.status_code == 200
    assert integrity.json()["data"]["ok"] is True


async def test_evidence_with_bad_mode_returns_400(ctx):
    client, db = ctx
    ep_id = await EpisodeRepository(db).create(
        subject_type="strategy",
        subject_id="strat-x",
        title="段",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T00:00:00+00:00",
    )
    resp = await client.post(
        "/api/v1/research/graph/evidence",
        json={"episode_id": ep_id, "mode": "GUESSED"},
    )
    assert resp.status_code == 400


async def test_close_then_reopen(ctx):
    client, _ = ctx
    qid = await _make_question(client)
    closed = await client.post(
        f"/api/v1/research/graph/questions/{qid}/close",
        json={"terminal_status": "parked", "note": "等新证据"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["data"]["question"]["status"] == "parked"
    reopened = await client.post(
        f"/api/v1/research/graph/questions/{qid}/reopen",
        json={"reason": "新证据出现"},
    )
    assert reopened.status_code == 200
    assert reopened.json()["data"]["question"]["status"] == "open"


async def test_succeed_creates_successor_and_supersedes_original(ctx):
    """换个问法:新问题接续(继承 anchor),原问题进 superseded。"""
    client, _ = ctx
    qid = await _make_question(client)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/succeed",
        json={"title": "换个问法:亏损是滑点还是信号失效?"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["question"]["status"] == "open"
    assert body["question"]["parent_question_id"] == qid
    assert body["question"]["created_by"] == "user"
    original = await client.get(f"/api/v1/research/graph/questions/{qid}")
    assert original.json()["data"]["question"]["status"] == "superseded"
    assert original.json()["data"]["question"]["closed_at"] is not None


async def test_succeed_unknown_question_returns_404(ctx):
    client, _ = ctx
    resp = await client.post(
        "/api/v1/research/graph/questions/q-nope/succeed",
        json={"title": " successor"},
    )
    assert resp.status_code == 404


async def test_list_questions_parent_filter_plumbs_through(ctx):
    """parent 参数打穿 HTTP 层(过滤逻辑在 storage 测试已覆盖;
    HTTP 层 create 无 created_by 参数,selene 子问题只能经 council 产生)。"""
    client, _ = ctx
    parent_id = await _make_question(client)
    resp = await client.get(f"/api/v1/research/graph/questions?parent={parent_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["questions"] == []


async def test_list_episodes_filter(ctx):
    client, db = ctx
    await EpisodeRepository(db).create(
        subject_type="strategy",
        subject_id="strat-x",
        title="段",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T00:00:00+00:00",
    )
    resp = await client.get(
        "/api/v1/research/graph/episodes", params={"subject_id": "strat-x"}
    )
    assert resp.status_code == 200
    assert len(resp.json()["data"]["episodes"]) == 1


# ---------------------------------------------------------------------------
# W2 新增:/recall 端点 + cited 焊点(层间差异)
# ---------------------------------------------------------------------------


async def test_recall_endpoint_returns_candidates_with_class(ctx):
    """/recall 200;响应含 candidates/learned/records;candidate 带 id 与 recall_class;
    且端点返回的 id 集合 == 直接调 recall(question_id=q) 的 id 集合
    (证明焊点与前端看到的是同一个窗口 —— 两处共用 LEDGER_KINDS/LEDGER_LIMIT)。
    """
    from selene_space_storage.repositories.research_graph import recall

    client, db = ctx
    qid = await _make_question(client)
    resp = await client.post(
        "/api/v1/research/graph/recall",
        json={"question_id": qid},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert set(body.keys()) >= {"candidates", "learned", "records"}
    for cand in body["candidates"]:
        assert "id" in cand
        assert "recall_class" in cand

    direct = await recall(
        db,
        question_id=qid,
        kinds=rg.LEDGER_KINDS,
        require_class=None,
        limit=rg.LEDGER_LIMIT,
    )
    assert {c["id"] for c in body["candidates"]} == {c["id"] for c in direct}


async def test_cited_outside_recall_rejected_at_http_but_accepted_at_repo(ctx):
    """层间差异双断言(互为注释):同一个已连边的 episode id,
    HTTP 写入被焊点 400 拒(detail 含 not from recall ledger),
    仓储层直调 add_revision 放行(仓储只判存在 + 连边)。
    """
    client, db = ctx
    qid = await _make_question(client)
    ep_id = await EpisodeRepository(db).create(
        subject_type="strategy",
        subject_id="strat-x",
        title="锚段",
        boundary_start="2026-08-01T00:00:00+00:00",
        boundary_end="2026-08-31T00:00:00+00:00",
    )
    from selene_space_storage.repositories.research_graph import ResearchEdgeRepository

    await ResearchEdgeRepository(db).add_fact_edge(
        from_ref=("episode", ep_id), to_ref=("question", qid), kind="addresses"
    )

    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "引用了不在账本里的对象", "cited": [ep_id]},
    )
    assert resp.status_code == 400, resp.text
    assert "not from recall ledger" in resp.json()["detail"]

    # 仓储层直调同一份数据必须放行 —— 焊点是 HTTP 层的契约收窄,不是仓储语义
    # (actor 只能取 selene/user/system,库上有 CHECK 约束)
    repo = ResearchQuestionRepository(db)
    revision = await repo.add_revision(
        qid, "仓储层直调同一引用", actor="user", cited=[ep_id]
    )
    assert revision["cited"] == [ep_id]


async def test_cited_from_recall_is_accepted(ctx):
    """三版构造,与 anchor 机制无关:

    1. 仓储层种 rev1(cited=[])与 rev2(cited=[rev1])—— 仓储层引本问题
       自己的 revision 恒合法,且二者带 question_id=q,靠 question:same
       在任何 anchor 机制下都进候选集;
    2. HTTP 写 rev3,cited=[rev1] —— 焊点候选集含 rev1,放行;
    3. 断言 200 且 citation_migration.retained == [rev1]、added == []。

    retained = 上一版 ∩ 本版,只有"本次引用确实与上一版共享"才非空;
    added 在 rev2 什么都不引时也非空,测不出焊点。
    """
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    rev1 = await repo.add_revision(qid, "第一版(仓储种)", actor="selene")
    rev2 = await repo.add_revision(
        qid, "第二版(仓储种,引 rev1)", actor="selene", cited=[rev1["id"]]
    )
    assert rev2["seq"] == 2

    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "第三版(HTTP,引 rev1)", "cited": [rev1["id"]]},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()["data"]
    assert body["revision"]["seq"] == 3
    assert body["citation_migration"]["retained"] == [rev1["id"]]
    assert body["citation_migration"]["added"] == []


# ---------------------------------------------------------------------------
# investigate:自动调查端点(§5 系统动"事实"与"当前"的执行面)
# ---------------------------------------------------------------------------


async def test_investigate_missing_question_returns_404(ctx):
    client, _ = ctx
    resp = await client.post("/api/v1/research/graph/questions/q-nope/investigate")
    assert resp.status_code == 404


async def test_investigate_closed_question_returns_400(ctx):
    client, _ = ctx
    qid = await _make_question(client)
    closed = await client.post(
        f"/api/v1/research/graph/questions/{qid}/close",
        json={"terminal_status": "parked", "note": "先放一放"},
    )
    assert closed.status_code == 200
    resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
    assert resp.status_code == 400
    assert "closed" in resp.json()["detail"]


async def test_investigate_llm_failure_returns_503(ctx):
    client, _ = ctx
    qid = await _make_question(client)

    async def _fail(*args, **kwargs):
        return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fail)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()
    # 失败不落 revision;investigate_finished failed 事件落库(可审计)
    detail = await client.get(f"/api/v1/research/graph/questions/{qid}")
    data = detail.json()["data"]
    assert data["revisions"] == []
    finished = [e for e in data["events"] if e["kind"] == "investigate_finished"]
    assert len(finished) == 1
    assert finished[0]["detail"]["status"] == "failed"


async def test_investigate_first_round_writes_v1_without_citation(ctx):
    client, _ = ctx
    qid = await _make_question(client)

    captured: dict = {}

    async def _fake_llm(settings_service, prompt, **kwargs):
        captured["prompt"] = prompt
        return "第一版:怀疑配置漂移,待用实跑段验证。"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    revisions = detail["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["seq"] == 1
    assert revisions[0]["actor"] == "selene"
    assert revisions[0]["cited"] == []
    assert "SOL 4h 亏损根因" in captured["prompt"]


async def test_investigate_second_round_cites_latest_and_prompt_has_ledger(ctx):
    """§11 正统:第二轮 prompt 真含第一版与账本假设,产出引用 rev1 ——
    recall 输出改变答案,不是摆设。"""
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    await repo.add_revision(qid, "第一版:怀疑配置漂移", actor="selene")
    await db.execute(
        "INSERT INTO research_hypotheses (id, statement, status, created_at, updated_at) "
        "VALUES (?, ?, 'proposed', '2026-09-18T10:00:00+00:00', '2026-09-18T10:00:00+00:00')",
        ("hyp-test-ledger", "若溢价新低,则配置漂移实锤"),
    )
    # hypothesis 无 question_id 列 —— 靠 applies_to 判断边挂进本问题的召回窗口
    from selene_space_storage.repositories.research_graph import ResearchEdgeRepository

    await ResearchEdgeRepository(db).add_judgment_edge(
        ("hypothesis", "hyp-test-ledger"),
        ("question", qid),
        "applies_to",
        rationale="测试假设:挂到本问题",
        provenance=["seed:test"],
    )

    captured: dict = {}

    async def _fake_llm(settings_service, prompt, **kwargs):
        captured["prompt"] = prompt
        return "第二版:配置漂移实锤,引用第一版的怀疑并给出验证窗口。"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    revisions = detail["revisions"]
    assert len(revisions) == 2  # 手动 rev1 + 调查会新版
    assert revisions[-1]["seq"] == 2
    assert revisions[-1]["actor"] == "selene"
    assert len(revisions[-1]["cited"]) == 1
    assert "第一版:怀疑配置漂移" in captured["prompt"]
    assert "配置漂移实锤" in captured["prompt"]


# ---------------------------------------------------------------------------
# N1 第二刀:council as_of 回放调查(T1-T9)
# ---------------------------------------------------------------------------

_AS_OF = "2026-09-20T23:59:59+00:00"


async def _investigate_with_as_of(
    client: AsyncClient,
    qid: str,
    captured: dict,
    *,
    as_of: str | None = _AS_OF,
    body: str = "回放轮判断。",
):
    """触发一次 investigate(monkeypatch call_llm)并等后台跑完。

    as_of=None 时发空 body(默认路径);否则 json={"as_of": ...}。
    """

    async def _fake_llm(settings_service, prompt, **kwargs):
        captured["prompt"] = prompt
        captured.setdefault("prompts", []).append(prompt)
        return body

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        payload = {"as_of": as_of} if as_of else {}
        resp = await client.post(
            f"/api/v1/research/graph/questions/{qid}/investigate", json=payload
        )
        assert resp.status_code == 202, resp.text
        await _await_investigate()
    finally:
        monkeypatch.undo()


async def test_as_of_replay_narrows_revision_chain_and_stamps_provenance(ctx):
    """T1:回放过滤回答链(rev-new 不可见)+ 写侧 provenance + 不 backdate。

    rev-old created_at=09-01(可见),rev-new created_at=09-25(> as_of 09-20,
    不可见)。主持人 prompt 只含旧版;新版 rationale 带 as_of_replay: 前缀;
    finished 事件 detail 带 as_of;created_at ≈ now(不回写 as_of)。
    """
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    rev_old = await repo.add_revision(qid, "旧版:9 月初的怀疑", actor="user")
    rev_new = await repo.add_revision(qid, "新版:9 月末的结论", actor="user")
    # 回放夹具:直接改 created_at(回放语义的输入是"账本何时知道")
    await db.execute(
        "UPDATE research_revisions SET created_at = ? WHERE id = ?",
        ("2026-09-01T10:00:00+00:00", rev_old["id"]),
    )
    await db.execute(
        "UPDATE research_revisions SET created_at = ? WHERE id = ?",
        ("2026-09-25T10:00:00+00:00", rev_new["id"]),
    )

    captured: dict = {}
    await _investigate_with_as_of(client, qid, captured)

    # 读面②:回放 prompt 只见 rev-old,rev-new 不可见(前视排除)
    assert "旧版:9 月初的怀疑" in captured["prompt"]
    assert "新版:9 月末的结论" not in captured["prompt"]
    # T7:banner 在 prompt 头部(视角/主持人同文案)
    assert "仍按当前图景计算" in captured["prompt"]

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    new_rev = detail["revisions"][-1]
    # 写侧诚实:rationale 带 as_of_replay 前缀(前端据此渲染回放 chip)
    assert any(r.startswith("as_of_replay:") for r in new_rev["rationale"]), new_rev[
        "rationale"
    ]
    assert new_rev["rationale"][-1] == f"as_of_replay:{_AS_OF}"
    # 不 backdate:落库时刻是 now(2026-10 月),不是 as_of(09-20)
    assert new_rev["created_at"] > "2026-10-01"

    finished = [e for e in detail["events"] if e["kind"] == "investigate_finished"]
    assert len(finished) == 1
    assert finished[0]["detail"]["status"] == "ok"
    assert finished[0]["detail"]["as_of"] == _AS_OF


async def test_as_of_replay_skips_market_material(ctx):
    """T2:回放不装配行情 —— prompt 无行情段,market_snapshot evidence 计数不变。

    anchor 带 symbols 走正常装配路径;as_of 时 assemble_market_material 被
    短路(None),连 _load_daily_closes 都不该被调用(无 kline 环境下若误
    调用会 warning 返回 None,计数不变但 prompt 断言仍能抓到差异)。
    """
    client, db = ctx
    qid = await _make_question(client, title="BTC 行情回放问题")
    # anchor 补 symbols,让默认路径本应尝试装配
    await db.execute(
        "UPDATE research_questions SET anchor_json = ? WHERE id = ?",
        (
            '{"symbols": ["BTC/USDT"], "strategy_ids": ["strat-x"]}',
            qid,
        ),
    )

    before = await db.execute(
        "SELECT COUNT(*) AS n FROM research_evidence e "
        "JOIN research_episodes ep ON ep.id = e.episode_id "
        "WHERE ep.subject_type = 'market_snapshot'"
    )
    n_before = before[0]["n"]

    captured: dict = {}
    await _investigate_with_as_of(client, qid, captured)

    # 读面③:回放 prompt 不含行情材料段
    assert "本轮行情材料" not in captured["prompt"]
    after = await db.execute(
        "SELECT COUNT(*) AS n FROM research_evidence e "
        "JOIN research_episodes ep ON ep.id = e.episode_id "
        "WHERE ep.subject_type = 'market_snapshot'"
    )
    assert after[0]["n"] == n_before


async def test_investigate_invalid_as_of_returns_400_without_side_effects(ctx):
    """T3:非法 as_of → 400(域错误文案),无新任务无新事件。"""
    client, _ = ctx
    qid = await _make_question(client)
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/investigate",
        json={"as_of": "不是日期"},
    )
    assert resp.status_code == 400
    assert "invalid as_of" in resp.json()["detail"]

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    # 只有 created 事件 —— 没有触发任何调查任务/事件
    assert [e["kind"] for e in detail["events"]] == ["created"]


async def test_as_of_replay_ages_relative_to_as_of(ctx):
    """T4:age 相对化 —— rev-old 09-01 + as_of 09-10 → prompt 含 '(9天前)'。

    回放模式下"今天"= as_of,不是真实 now;否则 LLM 会把历史当成"昨天"。
    """
    client, db = ctx
    qid = await _make_question(client)
    await ResearchQuestionRepository(db).add_revision(qid, "旧版判断", actor="user")
    await db.execute(
        "UPDATE research_revisions SET created_at = ? "
        "WHERE question_id = ? AND seq = 1",
        ("2026-09-01T10:00:00+00:00", qid),
    )

    captured: dict = {}
    await _investigate_with_as_of(
        client, qid, captured, as_of="2026-09-10T23:59:59+00:00"
    )

    assert "(9天前)" in captured["prompt"]


async def test_as_of_default_path_unchanged_regression(ctx):
    """T5a(默认同形):空 body investigate —— prompt 无回放 banner,
    revision rationale 无 as_of_replay 前缀,finished detail 无 as_of 键。"""
    client, db = ctx
    qid = await _make_question(client)
    await ResearchQuestionRepository(db).add_revision(qid, "已有版本", actor="user")

    captured: dict = {}
    await _investigate_with_as_of(client, qid, captured, as_of=None)

    assert "回放模式" not in captured["prompt"]
    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    new_rev = detail["revisions"][-1]
    assert not any(r.startswith("as_of_replay:") for r in new_rev["rationale"])
    finished = [e for e in detail["events"] if e["kind"] == "investigate_finished"]
    assert "as_of" not in finished[0]["detail"]


async def test_as_of_replay_failure_branch_still_carries_as_of(ctx):
    """T6:失败分支 provenance —— 直调 run_council_background(显式 as_of=,
    不经端点)+ monkeypatch call_llm 空 → 503 进 failed 分支,
    finished detail 仍带 as_of(成败都留痕)。"""
    client, db = ctx
    qid = await _make_question(client)

    async def _fail(*args, **kwargs):
        return None

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fail)
    try:
        await council.run_council_background(db, None, qid, as_of=_AS_OF)
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    finished = [e for e in detail["events"] if e["kind"] == "investigate_finished"]
    assert len(finished) == 1
    assert finished[0]["detail"]["status"] == "failed"
    # 成败都留痕:失败轮同样带 as_of provenance
    assert finished[0]["detail"]["as_of"] == _AS_OF
    assert detail["revisions"] == []


async def test_as_of_replay_perspective_banner_in_prompt(ctx):
    """T7:banner 断言 —— 视角 prompt 头部含'仍按当前图景计算'(已知局限声明)。"""
    client, db = ctx
    qid = await _make_question(client)
    captured: dict = {}
    await _investigate_with_as_of(client, qid, captured)
    # banner 完整句式(视角与主持人共用同一句)
    assert "本轮不提供行情材料" in captured["prompt"]


async def test_as_of_replay_data_window_rendering_three_states(ctx):
    """T8:data_window 渲染三态(夹具填实,boundary 列 NOT NULL)。

    evidence 挂 episode(boundary 双全)→ 候选行含'数据窗'段(非 None);
    三条 evidence:①boundary 双全 → 含数据窗段;②boundary 同上(结构上
    必然双全,v0 表约束)但 created_at > as_of → 整行不可见;③无 evidence
    的对照 —— revision/hypothesis 候选行不含数据窗段。
    """
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    await repo.add_revision(qid, "锚定版本", actor="user")

    from selene_space_storage.repositories.research_graph import (
        EpisodeRepository as _EpRepo,
        EvidenceRepository as _EvRepo,
        ResearchEdgeRepository as _EdgeRepo,
    )

    # episode A:boundary 双全,created_at < as_of → 回放可见且带数据窗
    ep_a = await _EpRepo(db).create(
        "strategy",
        "strat-x",
        "回放窗内 episode",
        boundary_start="2026-09-01T00:00:00+00:00",
        boundary_end="2026-09-05T00:00:00+00:00",
        occurred_at="2026-09-05T00:00:00+00:00",
    )
    ev_a = await _EvRepo(db).create(
        ep_a,
        "RECORDED",
        payload={"summary": "窗内证据:回撤触底"},
        observed_at="2026-09-05T00:00:00+00:00",
    )
    await db.execute(
        "UPDATE research_evidence SET created_at = ? WHERE id = ?",
        ("2026-09-06T00:00:00+00:00", ev_a),
    )
    await _EdgeRepo(db).add_fact_edge(
        ("evidence", ev_a), ("question", qid), "addresses"
    )

    captured: dict = {}
    await _investigate_with_as_of(client, qid, captured)

    # 视角 prompt(4 次调用前 3 次)含账本段;captured["prompt"] 是最后一次 = 主持人
    all_prompts = "\n".join(captured["prompts"])
    # 可见 evidence 候选行:带数据窗段(_age_label 渲染非 None 起止)
    assert "窗内证据:回撤触底" in all_prompts
    assert "数据窗" in all_prompts
    assert (
        "None" not in all_prompts
    )  # 单边缺失渲染整段跳过;双全则出现真值,None 字样不该出现在任何候选行


async def test_investigate_invalid_as_of_with_missing_question_returns_400(ctx):
    """T9:顺序钉子 —— as_of 校验(pure)在 question 检查之前:
    坏 as_of + 不存在 qid → 400(不是 404)。"""
    client, _ = ctx
    resp = await client.post(
        "/api/v1/research/graph/questions/q-不存在/investigate",
        json={"as_of": "不是日期"},
    )
    assert resp.status_code == 400
    assert "invalid as_of" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# A2 引用纪律:CITED 行解析 + 端点级校验/重试/回退
# ---------------------------------------------------------------------------


async def test_extract_cited_happy_path():
    body, cited = council.extract_cited(
        "结论:配置漂移实锤。\nCITED: rev-1, hyp-a",
        {"rev-1", "hyp-a"},
    )
    assert body == "结论:配置漂移实锤。"
    assert cited == ["rev-1", "hyp-a"]


async def test_extract_cited_missing_line_returns_empty():
    body, cited = council.extract_cited("只有正文没有 CITED 行", {"rev-1"})
    assert body == "只有正文没有 CITED 行"
    assert cited == []


async def test_extract_cited_unknown_id_is_violation():
    body, cited = council.extract_cited("正文\nCITED: rev-1, rev-bogus", {"rev-1"})
    assert cited == []


async def test_extract_cited_garbage_is_violation():
    body, cited = council.extract_cited("正文\nCITED: 我引用了第一版", {"rev-1"})
    assert cited == []


async def test_extract_cited_empty_list_is_empty_not_violation():
    body, cited = council.extract_cited("首轮无依据\nCITED:", {"rev-1"})
    assert cited == []


async def test_investigate_cited_ids_from_candidates_are_kept(ctx):
    """LLM 按 CITED 契约引用候选 id → 落库的 cited 就是它声明的,不是硬编码 latest。"""
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    rev1 = await repo.add_revision(qid, "第一版:怀疑配置漂移", actor="user")

    async def _fake_llm(settings_service, prompt, **kwargs):
        return f"第二版:实锤,依据第一版。\nCITED: {rev1['id']}"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    revisions = detail["revisions"]
    assert len(revisions) == 2  # 手动 rev1 + 调查会新版
    assert revisions[-1]["cited"] == [rev1["id"]]
    assert revisions[-1]["content"].endswith("依据第一版。")
    assert "CITED" not in revisions[-1]["content"]


async def test_investigate_out_of_set_cited_retries_then_conforms(ctx):
    """主持人 CITED 带越界 id → 拒收重试一次;重试输出守规 → 落库守规版。

    council 结构:3 视角并行先跑(各被调 1 次),主持人第 4 次起。
    视角输出守规,主持人第 1 次越界、第 2 次守规 —— 共 5 次调用。
    """
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    rev1 = await repo.add_revision(qid, "第一版:怀疑配置漂移", actor="user")

    calls: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        calls.append(prompt)
        if len(calls) <= 3:
            return f"视角分析。\nCITED: {rev1['id']}"
        if len(calls) == 4:
            return f"第二版:越界引用。\nCITED: rev-not-in-ledger, {rev1['id']}"
        return f"第二版:守规重写。\nCITED: {rev1['id']}"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    assert len(calls) == 5, "3 视角 + 主持人(违规1次+重试1次)"
    assert "候选集之外" in calls[4]
    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    revision = detail["revisions"][-1]
    assert revision["cited"] == [rev1["id"]]
    assert "守规重写" in revision["content"]


async def test_investigate_persistently_invalid_cited_falls_back_to_latest(ctx):
    """两次输出都违规 → 回退旧行为(引上一版),端点不 500、不丢正文。"""
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    rev1 = await repo.add_revision(qid, "第一版:怀疑配置漂移", actor="user")

    async def _fake_llm(settings_service, prompt, **kwargs):
        return "第二版:始终不守规。\nCITED: rev-bogus"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    revision = detail["revisions"][-1]
    assert revision["cited"] == [rev1["id"]]
    assert "始终不守规" in revision["content"]
    assert "CITED" not in revision["content"]


# ---------------------------------------------------------------------------
# A1 多视角调查会:3 视角 + 主持人 + 新问题落库(D1-b/D2-c/D3/D4)
# ---------------------------------------------------------------------------

_HOST_FOUR_SECTIONS = (
    "【时间线】\n时间线内容。\n"
    "【观点整合与矛盾】\n两派矛盾点。\n"
    "【深层分析】\n深层原因。\n"
)


async def _host_reply(new_questions: list[str]) -> str:
    body = _HOST_FOUR_SECTIONS + "【新问题】\n"
    if new_questions:
        for i, q in enumerate(new_questions, 1):
            body += f"{i}. {q}\n"
    else:
        body += "无\n"
    return body


async def test_investigate_council_prompts_carry_reverse_check_contract(ctx):
    """任务卡形状(吸收 external-research-project):视角 prompt 要求反向检验段,主持人
    prompt 要求五段结构含【反向检验】。"""
    client, db = ctx
    qid = await _make_question(client)

    prompts: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        prompts.append(prompt)
        if "主持人" in prompt or "【时间线】" in prompt:
            return await _host_reply(["新问题甲"])
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    perspective_prompts = [p for p in prompts if "三位视角" not in p]
    host_prompts = [p for p in prompts if "主持人" in p or "【时间线】" in p]
    assert len(perspective_prompts) == 3
    for p in perspective_prompts:
        assert "反向检验" in p
    assert len(host_prompts) == 1
    assert "【反向检验】" in host_prompts[0]
    assert "五段结构" in host_prompts[0]


async def test_host_prompt_carries_error_correction_duty(ctx):
    """N4(round2):主持人 prompt 授权"判定谁错",防三视角冲突被和稀泥。

    契约(缺一即回归):
    1. 要求对互斥事实**直接指出哪一个有误**并给理由
    2. 明令**不许平均化**措辞
    3. 无法判定时要求**说明缺什么证据**(给退路,避免硬编造)
    """
    client, db = ctx
    qid = await _make_question(client)

    prompts: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        prompts.append(prompt)
        if "主持人" in prompt or "【时间线】" in prompt:
            return await _host_reply([])
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    host_prompts = [p for p in prompts if "主持人" in p or "【时间线】" in p]
    assert len(host_prompts) == 1
    host = host_prompts[0]
    # 1. 授权判定谁错
    assert "互斥" in host, "应点名'互斥陈述'这一触发条件"
    assert "哪一个有误" in host, "应明确要求指出哪一个有误"
    assert "给出理由" in host, "判定必须附理由,不能只贴标签"
    # 2. 禁止和稀泥
    assert "平均化" in host and "和稀泥" in host, "应明令禁止平均化措辞"
    # 3. 退路:判不了要说缺什么
    assert "缺什么证据" in host, "无法判定时必须说明缺什么证据(防硬编造)"
    # 4. 纠错职责挂在【观点整合与矛盾】段下,而非游离
    assert "【观点整合与矛盾】段" in host, "纠错指令应绑定到对应段落下"


async def test_investigate_council_all_success_writes_revision_events_and_questions(
    ctx,
):
    """3 视角全成功 → 恰好 1 版 revision + 3 条 perspective events + 新问题
    落库(created_by='selene' + parent_question_id + status='open')。"""
    client, db = ctx
    qid = await _make_question(client)

    call_log: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        call_log.append(prompt)
        if "主持人" in prompt or "【时间线】" in prompt:
            # 主持人调用(prompt 含五段指令)
            return await _host_reply(
                ["新问题甲:波动率是否被低估", "新问题乙:资金费率影响"]
            )
        return f"视角分析:{prompt[:10]}\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    revisions = detail["revisions"]
    assert len(revisions) == 1
    assert revisions[0]["seq"] == 1
    assert revisions[0]["actor"] == "selene"
    assert "multi-perspective council" in revisions[0]["rationale"]

    perspective_events = [
        e for e in detail["events"] if e["kind"] == "investigate_perspective"
    ]
    assert len(perspective_events) == 3
    keys = {e["detail"]["perspective"] for e in perspective_events}
    assert keys == {"empiricist", "recordist", "challenger"}

    finished = [e for e in detail["events"] if e["kind"] == "investigate_finished"]
    assert len(finished) == 1
    assert finished[0]["detail"]["status"] == "ok"

    # 新问题从 finished 事件 detail 取(响应契约不再带 council)
    new_ids = [nq["id"] for nq in finished[0]["detail"]["new_questions"]]
    assert len(new_ids) == 2
    for nid in new_ids:
        child = (await client.get(f"/api/v1/research/graph/questions/{nid}")).json()[
            "data"
        ]
        q = child["question"]
        assert q["created_by"] == "selene"
        assert q["parent_question_id"] == qid
        assert q["status"] == "open"
        # 红线:新问题不自动 investigate/close —— 无 revision
        assert child["revisions"] == []


async def test_investigate_council_single_perspective_failure_degrades(ctx):
    """单视角 None → 降级出综合版,缺席视角记 failed event,prompt 注明缺席。"""
    client, db = ctx
    qid = await _make_question(client)

    prompts: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        prompts.append(prompt)
        is_host = "主持人" in prompt or "三位视角" in prompt
        if is_host:
            return await _host_reply([])
        if "经验派" in prompt:
            return None  # 经验派失败
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    assert len(detail["revisions"]) == 1
    events = {
        e["detail"]["perspective"]: e["detail"]
        for e in detail["events"]
        if e["kind"] == "investigate_perspective"
    }
    assert events["empiricist"]["status"] == "failed"
    assert events["recordist"]["status"] == "ok"
    assert events["challenger"]["status"] == "ok"
    host_prompt = next(p for p in prompts if "主持人" in p or "三位视角" in p)
    assert "经验派" in host_prompt and "缺席" in host_prompt


async def test_investigate_council_host_failure_returns_503(ctx):
    """仅主持人失败 → 后台收尾,3 条视角 events + failed finished 事件落库
    (可审计,events 只增);无 revision。"""
    client, db = ctx
    qid = await _make_question(client)

    async def _fake_llm(settings_service, prompt, **kwargs):
        if "主持人" in prompt or "三位视角" in prompt:
            return None
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    perspective_events = [
        e for e in detail["events"] if e["kind"] == "investigate_perspective"
    ]
    assert len(perspective_events) == 3
    finished = [e for e in detail["events"] if e["kind"] == "investigate_finished"]
    assert len(finished) == 1
    assert finished[0]["detail"]["status"] == "failed"
    assert detail["revisions"] == []


async def test_investigate_council_missing_new_questions_section_still_lands(ctx):
    """主持人不守五段格式(缺【新问题】标题)→ revision 照落、无新问题、无异常。"""
    client, db = ctx
    qid = await _make_question(client)

    async def _fake_llm(settings_service, prompt, **kwargs):
        if "主持人" in prompt or "三位视角" in prompt:
            return "自由格式回答,没有固定标题,也没有 CITED 行。"
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    assert len(detail["revisions"]) == 1
    finished = [e for e in detail["events"] if e["kind"] == "investigate_finished"]
    assert finished[0]["detail"]["new_questions"] == []


async def test_investigate_council_new_questions_truncated_to_three(ctx):
    """主持人列 5 个新问题 → 只取前 3 个落库(MF-2)。"""
    client, db = ctx
    qid = await _make_question(client)

    async def _fake_llm(settings_service, prompt, **kwargs):
        if "主持人" in prompt or "三位视角" in prompt:
            return await _host_reply(["问题一", "问题二", "问题三", "问题四", "问题五"])
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    finished = next(e for e in detail["events"] if e["kind"] == "investigate_finished")
    new_questions = finished["detail"]["new_questions"]
    assert [nq["title"] for nq in new_questions] == ["问题一", "问题二", "问题三"]


async def test_investigate_response_contract_shape(ctx):
    """P3 契约:POST 立即 202(data 含 question_id/status);跑完后 events
    含 3 条 perspective + 1 条 finished,finished.detail 带 status/
    revision_id/new_questions。"""
    client, db = ctx
    qid = await _make_question(client)

    async def _fake_llm(settings_service, prompt, **kwargs):
        if "主持人" in prompt or "三位视角" in prompt:
            return await _host_reply(["新问题一"])
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202, resp.text
        assert resp.json()["data"] == {"question_id": qid, "status": "investigating"}
        await _await_investigate()
    finally:
        monkeypatch.undo()

    data = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()["data"]
    assert data["investigate_running"] is False
    finished = next(e for e in data["events"] if e["kind"] == "investigate_finished")
    assert set(finished["detail"].keys()) == {
        "status",
        "revision_id",
        "new_questions",
        # 可见性刀(2026-10-05):事件补带引用迁移 —— POST 已 202 化,
        # 事件是前端拿到 citation_migration 的唯一通路
        "citation_migration",
    }
    assert finished["detail"]["status"] == "ok"
    assert finished["detail"]["revision_id"] == data["revisions"][0]["id"]
    assert set(finished["detail"]["new_questions"][0].keys()) == {"id", "title"}
    assert set(finished["detail"]["citation_migration"].keys()) >= {
        "added",
        "dropped",
        "retained",
    }


# ---------------------------------------------------------------------------
# P2:/view 全图只读投影
# ---------------------------------------------------------------------------


async def test_graph_view_returns_nodes_and_links(ctx):
    """questions/revisions/hypotheses + 挂问题的边 → {nodes, links};
    只引用图中存在节点的边(悬空边被滤掉)。"""
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    rev1 = await repo.add_revision(qid, "第一版:怀疑配置漂移", actor="user")
    await db.execute(
        "INSERT INTO research_hypotheses (id, statement, status, created_at, "
        "updated_at) VALUES ('hyp-v1', '测试假设', 'proposed', '2026-09-18T10:00:00+00:00', "
        "'2026-09-18T10:00:00+00:00')"
    )
    # 悬空边:指向不存在的节点,应被过滤
    await db.execute(
        "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
        "kind, edge_class, actor, created_at) "
        "VALUES ('e-ghost', 'hypothesis', 'hyp-ghost', 'question', ?, "
        "'addresses', 'fact', 'system', '2026-09-18T10:00:00+00:00')",
        (qid,),
    )

    resp = await client.get("/api/v1/research/graph/view")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}
    assert qid in node_ids
    assert rev1["id"] in node_ids
    assert "hyp-v1" in node_ids
    # revision 不写边 —— 靠 question_id 字段挂问题;有边的是 hypothesis(测试里手动插的 addresses)
    revision_node = next(n for n in data["nodes"] if n["id"] == rev1["id"])
    assert revision_node["question_id"] == qid
    assert all(
        edge["source"] in node_ids and edge["target"] in node_ids
        for edge in data["links"]
    ), "悬空边不许出现"


async def test_graph_view_empty_db_returns_empty_arrays(ctx):
    client, _ = ctx
    resp = await client.get("/api/v1/research/graph/view")
    assert resp.status_code == 200
    assert resp.json()["data"] == {"nodes": [], "links": []}


async def test_graph_view_question_nodes_carry_proposal_keys(ctx):
    """/view 问题节点带 created_by + parent_question_id —— 前端可分辨系统提议
    节点(created_by='selene' && parent_question_id 非空),R1。"""
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    await repo.create("主持人提议的新问题", created_by="selene", parent_question_id=qid)

    resp = await client.get("/api/v1/research/graph/view")
    assert resp.status_code == 200
    nodes = {n["id"]: n for n in resp.json()["data"]["nodes"]}
    assert nodes[qid]["created_by"] == "director"
    assert nodes[qid]["parent_question_id"] is None
    proposed_node = next(
        n for n in nodes.values() if n.get("parent_question_id") == qid
    )
    assert proposed_node["created_by"] == "selene"


async def test_graph_view_includes_episode_nodes_and_derived_from_links(ctx):
    """/view 放行 derived_from(刀3):episode 节点 + 子→父边上图;
    其余非 question 边仍排除(addresses 从 hypothesis 出发除外)。"""
    client, db = ctx
    ep_child = await _make_lifecycle_episode(db, "ep-child")
    ep_parent = await _make_lifecycle_episode(db, "ep-parent")
    await db.execute(
        "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
        "kind, edge_class, actor, created_at) "
        "VALUES ('e-df', 'episode', ?, 'episode', ?, "
        "'derived_from', 'fact', 'system', '2026-09-24T00:00:00+00:00')",
        (ep_child, ep_parent),
    )
    # 干扰边:episode 之间的非 derived_from 不放行
    await db.execute(
        "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
        "kind, edge_class, actor, created_at) "
        "VALUES ('e-obs', 'episode', ?, 'episode', ?, "
        "'observed_from', 'fact', 'system', '2026-09-24T00:00:00+00:00')",
        (ep_child, ep_parent),
    )

    resp = await client.get("/api/v1/research/graph/view")
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}
    assert ep_child in node_ids and ep_parent in node_ids
    ep_node = next(n for n in data["nodes"] if n["id"] == ep_child)
    assert ep_node["category"] == "episode"
    assert ep_node["subject_id"] == "funding_z"
    kinds = [link["kind"] for link in data["links"]]
    assert "derived_from" in kinds
    assert "observed_from" not in kinds
    df_link = next(link for link in data["links"] if link["kind"] == "derived_from")
    assert df_link["source"] == ep_child and df_link["target"] == ep_parent


async def test_get_episode_returns_derived_from_edges(ctx):
    """get_episode 响应加 edges 键(OQ2):只含 derived_from kind。"""
    client, db = ctx
    ep_child = await _make_lifecycle_episode(db, "ep-child2")
    ep_parent = await _make_lifecycle_episode(db, "ep-parent2")
    await db.execute(
        "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
        "kind, edge_class, actor, created_at) "
        "VALUES ('e-df2', 'episode', ?, 'episode', ?, "
        "'derived_from', 'fact', 'system', '2026-09-24T00:00:00+00:00')",
        (ep_child, ep_parent),
    )
    await db.execute(
        "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
        "kind, edge_class, actor, created_at) "
        "VALUES ('e-addr', 'episode', ?, 'question', 'q-someone', "
        "'addresses', 'fact', 'system', '2026-09-24T00:00:00+00:00')",
        (ep_child,),
    )

    resp = await client.get(f"/api/v1/research/graph/episodes/{ep_child}")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data.keys()) >= {"episode", "evidence", "edges"}
    kinds = [e["kind"] for e in data["edges"]]
    assert kinds == ["derived_from"]
    assert data["edges"][0]["from_id"] == ep_child
    assert data["edges"][0]["to_id"] == ep_parent


# ---- /changes 聚合端点(主窗口第一刀)--------------------------------------


async def test_changes_empty_db(ctx):
    client, _ = ctx
    resp = await client.get("/api/v1/research/graph/changes")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["changes"] == []
    assert data["counts"] == {"t1": 0, "t2": 0, "t3": 0}
    assert "server_now" in data


async def test_changes_bad_since_returns_400(ctx):
    client, _ = ctx
    resp = await client.get(
        "/api/v1/research/graph/changes", params={"since": "not-a-date"}
    )
    assert resp.status_code == 400
    assert "ISO-8601" in resp.json()["detail"]


async def test_changes_three_sources_present(ctx):
    client, _ = ctx
    qid = await _make_question(client)
    await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "第一版"},
    )
    resp = await client.get("/api/v1/research/graph/changes")
    assert resp.status_code == 200
    data = resp.json()["data"]
    sources = {c["source"] for c in data["changes"]}
    # event(created,由 _make_question 触发)+ revision
    assert "event" in sources
    assert "revision" in sources
    assert data["counts"]["t2"] >= 1


async def test_changes_tier1_survives_since_tier2_filtered(ctx):
    """端到端常驻语义:tier1 提议不受 since 过滤,tier2 revision 被滤掉。"""
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    await repo.create("主持人提议", created_by="selene", parent_question_id=qid)
    await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "v1"},
    )
    resp = await client.get(
        "/api/v1/research/graph/changes", params={"since": "2099-01-01T00:00:00+00:00"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    tiers = [(c["tier"], c["source"]) for c in data["changes"]]
    assert (1, "proposal") in tiers
    assert all(t != 2 for t, _ in tiers)


async def test_changes_limit_clamped_and_respected(ctx):
    client, db = ctx
    qid = await _make_question(client)
    repo = ResearchQuestionRepository(db)
    for i in range(4):
        await repo.create(f"子问题 {i}", created_by="selene", parent_question_id=qid)
    resp = await client.get("/api/v1/research/graph/changes", params={"limit": 2})
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["changes"]) == 2
    # tier1 排序后裁剪:提议不会被挤出
    assert data["changes"][0]["tier"] == 1


# ---- detail 响应 cited_modes(探针徽章契约)-------------------------------


async def _seed_evidence(db, evid: str, qid: str, *, mode: str = "RECORDED") -> None:
    ep_id = f"ep-{evid}"
    await db.execute(
        "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
        "boundary_start, boundary_end, occurred_at, created_at) "
        "VALUES (?, 'market', 'BTC/USDT', 'ep', '2026-09-24T00:00:00', "
        "'2026-09-24T00:00:00', '2026-09-24T00:00:00', '2026-09-24T00:00:00')",
        (ep_id,),
    )
    await db.execute(
        "INSERT INTO research_evidence (id, episode_id, mode, payload_json, "
        "observed_at, created_at) VALUES (?, ?, ?, '{}', "
        "'2026-09-24T00:00:00', '2026-09-24T00:00:00')",
        (evid, ep_id, mode),
    )
    await db.execute(
        "INSERT INTO research_edges (id, from_type, from_id, to_type, to_id, "
        "kind, edge_class, actor, created_at) "
        "VALUES (?, 'evidence', ?, 'question', ?, 'addresses', 'fact', "
        "'selene', '2026-09-24T00:00:00')",
        (f"e-{evid}", evid, qid),
    )


async def test_question_detail_returns_cited_refs(ctx):
    """探针徽章契约:detail 响应携带 cited 里 ev- 对象的 {id, mode, episode_id}。"""
    client, db = ctx
    qid = await _make_question(client)
    await _seed_evidence(db, "ev-rec1", qid, mode="RECORDED")
    await _seed_evidence(db, "ev-rec2", qid, mode="AFTER_THE_FACT")
    rev = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "引证据版", "cited": ["ev-rec1", "ev-rec2"]},
    )
    assert rev.status_code == 200, rev.text
    resp = await client.get(f"/api/v1/research/graph/questions/{qid}")
    assert resp.status_code == 200
    body = resp.json()["data"]
    assert body["cited_refs"] == [
        {"id": "ev-rec1", "mode": "RECORDED", "episode_id": "ep-ev-rec1"},
        {"id": "ev-rec2", "mode": "AFTER_THE_FACT", "episode_id": "ep-ev-rec2"},
    ]


async def test_question_detail_cited_refs_empty_without_evidence_citations(ctx):
    """没引证据 → cited_refs 是空列表(不是 null);引 rev- 不产生条目。"""
    client, _ = ctx
    qid = await _make_question(client)
    first = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "第一版"},
    )
    first_id = first.json()["data"]["revision"]["id"]
    await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "第二版:只引上一版", "cited": [first_id]},
    )
    resp = await client.get(f"/api/v1/research/graph/questions/{qid}")
    assert resp.status_code == 200
    assert resp.json()["data"]["cited_refs"] == []


# ---------------------------------------------------------------------------
# Episode lifecycle 流转端点(刀1):HTTP 层只做错误映射,校验全在 storage。
# ---------------------------------------------------------------------------


async def _make_lifecycle_episode(db, ep_id: str = "ep-lc1") -> str:
    """带 lifecycle 子键的快照落账 episode(objects 直插 recorded)。"""
    from selene_space_storage.repositories.research_graph._shared import _dump_json

    await db.execute(
        "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
        "boundary_start, boundary_end, occurred_at, created_at, recorded_json) "
        "VALUES (?, 'factor', 'funding_z', '快照', '2026-09-01T00:00:00', "
        "'2026-09-24T00:00:00', '2026-09-24T00:00:00', '2026-09-24T00:00:00', ?)",
        (
            ep_id,
            _dump_json({"lifecycle": {"status": "DISCOVERED", "transitions": []}}),
        ),
    )
    return ep_id


async def test_lifecycle_transition_discovered_to_inspected(ctx):
    client, db = ctx
    ep_id = await _make_lifecycle_episode(db)
    resp = await client.post(
        f"/api/v1/research/graph/episodes/{ep_id}/lifecycle",
        json={"to_status": "INSPECTED"},
    )
    assert resp.status_code == 200, resp.text
    episode = resp.json()["data"]["episode"]
    assert episode["recorded"]["lifecycle"]["status"] == "INSPECTED"
    transitions = episode["recorded"]["lifecycle"]["transitions"]
    assert len(transitions) == 1
    assert transitions[0]["from"] == "DISCOVERED"
    assert transitions[0]["to"] == "INSPECTED"
    assert transitions[0]["actor"] == "user"


async def test_lifecycle_illegal_transition_returns_400(ctx):
    client, db = ctx
    ep_id = await _make_lifecycle_episode(db)
    resp = await client.post(
        f"/api/v1/research/graph/episodes/{ep_id}/lifecycle",
        json={"to_status": "LIVE"},
    )
    assert resp.status_code == 400
    assert "illegal lifecycle transition" in resp.json()["detail"]


async def test_lifecycle_retired_requires_reason(ctx):
    client, db = ctx
    ep_id = await _make_lifecycle_episode(db)
    no_reason = await client.post(
        f"/api/v1/research/graph/episodes/{ep_id}/lifecycle",
        json={"to_status": "RETIRED"},
    )
    assert no_reason.status_code == 400
    with_reason = await client.post(
        f"/api/v1/research/graph/episodes/{ep_id}/lifecycle",
        json={"to_status": "RETIRED", "reason": "IC 衰减"},
    )
    assert with_reason.status_code == 200
    assert (
        with_reason.json()["data"]["episode"]["recorded"]["lifecycle"]["status"]
        == "RETIRED"
    )


async def test_lifecycle_unknown_status_returns_400(ctx):
    client, db = ctx
    ep_id = await _make_lifecycle_episode(db)
    resp = await client.post(
        f"/api/v1/research/graph/episodes/{ep_id}/lifecycle",
        json={"to_status": "WARPED"},
    )
    assert resp.status_code == 400


async def test_lifecycle_missing_episode_returns_404(ctx):
    client, _ = ctx
    resp = await client.post(
        "/api/v1/research/graph/episodes/ep-ghost/lifecycle",
        json={"to_status": "INSPECTED"},
    )
    assert resp.status_code == 404


async def test_lifecycle_episode_without_lifecycle_subkey_returns_404(ctx):
    """无 lifecycle 子键的历史 episode → KeyError 路径 → 404(不静默补状态)。"""
    client, db = ctx
    await db.execute(
        "INSERT INTO research_episodes (id, subject_type, subject_id, title, "
        "boundary_start, boundary_end, occurred_at, created_at, recorded_json) "
        "VALUES (?, 'market', 'BTC/USDT', '历史行', '2026-09-01T00:00:00', "
        "'2026-09-24T00:00:00', '2026-09-24T00:00:00', '2026-09-24T00:00:00', '{}')",
        ("ep-old",),
    )
    resp = await client.post(
        "/api/v1/research/graph/episodes/ep-old/lifecycle",
        json={"to_status": "INSPECTED"},
    )
    assert resp.status_code == 404
    assert "no lifecycle subkey" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# 行情材料装配(刀2):anchor.symbols 命中 → council prompt 带行情快照,
# 门控落 market_snapshot evidence;anchor 无 symbols → 零痕迹。
# market_material 的 KLINE_DB/DERIV_DB 是模块级 Path 常量 → monkeypatch 指向
# tmp mini 库,绝不碰真库(services/local-api/data/klines.db 222MB / 生产库)。
# ---------------------------------------------------------------------------


def _make_mini_market_dbs(tmp_path) -> tuple:
    """造 mini klines.db + derivatives.db(30 根 1d K线 + 40 条 funding),
    返回 (kline_db_path, deriv_db_path)。"""
    import sqlite3

    base_ms = 1790467200000  # 2026-09-27T00:00:00Z(与真库同截止日)
    day_ms = 86400000

    kdb = tmp_path / "klines.db"
    conn = sqlite3.connect(kdb)
    conn.execute(
        "CREATE TABLE klines (symbol TEXT, exchange TEXT, interval TEXT, "
        "market_type TEXT, timestamp INTEGER, open REAL, high REAL, low REAL, "
        "close REAL, volume REAL)"
    )
    rows = [
        (
            "BTC/USDT",
            "binance",
            "1d",
            "futures",
            base_ms - (29 - i) * day_ms,
            80000.0 + i * 100,
            81000.0 + i * 100,
            79000.0 + i * 100,
            80500.0 + i * 100,
            1000.0,
        )
        for i in range(30)
    ]
    conn.executemany("INSERT INTO klines VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    conn.commit()
    conn.close()

    ddb = tmp_path / "derivatives.db"
    conn = sqlite3.connect(ddb)
    conn.execute(
        "CREATE TABLE funding_rates (exchange TEXT, symbol TEXT, timestamp "
        "INTEGER, funding_rate REAL, mark_price REAL, index_price REAL, "
        "next_funding_time INTEGER)"
    )
    fr_rows = [
        (
            "binance",
            "BTC/USDT",
            base_ms - (39 - i) * 8 * 3600000,
            0.0001 + i * 1e-7,
            None,
            None,
            None,
        )
        for i in range(40)
    ]
    conn.executemany("INSERT INTO funding_rates VALUES (?, ?, ?, ?, ?, ?, ?)", fr_rows)
    conn.commit()
    conn.close()
    return kdb, ddb


def _patch_market_dbs(monkeypatch, kdb, ddb) -> None:
    from app.services import market_material as mm

    monkeypatch.setattr(mm, "KLINE_DB", kdb)
    monkeypatch.setattr(mm, "DERIV_DB", ddb)


async def _make_question_with_anchor(client, title: str, anchor: dict) -> str:
    resp = await client.post(
        "/api/v1/research/graph/questions",
        json={"title": title, "anchor": anchor},
    )
    assert resp.status_code == 200, resp.text
    payload: dict = resp.json()
    qid: str = payload["data"]["question_id"]
    return qid


async def test_market_material_in_council_prompt_and_ledger(ctx, tmp_path):
    """断言点 1+2:视角 prompt 含行情材料段与关键字段;首次 investigate 落
    1 条 subject_type='market_snapshot' 的 Evidence(带 addresses 边)。"""
    client, db = ctx
    kdb, ddb = _make_mini_market_dbs(tmp_path)
    qid = await _make_question_with_anchor(
        client, "BTC行情", {"symbols": ["BTC"], "intervals": ["1d"]}
    )

    prompts: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        prompts.append(prompt)
        if "主持人" in prompt or "三位视角" in prompt:
            return await _host_reply([])
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    _patch_market_dbs(monkeypatch, kdb, ddb)
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    # 断言 1:三视角 prompt 含材料段与关键字段(同快照在账本条目重复出现
    # 是预期行为 —— 材料段无 id + 账本条目有 id,不断言"只出现一处")
    perspective_prompts = [
        p for p in prompts if "三位视角" not in p and "你是主持人" not in p
    ]
    assert perspective_prompts, f"no perspective prompts captured: {len(prompts)}"
    assert all("本轮行情材料" in p for p in perspective_prompts), perspective_prompts[
        0
    ][:300]
    # mini 库最后一根 close = 80500 + 29*100 = 83400
    assert all("83400" in p for p in perspective_prompts), "最新收盘数字 83400 缺失"

    # 断言 2:恰好 1 条 market_snapshot evidence + addresses 边
    eps = await db.execute(
        "SELECT id, subject_type, subject_id FROM research_episodes "
        "WHERE subject_type='market_snapshot'"
    )
    assert len(eps) == 1, eps
    evs = await db.execute(
        "SELECT ev.id FROM research_evidence ev WHERE ev.episode_id = ?",
        (eps[0]["id"],),
    )
    assert len(evs) == 1
    edges = await db.execute(
        "SELECT 1 FROM research_edges WHERE from_type='evidence' AND from_id=? "
        "AND to_type='question' AND to_id=? AND kind='addresses'",
        (evs[0]["id"], qid),
    )
    assert edges, "addresses 边缺失"
    assert eps[0]["subject_id"] == "BTC/USDT"


async def test_market_material_gate_blocks_rewrite_within_7_days(ctx, tmp_path):
    """断言点 3:7 天内二次 investigate 门控命中,不重复写 evidence。"""
    client, db = ctx
    kdb, ddb = _make_mini_market_dbs(tmp_path)
    qid = await _make_question_with_anchor(
        client, "BTC行情", {"symbols": ["BTC"], "intervals": ["1d"]}
    )

    async def _fake_llm(settings_service, prompt, **kwargs):
        if "主持人" in prompt or "三位视角" in prompt:
            return await _host_reply([])
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    _patch_market_dbs(monkeypatch, kdb, ddb)
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        for _ in range(2):
            resp = await client.post(
                f"/api/v1/research/graph/questions/{qid}/investigate"
            )
            assert resp.status_code == 202
            await _await_investigate()
    finally:
        monkeypatch.undo()

    eps = await db.execute(
        "SELECT COUNT(*) AS c FROM research_episodes WHERE subject_type='market_snapshot'"
    )
    assert eps[0]["c"] == 1, f"gate failed: {eps[0]['c']} snapshots after 2 runs"


async def test_market_material_no_symbols_zero_traces(ctx, tmp_path):
    """断言点 4:anchor 无 symbols 键 → 不写库、prompt 零材料痕迹、不 crash。"""
    client, db = ctx
    kdb, ddb = _make_mini_market_dbs(tmp_path)
    qid = await _make_question_with_anchor(
        client, "无关行情", {"strategy_ids": ["strat-x"]}
    )

    prompts: list[str] = []

    async def _fake_llm(settings_service, prompt, **kwargs):
        prompts.append(prompt)
        if "主持人" in prompt or "三位视角" in prompt:
            return await _host_reply([])
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    _patch_market_dbs(monkeypatch, kdb, ddb)
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    assert prompts, "council 未产生任何 prompt(不应因装配失败而中断)"
    assert all("本轮行情材料" not in p for p in prompts)
    eps = await db.execute(
        "SELECT COUNT(*) AS c FROM research_episodes WHERE subject_type='market_snapshot'"
    )
    assert eps[0]["c"] == 0
    # 调查会本身照常完成
    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    finished_events = [
        e for e in detail["events"] if e["kind"] == "investigate_finished"
    ]
    assert len(finished_events) == 1
    assert finished_events[0]["detail"]["status"] == "ok"


async def test_market_material_gate_ignores_probe_market_episodes(ctx, tmp_path):
    """断言点 5:预置 subject_type='market'(run_probe 的探针件)不被门控
    误判 —— 门控只认 market_snapshot,本次 investigate 照常写新快照。"""
    client, db = ctx
    kdb, ddb = _make_mini_market_dbs(tmp_path)
    qid = await _make_question_with_anchor(
        client, "BTC行情", {"symbols": ["BTC"], "intervals": ["1d"]}
    )
    # 预置探针件:probe 写的是 subject_type='market'(run_probe.py L171)
    from selene_space_storage.repositories.research_graph import (
        EpisodeRepository,
        EvidenceRepository,
        ResearchEdgeRepository,
    )

    probe_ep_id = await EpisodeRepository(db).create(
        "market",
        "BTC/USDT",
        "IC probe funding_ic",
        "2026-09-20T00:00:00+00:00",
        "2026-09-27T00:00:00+00:00",
        recorded={"probe": "funding_ic"},
    )
    probe_ev_id = await EvidenceRepository(db).create(
        probe_ep_id,
        "RECORDED",
        payload={"summary": "funding_z last90d=+0.01"},
        source_ref="scripts/run_probe.py",
    )
    await ResearchEdgeRepository(db).add_fact_edge(
        ("evidence", probe_ev_id), ("question", qid), "addresses"
    )

    async def _fake_llm(settings_service, prompt, **kwargs):
        if "主持人" in prompt or "三位视角" in prompt:
            return await _host_reply([])
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    _patch_market_dbs(monkeypatch, kdb, ddb)
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    eps = await db.execute(
        "SELECT subject_type, COUNT(*) AS c FROM research_episodes "
        "WHERE subject_id='BTC/USDT' GROUP BY subject_type"
    )
    by_type = {r["subject_type"]: r["c"] for r in eps}
    assert by_type.get("market") == 1, f"probe episode disturbed: {by_type}"
    assert (
        by_type.get("market_snapshot") == 1
    ), f"gate misfired on probe episode: {by_type}"


async def test_market_material_host_cited_snapshot_evidence(ctx, tmp_path):
    """断言点 6:host CITED 新快照 id → add_revision 过 cited 校验(快照已
    在 host recall 账本),落库 cited 就是它。"""
    client, db = ctx
    kdb, ddb = _make_mini_market_dbs(tmp_path)
    qid = await _make_question_with_anchor(
        client, "BTC行情", {"symbols": ["BTC"], "intervals": ["1d"]}
    )

    async def _fake_llm(settings_service, prompt, **kwargs):
        if "主持人" in prompt or "三位视角" in prompt:
            # host prompt 不含账本 id 段(设计如此)—— 直接查库拿快照 evidence id;
            # 它能过 cited 校验的前提正是快照已进 host recall 候选集
            rows = await db.execute(
                "SELECT ev.id FROM research_evidence ev "
                "JOIN research_episodes e ON e.id = ev.episode_id "
                "WHERE e.subject_type='market_snapshot'"
            )
            assert rows, "库中无 market_snapshot evidence 可引"
            return f"综合判断。\nCITED: {rows[0]['id']}"
        return "视角分析。\nCITED:"

    monkeypatch = pytest.MonkeyPatch()
    _patch_market_dbs(monkeypatch, kdb, ddb)
    monkeypatch.setattr(council, "call_llm", _fake_llm)
    try:
        resp = await client.post(f"/api/v1/research/graph/questions/{qid}/investigate")
        assert resp.status_code == 202
        await _await_investigate()
    finally:
        monkeypatch.undo()

    detail = (await client.get(f"/api/v1/research/graph/questions/{qid}")).json()[
        "data"
    ]
    revision = detail["revisions"][-1]
    assert revision["cited"], "host 未引用任何对象"
    cited_evs = [c for c in revision["cited"] if c.startswith("ev-")]
    assert cited_evs, f"host 未引 evidence: {revision['cited']}"
    # 被引的确实是 market_snapshot 件
    row = await db.execute(
        "SELECT e.subject_type FROM research_evidence ev "
        "JOIN research_episodes e ON e.id = ev.episode_id WHERE ev.id = ?",
        (cited_evs[0],),
    )
    assert row[0]["subject_type"] == "market_snapshot"


# -- N1-a:候选带入库时间(纯函数,无需 DB)----------------------------------


def test_age_label_relative_buckets():
    """入库时间 → 相对年龄,LLM 才分得出新旧。"""
    today = "2026-10-02"
    assert council._age_label("2026-10-02T01:00:00+00:00", today=today) == "10-02(今天)"
    assert council._age_label("2026-10-01T01:00:00+00:00", today=today) == "10-01(昨天)"
    assert (
        council._age_label("2026-09-26T08:35:16+00:00", today=today) == "09-26(6天前)"
    )
    assert (
        council._age_label("2026-08-13T00:00:00+00:00", today=today) == "08-13(1个月前)"
    )
    assert (
        council._age_label("2025-09-26T00:00:00+00:00", today=today) == "09-26(1年前)"
    )


def test_age_label_failsafe_on_unparseable():
    """解析不了 → 空串,绝不抛(防直写 DB 的脏数据把 prompt 构造炸掉)。"""
    assert council._age_label(None) == ""
    assert council._age_label("") == ""
    assert council._age_label("not-a-date") == ""
    assert council._age_label("funding_rates 补到最新后重跑") == ""


def test_narrate_candidate_carries_age_and_keeps_chain_semantics():
    """时间前缀 + 原有证据链语义都在。"""
    cand = {
        "text": "动量因子在 BTC 1h 上没有边缘",
        "created_at": "2026-09-26T08:35:16+00:00",
        "evidence_chain": ["question:q-1", "evidence:ev-1"],
        "matched_on": ["anchor:BTC/USDT"],
    }
    out = council.narrate_candidate(cand, today="2026-10-02")
    assert out.startswith("[09-26(6天前)] ")
    assert "动量因子在 BTC 1h 上没有边缘" in out
    assert "证据链走到 evidence ev-1(共 2 跳)" in out
    assert "锚定 BTC/USDT" in out


def test_narrate_candidate_without_created_at_has_no_stamp():
    """无时间戳 → 不渲染空方括号(不留 '[ ] ' 噪音)。"""
    out = council.narrate_candidate({"text": "无时间线索"}, today="2026-10-02")
    assert out == "无时间线索(无证据链,待检验线索)"
    assert not out.startswith("[")


def test_clip_truncates_overlong_ledger_line():
    """跨问题 revision 整篇进账本 → 截断并标记,保留"还有更多"的信号。"""
    long_text = "【时间线】" + "内容" * 1200  # ~2400 字符
    out = council._clip(long_text)
    assert len(out) < len(long_text)
    assert out.startswith("【时间线】"), "保留开头(时间线是最有判断价值的部分)"
    assert "…(截断,原文" in out
    assert f"{len(long_text)} 字符)" in out


def test_clip_leaves_normal_lines_untouched():
    """正常长度原样返回,不加标记。"""
    assert council._clip("短文本") == "短文本"
    assert (
        council._clip("x" * council._LEDGER_LINE_MAX) == "x" * council._LEDGER_LINE_MAX
    )


def test_narrate_candidate_clips_long_revision_text():
    """端到端:narrate 输出的候选行不会因整篇 revision 而爆长。"""
    body = "【时间线】" + "y" * 3000
    cand = {"text": body, "created_at": "2026-09-26T00:00:00+00:00"}
    out = council.narrate_candidate(cand, today="2026-10-02")
    assert len(out) < council._LEDGER_LINE_MAX + 120, f"行长达 {len(out)}"
    assert f"…(截断,原文 {len(body)} 字符)" in out
    assert out.startswith("[09-26(6天前)] ")


def test_clip_threshold_above_evidence_max_avoids_double_truncation():
    """阈值必须高于 recall 的 evidence 截断线,否则已截断的 evidence 会被二次截断。"""
    from selene_space_storage.repositories.research_graph.recall import (
        _EVIDENCE_TEXT_MAX,
    )

    assert council._LEDGER_LINE_MAX > _EVIDENCE_TEXT_MAX


# -- 前置写检:账本不可写时,任何 LLM 花费之前就失败 ---------------------------


async def test_assert_writable_passes_on_writable_db(ctx):
    """可写库放行 —— 不误伤正常路径。"""
    _client, db = ctx
    await council.assert_writable(db)  # 不抛即通过


async def test_assert_writable_raises_503_on_locked_db():
    """库被锁 → 503 且理由明确(不是笼统 500)。"""
    from fastapi import HTTPException

    class LockedDB:
        def connection(self):
            raise RuntimeError("database is locked")

    with pytest.raises(HTTPException) as ei:
        await council.assert_writable(LockedDB())
    assert ei.value.status_code == 503
    assert "不可写" in ei.value.detail
    assert "LLM" in ei.value.detail  # 明确告知是为了不浪费调用


async def test_assert_writable_raises_503_on_readonly_db():
    """只读库(如误挂只读文件)→ 同样 503,不是 500。"""
    from fastapi import HTTPException

    class ReadOnlyDB:
        def connection(self):
            raise RuntimeError("attempt to write a readonly database")

    with pytest.raises(HTTPException) as ei:
        await council.assert_writable(ReadOnlyDB())
    assert ei.value.status_code == 503


# ---- v025 主动复访 + horizon -------------------------------------------------


async def test_revisit_set_and_clear(ctx):
    client, _ = ctx
    qid = await _make_question(client, "复访端点测试")

    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisit",
        json={"next_revisit_at": "2026-10-10T00:00:00+00:00", "actor": "user"},
    )
    assert resp.status_code == 200, resp.text
    assert (
        resp.json()["data"]["question"]["next_revisit_at"]
        == "2026-10-10T00:00:00+00:00"
    )

    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisit",
        json={"next_revisit_at": None},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["question"]["next_revisit_at"] is None


async def test_revisit_missing_question_404(ctx):
    client, _ = ctx
    resp = await client.post(
        "/api/v1/research/graph/questions/q-missing/revisit",
        json={"next_revisit_at": "2026-10-10T00:00:00+00:00"},
    )
    assert resp.status_code == 404


async def test_revisit_terminal_status_400(ctx):
    client, db = ctx
    qid = await _make_question(client, "终态拒绝测试")
    # 直落终态(绕过端点面,close 端点另有测试覆盖)
    async with db.connection() as conn:
        await conn.execute(
            "UPDATE research_questions SET status = 'falsified' WHERE id = ?", (qid,)
        )
        await conn.commit()
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisit",
        json={"next_revisit_at": "2026-10-10T00:00:00+00:00"},
    )
    assert resp.status_code == 400


async def test_due_endpoint_requires_now(ctx):
    client, _ = ctx
    resp = await client.get("/api/v1/research/graph/revisit/due")
    assert resp.status_code == 422  # FastAPI 缺必填 query 参数 = 422


async def test_due_endpoint_returns_due_list(ctx):
    client, _ = ctx
    q1 = await _make_question(client, "到期A")
    q2 = await _make_question(client, "未到期B")
    for qid, ts in (
        (q1, "2026-10-01T00:00:00+00:00"),
        (q2, "2026-12-01T00:00:00+00:00"),
    ):
        resp = await client.post(
            f"/api/v1/research/graph/questions/{qid}/revisit",
            json={"next_revisit_at": ts},
        )
        assert resp.status_code == 200

    resp = await client.get(
        "/api/v1/research/graph/revisit/due",
        params={"now": "2026-10-03T00:00:00+00:00"},
    )
    assert resp.status_code == 200, resp.text
    due_ids = [q["id"] for q in resp.json()["data"]["due"]]
    assert q1 in due_ids
    assert q2 not in due_ids


async def test_create_revision_with_tactical_horizon(ctx):
    client, _ = ctx
    qid = await _make_question(client, "horizon端点测试")
    resp = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "战术性判断", "horizon": "tactical"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["revision"]["horizon"] == "tactical"

    # 非法值在 pydantic 层就 422,根本到不了 repo
    resp_bad = await client.post(
        f"/api/v1/research/graph/questions/{qid}/revisions",
        json={"content": "非法档", "horizon": "weekly"},
    )
    assert resp_bad.status_code == 422


# ---------------------------------------------------------------------------
# /recall as_of 回放(N1 消费侧刀 Step 2)
# ---------------------------------------------------------------------------


async def test_recall_endpoint_passes_as_of_through(ctx):
    """带 as_of → 200 且候选被时间过滤(端点透传 working)。"""
    client, db = ctx
    qid = await _make_question(client)

    # 种一条 2026-09-01 的 revision(anchor 相同 → 会被 recall 取回)
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO research_revisions (id, question_id, seq, content, "
            "rationale_json, cited_json, actor, created_at) "
            "VALUES ('rev-old', ?, 1, ?, '[]', '[]', 'selene', ?)",
            (qid, "旧判断", "2026-09-01T00:00:00+00:00"),
        )

    base = await client.post("/api/v1/research/graph/recall", json={"question_id": qid})
    assert base.status_code == 200, base.text
    assert {c["id"] for c in base.json()["data"]["candidates"]} == {"rev-old"}

    # 回放日早于记录 → 空
    early = await client.post(
        "/api/v1/research/graph/recall",
        json={"question_id": qid, "as_of": "2026-08-01T00:00:00+00:00"},
    )
    assert early.status_code == 200, early.text
    assert early.json()["data"]["candidates"] == []

    # 回放日晚于记录 → 仍可见
    late = await client.post(
        "/api/v1/research/graph/recall",
        json={"question_id": qid, "as_of": "2026-12-31T23:59:59+00:00"},
    )
    assert late.status_code == 200, late.text
    assert {c["id"] for c in late.json()["data"]["candidates"]} == {"rev-old"}


async def test_recall_endpoint_invalid_as_of_is_400(ctx):
    """非法 as_of → 400(经 _domain_error,零新错误处理代码)。"""
    client, _ = ctx
    qid = await _make_question(client)
    resp = await client.post(
        "/api/v1/research/graph/recall",
        json={"question_id": qid, "as_of": "not-a-date"},
    )
    assert resp.status_code == 400, resp.text


async def test_recall_endpoint_without_as_of_shape_unchanged(ctx):
    """不带 as_of → 响应 shape 不变,候选无 data_window 键(回放专属)。"""
    client, db = ctx
    qid = await _make_question(client)
    async with db.connection() as conn:
        await conn.execute(
            "INSERT INTO research_revisions (id, question_id, seq, content, "
            "rationale_json, cited_json, actor, created_at) "
            "VALUES ('rev-s', ?, 1, ?, '[]', '[]', 'selene', ?)",
            (qid, "判断", "2026-09-01T00:00:00+00:00"),
        )
    resp = await client.post("/api/v1/research/graph/recall", json={"question_id": qid})
    assert resp.status_code == 200, resp.text
    for c in resp.json()["data"]["candidates"]:
        assert "data_window" not in c


# --- 因子级生命周期推导端点(derivation 消费口) ------------------------------


async def test_subject_derivation_endpoint_returns_chain_and_suggestions(ctx):
    """GET /subjects/{type}/{id}/derivation:链时间线 + 行动建议穿透。"""
    client, db = ctx
    repo = EpisodeRepository(db)
    key = "formula:ab8d2445"
    await repo.create(
        "formula",
        key,
        "Formula registration v1",
        "2026-10-01T00:00:00+00:00",
        "2026-10-01T00:00:00+00:00",
        recorded={
            "kind": "formula_registration",
            "lifecycle": {"status": "DISCOVERED", "transitions": []},
            "verdict": "VALID",
        },
        occurred_at="2026-10-01T00:00:00+00:00",
    )
    retest_id = await repo.create(
        "formula",
        key,
        "Formula registration v2 (retest)",
        "2026-10-03T00:00:00+00:00",
        "2026-10-03T00:00:00+00:00",
        recorded={
            "kind": "formula_registration",
            "lifecycle": {"status": "DISCOVERED", "transitions": []},
            "verdict": "SUSPICIOUS",
        },
        occurred_at="2026-10-03T00:00:00+00:00",
    )
    resp = await client.get(f"/api/v1/research/graph/subjects/formula/{key}/derivation")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["subject_type"] == "formula"
    assert len(data["chain"]) == 2
    assert data["chain"][0]["verdict"] == "SUSPICIOUS"
    assert data["latest"]["episode_id"] == retest_id
    assert [s["action"] for s in data["suggestions"]] == ["inspect"]
    assert data["suggestions"][0]["basis_episode_id"] == retest_id


async def test_subject_derivation_endpoint_empty_subject(ctx):
    """查无此 subject:200 + 空链 + 零建议(读路径不 404,前端好渲染)。"""
    client, db = ctx
    resp = await client.get(
        "/api/v1/research/graph/subjects/formula/formula:never-existed/derivation"
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["chain"] == []
    assert data["latest"] is None
    assert data["suggestions"] == []
