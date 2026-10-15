"""Replay endpoint tests (Time Machine v0).

Canon rules under test:
- State(T) carries only T-pre information (Outcome Reveal semantics):
  T-after trades withheld until ?reveal=true.
- Per-field provenance labels: RECORDED for recorded state, NOT_AVAILABLE
  for memory/events (no retrieval wired in v0), WITHHELD for outcome.
- Backfill derivation: ?at defaults to the last closed trade's exit_time.
- Missing backtest row degrades signal state to NOT_AVAILABLE, not 500.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from app.core.dependencies import set_db_manager, set_episode_repo
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.backtests import BacktestRepository
from selene_space_storage.repositories.episodes import EpisodeRepository
from selene_space_storage.sqlite import AsyncSQLiteManager
from selene_space_types.strategy import BacktestMetrics, BacktestResult

_H1 = 3_600_000


def _trade(entry_ms: int, pnl: float, side: str = "buy") -> dict:
    return {
        "symbol": "FAKE/USDT",
        "side": "long",
        "entry_action": side,
        "exit_action": "sell" if side == "buy" else "buy",
        "entry_price": 1.0,
        "exit_price": 1.1,
        "quantity": 100.0,
        "pnl": pnl,
        "entry_time": entry_ms,
        "exit_time": entry_ms + 2 * _H1,
    }


@pytest_asyncio.fixture
async def db_manager():
    db = AsyncSQLiteManager(
        ":memory:", migration_runner=MigrationRunner(ALL_MIGRATIONS)
    )
    await db.connect()
    set_db_manager(db)
    set_episode_repo(EpisodeRepository(db))
    yield db
    set_episode_repo(None)
    set_db_manager(None)
    await db.close()


async def _seed(
    db,
    *,
    backtest_id: str | None = "bt-fake1",
    trades: list[dict] | None = None,
    params: dict | None = None,
) -> str:
    if backtest_id is not None and trades is not None:
        # backtests.strategy_id has an FK to strategies.id — seed the parent.
        await db.execute(
            """
            INSERT OR IGNORE INTO strategies (id, name, type, created_at, updated_at)
            VALUES ('bridge:FAKEUSDT_autogen_factor', 'FAKE', 'bridge', 0, 0)
            """
        )
        result = BacktestResult(
            id=backtest_id,
            strategy_id="bridge:FAKEUSDT_autogen_factor",
            strategy_version=1,
            symbol="FAKE/USDT",
            interval="1h",
            metrics=BacktestMetrics(
                total_return=0.1,
                annual_return=0.3,
                sharpe_ratio=1.2,
                max_drawdown=-0.05,
                win_rate=0.5,
                profit_factor=1.4,
                total_trades=len(trades),
            ),
            equity_curve=[10000.0, 10100.0],
            trades=trades,
            trades_count=len(trades),
            created_at=1711000000000,
            start_date=datetime(2025, 1, 1, tzinfo=UTC),
            end_date=datetime(2025, 3, 31, tzinfo=UTC),
        )
        await BacktestRepository(db).save(result)
    return await EpisodeRepository(db).create(
        strategy_kind="bridge",
        strategy_id="bridge:FAKEUSDT_autogen_factor",
        symbol="FAKE/USDT",
        interval="1h",
        exchange="binance",
        market_type="futures",
        params=params,
        backtest_id=backtest_id,
        metrics={"sharpe_ratio": 1.2},
    )


@asynccontextmanager
async def api():
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as c:
        yield c


@pytest.mark.asyncio
async def test_outcome_withheld_until_reveal(db_manager):
    """T-after trades exist but stay WITHHELD without ?reveal=true."""
    t0 = 1_711_000_000_000
    trades = [_trade(t0, 10.0), _trade(t0 + 10 * _H1, -5.0), _trade(t0 + 20 * _H1, 7.0)]
    eid = await _seed(db_manager, trades=trades)

    at_mid = t0 + 5 * _H1  # after trade 1 closes, before trade 2 does
    async with api() as c:
        resp = await c.get(f"/api/v1/episodes/{eid}/replay", params={"at": at_mid})

    assert resp.status_code == 200
    body = resp.json()
    assert body["revealed"] is False
    assert body["at_source"] == "REQUESTED"
    assert body["outcome"]["provenance"] == "WITHHELD"
    assert body["outcome"]["trades_after_t"] == []
    assert body["outcome"]["withheld_count"] == 2

    # Outcome metrics absent pre-reveal (they cover the full window)
    assert "metrics" not in body["outcome"]

    # State(T) fields present
    assert body["state"]["signal"]["provenance"] == "RECORDED"
    assert body["state"]["signal"]["closed_trade_count"] == 1
    assert body["state"]["memory"]["provenance"] == "NOT_AVAILABLE"
    assert body["state"]["events"]["provenance"] == "NOT_AVAILABLE"


@pytest.mark.asyncio
async def test_reveal_releases_outcome_and_post_mortem(db_manager):
    t0 = 1_711_000_000_000
    trades = [_trade(t0, 10.0), _trade(t0 + 10 * _H1, -5.0)]
    eid = await _seed(db_manager, trades=trades)

    at_mid = t0 + 5 * _H1  # between the two trades
    async with api() as c:
        resp = await c.get(
            f"/api/v1/episodes/{eid}/replay", params={"at": at_mid, "reveal": "true"}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["revealed"] is True
    outcome = body["outcome"]
    assert outcome["provenance"] == "RECORDED"
    assert len(outcome["trades_after_t"]) == 1
    assert outcome["trades_after_t"][0]["pnl"] == -5.0
    assert outcome["metrics"]["sharpe_ratio"] == 1.2

    # post-mortem: last action was a buy; next trade lost → direction_correct False
    pm = outcome["post_mortem"]
    assert pm["last_action_side"] == "buy"
    assert pm["realized_pnl_after_t"] == -5.0
    assert pm["direction_correct"] is False


@pytest.mark.asyncio
async def test_signal_state_degrades_without_backtest_row(db_manager):
    """Old rows (pre-S0 bridge bug) have no backtests row → NOT_AVAILABLE, not 500."""
    eid = await _seed(db_manager, backtest_id="bt-missing-row", trades=None)

    async with api() as c:
        resp = await c.get(
            f"/api/v1/episodes/{eid}/replay", params={"at": 1_711_000_000_000}
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["signal"]["provenance"] == "NOT_AVAILABLE"
    assert body["state"]["signal"]["backtest_id"] == "bt-missing-row"


@pytest.mark.asyncio
async def test_market_state_absent_without_kline_partition(db_manager):
    """FAKE/USDT has no klines.db partition → market NOT_AVAILABLE, not 500."""
    t0 = 1_711_000_000_000
    eid = await _seed(db_manager, trades=[_trade(t0, 1.0)])

    async with api() as c:
        resp = await c.get(f"/api/v1/episodes/{eid}/replay")

    assert resp.status_code == 200
    body = resp.json()
    assert body["state"]["market"]["provenance"] == "NOT_AVAILABLE"
    assert body["state"]["market"]["bars"] == []


@pytest.mark.asyncio
async def test_at_derivation_priority(db_manager):
    """Explicit ?at beats LAST_ACTION derivation; 422 when neither derivable."""
    t0 = 1_711_000_000_000
    eid = await _seed(db_manager, trades=[_trade(t0, 1.0)])

    async with api() as c:
        r_explicit = await c.get(
            f"/api/v1/episodes/{eid}/replay", params={"at": t0 - _H1}
        )
        assert r_explicit.json()["at"] == t0 - _H1
        assert r_explicit.json()["at_source"] == "REQUESTED"

        # no ?at → falls to LAST_ACTION = last exit_time
        r_default = await c.get(f"/api/v1/episodes/{eid}/replay")
        assert r_default.json()["at"] == t0 + 2 * _H1
        assert r_default.json()["at_source"] == "LAST_ACTION"

    # Episode with neither trades nor backtest → unresolvable
    eid2 = await _seed(db_manager, backtest_id=None, trades=None)
    async with api() as c:
        r_unresolvable = await c.get(f"/api/v1/episodes/{eid2}/replay")
    assert r_unresolvable.status_code == 422


@pytest.mark.asyncio
async def test_replay_404_unknown_episode(db_manager):
    async with api() as c:
        resp = await c.get("/api/v1/episodes/epi-nope/replay")
    assert resp.status_code == 404
