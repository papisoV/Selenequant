"""C3 fix: live endpoints require auth + per-user approve gate.

POST /api/v1/live/approve and /api/v1/live/place_order now require
Depends(require_user). localhost → admin (local); remote without auth → 401.
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


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    monkeypatch.delenv("SELENE_AUTH_DISABLED", raising=False)
    from app.main import create_app
    return create_app()


def test_approve_endpoint_localhost_admin(app):
    """POST /api/v1/live/approve from localhost → 200 (local admin)."""
    mock_engine = MagicMock()
    mock_engine.approve_first_trade = MagicMock()
    mock_engine._approved_users = set()
    mock_engine.first_trade_approved = False

    import app.core.dependencies as deps
    original = deps.get_live_engine
    deps.get_live_engine = lambda: mock_engine
    try:
        client = TestClient(app)
        resp = client.post("/api/v1/live/approve")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["error"] is None
        assert body["data"]["approved"] is True
        # approve_first_trade called with user.id="local"
        mock_engine.approve_first_trade.assert_called_once_with("local")
    finally:
        deps.get_live_engine = original


def test_place_order_localhost_admin_with_approve(app):
    """POST /api/v1/live/place_order from localhost + approved → 200."""
    mock_engine = MagicMock()
    mock_order = MagicMock()
    mock_order.model_dump.return_value = {"id": "test-1", "symbol": "SOLUSDT"}
    mock_engine.place_order = AsyncMock(return_value=mock_order)
    mock_engine._approved_users = {"local"}  # pre-approved
    mock_engine.first_trade_approved = True

    import app.core.dependencies as deps
    original = deps.get_live_engine
    deps.get_live_engine = lambda: mock_engine
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/live/place_order",
            json={
                "symbol": "SOLUSDT",
                "side": "buy",
                "order_type": "market",
                "quantity": 0.066,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["error"] is None
        # place_order called with user_id="local"
        call_kwargs = mock_engine.place_order.call_args.kwargs
        assert call_kwargs.get("user_id") == "local"
    finally:
        deps.get_live_engine = original


def test_place_order_blocked_without_approve(app):
    """POST /api/v1/live/place_order without approve → approval_required error."""
    mock_engine = MagicMock()
    mock_engine.place_order = AsyncMock()
    mock_engine._approved_users = set()  # not approved
    mock_engine.first_trade_approved = True

    import app.core.dependencies as deps
    original = deps.get_live_engine
    deps.get_live_engine = lambda: mock_engine
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/live/place_order",
            json={
                "symbol": "SOLUSDT",
                "side": "buy",
                "order_type": "market",
                "quantity": 0.066,
            },
        )
        # HTTP 200 (APIResponse wraps error), but error present
        body = resp.json()
        assert body["error"] is not None
        assert body["error"]["code"] == "approval_required"
        mock_engine.place_order.assert_not_awaited()
    finally:
        deps.get_live_engine = original


def test_admin_kill_switch_trip_localhost_admin(app):
    """POST /api/v1/admin/kill-switch/trip from localhost → admin role → 200."""
    client = TestClient(app)
    resp = client.post(
        "/api/v1/admin/kill-switch/trip",
        json={"reason": "test"},
    )
    assert resp.status_code == 200, resp.text
