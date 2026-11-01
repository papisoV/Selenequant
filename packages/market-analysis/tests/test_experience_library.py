"""US-202 — 经验库检索与案例注入单测（ExperienceLibrary）。

覆盖验收标准 A-202：
- 按 symbol+interval+market_regime 命中历史可参考案例（Python 侧过滤
  ``list_records`` 反序列化结果）
- 成功/失败分类：entry_gate=proceed + decision≠no_trade 可参考；
  顶层 exception 为失败（排除）
- list_failures 嵌套 record 结构取值（item["record"]["input_data"]）
- 可参考案例不足 3 条 → 退化不注入；配额 ≤3 条；token 硬顶 ≤2400
"""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_market_analysis.experience_library import (
    ExperienceLibrary,
    _estimate_tokens,
)
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.analysis_records import (
    AnalysisRecordRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

_BAR_FEATURES = {"adx": 28.5, "ema20": 50000.0}

_DIAGNOSIS = {"regime": "range", "reason": "价格在区间内震荡"}


def _reference_record(
    symbol: str = "BTC/USDT",
    interval: str = "1h",
    regime: str = "range",
    *,
    decision: str = "limit",
) -> dict:
    return {
        "meta": {"input_id": "i1", "session_id": "s1", "timestamp": "t1"},
        "input_data": {
            "symbol": symbol,
            "interval": interval,
            "klines": [{"timestamp": 1, "close": 100.0}],
            "bar_features": _BAR_FEATURES,
        },
        "stage1": {
            "market_regime": regime,
            "entry_gate": "proceed",
            "signal_patterns": [
                {
                    "name": "bull_flag",
                    "bias": "bullish",
                    "confidence": 0.75,
                    "kline_range": "K1-K10",
                }
            ],
            "trend_context": {"direction": "up"},
            "bar_features": _BAR_FEATURES,
            "evidence": [
                {
                    "kind": "pattern",
                    "key": "breakout",
                    "statement": "回踩确认后放量",
                    "kline_range": "K1-K10",
                }
            ],
            "diagnosis_summary": _DIAGNOSIS,
        },
        "stage2": {
            "decision": decision,
            "dispatch_playbooks": [],
            "decision_summary": {
                "diagnosis_summary": _DIAGNOSIS,
                "reason": "顺势回调进场",
            },
            "evidence": [
                {
                    "kind": "decision",
                    "key": "entry",
                    "statement": "回踩支撑位轻仓",
                    "kline_range": "K1-K10",
                }
            ],
            "next_bar_forecast": None,
        },
        "dispatch_playbooks": [],
        "usage_total": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _failure_record(**kw) -> dict:
    rec = _reference_record(**kw)
    rec["exception"] = {"type": "SemanticError", "message": "statement 是模板化理由"}
    return rec


@pytest_asyncio.fixture
async def db():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as manager:
        yield manager


@pytest.mark.asyncio
async def test_list_reference_cases_filters_symbol_interval_regime(db) -> None:
    repo = AnalysisRecordRepository(db)
    await repo.save(_reference_record(symbol="BTC/USDT", interval="1h", regime="range"))
    await repo.save(
        _reference_record(symbol="BTC/USDT", interval="1h", regime="trend_up")
    )
    await repo.save(_reference_record(symbol="BTC/USDT", interval="4h", regime="range"))
    await repo.save(_reference_record(symbol="ETH/USDT", interval="1h", regime="range"))

    lib = ExperienceLibrary(repo)
    cases = await lib.list_reference_cases(
        symbol="BTC/USDT", interval="1h", market_regime="range"
    )
    assert len(cases) == 1
    assert cases[0]["stage1"]["market_regime"] == "range"
    assert cases[0]["input_data"]["interval"] == "1h"


@pytest.mark.asyncio
async def test_list_reference_cases_excludes_failure_and_no_trade(db) -> None:
    repo = AnalysisRecordRepository(db)
    await repo.save(_reference_record())  # 可参考
    await repo.save(_failure_record())  # 顶层 exception → 排除
    await repo.save(_reference_record(decision="no_trade"))  # no_trade → 排除

    lib = ExperienceLibrary(repo)
    cases = await lib.list_reference_cases(
        symbol="BTC/USDT", interval="1h", market_regime=None
    )
    assert len(cases) == 1
    assert cases[0]["stage2"]["decision"] == "limit"


@pytest.mark.asyncio
async def test_list_failure_inputs_extracts_nested_input_data(db) -> None:
    repo = AnalysisRecordRepository(db)
    await repo.save(_failure_record())
    await repo.save(_reference_record())

    lib = ExperienceLibrary(repo)
    failures = await lib.list_failure_inputs()
    assert len(failures) == 1
    assert failures[0]["symbol"] == "BTC/USDT"
    assert failures[0]["interval"] == "1h"
    assert "bar_features" in failures[0]


@pytest.mark.asyncio
async def test_build_context_degrades_when_under_three_cases(db) -> None:
    repo = AnalysisRecordRepository(db)
    await repo.save(_reference_record())  # 仅 1 条可参考

    lib = ExperienceLibrary(repo)
    context = await lib.build_stage1_context(
        symbol="BTC/USDT", interval="1h", market_regime=None
    )
    assert context == ""


@pytest.mark.asyncio
async def test_build_context_injects_when_at_least_three_cases(db) -> None:
    repo = AnalysisRecordRepository(db)
    for _ in range(3):
        await repo.save(_reference_record())
    await repo.save(_reference_record(interval="4h"))  # 不同 interval 不命中

    lib = ExperienceLibrary(repo)
    context = await lib.build_stage1_context(
        symbol="BTC/USDT", interval="1h", market_regime="range"
    )
    assert "【历史案例 1】" in context
    assert "【历史案例 3】" in context
    assert "【历史案例 4】" not in context  # 配额 ≤3
    assert _estimate_tokens(context) <= 2400


@pytest.mark.asyncio
async def test_build_context_enforces_token_cap(db) -> None:
    repo = AnalysisRecordRepository(db)
    big_reason = "这是一个用于撑大注入体积的长文本。" * 300  # 大量 CJK
    for _ in range(3):
        rec = _reference_record()
        rec["stage1"]["diagnosis_summary"] = {
            "regime": "range",
            "reason": big_reason,
        }
        rec["stage2"]["decision_summary"] = {
            "diagnosis_summary": {"regime": "range", "reason": big_reason},
            "reason": big_reason,
        }
        await repo.save(rec)

    lib = ExperienceLibrary(repo)
    context = await lib.build_stage1_context(
        symbol="BTC/USDT", interval="1h", market_regime=None
    )
    assert _estimate_tokens(context) <= 2400
    assert context != ""  # 仍有注入（预算内保留了靠前块）


# ---------------------------------------------------------------------------
# T3a — outcome 键透出 + win 优先排序 (W2)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_excerpt_exposes_outcome_block(db) -> None:
    """m2 验收:已判定记录的案例摘要在 excerpt 层携带紧凑 outcome 三元组。"""
    repo = AnalysisRecordRepository(db)
    rec = _reference_record()
    rec["outcome"] = {
        "status": "win",
        "pnl_r": 2.0,
        "entry_triggered": True,
        "exit_ts": 1234567890123.0,
        "source": "heartbeat_review",
        "closed_at": "2026-09-12T00:00:00+08:00",
    }
    await repo.save(rec)

    lib = ExperienceLibrary(repo)
    cases = await lib.list_reference_cases(
        symbol="BTC/USDT", interval="1h", market_regime=None
    )
    assert len(cases) == 1
    # m2:断言钉在 excerpt["outcome"]["status"] 层,不写死 prompt 字面串。
    from selene_space_market_analysis.experience_library import _excerpt_record

    excerpt = _excerpt_record(cases[0])
    assert excerpt["outcome"]["status"] == "win"
    assert excerpt["outcome"]["pnl_r"] == 2.0
    assert excerpt["outcome"]["entry_triggered"] is True
    # 紧凑三元组:exit_ts/source/closed_at 等审计字段不进 prompt
    assert set(excerpt["outcome"]) == {"status", "pnl_r", "entry_triggered"}


@pytest.mark.asyncio
async def test_win_outcome_cases_rank_first(db) -> None:
    """win 判例优先于未判定判例,同分内保持时戳新的在前。"""
    import asyncio

    repo = AnalysisRecordRepository(db)
    # 先存 2 条未判定(时戳靠前,会被 win 顶下去)
    for _ in range(2):
        await repo.save(_reference_record())
        await asyncio.sleep(1.1)  # created_at 是秒粒度,同秒排序不定
    # 再存 1 条已判定 win
    winner = _reference_record()
    winner["outcome"] = {"status": "win", "pnl_r": 1.5, "entry_triggered": True}
    await repo.save(winner)

    lib = ExperienceLibrary(repo)
    cases = await lib.list_reference_cases(
        symbol="BTC/USDT", interval="1h", market_regime=None, limit=2
    )
    assert len(cases) == 2
    # win 判例排第一,即便它是最新写入的
    assert cases[0].get("outcome", {}).get("status") == "win"
    assert "outcome" not in cases[1]
