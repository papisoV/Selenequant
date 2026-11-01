"""v028 decision_signals: registry position, column shape, CHECK vocab
enforcement, the (signal_id, horizon, engine_version) unique key, and the
nullable-outcome invariant that keeps 'unable' distinct from 'miss'."""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager

_SIGNAL_COLUMNS = {
    "id",
    "stock_code",
    "stock_name",
    "market",
    "source_type",
    "source_agent",
    "source_report_id",
    "trace_id",
    "decision_profile",
    "market_phase",
    "trigger_source",
    "action",
    "action_label",
    "confidence",
    "score",
    "horizon",
    "entry_low",
    "entry_high",
    "stop_loss",
    "target_price",
    "invalidation",
    "watch_conditions",
    "reason",
    "risk_summary",
    "catalyst_summary",
    "evidence_json",
    "data_quality_summary_json",
    "plan_quality",
    "status",
    "expires_at",
    "created_at",
    "updated_at",
    "metadata_json",
}

_OUTCOME_COLUMNS = {
    "id",
    "signal_id",
    "horizon",
    "engine_version",
    "eval_status",
    "outcome",
    "direction_expected",
    "direction_correct",
    "unable_reason",
    "anchor_date",
    "eval_window_days",
    "start_price",
    "end_close",
    "max_high",
    "min_low",
    "stock_return_pct",
    "action",
    "market",
    "market_phase",
    "source_type",
    "source_agent",
    "plan_quality",
    "data_quality_level",
    "holding_state",
    "created_at",
    "updated_at",
}

_NOW = "2026-10-11T00:00:00+00:00"


@pytest.mark.asyncio
async def test_v028_registered_after_v027():
    # v028 之后又有 v029(回测稳健性)入列,故不再是 ALL_MIGRATIONS[-1];
    # 改为对账式断言(同 v019 让号后的写法):v027 → v028 顺序正确即可。
    ids = [m.id for m in ALL_MIGRATIONS]
    assert "v028_decision_signals" in ids
    assert (
        ids.index("v028_decision_signals")
        == ids.index("v027_forward_validation_indexes") + 1
    )


@pytest_asyncio.fixture
async def signals_db():
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield db


