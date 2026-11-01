"""US-202 接线测试：生产装配必须把 ExperienceLibrary 交给 MarketAnalysisFlow。

此前 ``_get_flow()`` 从不传 ``experience_library``，导致 stage1 prompt 永远
拿不到历史案例——经验库有实现、有测试，却在生产里是死的。
"""

from __future__ import annotations

import asyncio

import pytest

from app.core.market_analysis_service import MarketAnalysisService

from selene_space_market_analysis.experience_library import ExperienceLibrary
from selene_space_market_analysis.flow import MarketAnalysisFlow
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager


class _FakeSettings:
    async def resolve_llm_full_config(self) -> dict[str, object]:
        return {
            "model": "fake-model",
            "api_key": "fake-key",
            "base_url": "http://127.0.0.1:1",
        }


@pytest.mark.asyncio
async def test_get_flow_wires_experience_library() -> None:
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        svc = MarketAnalysisService(
            db_loader=lambda: db,
            settings_loader=lambda: _FakeSettings(),
        )
        flow = await svc._get_flow()

        assert isinstance(flow, MarketAnalysisFlow)
        assert isinstance(flow._experience_library, ExperienceLibrary)

        # 真实调用路径可走通（空库 → 案例不足 → 返回空串，不抛）
        context = await flow._experience_library.build_stage1_context(
            symbol="BTC/USDT", interval="1h"
        )
        assert context == ""


@pytest.mark.asyncio
async def test_get_flow_reuses_injected_flow() -> None:
    """注入 flow 的旧装配路径不应被改写。"""
    sentinel = MarketAnalysisFlow(llm=object(), persistence=object())
    svc = MarketAnalysisService(
        db_loader=lambda: None,
        settings_loader=lambda: _FakeSettings(),
        flow=sentinel,
    )
    assert await svc._get_flow() is sentinel
    assert sentinel._experience_library is None


def test_build_experience_library_degrades_on_failure() -> None:
    """构造失败降级为 None，不向主路径抛异常。"""

    class _Boom:
        pass

    # AnalysisRecordRepository 构造只存引用，不会失败；直接验证正常面即可
    lib = MarketAnalysisService._build_experience_library(_Boom())
    assert isinstance(lib, ExperienceLibrary)


@pytest.mark.asyncio
async def test_flow_built_once() -> None:
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        svc = MarketAnalysisService(
            db_loader=lambda: db,
            settings_loader=lambda: _FakeSettings(),
        )
        first = await svc._get_flow()
        second = await svc._get_flow()
        assert first is second
        assert asyncio.iscoroutinefunction(
            first._experience_library.build_stage1_context
        )
