"""A6 — 真实链路 integration 测试（显式开启，无 key 时 skip）。

验收标准：带 integration marker；无 LLM key 时 skip（CI 缺 key 构建通过）。

真实链路 = KlineSource(klines.db) → MarketAnalysisFlow(
HttpLLMProvider(真实 LLMConfig) + SqliteRecordPersistence) → record 落盘可回放。
flow 构建方式与 MarketAnalysisService._get_flow() 的延迟路径一致。

开启方式：
  export SELENE_MA_TEST_API_KEY=<key>
  export SELENE_MA_TEST_BASE_URL=<endpoint>   # 可选
  export SELENE_MA_TEST_MODEL=<model>         # 可选
  python -m pytest packages/market-analysis/tests/test_integration_real_pipeline.py -q

CI 无 key → 全部 skip。测试不使用任何参考实现字段名。
"""
from __future__ import annotations

import os

import pytest

from selene_space_llm.base import LLMConfig

from selene_space_market_analysis.data_source import DEFAULT_KLINES_DB, KlineSource
from selene_space_market_analysis.flow import MarketAnalysisFlow
from selene_space_market_analysis.llm_provider import HttpLLMProvider
from selene_space_market_analysis.persistence_sqlite import SqliteRecordPersistence
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.analysis_records import (
    AnalysisRecordRepository,
)
from selene_space_storage.sqlite import AsyncSQLiteManager

pytestmark = pytest.mark.integration

_TEST_KEY_ENV = "SELENE_MA_TEST_API_KEY"
_TEST_BASE_URL_ENV = "SELENE_MA_TEST_BASE_URL"
_TEST_MODEL_ENV = "SELENE_MA_TEST_MODEL"

_PROBE = ("binance", "futures", "BTC/USDT", "1h")


@pytest.mark.asyncio
async def test_a6_real_pipeline_end_to_end() -> None:
    api_key = os.environ.get(_TEST_KEY_ENV)
    if not api_key:
        pytest.skip(f"no {_TEST_KEY_ENV} set — set to run real-LLM integration")
    if not DEFAULT_KLINES_DB.exists():
        pytest.skip("klines.db not present at services/local-api/data")

    input_data = KlineSource().build_input_data(*_PROBE)
    assert input_data["klines"], "probe symbol should yield bars"

    llm_config = LLMConfig(
        model=os.environ.get(_TEST_MODEL_ENV) or "default",
        api_key=api_key,
        base_url=os.environ.get(_TEST_BASE_URL_ENV) or None,
    )
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        flow = MarketAnalysisFlow(
            llm=HttpLLMProvider(llm_config),
            persistence=SqliteRecordPersistence(db),
        )
        record = await flow.analyze(input_data, input_id="integration-a6", session_id="a6")

        rid = record["meta"]["record_id"]
        assert rid.startswith("ar-")
        assert record["stage1"]["market_regime"] in {
            "trend_up",
            "trend_down",
            "range",
            "spike",
        }
        assert record["stage2"]["decision"] in {"limit", "breakout", "market", "no_trade"}
        assert not record.get("exception")

        loaded = await AnalysisRecordRepository(db).get(rid)
        assert loaded is not None
        assert loaded["stage1"]["market_regime"] == record["stage1"]["market_regime"]
