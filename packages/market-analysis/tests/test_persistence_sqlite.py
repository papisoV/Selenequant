"""Tests for SqliteRecordPersistence adapter (US-007)."""

from __future__ import annotations

import inspect
import json

import pytest
import pytest_asyncio

from selene_space_market_analysis.models import Stage1Diagnosis, Stage2Decision
from selene_space_market_analysis.persistence_sqlite import SqliteRecordPersistence
from selene_space_market_analysis.validator import validate_stage1, validate_stage2
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.analysis_records import (
    AnalysisRecordRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager


def _sample_record() -> dict:
    return {
        "meta": {"input_id": "i1", "session_id": "s1", "timestamp": "t1"},
        "input_data": {
            "symbol": "BTC/USDT",
            "interval": "1h",
            "klines": [{"timestamp": 1, "close": 100.0}],
            "bar_features": {"close_last": 100.0},
        },
        "stage1": {"entry_gate": "wait", "market_regime": "range"},
        "stage2": None,
        "dispatch_playbooks": [],
        "usage_total": {"prompt_tokens": 10},
        "raw": {"stage1": "raw1", "stage2": None},
    }


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


@pytest.mark.asyncio
async def test_conforms_to_record_persistence_protocol(db: AsyncSQLiteManager):
    persistence = SqliteRecordPersistence(db)
    # 结构性验证协议契约：存在可 await 的 save -> str
    assert callable(getattr(persistence, "save", None))
    assert inspect.iscoroutinefunction(persistence.save)


@pytest.mark.asyncio
async def test_save_and_replay_roundtrip(db: AsyncSQLiteManager):
    persistence = SqliteRecordPersistence(db)
    record = _sample_record()
    rid = await persistence.save(record)
    assert rid.startswith("ar-")

    # 通过仓库回放，验证适配器落盘可重新加载
    repo = AnalysisRecordRepository(db)
    loaded = await repo.get(rid)
    assert loaded is not None
    assert loaded["meta"]["input_id"] == "i1"
    assert loaded["stage1"]["entry_gate"] == "wait"
    assert loaded["raw"]["stage1"] == "raw1"
    assert loaded["input_data"]["bar_features"] == {"close_last": 100.0}


@pytest.mark.asyncio
async def test_reloaded_record_replays_through_validators(db: AsyncSQLiteManager):
    """存储落盘后重新加载，两阶段 JSON 仍能过市场校验器（回放契约）。"""
    diagnosis_summary = {"regime": "range", "reason": "sideways"}
    record = _sample_record()
    record["stage1"] = {
        "market_regime": "range",
        "entry_gate": "wait",
        "signal_patterns": [],
        "trend_context": {},
        "bar_features": {},
        "evidence": [],
        "diagnosis_summary": diagnosis_summary,
    }
    record["stage2"] = {
        "decision": "no_trade",
        "dispatch_playbooks": [],
        "decision_summary": {
            "short_circuited": True,
            "entry_gate": "wait",
            "diagnosis_summary": diagnosis_summary,
        },
        "evidence": [],
        "next_bar_forecast": None,
    }

    persistence = SqliteRecordPersistence(db)
    rid = await persistence.save(record)

    loaded = await AnalysisRecordRepository(db).get(rid)
    assert loaded is not None
    s1 = Stage1Diagnosis.model_validate(loaded["stage1"])
    assert validate_stage1(s1).is_valid
    s2 = Stage2Decision.model_validate(loaded["stage2"])
    assert validate_stage2(s2, stage1=s1).is_valid


# ---------------------------------------------------------------------------
# US-012 C1 — 经验库检索（list_recent + 复用为增量先验）
# ---------------------------------------------------------------------------

_BAR_FEATURES = {"adx": 28.5, "ema20": 50000.0}

_C1_INPUT = {
    "symbol": "BTC/USDT",
    "interval": "1h",
    "bar_features": _BAR_FEATURES,
    "klines": [{"open": 49000, "high": 51000, "low": 48000, "close": 50000}],
}

_C1_STAGE1_WAIT = {
    "market_regime": "range",
    "entry_gate": "wait",
    "signal_patterns": [],
    "trend_context": {"direction": "flat"},
    "bar_features": _BAR_FEATURES,
    "evidence": [],
    "diagnosis_summary": {"regime": "range", "reason": "无明确趋势"},
}


@pytest.mark.asyncio
async def test_list_recent_returns_full_records(db: AsyncSQLiteManager):
    persistence = SqliteRecordPersistence(db)
    rec = _sample_record()
    rec["prompt"] = {"stage1": [{"role": "user", "content": "u"}]}
    rid = await persistence.save(rec)

    recent = await persistence.list_recent()
    assert len(recent) == 1
    item = recent[0]
    # 完整记录：含 record_id 与 prompt（检索可复用 / 可回放）
    assert item["meta"]["record_id"] == rid
    assert item["prompt"]["stage1"] == [{"role": "user", "content": "u"}]
    assert item["input_data"]["symbol"] == "BTC/USDT"


@pytest.mark.asyncio
async def test_list_recent_symbol_filter(db: AsyncSQLiteManager):
    persistence = SqliteRecordPersistence(db)
    btc = _sample_record()
    eth = _sample_record()
    eth["input_data"] = {"symbol": "ETH/USDT", "interval": "1h"}
    await persistence.save(btc)
    await persistence.save(eth)

    eth_recent = await persistence.list_recent(symbol="ETH/USDT")
    assert len(eth_recent) == 1
    assert eth_recent[0]["input_data"]["symbol"] == "ETH/USDT"


@pytest.mark.asyncio
async def test_list_recent_record_reused_as_incremental_prior(db: AsyncSQLiteManager):
    """C1×C2 集成：经验库检索出的记录可直接作为增量分析 prev_record 复用。"""
    from selene_space_llm.base import LLMConfig

    from selene_space_market_analysis.flow import MarketAnalysisFlow
    from selene_space_market_analysis.llm_provider import HttpLLMProvider

    captured: list[list[dict]] = []

    async def fake(messages):
        captured.append([{"role": m.role, "content": m.content} for m in messages])
        return json.dumps(_C1_STAGE1_WAIT)

    provider = HttpLLMProvider(
        config=LLMConfig(
            model="test-model",
            temperature=0.2,
            max_tokens=256,
            api_key=None,
            base_url=None,
        ),
        provider=fake,
    )
    persistence = SqliteRecordPersistence(db)
    flow = MarketAnalysisFlow(llm=provider, persistence=persistence)

    first = await flow.analyze(_C1_INPUT, input_id="i1", session_id="s1")
    rid = first["meta"]["record_id"]

    # 经验库检索取回完整记录
    recent = await persistence.list_recent(symbol="BTC/USDT")
    assert len(recent) == 1
    assert recent[0]["meta"]["record_id"] == rid

    # 复用检索记录作为增量先验 → prior 注入 + prev_record_id 关联
    second = await flow.analyze_incremental(
        _C1_INPUT,
        prev_record=recent[0],
        input_id="i1",
        session_id="s1",
    )
    assert second["meta"]["prev_record_id"] == rid
    prompt = captured[1][-1]["content"]
    assert "【上次结论（复用，仅参考）】" in prompt
    assert len(await persistence.list_recent()) == 2
