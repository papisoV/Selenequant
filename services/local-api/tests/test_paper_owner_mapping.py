"""P0 fix: paper account ownership mapping was permanently empty.

``_ensure_owners_loaded`` called ``db.query()``, a method that does not exist
on AsyncSQLiteManager, so the load raised AttributeError and was swallowed by
a debug-level except. Consequence: ``_account_owners`` stayed empty forever
(and the one-shot ``_owners_loaded`` flag was set True even on failure, so it
never retried), and every non-local user was denied access to their OWN
accounts via ``_verify_ownership`` (owner None -> 403), while the list
endpoint silently handed them a fresh default account instead.

Red state of these tests (before the fix): 403 on own account / owned account
missing from the list. Green state (after): both behave.
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from app.api.v1 import paper as paper_mod
from app.core.dependencies import (
    get_paper_account_manager,
    optional_user,
    set_db_manager,
)
from app.core.dependencies import AuthenticatedUser
from app.main import create_app
from httpx import ASGITransport, AsyncClient
from selene_space_paper.accounts import AccountManager
from selene_space_storage.migrations import MigrationRunner
from selene_space_storage.migrations_pkg import ALL_MIGRATIONS
from selene_space_storage.sqlite import AsyncSQLiteManager

USER = "user-bob"


@pytest_asyncio.fixture
async def bob_client():
    runner = MigrationRunner(ALL_MIGRATIONS)
    db = AsyncSQLiteManager(":memory:", migration_runner=runner)
    await db.connect()
    set_db_manager(db)

    accounts = AccountManager()
    acct = accounts.create_account("Bob's Own", 1000.0)

    # Seed the ownership row exactly the way production paper flows do
    # (v006 added the user_id column; default 'local' must NOT match bob).
    await db.execute(
        "INSERT INTO paper_accounts (id, name, user_id) VALUES (?, ?, ?)",
        (acct.id, "Bob's Own", USER),
    )

    # Module globals are process-wide and the one-shot flag survives failures;
    # reset so this test observes a real load attempt.
    paper_mod._account_owners.clear()
    paper_mod._owners_loaded = False

    app = create_app()
    app.dependency_overrides[get_paper_account_manager] = lambda: accounts

    async def _bob() -> AuthenticatedUser:
        return AuthenticatedUser(id=USER, role="user")

    app.dependency_overrides[optional_user] = _bob

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as c:
        yield c, acct.id

    app.dependency_overrides.clear()
    paper_mod._account_owners.clear()
    paper_mod._owners_loaded = False
    set_db_manager(None)
    await db.close()


@pytest.mark.asyncio
async def test_non_local_user_can_fetch_own_account(bob_client):
    """Ownership row lives in the DB; the user must be able to use it."""
    c, acct_id = bob_client
    resp = await c.get(f"/api/v1/paper/accounts/{acct_id}")
    body = resp.json()
    # Red before fix: PERMISSION_DENIED because _account_owners was empty.
    assert resp.status_code == 200, f"expected 200, got {resp.status_code}: {body}"
    assert body["error"] is None
    assert body["data"]["id"] == acct_id


@pytest.mark.asyncio
async def test_list_shows_owned_account_not_a_fresh_default(bob_client):
    """The list endpoint must surface the owned account, not quietly
    substitute a brand-new default when the mapping is empty."""
    c, acct_id = bob_client
    resp = await c.get("/api/v1/paper/accounts")
    body = resp.json()
    assert resp.status_code == 200
    ids = [a["id"] for a in body["data"]]
    assert acct_id in ids, f"owned account missing from list: {ids}"
    names = [a.get("name") for a in body["data"]]
    assert (
        "Default Paper Account" not in names
    ), "list silently substituted a fresh default account for the owned one"
