"""F-A-test HTTP 层: 验证 POST /api/v1/live/place_order 把 req.leverage 透传到 engine.place_order(leverage=)。

只覆盖 HTTP 层契约 (req.leverage → engine.place_order 形参)。
不覆盖 leverage → risk_engine.pre_check 的内部 routing,见
packages/exchange-sdk/tests/test_live_engine_leverage_routing.py。
"""

# TODO(Tech Debt): E-B-fallback-delete + 4-A-真-prod-verify — see
# scripts/issues/mainnet-retest-followup.md 顶部 Open Tech Debt 表。
# 这两项 pending,mainnet API key 401 解除后必须补跑,不可关闭。

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from fastapi.testclient import TestClient

import app.core.dependencies as deps
from app.main import create_app


def test_place_order_routes_leverage_to_engine_kwargs() -> None:
    """POST /api/v1/live/place_order body leverage=10 → engine.place_order(leverage=10)."""
    app = create_app()

    mock_engine = MagicMock()
    mock_order = MagicMock()
    mock_order.model_dump.return_value = {
        "id": "test-1",
        "symbol": "SOLUSDT",
        "side": "buy",
        "quantity": 0.066,
        "price": 76.73,
        "status": "filled",
    }
    mock_engine.place_order = AsyncMock(return_value=mock_order)
    mock_engine.first_trade_approved = True
    mock_engine._approved_users = {"local"}

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
                "leverage": 10,
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["error"] is None
        assert body["data"]["order"]["id"] == "test-1"

        mock_engine.place_order.assert_awaited_once()
        call_kwargs = mock_engine.place_order.call_args.kwargs
        assert call_kwargs["symbol"] == "SOLUSDT"
        assert call_kwargs["quantity"] == 0.066
        assert call_kwargs["leverage"] == 10
    finally:
        deps.get_live_engine = original


def test_place_order_default_leverage_is_one() -> None:
    """POST /api/v1/live/place_order 不传 leverage → engine.place_order(leverage=1)."""
    app = create_app()

    mock_engine = MagicMock()
    mock_order = MagicMock()
    mock_order.model_dump.return_value = {"id": "test-2", "symbol": "SOLUSDT"}
    mock_engine.place_order = AsyncMock(return_value=mock_order)
    mock_engine.first_trade_approved = True
    mock_engine._approved_users = {"local"}

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
        assert resp.json()["error"] is None
        mock_engine.place_order.assert_awaited_once()
        call_kwargs = mock_engine.place_order.call_args.kwargs
        assert call_kwargs["leverage"] == 1
    finally:
        deps.get_live_engine = original


def test_place_order_invalid_side_returns_error() -> None:
    """POST /api/v1/live/place_order side='BUYX' → error.code=invalid_side."""
    app = create_app()

    mock_engine = MagicMock()
    mock_engine.place_order = AsyncMock()
    mock_engine.first_trade_approved = True
    mock_engine._approved_users = {"local"}

    original = deps.get_live_engine
    deps.get_live_engine = lambda: mock_engine
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/v1/live/place_order",
            json={
                "symbol": "SOLUSDT",
                "side": "BUYX",
                "order_type": "market",
                "quantity": 0.066,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is not None
        assert body["error"]["code"] == "invalid_side"
        mock_engine.place_order.assert_not_awaited()
    finally:
        deps.get_live_engine = original


def test_place_order_engine_not_wired_returns_error() -> None:
    """POST /api/v1/live/place_order 当 engine=None → error.code=live_engine_not_wired."""
    app = create_app()

    original = deps.get_live_engine
    deps.get_live_engine = lambda: None
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
        assert resp.status_code == 200
        body = resp.json()
        assert body["error"] is not None
        assert body["error"]["code"] == "live_engine_not_wired"
    finally:
        deps.get_live_engine = original
