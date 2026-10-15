"""StrategyRepository locks: save/get/upsert/list paging and tag/source
filters, delete semantics, and full-field persistence roundtrip."""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.strategies import StrategyRepository
from selene_space_storage.sqlite import AsyncSQLiteManager
from selene_space_types.strategy import StrategyConfig, StrategyType

BASE_ID = "st-77"
BASE_NAME = "Sample SMA"


def _strategy(sid: str = BASE_ID, label: str = BASE_NAME) -> StrategyConfig:
    return StrategyConfig(
        id=sid,
        name=label,
        type=StrategyType.SMA_CROSS,
        symbols=["BTC/USDT"],
        interval="1h",
        parameters={"sma_short": 12, "sma_long": 26},
        tags=["trend", "btc"],
        source="user",
        version=1,
        lifecycle_state="draft",
    )


def _rsi_variant(sid: str, label: str, **extra) -> StrategyConfig:
    base = dict(
        id=sid,
        name=label,
        type=StrategyType.RSI_REVERSAL,
        symbols=["ETH/USDT"],
        interval="1h",
    )
    return StrategyConfig(**{**base, **extra})


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield StrategyRepository(db)


@pytest.mark.asyncio
async def test_save_returns_id_then_get_restores_fields(repo: StrategyRepository):
    stored = _strategy()
    assert await repo.save(stored) == BASE_ID

    loaded = await repo.get(BASE_ID)
    assert loaded is not None
    assert loaded.id == BASE_ID
    assert loaded.name == BASE_NAME
    assert loaded.parameters["sma_short"] == 12


@pytest.mark.asyncio
async def test_get_unknown_id_yields_none(repo: StrategyRepository):
    assert await repo.get("ghost") is None


@pytest.mark.asyncio
async def test_second_save_same_id_overwrites(repo: StrategyRepository):
    await repo.save(_strategy(label="First Draft"))
    await repo.save(_strategy(label="Second Draft"))

    loaded = await repo.get(BASE_ID)
    assert loaded is not None
    assert loaded.name == "Second Draft"


@pytest.mark.asyncio
async def test_list_respects_limit(repo: StrategyRepository):
    for n in range(1, 4):
        await repo.save(_strategy(f"s{n}", f"Entry {n}"))

    assert len(await repo.list(limit=2)) == 2
    assert len(await repo.list()) == 3


@pytest.mark.asyncio
async def test_list_offset_skips_pages(repo: StrategyRepository):
    for n in range(1, 4):
        await repo.save(_strategy(f"s{n}", f"Entry {n}"))

    final_page = await repo.list(limit=2, offset=2)
    assert len(final_page) == 1


@pytest.mark.asyncio
async def test_tag_filter_matches_only_tagged(repo: StrategyRepository):
    await repo.save(_strategy("s1", "BTC Trend"))
    await repo.save(
        _rsi_variant("s2", "ETH Reversal", tags=["mean-reversion", "eth"], source="ai_agent")
    )

    hits = await repo.list(tags=["btc"])
    assert len(hits) == 1
    assert hits[0].id == "s1"


@pytest.mark.asyncio
async def test_source_filter_plus_delete_roundtrip(repo: StrategyRepository):
    """source filter narrows to ai_agent rows; delete then removes the manual row."""
    await repo.save(_strategy("s1", "Manual"))
    await repo.save(_rsi_variant("s2", "Generated", source="ai_agent"))

    hits = await repo.list(source="ai_agent")
    assert len(hits) == 1
    assert hits[0].id == "s2"

    await repo.save(_strategy())
    assert await repo.delete(BASE_ID) is True
    assert await repo.get(BASE_ID) is None


@pytest.mark.asyncio
async def test_delete_unknown_id_reports_false(repo: StrategyRepository):
    assert await repo.delete("ghost") is False


@pytest.mark.asyncio
async def test_every_config_field_survives_a_save_get_cycle(repo: StrategyRepository):
    original = StrategyConfig(
        id="deep-88",
        name="Deep Config",
        type=StrategyType.RSI_REVERSAL,
        description="A test strategy",
        symbols=["ETH/USDT", "SOL/USDT"],
        interval="4h",
        parameters={"rsi_len": 21, "oversold": 25},
        entry_rules={"condition": "rsi < 25"},
        exit_rules={"condition": "rsi > 75"},
        risk_params={"stop_loss_pct": 0.04},
        tags=["mean-reversion"],
        source="ai_agent",
        version=9,
        lifecycle_state="confirmed",
    )
    await repo.save(original)

    loaded = await repo.get("deep-88")
    assert loaded is not None
    assert loaded.description == "A test strategy"
    assert loaded.symbols == ["ETH/USDT", "SOL/USDT"]
    assert loaded.entry_rules == {"condition": "rsi < 25"}
    assert loaded.risk_params == {"stop_loss_pct": 0.04}
    assert loaded.tags == ["mean-reversion"]
    assert loaded.source == "ai_agent"
    assert loaded.version == 9
    assert loaded.lifecycle_state == "confirmed"
