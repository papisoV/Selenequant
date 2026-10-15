"""透明性批2-6: notify_experiment_completed 写 mission_activity 验证。

helper 契约(fire-and-forget):
- 匹配 subject 的 open mission(phase 非 resolved/parked)追加 experiment_completed
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
