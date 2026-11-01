"""BacktestRepository locks: FK-seeded saves, metric/date/created_at/curve
roundtrips, list paging by strategy, and recompute of missing derived curves."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.backtests import BacktestRepository
from selene_space_storage.repositories.strategies import StrategyRepository
from selene_space_storage.sqlite import AsyncSQLiteManager
from selene_space_types.strategy import (
    BacktestMetrics,
    BacktestResult,
    StrategyConfig,
    StrategyType,
)

SAMPLE_METRICS = BacktestMetrics(
    total_return=0.12,
    annual_return=0.38,
    sharpe_ratio=1.4,
    max_drawdown=-0.06,
    win_rate=0.52,
    profit_factor=1.35,
    total_trades=57,
)

WINDOW = dict(
    start_date=datetime(2024, 7, 1, tzinfo=UTC),
    end_date=datetime(2024, 9, 30, tzinfo=UTC),
)

PARENT_ID = "strat-parent"


def _result(rid: str, strategy_id: str = PARENT_ID) -> BacktestResult:
    return BacktestResult(
        id=rid,
        strategy_id=strategy_id,
        strategy_version=5,
        symbol="SOL/USDT",
        interval="15m",
        metrics=SAMPLE_METRICS,
        equity_curve=[5000.0, 5080.0, 5122.0, 5310.0],
        trades_count=57,
        created_at=1720000000000,
        **WINDOW,
    )


@pytest_asyncio.fixture
async def repos():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        strat_repo = StrategyRepository(db)

        # backtests.strategy_id carries a FK into strategies
        await strat_repo.save(
            StrategyConfig(
                id=PARENT_ID,
                name="Fixture Parent",
                type=StrategyType.SMA_CROSS,
                symbols=["SOL/USDT"],
                interval="15m",
            )
        )
        yield strat_repo, BacktestRepository(db)


@pytest.mark.asyncio
async def test_save_then_get_restores_core_fields(repos):
    _, bt = repos
    original = _result("bt-A")
    assert await bt.save(original) == "bt-A"

    loaded = await bt.get("bt-A")
    assert loaded is not None
    assert loaded.strategy_id == PARENT_ID
    assert loaded.metrics.sharpe_ratio == 1.4
    assert loaded.strategy_version == 5
    assert loaded.symbol == "SOL/USDT"
    assert loaded.interval == "15m"
    assert loaded.equity_curve == [5000.0, 5080.0, 5122.0, 5310.0]
    assert loaded.trades_count == 57
    assert loaded.created_at == original.created_at


@pytest.mark.asyncio
async def test_get_unknown_run_yields_none(repos):
    _, bt = repos
    assert await bt.get("ghost") is None


@pytest.mark.asyncio
async def test_listing_paths_count_rows(repos):
    _, bt = repos
    await bt.save(_result("r1"))
    await bt.save(_result("r2"))
    assert len(await bt.list_all(limit=10, offset=0)) == 2

    # second scenario: same ids are upserted, adding only one net-new row
    _, bt = repos
    for rid in ("r1", "r2", "n3"):
        await bt.save(_result(rid))
    assert len(await bt.list_by_strategy(PARENT_ID)) == 3


@pytest.mark.asyncio
async def test_list_by_strategy_limit(repos):
    _, bt = repos
    for rid in ("n1", "n2", "n3"):
        await bt.save(_result(rid))
    assert len(await bt.list_by_strategy(PARENT_ID, limit=2)) == 2


@pytest.mark.asyncio
async def test_list_by_unknown_strategy_is_empty(repos):
    _, bt = repos
    assert await bt.list_by_strategy("ghost") == []


@pytest.mark.asyncio
async def test_metrics_survive_persistence(repos):
    _, bt = repos
    await bt.save(_result("bt-A"))

    loaded = await bt.get("bt-A")
    assert loaded is not None
    assert loaded.metrics.total_return == 0.12
    assert loaded.metrics.max_drawdown == -0.06
    assert loaded.metrics.win_rate == 0.52
    assert loaded.metrics.profit_factor == 1.35


@pytest.mark.asyncio
async def test_date_bounds_survive_persistence(repos):
    _, bt = repos
    await bt.save(_result("bt-A"))

    loaded = await bt.get("bt-A")
    assert loaded is not None
    assert loaded.start_date.year == 2024
    assert loaded.start_date.month == 7
    assert loaded.end_date.month == 9


@pytest.mark.asyncio
async def test_created_at_epoch_roundtrips_exactly(repos):
    _, bt = repos
    original = _result("bt-epoch")
    await bt.save(original)

    loaded = await bt.get("bt-epoch")
    assert loaded is not None
    assert loaded.created_at == original.created_at


@pytest.mark.asyncio
async def test_buy_hold_drawdown_and_trades_roundtrip(repos):
    """buy_hold_curve, drawdown_curve, and trades should persist and reload."""
    _, bt = repos
    await bt.save(
        BacktestResult(
            id="bt-shape",
            strategy_id=PARENT_ID,
            strategy_version=3,
            metrics=SAMPLE_METRICS,
            equity_curve=[8000.0, 8160.0, 8090.0, 8240.0],
            buy_hold_curve=[8000.0, 7920.0, 8110.0, 8180.0],
            drawdown_curve=[0.0, -0.02, -0.031, -0.009],
            trades=[
                {
                    "side": "short",
                    "entry_price": 212.4,
                    "exit_price": 205.1,
                    "pnl": 73.0,
                }
            ],
            trades_count=1,
            created_at=1720000000000,
            **WINDOW,
        )
    )

    loaded = await bt.get("bt-shape")
    assert loaded is not None
    assert loaded.equity_curve == [8000.0, 8160.0, 8090.0, 8240.0]
    assert loaded.buy_hold_curve == [8000.0, 7920.0, 8110.0, 8180.0]
    assert loaded.drawdown_curve == [0.0, -0.02, -0.031, -0.009]
    assert len(loaded.trades) == 1
    assert loaded.trades[0]["pnl"] == 73.0


@pytest.mark.asyncio
async def test_symbol_and_interval_roundtrip(repos):
    """symbol and interval should persist and reload correctly."""
    _, bt = repos
    await bt.save(
        BacktestResult(
            id="bt-market",
            strategy_id=PARENT_ID,
            strategy_version=1,
            symbol="BNB/USDT",
            interval="2h",
            metrics=SAMPLE_METRICS,
            equity_curve=[9000.0, 9060.0],
            trades_count=0,
            created_at=1720000000000,
            **WINDOW,
        )
    )

    loaded = await bt.get("bt-market")
    assert loaded is not None
    assert loaded.symbol == "BNB/USDT"
    assert loaded.interval == "2h"


@pytest.mark.asyncio
async def test_missing_derived_curves_recomputed_from_equity(repos):
    """When buy_hold_curve and drawdown_curve are empty, they should be recomputed from equity_curve."""
    _, bt = repos
    await bt.save(
        BacktestResult(
            id="bt-infer",
            strategy_id=PARENT_ID,
            strategy_version=1,
            metrics=SAMPLE_METRICS,
            equity_curve=[6000.0, 6180.0, 6300.0, 6110.0],
            trades_count=0,
            created_at=1720000000000,
            **WINDOW,
        )
    )

    loaded = await bt.get("bt-infer")
    assert loaded is not None
    assert len(loaded.drawdown_curve) == 4
    assert loaded.drawdown_curve[0] == 0.0
    assert loaded.drawdown_curve[3] < 0
    assert len(loaded.buy_hold_curve) == 4


@pytest.mark.asyncio
async def test_save_virtual_strategy_id_backfills_stub_host(repos):
    """bridge:/qd:/alpha: ids have no strategies row — save must backfill a
    stub host instead of dying on the FK (S0b regression lock)."""
    _, bt = repos
    virtual = _result("bt-bridge", strategy_id="bridge:ARBUSDT_autogen_factor")
    assert await bt.save(virtual) == "bt-bridge"

    loaded = await bt.get("bt-bridge")
    assert loaded is not None
    assert loaded.strategy_id == "bridge:ARBUSDT_autogen_factor"

    # stub row visible in strategies with a StrategyConfig-valid payload,
    # and re-save (upsert) of another virtual id still works
    strat_rows = await bt._db.execute(
        "SELECT id, name, type, config_json FROM strategies WHERE id = ?",
        ("bridge:ARBUSDT_autogen_factor",),
    )
    assert strat_rows and strat_rows[0]["type"] == "custom"
    stub = StrategyConfig.model_validate(json.loads(strat_rows[0]["config_json"]))
    assert stub.id == "bridge:ARBUSDT_autogen_factor"
    again = _result("bt-bridge-2", strategy_id="qd:src48")
    await bt.save(again)
    assert await bt.get("bt-bridge-2") is not None


@pytest.mark.asyncio
async def test_legacy_iso_created_at_row_still_loads(repos):
    """Legacy rows wrote created_at as an ISO string while Timestamp is int.

    pydantic rejects the string, so before coercion `get()` returned None and
    the row vanished from every listing (S0c regression lock).
    """
    _, bt = repos
    await bt.save(_result("bt-iso"))
    await bt._db.execute(
        "UPDATE backtests SET created_at = ? WHERE id = ?",
        ("2026-05-19T17:11:03.417000+00:00", "bt-iso"),
    )

    loaded = await bt.get("bt-iso")
    assert loaded is not None
    assert loaded.created_at == int(
        datetime.fromisoformat("2026-05-19T17:11:03.417000+00:00").timestamp() * 1000
    )


@pytest.mark.asyncio
async def test_poisoned_row_does_not_abort_listing(repos):
    """One unloadable row must not blank the whole list.

    The list path loads every row; an unguarded comprehension let a single
    corrupt row (here: a non-parseable created_at) raise and drop all healthy
    rows with it.
    """
    _, bt = repos
    await bt.save(_result("bt-good-1"))
    await bt.save(_result("bt-good-2"))
    await bt.save(_result("bt-poison"))
    await bt._db.execute(
        "UPDATE backtests SET created_at = ? WHERE id = ?",
        ("not-a-date", "bt-poison"),
    )

    rows = await bt.list_all(limit=10, offset=0)
    ids = {r.id for r in rows}
    assert {"bt-good-1", "bt-good-2"} <= ids
    assert "bt-poison" not in ids


@pytest.mark.asyncio
async def test_list_summaries_matches_full_listing_ids(repos):
    """轻量列举与完整列举必须给出同一批 id —— 列表用它替代逐行 get()。"""
    _, bt = repos
    await bt.save(_result("bt-s1"))
    await bt.save(_result("bt-s2"))

    full_ids = {r.id for r in await bt.list_all(limit=10, offset=0)}
    light_ids = {s["id"] for s in await bt.list_summaries(limit=10, offset=0)}
    assert full_ids == light_ids == {"bt-s1", "bt-s2"}


@pytest.mark.asyncio
async def test_list_summaries_carries_metrics_not_curves(repos):
    """轻量列举带指标(前端表格要),但不解码曲线/成交(那是详情的事)。"""
    _, bt = repos
    await bt.save(_result("bt-s3"))

    (summary,) = await bt.list_summaries(limit=10, offset=0)
    assert summary["id"] == "bt-s3"
    assert summary["symbol"] == "SOL/USDT"
    assert summary["interval"] == "15m"
    assert summary["metrics"]["sharpe_ratio"] == SAMPLE_METRICS.sharpe_ratio
    assert summary["trades_count"] == 57
    # 轻量 dict 里没有曲线键
    for heavy in ("equity_curve", "trades", "drawdown_curve", "buy_hold_curve"):
        assert heavy not in summary


@pytest.mark.asyncio
async def test_list_summaries_skips_poisoned_row(repos):
    """单条坏行不得拖垮轻量列举(与 list_all 同等容错)。"""
    _, bt = repos
    await bt.save(_result("bt-l-good"))
    await bt.save(_result("bt-l-poison"))
    await bt._db.execute(
        "UPDATE backtests SET metrics_json = ? WHERE id = ?",
        ("not-json", "bt-l-poison"),
    )

    ids = {s["id"] for s in await bt.list_summaries(limit=10, offset=0)}
    assert "bt-l-good" in ids
    assert "bt-l-poison" not in ids


@pytest.mark.asyncio
async def test_robustness_roundtrip(repos):
    """Resampling payload survives save→get; absent payload reads back as {}."""
    _, bt = repos
    payload = {
        "permutation": {"p_value_sharpe": 0.031, "n_trades": 200},
        "bootstrap": {"ci_lower": 0.4, "ci_upper": 1.9},
        "seed": 42,
    }
    original = _result("bt-rob").model_copy(update={"robustness": payload})
    await bt.save(original)

    loaded = await bt.get("bt-rob")
    assert loaded is not None
    assert loaded.robustness == payload

    # A result with no robustness block must round-trip as an empty dict, not
    # None — callers branch on truthiness, and None would crash them.
    await bt.save(_result("bt-rob-empty"))
    empty = await bt.get("bt-rob-empty")
    assert empty is not None
    assert empty.robustness == {}
