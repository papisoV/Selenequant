"""``StockProfileService`` —— 部分数据契约、块隔离、证据质量汇总(📋6)。

纯粹测聚合层:六个 provider 全部注入,不碰任何真实源。装配层(把真实源
绑到块上)的验证在 ``test_api_stock_profile.py``。
"""

from __future__ import annotations

import pytest
from app.services.stock_profile_service import (
    BLOCK_NAMES,
    InvalidStockProfileSymbol,
    StockProfileService,
)

pytestmark = pytest.mark.asyncio


def _fresh(value):
    """A provider that always succeeds with *value*."""

    def provider(symbol):
        return value

    return provider


async def test_blank_symbol_is_rejected():
    service = StockProfileService()
    with pytest.raises(InvalidStockProfileSymbol):
        await service.get_profile("   ")


async def test_symbol_is_uppercased_and_stripped():
    service = StockProfileService()
    profile = await service.get_profile("  aapl ")
    assert profile["symbol"] == "AAPL"
    assert profile["requested_symbol"] == "aapl"


async def test_profile_always_has_all_six_blocks():
    service = StockProfileService()
    profile = await service.get_profile("AAPL")
    for name in BLOCK_NAMES:
        assert name in profile
        assert profile[name]["status"] in {"fresh", "partial", "unavailable"}


async def test_missing_providers_yield_unavailable_with_reason():
    service = StockProfileService()
    profile = await service.get_profile("AAPL")
    assert profile["quote"]["status"] == "unavailable"
    assert profile["quote"]["limitations"] == ["quote_source_not_configured"]
    assert profile["evidence_quality"]["status"] == "unavailable"


async def test_fresh_provider_wraps_bare_payload():
    service = StockProfileService(quote_provider=_fresh({"last": 1.5}))
    profile = await service.get_profile("AAPL")
    assert profile["quote"] == {
        "status": "fresh",
        "data": {"last": 1.5},
        "limitations": [],
    }


async def test_partial_provider_status_is_preserved():
    def provider(symbol):
        return {
            "status": "partial",
            "data": {"held": True},
            "limitations": ["paper_accounts_only"],
        }

    service = StockProfileService(portfolio_provider=provider)
    profile = await service.get_profile("AAPL")
    assert profile["portfolio"]["status"] == "partial"
    assert profile["portfolio"]["limitations"] == ["paper_accounts_only"]


async def test_provider_returning_none_is_unavailable_not_error():
    service = StockProfileService(quote_provider=_fresh(None))
    profile = await service.get_profile("AAPL")
    assert profile["quote"]["status"] == "unavailable"
    assert profile["quote"]["limitations"] == ["quote_unavailable"]


async def test_provider_exception_is_contained_to_its_block():
    def boom(symbol):
        raise RuntimeError("upstream down")

    service = StockProfileService(
        quote_provider=boom,
        history_provider=_fresh({"count": 3}),
    )
    profile = await service.get_profile("AAPL")
    assert profile["quote"]["status"] == "unavailable"
    assert profile["quote"]["limitations"] == ["quote_source_error"]
    # the sibling block is untouched by the failure
    assert profile["history"]["status"] == "fresh"


async def test_async_provider_is_awaited():
    async def provider(symbol):
        return {"ok": True}

    service = StockProfileService(quote_provider=provider)
    profile = await service.get_profile("AAPL")
    assert profile["quote"]["status"] == "fresh"
    assert profile["quote"]["data"] == {"ok": True}


async def test_history_days_is_forwarded_only_when_accepted():
    seen: dict[str, int] = {}

    def with_days(symbol, *, history_days):
        seen["days"] = history_days
        return {"count": history_days}

    service = StockProfileService(history_provider=with_days)
    await service.get_profile("AAPL", history_days=42)
    assert seen["days"] == 42


async def test_provider_without_history_days_param_is_called_cleanly():
    def simple(symbol):
        return {"ok": True}

    service = StockProfileService(history_provider=simple)
    profile = await service.get_profile("AAPL", history_days=42)
    assert profile["history"]["status"] == "fresh"


@pytest.mark.parametrize(
    ("statuses", "expected"),
    [
        (["fresh"] * 6, "fresh"),
        (["unavailable"] * 6, "unavailable"),
        (["fresh", "unavailable", "fresh", "fresh", "fresh", "fresh"], "partial"),
        (["partial"] * 6, "partial"),
    ],
)
async def test_evidence_quality_rollup(statuses, expected):
    blocks = {}
    for name, status in zip(BLOCK_NAMES, statuses):
        blocks[name] = {"status": status, "limitations": []}
    assert StockProfileService.evidence_quality(blocks)["status"] == expected


async def test_evidence_quality_dedupes_limitations_in_block_order():
    blocks = {
        name: {"status": "partial", "limitations": ["same", f"{name}_why"]}
        for name in BLOCK_NAMES
    }
    quality = StockProfileService.evidence_quality(blocks)
    assert quality["limitations"][0] == "same"
    assert quality["limitations"].count("same") == 1
    assert len(quality["limitations"]) == 1 + len(BLOCK_NAMES)


async def test_evidence_quality_reports_per_block_statuses():
    service = StockProfileService(quote_provider=_fresh({"last": 1}))
    profile = await service.get_profile("AAPL")
    assert profile["evidence_quality"]["blocks"] == {
        "quote": "fresh",
        "history": "unavailable",
        "research": "unavailable",
        "intelligence": "unavailable",
        "portfolio": "unavailable",
        "monitors": "unavailable",
    }
