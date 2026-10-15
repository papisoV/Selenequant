"""Integration tests for grid API endpoints.

Uses FastAPI TestClient against the grid router with mock engines.
These tests verify the API contract without requiring real DEX connections.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.grid import router, _engines, _adapters


def _create_test_app() -> FastAPI:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    return app


def _mock_engine(running: bool = False) -> MagicMock:
    """Create a mock GridExecutionEngine."""
    eng = MagicMock()
    state = MagicMock()
    state.running = running
    state.recovery = False
    state.config = None
    state.last_price = 100000.0
    state.equity = 10500.0
    state.realized_pnl = 50.0
    state.unrealized_pnl = -20.0
    state.return_pct = 0.29
    state.model_dump.return_value = {
        "running": running,
        "recovery": False,
        "config": None,
        "last_price": 100000.0,
        "equity": 10500.0,
        "realized_pnl": 50.0,
        "unrealized_pnl": -20.0,
        "return_pct": 0.29,
    }
    eng.get_state.return_value = state
    eng.start_grid = AsyncMock(return_value=state)
    eng.stop_grid = AsyncMock(return_value=state)
    eng.cancel_all_orders = AsyncMock(return_value=state)
    eng.adjust_range = AsyncMock(return_value=state)
    eng.start_recovery = AsyncMock(return_value=state)
    eng.close_position_now = AsyncMock(return_value=state)
    eng.reset_stats.return_value = state
    return eng


@pytest.fixture
def client():
    """TestClient with clean engine registry."""
    # Save and clear global state
    saved_engines = dict(_engines)
    saved_adapters = dict(_adapters)
    _engines.clear()
    _adapters.clear()

    app = _create_test_app()
    with TestClient(app) as c:
        yield c

    # Restore
    _engines.update(saved_engines)
    _adapters.update(saved_adapters)


@pytest.fixture
def client_with_engine(client):
    """TestClient with a mock engine pre-registered."""
    eng = _mock_engine()
    _engines["ex"] = eng
    return client, eng


# ---------------------------------------------------------------------------
# GET /state
# ---------------------------------------------------------------------------


class TestGetState:
    def test_empty_state(self, client: TestClient) -> None:
        res = client.get("/api/v1/grid/state")
        assert res.status_code == 200
        data = res.json()["data"]
        assert set(data.keys()) == {"de", "ex", "rs"}

    def test_state_with_engine(self, client_with_engine: tuple) -> None:
        client, eng = client_with_engine
        res = client.get("/api/v1/grid/state")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["ex"]["running"] is False

    def test_state_single_exchange(self, client_with_engine: tuple) -> None:
        client, eng = client_with_engine
        res = client.get("/api/v1/grid/state", params={"exchange": "ex"})
        assert res.status_code == 200
        data = res.json()["data"]
        assert "running" in data

    def test_state_unknown_exchange(self, client: TestClient) -> None:
        res = client.get("/api/v1/grid/state", params={"exchange": "unknown"})
        assert res.status_code == 200
        assert res.json()["error"] is not None


# ---------------------------------------------------------------------------
# POST /stop
# ---------------------------------------------------------------------------


class TestStopGrid:
    def test_stop_no_engine(self, client: TestClient) -> None:
        res = client.post("/api/v1/grid/stop", params={"exchange": "de"})
        assert res.status_code == 200
        assert res.json()["error"] is not None

    def test_stop_with_engine(self, client_with_engine: tuple) -> None:
        client, eng = client_with_engine
        res = client.post("/api/v1/grid/stop", params={"exchange": "ex"})
        assert res.status_code == 200
        eng.stop_grid.assert_called_once()


# ---------------------------------------------------------------------------
# POST /cancel-orders
# ---------------------------------------------------------------------------


class TestCancelOrders:
    def test_cancel_no_engine(self, client: TestClient) -> None:
        res = client.post("/api/v1/grid/cancel-orders", params={"exchange": "de"})
        assert res.status_code == 200
        assert res.json()["error"] is not None

    def test_cancel_with_engine(self, client_with_engine: tuple) -> None:
        client, eng = client_with_engine
        res = client.post("/api/v1/grid/cancel-orders", params={"exchange": "ex"})
        assert res.status_code == 200
        eng.cancel_all_orders.assert_called_once()


# ---------------------------------------------------------------------------
# POST /close-position
# ---------------------------------------------------------------------------


class TestClosePosition:
    def test_close_no_engine(self, client: TestClient) -> None:
        res = client.post("/api/v1/grid/close-position", params={"exchange": "rs"})
        assert res.status_code == 200
        assert res.json()["error"] is not None

    def test_close_with_engine(self, client_with_engine: tuple) -> None:
        client, eng = client_with_engine
        res = client.post("/api/v1/grid/close-position", params={"exchange": "ex"})
        assert res.status_code == 200
        eng.close_position_now.assert_called_once()


# ---------------------------------------------------------------------------
# POST /reset-stats
# ---------------------------------------------------------------------------


class TestResetStats:
    def test_reset_no_engine(self, client: TestClient) -> None:
        res = client.post("/api/v1/grid/reset-stats", params={"exchange": "de"})
        assert res.status_code == 200
        assert res.json()["error"] is not None

    def test_reset_with_engine(self, client_with_engine: tuple) -> None:
        client, eng = client_with_engine
        res = client.post("/api/v1/grid/reset-stats", params={"exchange": "ex"})
        assert res.status_code == 200
        eng.reset_stats.assert_called_once()


# ---------------------------------------------------------------------------
# GET /sentinel
# ---------------------------------------------------------------------------


class TestSentinel:
    def test_sentinel_no_scheduler(self, client: TestClient) -> None:
        res = client.get("/api/v1/grid/sentinel")
        assert res.status_code == 200
        data = res.json()["data"]
        assert data["running"] is False


# ---------------------------------------------------------------------------
# POST /chat
# ---------------------------------------------------------------------------


class TestChat:
    def test_chat_missing_message(self, client: TestClient) -> None:
        res = client.post("/api/v1/grid/chat", json={})
        assert res.status_code == 200
        assert res.json()["error"] is not None
