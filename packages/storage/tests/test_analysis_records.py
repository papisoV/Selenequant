"""Tests for AnalysisRecordRepository (US-007)."""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.analysis_records import (
    AnalysisRecordRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


def _sample_record() -> dict:
    """构造一条与编排器 record_assembly 对齐的完整记录。"""
    return {
        "meta": {
            "timestamp": "2026-08-11T00:00:00",
            "input_id": "input-abc",
            "session_id": "sess-1",
        },
        "input_data": {
            "symbol": "BTC/USDT",
            "interval": "1h",
            "klines": [{"timestamp": 1, "close": 100.0}],
            "bar_features": {"close_last": 100.0},
        },
        "stage1": {"market_regime": "trend_up", "entry_gate": "proceed"},
        "stage2": {"decision": "market", "dispatch_playbooks": ["al-brooks-trend"]},
        "dispatch_playbooks": ["al-brooks-foundations", "al-brooks-trend"],
        "usage_total": {"prompt_tokens": 100, "completion_tokens": 50},
        "prompt": {"stage1": [{"role": "system", "content": "sys"}]},
        "raw": {"stage1": "{...}", "stage2": "{...}"},
    }


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield AnalysisRecordRepository(db)


@pytest.mark.asyncio
async def test_save_returns_id(repo: AnalysisRecordRepository):
    rid = await repo.save(_sample_record())
    assert rid.startswith("ar-")
    assert len(rid) > 3


@pytest.mark.asyncio
async def test_save_get_roundtrip_preserves_nested(repo: AnalysisRecordRepository):
    record = _sample_record()
    rid = await repo.save(record)
    loaded = await repo.get(rid)
    assert loaded is not None
    assert loaded["meta"]["input_id"] == "input-abc"
    assert loaded["stage1"]["entry_gate"] == "proceed"
    assert loaded["stage2"]["decision"] == "market"
    assert loaded["raw"] == {"stage1": "{...}", "stage2": "{...}"}
    assert loaded["usage_total"]["prompt_tokens"] == 100


@pytest.mark.asyncio
async def test_save_extracts_index_columns(repo: AnalysisRecordRepository):
    await repo.save(_sample_record())
    rows = await repo.list()
    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "BTC/USDT"
    assert row["interval"] == "1h"
    assert row["entry_gate"] == "proceed"
    assert row["decision"] == "market"
    assert row["input_id"] == "input-abc"
    assert row["session_id"] == "sess-1"


@pytest.mark.asyncio
async def test_list_symbol_filter(repo: AnalysisRecordRepository):
    btc = _sample_record()
    eth = _sample_record()
    eth["input_data"] = {"symbol": "ETH/USDT", "interval": "1h"}
    await repo.save(btc)
    await repo.save(eth)

    rows = await repo.list(symbol="ETH/USDT")
    assert len(rows) == 1
    assert rows[0]["symbol"] == "ETH/USDT"


@pytest.mark.asyncio
async def test_list_limit_offset(repo: AnalysisRecordRepository):
    for i in range(5):
        rec = _sample_record()
        rec["meta"]["input_id"] = f"input-{i}"
        await repo.save(rec)

    first_page = await repo.list(limit=2)
    assert len(first_page) == 2
    second_page = await repo.list(limit=2, offset=2)
    assert len(second_page) == 2

    all_ids = [r["input_id"] for r in first_page + second_page]
    assert len(set(all_ids)) == 4  # 无重复


@pytest.mark.asyncio
async def test_count(repo: AnalysisRecordRepository):
    assert await repo.count() == 0
    await repo.save(_sample_record())
    await repo.save(_sample_record())
    assert await repo.count() == 2


@pytest.mark.asyncio
async def test_get_missing_returns_none(repo: AnalysisRecordRepository):
    assert await repo.get("ar-nope") is None


@pytest.mark.asyncio
async def test_list_failures_only_exception_records(repo: AnalysisRecordRepository):
    """C4 — 失败样本：仅 record_json 含 exception 的记录被列出，且带完整 record。"""
    ok = _sample_record()
    ok["meta"]["input_id"] = "ok-record"
    await repo.save(ok)

    bad = _sample_record()
    bad["meta"]["input_id"] = "bad-record"
    bad["exception"] = {"type": "ProviderError", "message": "connect timeout"}
    await repo.save(bad)

    rows = await repo.list_failures()
    assert len(rows) == 1
    item = rows[0]
    assert item["input_id"] == "bad-record"
    assert item["exception_type"] == "ProviderError"
    assert item["exception_message"] == "connect timeout"
    # 完整 record 保留（prompt/raw 供调优输入）
    assert item["record"]["exception"] == {
        "type": "ProviderError",
        "message": "connect timeout",
    }
    assert "prompt" in item["record"]
    assert "raw" in item["record"]


@pytest.mark.asyncio
async def test_list_failures_limit_offset(repo: AnalysisRecordRepository):
    for i in range(3):
        rec = _sample_record()
        rec["meta"]["input_id"] = f"bad-{i}"
        rec["exception"] = {"type": "ProviderError", "message": f"err-{i}"}
        await repo.save(rec)

    page1 = await repo.list_failures(limit=2)
    page2 = await repo.list_failures(limit=2, offset=2)
    ids = [r["input_id"] for r in page1 + page2]
    assert len(page1) == 2
    assert len(page2) == 1
    assert len(set(ids)) == 3  # 两页合并覆盖全部且无重复
    assert set(ids) == {"bad-0", "bad-1", "bad-2"}


# ---------------------------------------------------------------------------
# W2 append_payload (US-001): optimistic-lock attach of a top-level key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_payload_attaches_key(repo: AnalysisRecordRepository):
    """Normal path: the latest record for input_id gains the key, readable back."""
    rec = _sample_record()
    rec["meta"]["input_id"] = "w2-attach"
    await repo.save(rec)

    ok = await repo.append_payload(
        "w2-attach", "backtest_verification", {"result": "win", "pnl_r": 1.5}
    )
    assert ok is True

    loaded = await repo.get_by_input_id("w2-attach")
    assert loaded is not None
    assert loaded["backtest_verification"] == {"result": "win", "pnl_r": 1.5}
    # 原有字段不被覆写
    assert loaded["meta"]["input_id"] == "w2-attach"
    assert loaded["stage1"]["entry_gate"] == "proceed"


@pytest.mark.asyncio
async def test_append_payload_missing_input_id_returns_false(
    repo: AnalysisRecordRepository,
):
    assert await repo.append_payload("no-such", "outcome", {"status": "win"}) is False


@pytest.mark.asyncio
async def test_append_payload_optimistic_lock_conflict(
    repo: AnalysisRecordRepository,
    monkeypatch: pytest.MonkeyPatch,
):
    """A concurrent writer landing between our SELECT and UPDATE → False.

    SQLite evaluates an UPDATE's WHERE during the row scan — before BEFORE
    UPDATE triggers fire — so a trigger cannot simulate the race. Instead we
    interleave at the repo boundary: the db's execute is patched so the
    moment append_payload's SELECT returns, caller A rewrites the row. B's
    UPDATE then carries the stale record_json as its predicate → rowcount 0.
    """
    rec = _sample_record()
    rec["meta"]["input_id"] = "w2-race"
    await repo.save(rec)

    real_execute = repo._db.execute
    fired: dict[str, bool] = {}

    async def racing_execute(sql: str, params: tuple = ()) -> list:
        rows: list = await real_execute(sql, params)
        if "SELECT id, record_json FROM analysis_records" in sql and not fired.get(
            "a_won"
        ):
            fired["a_won"] = True
            await real_execute(
                "UPDATE analysis_records SET record_json = record_json || ' '"
                " WHERE input_id = ?",
                ("w2-race",),
            )
        return rows

    monkeypatch.setattr(repo._db, "execute", racing_execute)

    ok = await repo.append_payload(
        "w2-race", "backtest_verification", {"result": "win"}
    )
    assert ok is False

    monkeypatch.undo()
    loaded = await repo.get_by_input_id("w2-race")
    assert loaded is not None
    assert fired["a_won"]  # 竞态真的被注入了,测试没有空转
    assert "backtest_verification" not in loaded


@pytest.mark.asyncio
async def test_append_payload_uses_latest_row_for_input_id(
    repo: AnalysisRecordRepository,
):
    """Multiple saves under one input_id: append targets the newest row.

    created_at is second-granular and the row id is a random uuid, so a
    same-second pair has nondeterministic ordering — stage the two saves a
    second apart (tmp_path-free: sqlite 'now' has 1s resolution; use
    meta.timestamp differing and rely on the ordering only after a real
    created_at gap). Sleep 1.1s keeps the test honest without flaking.
    """
    first = _sample_record()
    first["meta"]["input_id"] = "w2-multi"
    await repo.save(first)
    import asyncio

    await asyncio.sleep(1.1)
    second = _sample_record()
    second["meta"]["input_id"] = "w2-multi"
    second["stage1"] = {"market_regime": "range", "entry_gate": "proceed"}
    await repo.save(second)

    ok = await repo.append_payload("w2-multi", "outcome", {"status": "expired"})
    assert ok is True

    loaded = await repo.get_by_input_id("w2-multi")
    assert loaded is not None
    assert loaded["outcome"] == {"status": "expired"}
    assert loaded["stage1"]["market_regime"] == "range"  # newest row, not the first


# ---------------------------------------------------------------------------
# Phase D append_followup: append-to-list (append_payload would replace it)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_append_followup_extend_not_replace(repo: AnalysisRecordRepository):
    """B1 守门：第二条追问后是 2 条，且首条仍在。

    这正是 append_payload 做不到的 —— 它是 set 语义(payload[key]=value),
    传列表进去会把历史替换成单元素,用户上一个问题静默消失。
    """
    rec = _sample_record()
    rec["meta"]["input_id"] = "d-extend"
    await repo.save(rec)

    first = await repo.append_followup("d-extend", {"question": "q1", "answer": "a1"})
    assert first is not None
    assert len(first) == 1

    second = await repo.append_followup("d-extend", {"question": "q2", "answer": "a2"})
    assert second is not None
    assert len(second) == 2
    assert [f["question"] for f in second] == ["q1", "q2"]

    loaded = await repo.get_by_input_id("d-extend")
    assert loaded is not None
    assert [f["question"] for f in loaded["followups"]] == ["q1", "q2"]


@pytest.mark.asyncio
async def test_append_followup_missing_input_id_returns_none(
    repo: AnalysisRecordRepository,
):
    assert await repo.append_followup("no-such", {"question": "q"}) is None


@pytest.mark.asyncio
async def test_append_followup_retries_after_lost_optimistic_lock(
    repo: AnalysisRecordRepository,
    monkeypatch: pytest.MonkeyPatch,
):
    """锁输一次 → 重读重写即成功(append_payload 在这里会 fail-closed 丢问题)。

    竞态注入同 test_append_payload_optimistic_lock_conflict:SQLite 的行扫描
    先于 BEFORE UPDATE 触发器,所以只能在 repo 边界插队 —— SELECT 一返回就让
    对手改写 record_json,我方 UPDATE 的谓词立刻变成陈旧值。
    """
    rec = _sample_record()
    rec["meta"]["input_id"] = "d-retry"
    await repo.save(rec)

    real_execute = repo._db.execute
    races = {"n": 0}

    async def racing_execute(sql: str, params: tuple = ()) -> list:
        rows: list = await real_execute(sql, params)
        if "SELECT id, record_json FROM analysis_records" in sql and races["n"] == 0:
            races["n"] += 1
            await real_execute(
                "UPDATE analysis_records SET record_json = record_json || ' '"
                " WHERE input_id = ?",
                ("d-retry",),
            )
        return rows

    monkeypatch.setattr(repo._db, "execute", racing_execute)

    out = await repo.append_followup("d-retry", {"question": "q1", "answer": "a1"})
    assert races["n"] == 1  # 竞态真注入了,第一次必输
    assert out is not None
    assert len(out) == 1

    monkeypatch.undo()
    loaded = await repo.get_by_input_id("d-retry")
    assert loaded is not None
    assert loaded["followups"] == [{"question": "q1", "answer": "a1"}]


@pytest.mark.asyncio
async def test_append_followup_gives_up_after_bounded_retries(
    repo: AnalysisRecordRepository,
    monkeypatch: pytest.MonkeyPatch,
):
    """每次都被抢锁 → 重试有界,耗尽返回 None(不静默假成功、不死循环)。"""
    rec = _sample_record()
    rec["meta"]["input_id"] = "d-lost"
    await repo.save(rec)

    real_execute = repo._db.execute
    selects = {"n": 0}

    async def always_racing(sql: str, params: tuple = ()) -> list:
        rows: list = await real_execute(sql, params)
        if "SELECT id, record_json FROM analysis_records" in sql:
            selects["n"] += 1
            await real_execute(
                "UPDATE analysis_records SET record_json = record_json || ' '"
                " WHERE input_id = ?",
                ("d-lost",),
            )
        return rows

    monkeypatch.setattr(repo._db, "execute", always_racing)

    out = await repo.append_followup("d-lost", {"question": "q"}, attempts=2)
    assert out is None
    assert selects["n"] == 2  # 正好 attempts 次,没有多试

    monkeypatch.undo()
    loaded = await repo.get_by_input_id("d-lost")
    assert loaded is not None
    assert loaded.get("followups") is None  # 失败路径不留半截数据