async def _seed_signal(db, **overrides) -> int:
    params = {
        "stock_code": "600519",
        "market": "cn",
        "source_type": "manual",
        "trigger_source": "test",
        "action": "sell",
        "status": "active",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    params.update(overrides)
    rows = await db.execute(
        """
        INSERT INTO decision_signals
            (stock_code, market, source_type, trigger_source, action, status,
             created_at, updated_at)
        VALUES (:stock_code, :market, :source_type, :trigger_source, :action,
                :status, :created_at, :updated_at)
        RETURNING id
        """,
        params,
    )
    signal_id = rows[0]["id"]
    assert isinstance(signal_id, int)
    return signal_id


async def _seed_outcome(db, signal_id: int, **overrides) -> None:
    params = {
        "signal_id": signal_id,
        "horizon": "5d",
        "engine_version": "decision-signal-v1",
        "eval_status": "unable",
        "holding_state": "unknown",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    params.update(overrides)
    columns = ", ".join(params)
    placeholders = ", ".join(f":{key}" for key in params)
    await db.execute(
        f"INSERT INTO decision_signal_outcomes ({columns}) VALUES ({placeholders})",
        params,
    )


@pytest.mark.asyncio
async def test_v028_creates_both_tables_with_expected_columns(signals_db):
    signal_cols = {
        r["name"]
        for r in await signals_db.execute("PRAGMA table_info(decision_signals)")
    }
    outcome_cols = {
        r["name"]
        for r in await signals_db.execute("PRAGMA table_info(decision_signal_outcomes)")
    }
    assert _SIGNAL_COLUMNS <= signal_cols
    assert _OUTCOME_COLUMNS <= outcome_cols


@pytest.mark.asyncio
async def test_v028_creates_expected_indexes(signals_db):
    rows = await signals_db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND name LIKE 'idx_decision_signal%'"
    )
    names = {r["name"] for r in rows}
    assert names == {
        "idx_decision_signals_stock_status_time",
        "idx_decision_signals_market_status_time",
        "idx_decision_signals_action_horizon",
        "idx_decision_signals_status_created",
        "idx_decision_signal_outcome_stats_action",
        "idx_decision_signal_outcome_stats_market",
        "idx_decision_signal_outcome_signal",
        "idx_decision_signal_outcome_retry",
    }


@pytest.mark.asyncio
async def test_v028_signal_action_check_rejects_unknown_state(signals_db):
    # 九态以上/拼错的动作必须被 schema 拒绝 —— 词表是 📋1 的八态,没有别的。
    for bad_action in ("long", "strong_buy", "BUY", "not_up"):
        with pytest.raises(Exception, match="CHECK"):
            await _seed_signal(signals_db, action=bad_action)


@pytest.mark.asyncio
async def test_v028_signal_status_check_rejects_unknown_state(signals_db):
    with pytest.raises(Exception, match="CHECK"):
        await _seed_signal(signals_db, status="pending")


@pytest.mark.asyncio
async def test_v028_outcome_eval_status_check_rejects_third_state(signals_db):
    signal_id = await _seed_signal(signals_db)
    with pytest.raises(Exception, match="CHECK"):
        await _seed_outcome(signals_db, signal_id, eval_status="partial")


@pytest.mark.asyncio
async def test_v028_outcome_value_check_rejects_unknown_verdict(signals_db):
    signal_id = await _seed_signal(signals_db)
    with pytest.raises(Exception, match="CHECK"):
        await _seed_outcome(
            signals_db,
            signal_id,
            eval_status="completed",
            outcome="mostly_right",
        )


@pytest.mark.asyncio
async def test_v028_unable_row_carries_no_outcome(signals_db):
    # 诚实拒答的核心不变量:unable 时 outcome 必须为 NULL。若允许"顺手填个 miss",
    # 数据不足就会被记成判错。
    signal_id = await _seed_signal(signals_db)
    await _seed_outcome(signals_db, signal_id, eval_status="unable", outcome=None)
    rows = await signals_db.execute(
        "SELECT eval_status, outcome FROM decision_signal_outcomes "
        "WHERE signal_id = ?",
        (signal_id,),
    )
    assert rows[0]["eval_status"] == "unable"
    assert rows[0]["outcome"] is None


@pytest.mark.asyncio
async def test_v028_stats_exclude_archived_signals_via_status(signals_db):
    # archived 是"收进柜子不再计分",不是"判错"。统计查询靠 status 过滤,
    # 这里锁住 archived 确实可写、且与 active 区分得开。
    active_id = await _seed_signal(signals_db, status="active")
    archived_id = await _seed_signal(signals_db, status="archived")
    await _seed_outcome(signals_db, active_id, eval_status="completed", outcome="hit")
    await _seed_outcome(
        signals_db, archived_id, eval_status="completed", outcome="miss"
    )
    rows = await signals_db.execute(
        """
        SELECT o.outcome
        FROM decision_signal_outcomes o
        JOIN decision_signals s ON s.id = o.signal_id
        WHERE s.status = 'active'
        """
    )
    assert [r["outcome"] for r in rows] == ["hit"]


@pytest.mark.asyncio
async def test_v028_unique_key_blocks_duplicate_evaluation(signals_db):
    # 同一 (signal, horizon, engine_version) 只能有一条判定 —— 重评是 UPDATE
    # 不是再插一行,否则命中率会重复计数。
    signal_id = await _seed_signal(signals_db)
    await _seed_outcome(signals_db, signal_id, eval_status="unable")
    with pytest.raises(Exception, match="UNIQUE"):
        await _seed_outcome(
            signals_db, signal_id, eval_status="completed", outcome="hit"
        )


@pytest.mark.asyncio
async def test_v028_distinct_horizons_coexist(signals_db):
    signal_id = await _seed_signal(signals_db)
    for horizon in ("1d", "3d", "5d", "10d"):
        await _seed_outcome(signals_db, signal_id, horizon=horizon)
    rows = await signals_db.execute(
        "SELECT horizon FROM decision_signal_outcomes WHERE signal_id = ? "
        "ORDER BY horizon",
        (signal_id,),
    )
    assert [r["horizon"] for r in rows] == ["10d", "1d", "3d", "5d"]


@pytest.mark.asyncio
async def test_v028_distinct_engine_versions_coexist(signals_db):
    # 换引擎版本要能并存,老版本的历史判定不能被新版本覆盖 —— 否则统计口径
    # 会随代码升级悄悄重写。
    signal_id = await _seed_signal(signals_db)
    await _seed_outcome(signals_db, signal_id, engine_version="decision-signal-v1")
    await _seed_outcome(signals_db, signal_id, engine_version="decision-signal-v2")
    rows = await signals_db.execute(
        "SELECT COUNT(*) AS n FROM decision_signal_outcomes WHERE signal_id = ?",
        (signal_id,),
    )
    assert rows[0]["n"] == 2


@pytest.mark.asyncio
async def test_v028_outcome_requires_an_existing_signal(signals_db):
    with pytest.raises(Exception, match="FOREIGN KEY"):
        await _seed_outcome(signals_db, 999999, eval_status="unable")


@pytest.mark.asyncio
async def test_v028_rerun_is_idempotent(signals_db):
    # 管理器建库时已经跑过一遍全量迁移;再跑一次必须无事可做。
    runner = MigrationRunner(list(ALL_MIGRATIONS))
    async with signals_db.connection() as conn:
        assert await runner.run_pending(conn) == []
