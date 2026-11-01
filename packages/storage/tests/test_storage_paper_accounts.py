"""PaperAccountRepository locks: account save/get/upsert, order and position
CRUD with status filters and upserts, and empty-collection behavior."""

from __future__ import annotations

import pytest
import pytest_asyncio

from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.repositories.paper_accounts import PaperAccountRepository
from selene_space_storage.sqlite import AsyncSQLiteManager

FUNDING = 25_000.0


def _account(aid: str, **extra) -> dict:
    base = {"id": aid, "name": "Sandbox", "initial_balance": FUNDING}
    return {**base, **extra}


@pytest_asyncio.fixture
async def repo():
    runner = MigrationRunner(ALL_MIGRATIONS)
    async with AsyncSQLiteManager(":memory:", migration_runner=runner) as db:
        yield PaperAccountRepository(db)


# --- accounts -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_account_save_then_get(repo: PaperAccountRepository):
    assert await repo.save_account(_account("acc-A7")) == "acc-A7"

    loaded = await repo.get_account("acc-A7")
    assert loaded is not None
    assert loaded["name"] == "Sandbox"
    assert loaded["initial_balance"] == FUNDING
    assert loaded["status"] == "active"


@pytest.mark.asyncio
async def test_unknown_account_id_yields_none(repo: PaperAccountRepository):
    assert await repo.get_account("ghost") is None


@pytest.mark.asyncio
async def test_account_upsert_keeps_latest_state(repo: PaperAccountRepository):
    draft_v1 = {"id": "a1", "initial_balance": 8000.0}
    draft_v2 = {"id": "a1", "current_balance": 7600.0}

    await repo.save_account({**draft_v1, "name": "Gen One"})
    await repo.save_account({**draft_v2, "name": "Gen Two"})

    loaded = await repo.get_account("a1")
    assert loaded is not None
    assert loaded["name"] == "Gen Two"
    assert loaded["current_balance"] == 7600.0


# --- orders --------------------------------------------------------------------


async def _seed_account(repo: PaperAccountRepository) -> None:
    await repo.save_account(_account("a1"))


def _order(oid: str, *, symbol="SOL/USDT", side="buy", otype="market", qty=2.0, **extra) -> dict:
    body = {
        "id": oid,
        "account_id": "a1",
        "symbol": symbol,
        "side": side,
        "type": otype,
        "quantity": qty,
    }
    return {**body, **extra}


@pytest.mark.asyncio
async def test_order_persists_with_created_status(repo: PaperAccountRepository):
    await _seed_account(repo)
    await repo.save_order(_order("ord-X1", qty=1.25))

    orders = await repo.get_orders("a1")
    assert len(orders) == 1
    assert orders[0]["symbol"] == "SOL/USDT"
    assert orders[0]["status"] == "created"


@pytest.mark.asyncio
async def test_order_status_filter_narrows_results(repo: PaperAccountRepository):
    await _seed_account(repo)
    await repo.save_order(_order("k1", status="filled"))
    await repo.save_order(_order("k2", symbol="BNB/USDT", side="sell", otype="limit", qty=5.0))

    filled = await repo.get_orders("a1", status="filled")
    assert len(filled) == 1
    assert filled[0]["id"] == "k1"

    assert len(await repo.get_orders("a1")) == 2


@pytest.mark.asyncio
async def test_order_upsert_replaces_earlier_row(repo: PaperAccountRepository):
    await _seed_account(repo)
    await repo.save_order(_order("k1"))
    await repo.save_order(
        _order("k1", status="filled", filled_quantity=2.0, avg_fill_price=148.35)
    )

    orders = await repo.get_orders("a1")
    assert len(orders) == 1
    assert orders[0]["status"] == "filled"
    assert orders[0]["avg_fill_price"] == 148.35


# --- positions -------------------------------------------------------------------


def _position(pid: str, qty: float, entry: float, pnl=None) -> dict:
    body = {
        "id": pid,
        "account_id": "a1",
        "symbol": "SOL/USDT",
        "side": "buy",
        "quantity": qty,
        "avg_entry_price": entry,
    }
    if pnl is not None:
        body["unrealized_pnl"] = pnl
    return body


@pytest.mark.asyncio
async def test_position_save_then_list(repo: PaperAccountRepository):
    await _seed_account(repo)
    await repo.save_position(_position("pos-Z9", 1.25, 148.35, pnl=62.5))

    positions = await repo.get_positions("a1")
    assert len(positions) == 1
    assert positions[0]["symbol"] == "SOL/USDT"
    assert positions[0]["quantity"] == 1.25
    assert positions[0]["unrealized_pnl"] == 62.5


@pytest.mark.asyncio
async def test_position_upsert_overwrites_by_id(repo: PaperAccountRepository):
    await _seed_account(repo)
    await repo.save_position(_position("p9", 1.25, 148.35))
    await repo.save_position(_position("p9", 2.4, 151.2, pnl=88.0))

    positions = await repo.get_positions("a1")
    assert len(positions) == 1
    assert positions[0]["quantity"] == 2.4
    assert positions[0]["avg_entry_price"] == 151.2


@pytest.mark.asyncio
async def test_fresh_account_has_no_positions(repo: PaperAccountRepository):
    await _seed_account(repo)
    assert await repo.get_positions("a1") == []
