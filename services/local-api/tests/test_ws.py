"""Behavioural checks for the WebSocket gateway endpoints."""

from __future__ import annotations

import app.api.v1.ws as ws_module
import pytest
from app.main import create_app
from starlette.testclient import TestClient

MARKETS_PATH = "/api/v1/ws/markets"
PAPER_PATH = "/api/v1/ws/paper"
SYMBOLS = ["BTC/USDT"]
ACCOUNT = "pa-12345"


@pytest.fixture(autouse=True)
def _fresh_managers():
    """Swap in pristine connection managers so tests never share state."""
    ws_module._market_manager = ws_module.ConnectionManager()
    ws_module._paper_manager = ws_module.ConnectionManager()
    yield


def _dial(client: TestClient, path: str):
    return client.websocket_connect(path)


def _say(ws, action: str, **fields) -> None:
    ws.send_json({"action": action, **fields})


def _hear(ws):
    return ws.receive_json()


def test_market_channel_accepts_subscription():
    with _dial(TestClient(create_app()), MARKETS_PATH) as ws:
        _say(ws, "subscribe", symbols=SYMBOLS)
        ack = _hear(ws)
        assert ack["type"] == "subscribed"
        assert ack["symbols"] == SYMBOLS


def test_market_channel_processes_unsubscription():
    with _dial(TestClient(create_app()), MARKETS_PATH) as ws:
        _say(ws, "subscribe", symbols=SYMBOLS)
        _hear(ws)  # subscribe ack

        _say(ws, "unsubscribe", symbols=SYMBOLS)
        bye = _hear(ws)
        assert bye["type"] == "unsubscribed"


def test_market_channel_rejects_malformed_json():
    with _dial(TestClient(create_app()), MARKETS_PATH) as ws:
        ws.send_text("{oops")
        problem = _hear(ws)
        assert problem["type"] == "error"
        assert "Invalid JSON" in problem["message"]


def test_market_channel_rejects_unknown_action():
    with _dial(TestClient(create_app()), MARKETS_PATH) as ws:
        _say(ws, "frobnicate")
        problem = _hear(ws)
        assert problem["type"] == "error"
        assert "Unknown action" in problem["message"]


def test_paper_channel_accepts_account_subscription():
    with _dial(TestClient(create_app()), PAPER_PATH) as ws:
        _say(ws, "subscribe", account_id=ACCOUNT)
        ack = _hear(ws)
        assert ack["type"] == "subscribed"
        assert ack["account_id"] == ACCOUNT


def test_paper_channel_processes_unsubscription():
    with _dial(TestClient(create_app()), PAPER_PATH) as ws:
        _say(ws, "subscribe", account_id=ACCOUNT)
        _hear(ws)

        _say(ws, "unsubscribe", account_id=ACCOUNT)
        bye = _hear(ws)
        assert bye["type"] == "unsubscribed"


@pytest.mark.asyncio
async def test_manager_broadcast_survives_zero_connections():
    manager = ws_module.ConnectionManager()
    # Broadcasting over an empty channel must be a silent no-op.
    await manager.broadcast("ops:fanout", {"ping": 1})
    assert manager.active_count == 0
