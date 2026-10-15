"""Billing foundation tests: v016 migration + BillingRepository + auth API."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import pytest_asyncio

_REPO = Path(__file__).resolve().parents[2]
_STORAGE = _REPO / "packages" / "storage"
for p in (str(_STORAGE),):
    if p not in sys.path:
        sys.path.insert(0, p)

from selene_space_storage import AsyncSQLiteManager  # noqa: E402
from selene_space_storage.repositories.billing import (  # noqa: E402
    BillingRepository,
    hash_password,
    verify_password,
)


@pytest_asyncio.fixture()
async def db(tmp_path):
    manager = AsyncSQLiteManager(db_path=tmp_path / "billing_test.db")
    await manager.connect()
    yield manager
    await manager.close()


@pytest.fixture()
def repo(db):
    return BillingRepository(db)


# ---------------------------------------------------------------------------
# password hashing
# ---------------------------------------------------------------------------


def test_hash_password_roundtrip():
    h = hash_password("secret-password")
    assert h.startswith("pbkdf2$")
    assert verify_password("secret-password", h)
    assert not verify_password("wrong-password", h)


def test_hash_password_unique_salts():
    assert hash_password("same") != hash_password("same")


def test_verify_password_malformed_stored():
    assert not verify_password("x", "garbage")
    assert not verify_password("x", "md5$1$ab$cd")


# ---------------------------------------------------------------------------
# repository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_migration_creates_tables(db):
    rows = await db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name IN"
        " ('users','redemption_codes')"
    )
    assert {r["name"] for r in rows} == {"users", "redemption_codes"}


@pytest.mark.asyncio
async def test_create_user_and_duplicate_email(repo):
    user = await repo.create_user("Alice@Example.com", "password123")
    assert user["email"] == "alice@example.com"
    stored = await repo.get_user_by_email("alice@example.com")
    assert stored is not None
    assert verify_password("password123", stored["password_hash"])
    with pytest.raises(ValueError, match="already"):
        await repo.create_user("alice@example.com", "other-pass")


@pytest.mark.asyncio
async def test_redeem_and_entitlement_flow(repo):
    user = await repo.create_user("u1@test.com", "password123")
    uid = user["id"]
    # No entitlement before redeem.
    assert not await repo.has_active_entitlement(uid)
    await repo.insert_codes([{"code": "SL-TEST-CODE-0001", "plan": "monthly"}])
    result = await repo.redeem_code("SL-TEST-CODE-0001", uid)
    assert result["status"] == "used"
    assert result["expires_at"] is not None
    assert await repo.has_active_entitlement(uid)
    expiry = await repo.entitlement_expiry(uid)
    assert expiry == result["expires_at"]


@pytest.mark.asyncio
async def test_redeem_idempotent_same_user_repo_raises_other(repo):
    user_a = await repo.create_user("a@test.com", "password123")
    user_b = await repo.create_user("b@test.com", "password123")
    await repo.insert_codes([{"code": "SL-TEST-CODE-0002", "plan": "monthly"}])
    first = await repo.redeem_code("SL-TEST-CODE-0002", user_a["id"])
    again = await repo.redeem_code("SL-TEST-CODE-0002", user_a["id"])
    assert again["expires_at"] == first["expires_at"]
    with pytest.raises(ValueError, match="already used"):
        await repo.redeem_code("SL-TEST-CODE-0002", user_b["id"])


@pytest.mark.asyncio
async def test_redeem_invalid_code(repo):
    user = await repo.create_user("c@test.com", "password123")
    with pytest.raises(ValueError, match="invalid"):
        await repo.redeem_code("SL-NOPE-NOPE-NOPE", user["id"])


@pytest.mark.asyncio
async def test_plan_expiry_windows(repo):
    from datetime import datetime

    monthly = repo._plan_expiry("monthly")
    yearly = repo._plan_expiry("yearly")
    trial = repo._plan_expiry("trial")
    m = datetime.strptime(monthly, "%Y-%m-%dT%H:%M:%SZ")
    y = datetime.strptime(yearly, "%Y-%m-%dT%H:%M:%SZ")
    t = datetime.strptime(trial, "%Y-%m-%dT%H:%M:%SZ")
    now = datetime.utcnow()
    assert 25 <= (m - now).days <= 35
    assert 360 <= (y - now).days <= 372
    assert 6 <= (t - now).days <= 8


@pytest.mark.asyncio
async def test_expired_code_no_entitlement(repo):
    user = await repo.create_user("d@test.com", "password123")
    await repo.insert_codes([{"code": "SL-TEST-CODE-0003", "plan": "monthly"}])
    # Force-expire by direct UPDATE (simulates time passing).
    await repo._db.execute(
        "UPDATE redemption_codes SET status='used', bound_user_id=?,"
        " expires_at='2020-01-01T00:00:00Z' WHERE code=?",
        (user["id"], "SL-TEST-CODE-0003"),
    )
    assert not await repo.has_active_entitlement(user["id"])


# ---------------------------------------------------------------------------
# API layer (billing mode on)
# ---------------------------------------------------------------------------


@pytest.fixture()
def billing_env(monkeypatch):
    monkeypatch.setenv("SELENE_BILLING_MODE", "1")
    monkeypatch.setenv("SELENE_JWT_SECRET", "test-secret-key-for-pytest-only")
    yield
    monkeypatch.delenv("SELENE_BILLING_MODE", raising=False)
    monkeypatch.delenv("SELENE_JWT_SECRET", raising=False)


@pytest.mark.asyncio
async def test_register_login_redeem_api(db, billing_env, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.api.v1.auth import router as auth_router

    real_repo = BillingRepository(db)
    monkeypatch.setattr("app.api.v1.auth.get_billing_repo", lambda: real_repo)

    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1")
    with TestClient(app) as tc:
        r = tc.post(
            "/api/v1/auth/register",
            json={"email": "api@test.com", "password": "password123"},
        )
        assert r.status_code == 200, r.text
        token = r.json()["data"]["token"]
        assert token

        # duplicate register → 409
        r2 = tc.post(
            "/api/v1/auth/register",
            json={"email": "api@test.com", "password": "password456"},
        )
        assert r2.status_code == 409

        # login ok
        r3 = tc.post(
            "/api/v1/auth/login",
            json={"email": "api@test.com", "password": "password123"},
        )
        assert r3.status_code == 200

        # login wrong password → 401
        r4 = tc.post(
            "/api/v1/auth/login",
            json={"email": "api@test.com", "password": "wrong-pass"},
        )
        assert r4.status_code == 401

        # entitlement empty
        r5 = tc.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r5.status_code == 200
        assert r5.json()["data"]["entitlement_expiry"] is None

        # invalid code → 409
        r6 = tc.post(
            "/api/v1/auth/redeem",
            json={"code": "SL-BAD1-CODE-9999"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r6.status_code == 409

        # valid redeem
        await real_repo.insert_codes([{"code": "SL-API1-CODE-0001", "plan": "monthly"}])
        r7 = tc.post(
            "/api/v1/auth/redeem",
            json={"code": "SL-API1-CODE-0001"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r7.status_code == 200, r7.text
        assert r7.json()["data"]["expires_at"]

        # entitlement now visible
        r8 = tc.get(
            "/api/v1/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r8.json()["data"]["entitlement_expiry"] is not None

    # billing off → auth endpoints 404
    monkeypatch.delenv("SELENE_BILLING_MODE", raising=False)
    app2 = FastAPI()
    app2.include_router(auth_router, prefix="/api/v1")
    with TestClient(app2) as tc2:
        r9 = tc2.post(
            "/api/v1/auth/register",
            json={"email": "x@test.com", "password": "password123"},
        )
        assert r9.status_code == 404


def test_billing_mode_off_guard_is_noop(monkeypatch):
    monkeypatch.delenv("SELENE_BILLING_MODE", raising=False)
    from app.core.billing_auth import billing_mode_enabled

    assert billing_mode_enabled() is False


def test_billing_mode_requires_secret(monkeypatch):
    monkeypatch.setenv("SELENE_BILLING_MODE", "1")
    monkeypatch.delenv("SELENE_JWT_SECRET", raising=False)
    import app.core.billing_auth as mod

    monkeypatch.setattr(mod, "_jwt_secret", None)
    with pytest.raises(RuntimeError, match="SELENE_JWT_SECRET"):
        mod.require_jwt_secret()
