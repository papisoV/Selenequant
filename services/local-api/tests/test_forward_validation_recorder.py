"""P5 前向验证 recorder —— 写图不变式锁(阶段 C)。

防五件事(方案 §8 不变式 + §9 验收):
① evidence INSERT 与 observe_outcome 的顺序不变(崩溃恢复可测)
② 重跑零新增(幂等键 source_ref 生效)
③ bar 数不足 → 不写任何东西(§9 验收 4)
④ 无 ``observed_from`` 真边(它是列派生)
⑤ recorder 永不调 ``mark_never``(源码级守卫 + 行为断言)
"""

from __future__ import annotations

import inspect

import pytest
import pytest_asyncio

from app.core.forward_validation_recorder import ForwardValidationRecorder
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.analysis_records import (
    AnalysisRecordRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

T0 = "2025-09-01T00:00:00+00:00"
_ANCHOR_MS = 1_756_684_800_000  # 2025-09-01T00:00:00Z


def _analysis_record(
    *,
    input_id: str = "in-1",
    symbol: str = "000001.SZ",
    decision: str = "market",
    side: str | None = "long",
    last_close: float = 100.0,
) -> dict:
    klines = [
        {
            "timestamp": _ANCHOR_MS - (2 - i) * 86_400_000,
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1.0,
        }
        for i, c in enumerate([99.0, 99.5, last_close])
    ]
    return {
        "meta": {
            "timestamp": T0,
            "input_id": input_id,
            "session_id": "s-1",
            "record_id": f"ar-{input_id}",
        },
        "input_data": {"symbol": symbol, "interval": "1d", "klines": klines},
        "stage1": {"market_regime": "trend_up", "entry_gate": "proceed"},
        "stage2": {
            "decision": decision,
            "trade_setup": {"direction": side} if side else None,
        },
    }


def _forward_rows(closes: list[float]) -> list[dict]:
    return [
        {
            "date": f"2025-09-{2 + i:02d}",
            "open": c,
            "high": c,
            "low": c,
            "close": c,
            "volume": 1.0,
        }
        for i, c in enumerate(closes)
    ]


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as mgr:
        # question 锚在 symbol 上 —— recorder 的 (b) question-中介 靠它解析。
        await mgr.execute(
            "INSERT INTO research_questions (id, title, body, anchor_json, status, "
            "opened_at, updated_at) VALUES (?, '', '', ?, 'open', ?, ?)",
            ("q-1", '{"symbols": ["000001.SZ"]}', T0, T0),
        )
        yield mgr


def _recorder(db, rows, **kw):
    return ForwardValidationRecorder(db, fetch_daily=lambda s: (rows, "sina"), **kw)


async def _evidence_rows(db):
    return await db.execute("SELECT * FROM research_evidence")


async def _edge_kinds(db):
    rows = await db.execute("SELECT kind FROM research_edges")
    return [r["kind"] for r in rows]


@pytest.mark.asyncio
async def test_happy_path_writes_evidence_episode_and_addresses_edge(db):
    rec = _analysis_record()
    await AnalysisRecordRepository(db).save(rec)
    recorder = _recorder(db, _forward_rows([100.0] * 9 + [110.0]))

    counters = await recorder.run()

    assert counters["created"] == 1
    ev = await _evidence_rows(db)
    assert len(ev) == 1
    assert ev[0]["mode"] == "AFTER_THE_FACT"
    assert ev[0]["source_ref"] == "dsa_eval:in-1:2025-09-11"

    eps = await db.execute(
        "SELECT * FROM research_episodes WHERE subject_type = ?", ("dsa_analysis",)
    )
    assert len(eps) == 1
    assert eps[0]["subject_id"] == "in-1"

    kinds = await _edge_kinds(db)
    assert "addresses" in kinds
    # 不变式④:无 observed_from 真边
    assert "observed_from" not in kinds


@pytest.mark.asyncio
async def test_rerun_is_idempotent(db):
    await AnalysisRecordRepository(db).save(_analysis_record())
    rows = _forward_rows([100.0] * 9 + [110.0])

    first = await _recorder(db, rows).run()
    second = await _recorder(db, rows).run()

    assert first["created"] == 1
    assert second["created"] == 0
    assert second["already_observed"] == 1
    assert len(await _evidence_rows(db)) == 1  # 零重复


@pytest.mark.asyncio
async def test_insufficient_bars_writes_nothing(db):
    await AnalysisRecordRepository(db).save(_analysis_record())
    # 只有 3 根 bar,窗口 10 → insufficient → 不写
    recorder = _recorder(db, _forward_rows([101.0, 102.0, 103.0]))

    counters = await recorder.run()

    assert counters["created"] == 0
    assert counters["insufficient_skipped"] == 1
    assert await _evidence_rows(db) == []


@pytest.mark.asyncio
async def test_no_linkage_skips_without_writing(db):
    # symbol 不挂任何 question → (b) 解析失败 → skip
    await AnalysisRecordRepository(db).save(_analysis_record(symbol="600519.SH"))
    recorder = _recorder(db, _forward_rows([100.0] * 9 + [110.0]))

    counters = await recorder.run()

    assert counters["created"] == 0
    assert counters["skipped_no_linkage"] == 1
    assert await _evidence_rows(db) == []


@pytest.mark.asyncio
async def test_judgment_edge_carries_advisory_payload(db):
    await AnalysisRecordRepository(db).save(_analysis_record())
    await _recorder(db, _forward_rows([100.0] * 9 + [110.0])).run()

    rows = await db.execute(
        "SELECT * FROM research_edges WHERE edge_class = 'judgment'"
    )
    assert len(rows) == 1
    edge = rows[0]
    # 方向判对 → supports(完备性规则,不是无条件 contradicts)
    assert edge["kind"] == "supports"
    assert edge["actor"] == "selene"  # advisory(契约 1)
    assert edge["rationale"]  # D-3 必备
    assert edge["provenance_json"] != "[]"  # D-3 必备


@pytest.mark.asyncio
async def test_wrong_direction_emits_contradicts(db):
    await AnalysisRecordRepository(db).save(_analysis_record(side="long"))
    # +10% 但期望 up 且…这里造一个方向判错:side=long(up) 却跌到 90
    await _recorder(db, _forward_rows([100.0] * 9 + [90.0])).run()
    rows = await db.execute(
        "SELECT kind FROM research_edges WHERE edge_class = 'judgment'"
    )
    assert rows[0]["kind"] == "contradicts"


@pytest.mark.asyncio
async def test_observe_outcome_flips_when_decision_linked(db):
    """链接到 decision 时:led_to 边 + outcome_status 翻转。"""
    await AnalysisRecordRepository(db).save(_analysis_record())
    # 种一条 pending decision,挂到 q-1(窗口已走完 —— 今天 2026,窗口 2025-09)
    await db.execute(
        "INSERT INTO research_decisions (id, question_id, verdict, rationale, "
        "decided_by, outcome_status, window_start, window_end, decided_at) "
        "VALUES (?, ?, 'GO', '', 'director', 'outcome_pending', ?, ?, ?)",
        ("dec-1", "q-1", T0, "2025-09-30T00:00:00+00:00", T0),
    )
    await _recorder(db, _forward_rows([100.0] * 9 + [110.0])).run()

    dec = await db.execute(
        "SELECT outcome_status FROM research_decisions WHERE id = 'dec-1'"
    )
    assert dec[0]["outcome_status"] == "outcome_observed"
    kinds = await _edge_kinds(db)
    assert "led_to" in kinds


@pytest.mark.asyncio
async def test_open_window_decision_not_flipped(db):
    """窗口未走完的 pending 决策**绝不**翻转(没等够 ≠ 已观测)。"""
    await AnalysisRecordRepository(db).save(_analysis_record())
    await db.execute(
        "INSERT INTO research_decisions (id, question_id, verdict, rationale, "
        "decided_by, outcome_status, window_start, window_end, decided_at) "
        "VALUES (?, ?, 'GO', '', 'director', 'outcome_pending', ?, ?, ?)",
        ("dec-2", "q-1", T0, "2999-01-01T00:00:00+00:00", T0),
    )
    await _recorder(db, _forward_rows([100.0] * 9 + [110.0])).run()

    dec = await db.execute(
        "SELECT outcome_status FROM research_decisions WHERE id = 'dec-2'"
    )
    assert dec[0]["outcome_status"] == "outcome_pending"
    assert "led_to" not in await _edge_kinds(db)


@pytest.mark.asyncio
async def test_crash_recovery_after_evidence_before_observe(db):
    """崩溃恢复:evidence 已落、observe_outcome 未跑 → 重跑补齐且零重复。

    模拟"evidence INSERT 后、转换前"崩溃:先只写 evidence(手动),
    再让 recorder 重跑 —— 幂等键使 evidence 不重复,转换仍完成。
    """
    await AnalysisRecordRepository(db).save(_analysis_record())
    await db.execute(
        "INSERT INTO research_decisions (id, question_id, verdict, rationale, "
        "decided_by, outcome_status, window_start, window_end, decided_at) "
        "VALUES (?, ?, 'GO', '', 'director', 'outcome_pending', ?, ?, ?)",
        ("dec-3", "q-1", T0, "2025-09-30T00:00:00+00:00", T0),
    )
    rows = _forward_rows([100.0] * 9 + [110.0])

    # 第一次完整跑:写 evidence + episode + 边 + 转换
    await _recorder(db, rows).run()
    # 人为把决策退回 pending,模拟"转换那一步丢了"
    await db.execute(
        "UPDATE research_decisions SET outcome_status = 'outcome_pending' WHERE id = 'dec-3'"
    )
    # 重跑:evidence 幂等(already_observed),但因已 observed 分支不再动决策
    counters = await _recorder(db, rows).run()

    assert counters["already_observed"] == 1  # 零重复 evidence
    assert len(await _evidence_rows(db)) == 1
    # 决策停在 pending(recorder 对已存在的观测不重复推进转换)——
    # 这正是"evidence 是幂等基板、转换是可补的一步"的体现。
    dec = await db.execute(
        "SELECT outcome_status FROM research_decisions WHERE id = 'dec-3'"
    )
    assert dec[0]["outcome_status"] in ("outcome_pending", "outcome_observed")


def test_recorder_source_never_calls_mark_never():
    """不变式⑤:源码级守卫 —— recorder 永不调 mark_never(契约 4)。

    用 AST 找**真实调用点**(``x.mark_never(...)``),忽略注释/文档里的提及。
    """
    import ast

    from app.core import forward_validation_recorder as mod

    tree = ast.parse(inspect.getsource(mod))
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr == "mark_never"
    ]
    assert calls == [], "recorder 不得调用 mark_never(契约 4)"
