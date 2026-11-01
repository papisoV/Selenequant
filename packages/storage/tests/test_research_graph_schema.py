"""v016 研究图谱九表 —— DDL 与约束层锁。

锁三件事:①九张表都真的建出来了 ②CHECK 约束真的挡得住非法值
③唯一键真的挡得住重复(同一 Question 的 revision seq 不可重)。
"""

from __future__ import annotations

import sqlite3

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager

EXPECTED_TABLES = (
    "research_questions",
    "research_question_events",
    "research_revisions",
    "research_edges",
    "research_episodes",
    "research_evidence",
    "research_hypotheses",
    "research_experiments",
    "research_decisions",
)


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


async def _raw_execute(db, sql: str, params: tuple = ()) -> None:
    """绕过仓储直接打 SQL,用来探测约束是否真的落在 schema 上。"""
    async with db.connection() as conn:
        await conn.execute(sql, params)
        await conn.commit()


_TS = "2026-09-16T00:00:00+00:00"


async def _seed_question(db, question_id: str) -> None:
    """FK 是开着的,子表测试必须先有父行 —— 否则报的是 FK 不是被测约束。"""
    await _raw_execute(
        db,
        "INSERT INTO research_questions "
        "(id, title, status, opened_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (question_id, "种子问题", "open", _TS, _TS),
    )


@pytest.mark.asyncio
async def test_all_nine_tables_exist(db):
    rows = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'research_%'"
    )
    names = {r["name"] for r in rows}
    for table in EXPECTED_TABLES:
        assert table in names, f"missing table: {table}"


@pytest.mark.asyncio
async def test_question_status_check_rejects_unknown(db):
    with pytest.raises(sqlite3.IntegrityError):
        await _raw_execute(
            db,
            "INSERT INTO research_questions "
            "(id, title, status, opened_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (
                "q-bad",
                "t",
                "已解决",
                "2026-09-16T00:00:00+00:00",
                "2026-09-16T00:00:00+00:00",
            ),
        )


@pytest.mark.asyncio
async def test_question_accepts_five_statuses(db):
    for i, status in enumerate(
        ("open", "sufficiently_answered", "falsified", "parked", "superseded")
    ):
        await _raw_execute(
            db,
            "INSERT INTO research_questions "
            "(id, title, status, opened_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (
                f"q-{i}",
                "t",
                status,
                "2026-09-16T00:00:00+00:00",
                "2026-09-16T00:00:00+00:00",
            ),
        )
    rows = await db.execute("SELECT id FROM research_questions")
    assert len(rows) == 5


@pytest.mark.asyncio
async def test_revision_seq_unique_per_question(db):
    await _seed_question(db, "q-1")
    await _raw_execute(
        db,
        "INSERT INTO research_revisions "
        "(id, question_id, seq, content, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("rev-1", "q-1", 1, "第一版", "selene", _TS),
    )
    # 主键不同但 (question_id, seq) 相同 —— 必须被唯一键拦下
    with pytest.raises(sqlite3.IntegrityError):
        await _raw_execute(
            db,
            "INSERT INTO research_revisions "
            "(id, question_id, seq, content, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            ("rev-2", "q-1", 1, "重复序号", "selene", _TS),
        )
    # 换个 seq 则放行 —— 证明拦的是 (question_id, seq),不是 question_id
    await _raw_execute(
        db,
        "INSERT INTO research_revisions "
        "(id, question_id, seq, content, actor, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        ("rev-3", "q-1", 2, "第二版", "selene", _TS),
    )


@pytest.mark.asyncio
async def test_edge_class_check_rejects_unknown(db):
    with pytest.raises(sqlite3.IntegrityError):
        await _raw_execute(
            db,
            "INSERT INTO research_edges "
            "(id, from_type, from_id, to_type, to_id, kind, edge_class, actor, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "edge-x",
                "question",
                "q-1",
                "evidence",
                "ev-1",
                "supports",
                "opinion",
                "selene",
                "2026-09-16T00:00:00+00:00",
            ),
        )


@pytest.mark.asyncio
async def test_evidence_mode_check(db):
    await _raw_execute(
        db,
        "INSERT INTO research_episodes "
        "(id, subject_type, subject_id, title, boundary_start, boundary_end, "
        "occurred_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("ep-1", "strategy", "keltner", "种子 Episode", _TS, _TS, _TS, _TS),
    )
    for i, mode in enumerate(("RECORDED", "RECONSTRUCTED", "AFTER_THE_FACT")):
        await _raw_execute(
            db,
            "INSERT INTO research_evidence "
            "(id, episode_id, mode, observed_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                f"ev-{i}",
                "ep-1",
                mode,
                "2026-09-16T00:00:00+00:00",
                "2026-09-16T00:00:00+00:00",
            ),
        )
    with pytest.raises(sqlite3.IntegrityError):
        await _raw_execute(
            db,
            "INSERT INTO research_evidence "
            "(id, episode_id, mode, observed_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (
                "ev-bad",
                "ep-1",
                "GUESSED",
                "2026-09-16T00:00:00+00:00",
                "2026-09-16T00:00:00+00:00",
            ),
        )


