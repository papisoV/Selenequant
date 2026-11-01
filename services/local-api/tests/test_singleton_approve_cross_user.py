"""C3 fix: per-user approve gate on LiveExecutionEngine singleton.

The LiveExecutionEngine is a singleton shared across users. Before C3,
``approve_first_trade()`` set a global ``_first_trade_approved=True``
that approved ALL users. C3 adds ``_approved_users: set[str]`` so
``approve_first_trade(user_a)`` only approves user_a; user_b's
``place_order(user_id=user_b)`` is blocked.

Tests:
- user A approves, user B place_order → blocked (RuntimeError from engine)
- user A approves, user A place_order → allowed
- HTTP layer: user A approve, user B place_order → approval_required (not place_order_blocked)
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


def _make_engine():
    """Build a real LiveExecutionEngine-like mock with per-user approve."""
    mock_engine = MagicMock()
    mock_engine._approved_users = set()
    mock_engine._first_trade_approved = False
    mock_engine.first_trade_approved = False
    mock_order = MagicMock()
    mock_order.model_dump.return_value = {"id": "test-1", "symbol": "SOLUSDT"}
    mock_engine.place_order = AsyncMock(return_value=mock_order)

    # Wire approve_first_trade to actually add to _approved_users (real behavior).
    def _approve(user_id=None):
        mock_engine._first_trade_approved = True
        mock_engine.first_trade_approved = True
        if user_id is not None:
            mock_engine._approved_users.add(user_id)
    mock_engine.approve_first_trade = _approve
    return mock_engine


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    monkeypatch.delenv("SELENE_AUTH_DISABLED", raising=False)
    from app.main import create_app
    return create_app()


def test_user_a_approve_user_b_place_order_blocked(app):
    """User A approve, user B place_order → approval_required (cross-user blocked)."""
    mock_engine = _make_engine()

    import app.core.dependencies as deps
    original = deps.get_live_engine
    deps.get_live_engine = lambda: mock_engine
    try:
        client = TestClient(app)

        # User A (local) approves.
        resp = client.post("/api/v1/live/approve")
        assert resp.status_code == 200
        assert "local" in mock_engine._approved_users

        # User B (also local in this test harness — but we simulate cross-user
        # by directly checking the set). Since TestClient is always localhost,
        # both appear as "local". To test cross-user, we monkeypatch the
        # place_order endpoint's user by removing "local" from approved set
        # and calling place_order as a different user_id via engine mock.
        # Instead: verify the engine-level block by calling place_order with
        # a user_id NOT in _approved_users.
        mock_engine._approved_users.discard("user-b")  # ensure user-b not approved
        # Place order as user-b by overriding require_user to return user-b.
        from app.core.dependencies import AuthenticatedUser

        async def _user_b():
            return AuthenticatedUser(id="user-b", role="user")

        app.dependency_overrides[deps.require_user] = _user_b
        resp2 = client.post(
            "/api/v1/live/place_order",
            json={
                "symbol": "SOLUSDT",
                "side": "buy",
                "order_type": "market",
                "quantity": 0.066,
            },
        )
        body = resp2.json()
        assert body["error"] is not None
        assert body["error"]["code"] == "approval_required"
        mock_engine.place_order.assert_not_awaited()
    finally:
        deps.get_live_engine = original
        app.dependency_overrides.pop(deps.require_user, None)


def test_user_a_approve_user_a_place_order_allowed(app):
    """User A approve, user A place_order → 200 (same user allowed)."""
    mock_engine = _make_engine()

    import app.core.dependencies as deps
    original = deps.get_live_engine
    deps.get_live_engine = lambda: mock_engine
    try:
        client = TestClient(app)

        resp = client.post("/api/v1/live/approve")
        assert resp.status_code == 200

        resp2 = client.post(
            "/api/v1/live/place_order",
            json={
                "symbol": "SOLUSDT",
                "side": "buy",
                "order_type": "market",
                "quantity": 0.066,
            },
        )
        assert resp2.status_code == 200, resp2.text
        body = resp2.json()
        assert body["error"] is None
        mock_engine.place_order.assert_awaited_once()
        assert mock_engine.place_order.call_args.kwargs.get("user_id") == "local"
    finally:
        deps.get_live_engine = original


def test_engine_level_per_user_block():
    """LiveExecutionEngine.place_order blocks user not in _approved_users."""
    # Use the real engine class with a stubbed client.
    from selene_space_exchange.execution.live_engine import LiveExecutionEngine
    import os as _os
    _os.environ["SELENE_ENABLE_REAL_TRADING"] = "1"

    try:
        # Build with minimal stubs — we only test the approve gate.
        engine = LiveExecutionEngine.__new__(LiveExecutionEngine)
        engine._first_trade_approved = True
        engine._approved_users = {"user-a"}
        import asyncio

        async def _run():
            with pytest.raises(RuntimeError, match="user user-b has not approved"):
                await engine.place_order(
                    account_id="live",
                    symbol="SOLUSDT",
                    side=None,
                    order_type=None,
                    quantity=0.1,
                    user_id="user-b",
                )

        asyncio.run(_run())
    finally:
        _os.environ.pop("SELENE_ENABLE_REAL_TRADING", None)


def test_engine_approve_adds_user_to_set():
    """LiveExecutionEngine.approve_first_trade(user_id) adds to _approved_users."""
    from selene_space_exchange.execution.live_engine import LiveExecutionEngine

    engine = LiveExecutionEngine.__new__(LiveExecutionEngine)
    engine._first_trade_approved = False
    engine._approved_users = set()

    engine.approve_first_trade("user-a")
    assert "user-a" in engine._approved_users
    assert engine._first_trade_approved is True

    # user-b not added → still not in set.
    assert "user-b" not in engine._approved_users


def test_engine_approve_backward_compat_no_user_id():
    """approve_first_trade() with no user_id → legacy global flag (backward compat)."""
    from selene_space_exchange.execution.live_engine import LiveExecutionEngine

    engine = LiveExecutionEngine.__new__(LiveExecutionEngine)
    engine._first_trade_approved = False
    engine._approved_users = set()

    engine.approve_first_trade()  # no user_id
    assert engine._first_trade_approved is True
    assert engine._approved_users == set()  # nothing added
