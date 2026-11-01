"""E2E billing-mode integration: register -> login -> 402 on paid endpoint ->
redeem -> 200, against a live FastAPI app backed by a tmp SQLite DB."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest_asyncio

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "services" / "local-api"))
sys.path.insert(0, str(_REPO / "packages" / "storage"))

from selene_space_storage import AsyncSQLiteManager  # noqa: E402
from selene_space_storage.repositories.billing import BillingRepository  # noqa: E402


@pytest_asyncio.fixture()
async def billing_app(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import app.core.billing_auth as billing_mod
    from app.api.v1 import backtests as backtests_mod
    from app.api.v1.auth import router as auth_router

    monkeypatch.setenv("SELENE_BILLING_MODE", "1")
    monkeypatch.setenv("SELENE_JWT_SECRET", "test-secret-key-for-pytest-32b!")
    billing_mod._jwt_secret = None  # force re-read of env

    manager = AsyncSQLiteManager(db_path=tmp_path / "e2e_billing.db")
    await manager.connect()
    repo = BillingRepository(manager)
    await repo.insert_codes([{"code": "SL-E2E1-CODE-0002", "plan": "monthly"}])

    app = FastAPI()
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(backtests_mod.router, prefix="/api/v1")
    # Both modules import get_billing_repo by name at call time via module
    # global — patch the shared module attribute once.
    monkeypatch.setattr(billing_mod, "_billing_repo", repo)

    yield TestClient(app)
    await manager.close()
    monkeypatch.delenv("SELENE_BILLING_MODE", raising=False)
    monkeypatch.delenv("SELENE_JWT_SECRET", raising=False)


def test_full_billing_loop(billing_app):
    tc = billing_app

    # register
    r = tc.post(
        "/api/v1/auth/register",
        json={"email": "e2e@test.com", "password": "password123"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["data"]["token"]

    # paid endpoint without entitlement -> 402
    # (POST /backtests with empty body will fail validation first; the
    # entitlement dependency runs before the handler but after body parsing,
    # so send a minimal valid body.)
    r2 = tc.post(
        "/api/v1/backtests",
        json={"strategy_id": "x", "symbol": "BTCUSDT", "interval": "1d"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r2.status_code == 402, f"expected 402, got {r2.status_code}: {r2.text[:200]}"

    # redeem invalid code -> 409
    r3 = tc.post(
        "/api/v1/auth/redeem",
        json={"code": "SL-E2E0-CODE-0001"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r3.status_code == 409

    # redeem valid code -> 200 (code pre-inserted by fixture)
    r4 = tc.post(
        "/api/v1/auth/redeem",
        json={"code": "SL-E2E1-CODE-0002"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r4.status_code == 200, r4.text

    # paid endpoint with entitlement now passes the 402 gate
    # (handler may fail later on missing services, but NOT with 402/401)
    r6 = tc.post(
        "/api/v1/backtests",
        json={"strategy_id": "x", "symbol": "BTCUSDT", "interval": "1d"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r6.status_code not in (
        401,
        402,
    ), f"gate failed: {r6.status_code}: {r6.text[:200]}"