@pytest.mark.asyncio
async def test_decision_outcome_status_check(db):
    # 先种父行,让这条测试失败的原因唯一 = outcome_status 的 CHECK(而非 FK)。
    await _seed_question(db, "q-1")
    with pytest.raises(sqlite3.IntegrityError):
        await _raw_execute(
            db,
            "INSERT INTO research_decisions "
            "(id, question_id, verdict, decided_by, outcome_status, decided_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "dec-1",
                "q-1",
                "REVALIDATE",
                "director",
                "maybe_later",
                "2026-09-16T00:00:00+00:00",
            ),
        )


@pytest.mark.asyncio
async def test_migration_ledger_records_v016(db):
    rows = await db.execute("SELECT id, version FROM _migrations WHERE version = 16")
    assert len(rows) == 1
    assert rows[0]["id"] == "v016_research_graph"


@pytest.mark.asyncio
async def test_migration_is_idempotent(db):
    """重跑 pending 不应报错,也不应重复记账。"""
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with db.connection() as conn:
        applied = await runner.run_pending(conn)
    assert applied == []


# ---- v025 question_revisit:两列 + CHECK + partial index ---------------------


@pytest.mark.asyncio
async def test_v025_columns_exist(db):
    rows = await db.execute("PRAGMA table_info(research_questions)")
    q_cols = {r["name"] for r in rows}
    assert "next_revisit_at" in q_cols

    rows = await db.execute("PRAGMA table_info(research_revisions)")
    r_cols = {r["name"] for r in rows}
    assert "horizon" in r_cols


@pytest.mark.asyncio
async def test_v025_horizon_check_rejects_unknown(db):
    await _seed_question(db, "q-v025bad")
    await _raw_execute(
        db,
        "INSERT INTO research_revisions "
        "(id, question_id, seq, content, actor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("rev-v025bad", "q-v025bad", 1, "内容", "user", _TS),
    )
    # 先证 INSERT 显式非法值就被 CHECK 挡在门外,
    # 再证 UPDATE 改成非法值同样挡 —— 两路都不给 weekly 进库。
    with pytest.raises(sqlite3.IntegrityError):
        await _raw_execute(
            db,
            "INSERT INTO research_revisions "
            "(id, question_id, seq, content, actor, created_at, horizon) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("rev-v025bad2", "q-v025bad", 2, "内容", "user", _TS, "weekly"),
        )
    with pytest.raises(sqlite3.IntegrityError):
        await _raw_execute(
            db,
            "UPDATE research_revisions SET horizon = 'weekly' WHERE id = ?",
            ("rev-v025bad",),
        )


@pytest.mark.asyncio
async def test_v025_horizon_accepts_both_values(db):
    await _seed_question(db, "q-v025ok")
    await _raw_execute(
        db,
        "INSERT INTO research_revisions "
        "(id, question_id, seq, content, actor, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("rev-v025a", "q-v025ok", 1, "结构性结论", "user", _TS),
    )
    await _raw_execute(
        db,
        "INSERT INTO research_revisions "
        "(id, question_id, seq, content, actor, created_at, horizon) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("rev-v025b", "q-v025ok", 2, "战术判断", "user", _TS, "tactical"),
    )
    rows = await db.execute(
        "SELECT id, horizon FROM research_revisions WHERE question_id = ? ORDER BY seq",
        ("q-v025ok",),
    )
    assert rows[0]["horizon"] == "structural"  # 存量语义:默认保守
    assert rows[1]["horizon"] == "tactical"


@pytest.mark.asyncio
async def test_v025_partial_index_exists(db):
    rows = await db.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'index' "
        "AND name = 'idx_research_questions_revisit'"
    )
    assert len(rows) == 1
    assert "WHERE" in (rows[0]["sql"] or "")


@pytest.mark.asyncio
async def test_v025_ledger_recorded(db):
    rows = await db.execute("SELECT id, version FROM _migrations WHERE version = 25")
    assert len(rows) == 1
    assert rows[0]["id"] == "v025_question_revisit"
    versions = sorted(m.version for m in ALL_MIGRATIONS)
    assert versions == sorted(set(versions)), "migration version 不可撞号"
    assert 25 in versions
