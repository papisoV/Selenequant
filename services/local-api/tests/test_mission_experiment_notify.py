"""透明性批2-6 + P1-③: notify_experiment_completed 写活动并推进 phase。

helper 契约(fire-and-forget):
- 匹配 subject 的 open mission(phase 非 resolved/parked)追加 experiment_completed
- 实验完成 = 研究前进一小步 → phase 推到 ``needs_input``(决策点为止)
- 已在 ``needs_input`` 的不再转移,但活动照记
- resolved/parked mission 不追加
- DB 不可用 / 无 mission / 异常 → 静默返回,绝不抛
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.api.v1.research_missions import notify_experiment_completed
from app.core import dependencies as deps

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        deps.set_db_manager(mgr)
        yield mgr
        deps.set_db_manager(None)


async def _insert_mission(
    db: AsyncSQLiteManager, mission_id: str, phase: str, subject_id: str = "st-keltner"
) -> None:
    await db.execute(
        """
        INSERT INTO research_missions
            (id, title, question, subject_type, subject_id, phase, stage,
             created_by, opened_at, updated_at)
        VALUES (?, 'Keltner 重验证',
                'Keltner 突破在 SOL 4h 上仍有正期望吗?',
                'strategy', ?, ?, 'opened',
                'selene', '2026-09-23T00:00:00+00:00', '2026-09-23T00:00:00+00:00')
        """,
        (mission_id, subject_id, phase),
    )


async def _activity_count(db: AsyncSQLiteManager, mission_id: str, kind: str) -> int:
    rows = await db.execute(
        "SELECT COUNT(*) AS n FROM mission_activity WHERE mission_id = ? AND kind = ?",
        (mission_id, kind),
    )
    return int(rows[0]["n"])


@pytest.mark.asyncio
async def test_open_mission_gets_experiment_completed(db):
    await _insert_mission(db, "mis-open", "investigation")
    await notify_experiment_completed(
        "strategy",
        "st-keltner",
        task_id="bt-001",
        experiment_kind="backtest",
        summary={"total_return": 0.12, "sharpe_ratio": 1.1},
    )
    assert await _activity_count(db, "mis-open", "experiment_completed") == 1
    rows = await db.execute(
        "SELECT actor, detail_json FROM mission_activity WHERE kind = 'experiment_completed'"
    )
    assert rows[0]["actor"] == "system"
    assert '"backtest"' in rows[0]["detail_json"]
    assert "bt-001" in rows[0]["detail_json"]


@pytest.mark.asyncio
async def test_resolved_mission_not_appended(db):
    await _insert_mission(db, "mis-resolved", "resolved")
    await notify_experiment_completed(
        "strategy", "st-keltner", task_id="bt-002", experiment_kind="backtest"
    )
    assert await _activity_count(db, "mis-resolved", "experiment_completed") == 0


@pytest.mark.asyncio
async def test_no_db_manager_silent_noop():
    deps.set_db_manager(None)
    # 不抛即通过(fire-and-forget 契约)
    await notify_experiment_completed(
        "strategy", "st-keltner", task_id="bt-003", experiment_kind="backtest"
    )


@pytest.mark.asyncio
async def test_no_matching_mission_silent(db):
    await notify_experiment_completed(
        "strategy", "st-nonexistent", task_id="bt-004", experiment_kind="backtest"
    )
    rows = await db.execute("SELECT COUNT(*) AS n FROM mission_activity")
    assert int(rows[0]["n"]) == 0


async def _phase(db: AsyncSQLiteManager, mission_id: str) -> str:
    rows = await db.execute(
        "SELECT phase FROM research_missions WHERE id = ?", (mission_id,)
    )
    return str(rows[0]["phase"])


@pytest.mark.asyncio
async def test_investigation_advances_to_needs_input(db):
    await _insert_mission(db, "mis-inv", "investigation")
    await notify_experiment_completed(
        "strategy", "st-keltner", task_id="bt-005", experiment_kind="backtest"
    )
    assert await _phase(db, "mis-inv") == "needs_input"
    # 只推到决策点:不许替人拍板。
    assert await _phase(db, "mis-inv") != "resolved"


@pytest.mark.asyncio
async def test_validation_advances_to_needs_input(db):
    await _insert_mission(db, "mis-val", "validation")
    await notify_experiment_completed(
        "strategy", "st-keltner", task_id="bt-006", experiment_kind="backtest"
    )
    assert await _phase(db, "mis-val") == "needs_input"


@pytest.mark.asyncio
async def test_needs_input_mission_stays_and_still_logs(db):
    await _insert_mission(db, "mis-wait", "needs_input")
    await notify_experiment_completed(
        "strategy", "st-keltner", task_id="bt-007", experiment_kind="backtest"
    )
    # needs_input → needs_input 不是合法边;不该因为重复实验而报错或乱跳。
    assert await _phase(db, "mis-wait") == "needs_input"
    assert await _activity_count(db, "mis-wait", "experiment_completed") == 1


@pytest.mark.asyncio
async def test_transition_records_audit_activity(db):
    await _insert_mission(db, "mis-audit", "investigation")
    await notify_experiment_completed(
        "strategy", "st-keltner", task_id="bt-008", experiment_kind="backtest"
    )
    rows = await db.execute(
        "SELECT detail_json FROM mission_activity "
        "WHERE mission_id = 'mis-audit' AND kind = 'phase_transition'"
    )
    assert len(rows) == 1
    detail = rows[0]["detail_json"]
    assert '"from": "investigation"' in detail
    assert '"to": "needs_input"' in detail
    assert "bt-008" in detail


@pytest.mark.asyncio
async def test_one_bad_mission_does_not_block_the_other(db):
    await _insert_mission(db, "mis-bad", "needs_input")
    await _insert_mission(db, "mis-good", "investigation")
    await notify_experiment_completed(
        "strategy", "st-keltner", task_id="bt-009", experiment_kind="backtest"
    )
    assert await _activity_count(db, "mis-bad", "experiment_completed") == 1
    assert await _phase(db, "mis-good") == "needs_input"
