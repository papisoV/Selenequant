"""TODAY 投影 / 决策端点回归测试(2026-10-21 审计发现的「看得见的假象」)。

两条缺陷都是"数据在库里,窗口永远看不到",且都是先 LIMIT 再在 Python 里过滤:

1. ``GET /research/today`` 的 LAST HANDOFF 恒为 null —— 37 条 mission 里唯一一条
   ``resolved`` 恰好排第 37,而查询只取最近 20 条再过滤 resolved/parked。
2. ``POST /research/missions/{id}/decision`` 只接受 ``needs_input`` 出发,而
   ``validation`` 在状态机里没有任何产品出口 —— 批准后的 Mission 永远走不到结案。

这两个测试都必须能抓到原实现。写法上刻意复刻生产数据形状(大量新 investigation
+ 一条旧 resolved),因为"最近 N 条恰好包含"才是让缺陷显形的条件。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.core import dependencies as deps
from app.main import app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def client_and_db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        deps.set_db_manager(db)
        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            yield client, db
        deps.set_db_manager(None)


async def _insert_mission(
    db: AsyncSQLiteManager,
    mission_id: str,
    *,
    phase: str,
    updated_at: str,
    title: str = "PA diagnosis BTC/USDT 1h",
) -> None:
    await db.execute(
        """
        INSERT INTO research_missions
            (id, title, question, subject_type, subject_id, phase, stage,
             created_by, opened_at, updated_at)
        VALUES (?, ?, 'BTC 1h 结构是否可交易?', 'strategy', 'st-btc',
                ?, 'opened', 'selene', ?, ?)
        """,
        (mission_id, title, phase, updated_at, updated_at),
    )


async def _add_user_activity(
    db: AsyncSQLiteManager, mission_id: str, *, occurred_at: str, note: str
) -> None:
    await db.execute(
        """
        INSERT INTO mission_activity (mission_id, occurred_at, actor, kind, detail_json)
        VALUES (?, ?, 'user', 'phase_transition', ?)
        """,
        (mission_id, occurred_at, '{"note": "' + note + '"}'),
    )


@pytest.mark.asyncio
async def test_today_handoff_survives_older_than_any_window(client_and_db):
    """唯一一条 resolved 排在 30 条更新的 mission 之后,仍必须出现在 LAST HANDOFF。

    原实现 ``list_missions(limit=20)`` 后再过滤:resolved 那条排第 31,永远进不了
    候选集,``last_handoff`` 恒为 null —— 前端于是渲染"尚无交接记录"。
    """
    _client, db = client_and_db
    # 30 条更新的开放 mission(模拟 PA 心跳批量产出)
    for i in range(30):
        await _insert_mission(
            db,
            f"mis-open{i:02d}",
            phase="investigation",
            updated_at=f"2026-10-20T12:{i:02d}:00+00:00",
        )
    # 一条 8 天前结案、且人是真的做过决策的 mission
    await _insert_mission(
        db,
        "mis-old",
        phase="resolved",
        updated_at="2026-10-12T09:00:00+00:00",
        title="Keltner V2 配置漂移调查",
    )
    await _add_user_activity(
        db,
        "mis-old",
        occurred_at="2026-10-12T09:00:00+00:00",
        note="Approve REVALIDATE — 修正配置回声明意图后重验证",
    )

    resp = await _client.get("/api/v1/research/today")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    handoff = body["last_handoff"]
    assert handoff is not None, "唯一一条 resolved 落在窗口外时,LAST HANDOFF 被剪掉"
    assert handoff["mission_id"] == "mis-old"
    assert handoff["occurred_at"] == "2026-10-12T09:00:00+00:00"
    assert handoff["detail"]["note"].startswith("Approve REVALIDATE")
    # 新增字段:交接必须有主语
    assert handoff["mission_title"] == "Keltner V2 配置漂移调查"
    # world_changed 也必须是"符合条件里最近的 5 条",不是"最近 20 条里碰巧符合的"
    assert [m["id"] for m in body["world_changed"]] == ["mis-old"]


@pytest.mark.asyncio
async def test_today_working_is_recent_open_missions_not_recent_missions(client_and_db):
    """WORKING 必须是"开放的里面最近的",不是"最近的里面碰巧开放的"。

    原实现 ``list_missions(limit=50)`` 后再过滤:开放 mission 一旦在最近 50 条
    里排不上(被大量 resolved/parked 挤掉),WORKING 就会凭空少件。
    """
    _client, db = client_and_db
    # 55 条更新的 parked —— 把 open mission 全部挤出最近 50
    for i in range(55):
        await _insert_mission(
            db,
            f"mis-p{i:02d}",
            phase="parked",
            updated_at=f"2026-10-20T13:{i:02d}:00+00:00",
        )
    await _insert_mission(
        db,
        "mis-working",
        phase="validation",
        updated_at="2026-10-01T00:00:00+00:00",
        title="验收种子 · 配置漂移裁决演示",
    )

    resp = await _client.get("/api/v1/research/today")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [m["id"] for m in body["working"]] == ["mis-working"]


@pytest.mark.asyncio
async def test_resolve_action_closes_validation_and_records_outcome(client_and_db):
    """validation 必须有产品出口:resolve → resolved,且 outcome 非空。

    原实现只接受 needs_input 出发,validation 在 UI 上没有任何可点的动作,
    mission 只能停在 validation,或在实验跑完被推回 needs_input 再批一次。
    """
    client, db = client_and_db
    await _insert_mission(
        db,
        "mis-val",
        phase="validation",
        updated_at="2026-10-13T12:35:57+00:00",
        title="验收种子 · 配置漂移裁决演示",
    )

    resp = await client.post(
        "/api/v1/research/missions/mis-val/decision",
        json={"action": "resolve", "note": "OOS 通过,配置漂移已确认为真"},
    )
    assert resp.status_code == 200, resp.text
    mission = resp.json()["mission"]
    assert mission["phase"] == "resolved"
    assert mission["outcome"], "结案必须落 outcome,不能结个空案"
    assert mission["outcome"]["verdict"] == "RESOLVED_BY_USER"
    assert mission["outcome"]["note"] == "OOS 通过,配置漂移已确认为真"
    assert mission["resolved_at"]

    # 活动流里要有这次人类决策 —— LAST HANDOFF 靠它
    act = await client.get("/api/v1/research/missions/mis-val/activity")
    assert act.status_code == 200, act.text
    rows = act.json()["activity"]
    assert any(r["kind"] == "outcome_recorded" for r in rows)
    assert any(
        r["actor"] == "user" and r["detail"].get("action") == "resolve" for r in rows
    )


@pytest.mark.asyncio
async def test_resolve_accepts_explicit_outcome(client_and_db):
    """调用方给了 outcome 就用它,不要被默认 verdict 盖掉。"""
    client, db = client_and_db
    await _insert_mission(
        db,
        "mis-val2",
        phase="validation",
        updated_at="2026-10-13T12:35:57+00:00",
    )

    resp = await client.post(
        "/api/v1/research/missions/mis-val2/decision",
        json={
            "action": "resolve",
            "outcome": {"verdict": "REVALIDATED", "oos_return": 0.181},
        },
    )
    assert resp.status_code == 200, resp.text
    outcome = resp.json()["mission"]["outcome"]
    assert outcome["verdict"] == "REVALIDATED"
    assert outcome["oos_return"] == 0.181


@pytest.mark.asyncio
async def test_resolve_rejected_from_needs_input(client_and_db):
    """动作与来源相位必须匹配 —— resolve 只能从 validation 出发。"""
    client, db = client_and_db
    await _insert_mission(
        db,
        "mis-ni",
        phase="needs_input",
        updated_at="2026-10-20T00:00:00+00:00",
    )

    resp = await client.post(
        "/api/v1/research/missions/mis-ni/decision", json={"action": "resolve"}
    )
    assert resp.status_code == 409
    assert "validation" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_existing_actions_still_require_needs_input(client_and_db):
    """新增 resolve 不能放松其它动作的出发相位。"""
    client, db = client_and_db
    await _insert_mission(
        db,
        "mis-val3",
        phase="validation",
        updated_at="2026-10-20T00:00:00+00:00",
    )

    resp = await client.post(
        "/api/v1/research/missions/mis-val3/decision", json={"action": "approve"}
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_today_counts_questions_from_question_table(client_and_db):
    """顶条七环的「问句」必须读 research_questions,不是 mission 计数。

    旧写法两环共用 ``len(needs_you) + len(working)``:数据源不同却永远同数,
    看着像计数坏了。这里让两张表的数字刻意不同 —— 60 条 open 问题、2 条
    mission —— 断言 counts 跟的是问题表,且是全量(问题列表 limit 默认 50)。
    """
    _client, db = client_and_db
    for i in range(60):
        await db.execute(
            "INSERT INTO research_questions (id, title, body, anchor_json, status, "
            "opened_at, updated_at) VALUES (?, '', '', '{}', 'open', ?, ?)",
            (f"q-{i:02d}", "2026-10-20T00:00:00+00:00", "2026-10-20T00:00:00+00:00"),
        )
    for i in range(5):
        await db.execute(
            "INSERT INTO research_questions (id, title, body, anchor_json, status, "
            "opened_at, updated_at) VALUES (?, '', '', '{}', 'parked', ?, ?)",
            (f"qp-{i:02d}", "2026-10-20T00:00:00+00:00", "2026-10-20T00:00:00+00:00"),
        )
    for i in range(2):
        await _insert_mission(
            db,
            f"mis-c{i}",
            phase="investigation",
            updated_at=f"2026-10-20T12:0{i}:00+00:00",
        )

    resp = await _client.get("/api/v1/research/today")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert (
        body["counts"]["open_questions"] == 60
    ), "问句环必须取问题表全量(不受列表 limit 截断),不是 mission 计数"
    assert len(body["working"]) + body["needs_you_total"] == 2


@pytest.mark.asyncio
async def test_today_needs_you_folds_same_question_across_titles(client_and_db):
    """同一「标的 + 问句」的重复开案在 NEEDS YOU 里是一张卡,不是 N 张。

    实测库里的形状:26 条 open mission 是同一个问句,却有三种标题(心跳
    「X PA 心跳诊断」/ CLI「PA diagnosis X 1h」/ 早期标题写 4h 而问句是 1h)。
    后端原本 ``list_missions(phase="needs_input", limit=10)`` 直接倒给前端,
    窗口里 3 张待办卡是同一件事,别的问句一件都露不出来。
    """
    _client, db = client_and_db
    question = "当前 1h 结构是什么状态?是否满足入场质量(gate)?"
    titles = [
        "PA diagnosis BTC/USDT 1h",
        "BTC/USDT PA 心跳诊断",
        "PA diagnosis BTC/USDT 4h",
    ]
    for i in range(12):
        await db.execute(
            """
            INSERT INTO research_missions
                (id, title, question, subject_type, subject_id, phase, stage,
                 created_by, opened_at, updated_at)
            VALUES (?, ?, ?, 'market', 'BTC/USDT', 'needs_input', 'opened',
                    'selene', ?, ?)
            """,
            (
                f"mis-dup{i:02d}",
                titles[i % len(titles)],
                question,
                f"2026-10-20T12:{i:02d}:00+00:00",
                f"2026-10-20T12:{i:02d}:00+00:00",
            ),
        )
    # 另一个真正不同的问句 —— 折叠不能把不同的事并进来
    await db.execute(
        """
        INSERT INTO research_missions
            (id, title, question, subject_type, subject_id, phase, stage,
             created_by, opened_at, updated_at)
        VALUES ('mis-other', 'ETH 4h 结构', '当前 4h 结构是什么状态?',
                'market', 'ETH/USDT', 'needs_input', 'opened', 'selene',
                '2026-10-20T09:00:00+00:00', '2026-10-20T09:00:00+00:00')
        """
    )

    resp = await _client.get("/api/v1/research/today")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert len(body["needs_you"]) == 2, "12 条同问句 + 1 条别的问句 → 2 组"
    first = body["needs_you"][0]  # 最新一条来自被折叠的组
    assert first["question"] == question
    assert first["duplicates"] == 11
    assert len(first["duplicate_ids"]) == 11
    # 件数口径(折叠前)与组数口径分开给,前端两处都要用
    assert body["needs_you_total"] == 13
    assert body["needs_you"][1]["subject_id"] == "ETH/USDT"
    assert body["needs_you"][1]["duplicates"] == 0


@pytest.mark.asyncio
async def test_today_needs_you_folds_before_capping(client_and_db):
    """折叠必须发生在限流之前:10 条同问句不能把窗口占满。

    原实现先 ``limit=10`` 再交给前端,前端看到的 10 行全是同一件事;
    窗口上限是 10 组,所以整页只显得出 1 件待办。
    """
    _client, db = client_and_db
    # 20 条同问句(最新)+ 3 条别的问句(更旧)
    for i in range(20):
        await db.execute(
            """
            INSERT INTO research_missions
                (id, title, question, subject_type, subject_id, phase, stage,
                 created_by, opened_at, updated_at)
            VALUES (?, 'PA diagnosis BTC/USDT 1h', '同一个问句?', 'market',
                    'BTC/USDT', 'needs_input', 'opened', 'selene', ?, ?)
            """,
            (
                f"mis-same{i:02d}",
                f"2026-10-20T13:{i:02d}:00+00:00",
                f"2026-10-20T13:{i:02d}:00+00:00",
            ),
        )
    for i in range(3):
        await db.execute(
            """
            INSERT INTO research_missions
                (id, title, question, subject_type, subject_id, phase, stage,
                 created_by, opened_at, updated_at)
            VALUES (?, ?, ?, 'market', ?, 'needs_input', 'opened', 'selene', ?, ?)
            """,
            (
                f"mis-other{i}",
                f"别的问题 {i}",
                f"别的问题 {i}?",
                "SOL/USDT",
                f"2026-10-20T10:{i:02d}:00+00:00",
                f"2026-10-20T10:{i:02d}:00+00:00",
            ),
        )

    resp = await _client.get("/api/v1/research/today")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert (
        len(body["needs_you"]) == 4
    ), "1 组同问句 + 3 组别的问句 = 4 组;先 limit 再折叠的话这里只剩 1 组"
    assert body["needs_you"][0]["duplicates"] == 19
    assert body["needs_you_total"] == 23


@pytest.mark.asyncio
async def test_today_excludes_maintenance_archives_from_settled_windows(client_and_db):
    """重复开案的批量归档不能挤掉 WORLD CHANGED / LAST HANDOFF 的真实内容。

    2026-10-22 实测:27 条同问句重复开案被转 parked 后,parked 的 5 行窗口
    全是这些归档,WORLD CHANGED 显示 5 条一模一样的 parked,LAST HANDOFF 的
    候选也被它们占满 —— 归档动作本身制造了新的"看得见的假象"。

    排除必须下沉到 SQL(``exclude_archived_duplicates``):先 LIMIT 5 再在
    Python 里筛的话,下面 30 条归档照样把窗口占满,真实的 parked 一条都进不来。
    """
    _client, db = client_and_db
    # 30 条更新的维护性归档
    for i in range(30):
        await _insert_mission(
            db,
            f"mis-arch{i:02d}",
            phase="parked",
            updated_at=f"2026-10-22T16:{i:02d}:00+00:00",
        )
        await db.execute(
            "UPDATE research_missions SET context_json = ? WHERE id = ?",
            (
                '{"archive": {"duplicate_of": "mis-canon", '
                '"reason": "duplicate_open_mission", "maintenance": true}}',
                f"mis-arch{i:02d}",
            ),
        )
    # 一条更旧的真实归档(人工停放,不是维护性),必须是 WORLD CHANGED 的赢家
    await _insert_mission(
        db,
        "mis-real",
        phase="parked",
        updated_at="2026-10-01T00:00:00+00:00",
        title="真实停放的研究线",
    )
    await _add_user_activity(
        db,
        "mis-real",
        occurred_at="2026-10-01T00:00:00+00:00",
        note="Park — 需求方暂停",
    )

    resp = await _client.get("/api/v1/research/today")
    assert resp.status_code == 200, resp.text
    body = resp.json()

    assert [m["id"] for m in body["world_changed"]] == [
        "mis-real"
    ], "30 条维护性归档把 parked 窗口占满时,真实停放的研究线被挤掉了"
    assert body["last_handoff"]["mission_id"] == "mis-real"


@pytest.mark.asyncio
async def test_recent_activity_skips_maintenance_rows(client_and_db):
    """批量归档的 27 条同形活动不能把「最近动作」刷屏。"""
    _client, db = client_and_db
    await _insert_mission(
        db,
        "mis-canon",
        phase="needs_input",
        updated_at="2026-10-22T16:00:00+00:00",
    )
    await _insert_mission(
        db,
        "mis-keep",
        phase="investigation",
        updated_at="2026-10-22T10:00:00+00:00",
    )
    await _add_user_activity(
        db,
        "mis-keep",
        occurred_at="2026-10-22T10:00:00+00:00",
        note="真实推进",
    )
    for i in range(27):
        await db.execute(
            "INSERT INTO mission_activity (mission_id, occurred_at, actor, kind, detail_json) "
            "VALUES (?, ?, 'system', 'phase_transition', ?)",
            (
                "mis-canon",
                f"2026-10-22T16:04:{i:02d}+00:00",
                '{"from": "needs_input", "to": "parked", '
                '"reason": "duplicate_open_mission", "maintenance": true}',
            ),
        )

    resp = await _client.get("/api/v1/research/agent-activity")
    assert resp.status_code == 200, resp.text
    kinds = [(e["mission_id"], e["kind"]) for e in resp.json()["recent_events"]]
    assert (
        "mis-canon",
        "phase_transition",
    ) not in kinds, "维护性归档活动出现在「最近动作」里,把真实进展挤出了窗口"
    assert ("mis-keep", "phase_transition") in kinds
