"""WS account_state subscribe/resync protocol tests (protocol §3.3, §4 Step 5).

Uses the Starlette TestClient (sync) against the real /api/v1/ws/trading
endpoint, mirroring the existing test_ws.py pattern.

Covers:
- subscribe -> the FIRST subscribed channel immediately receives an
  ``account_state`` snapshot whose envelope seq is that channel's own counter
  taken after the gather (A5, zero client special-casing).
- resync action -> a full snapshot is pushed on each requested channel.
- Multi-channel subscribe: only the first channel receives a snapshot; the
  other channels' counters stay untouched (this is what enables the client's
  P3 first-event baseline rule — see the ws_trading docstring).
- Unknown-action guard stays intact.
"""

from __future__ import annotations

import pytest
import app.api.v1.ws as ws_mod
from app.core.sequence import ChannelSequenceManager, set_channel_sequence
from app.main import create_app
from selene_space_types.trading import AccountStateSeq
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_state():
    ws_mod._trading_manager = ws_mod.ConnectionManager()
    ws_mod._paper_manager = ws_mod.ConnectionManager()
    ws_mod._ip_connection_counts.clear()
    set_channel_sequence(None)
    yield


def test_subscribe_pushes_first_channel_snapshot() -> None:
    set_channel_sequence(ChannelSequenceManager(session_id="srv-test"))
    app = create_app()
    client = TestClient(app)
    with client.websocket_connect("/api/v1/ws/trading") as ws:
        ws.send_json({"action": "subscribe", "channels": ["orders", "positions"]})

        snap = ws.receive_json()
        assert snap["type"] == "account_state"
        assert snap["seq"] == 1  # trading:local:orders counter, after gather
        assert snap["session_id"] == "srv-test"
        assert snap["data"]["mode"] == "paper"
        # Zero watermark: nothing has been broadcast yet in this session.
        assert snap["data"]["seq"] == {"orders": 0, "positions": 0, "balances": 0}

        ack = ws.receive_json()
        assert ack["type"] == "subscribed"
        assert ack["channels"] == ["orders", "positions"]


def test_resync_pushes_snapshot_on_requested_channel() -> None:
    set_channel_sequence(ChannelSequenceManager(session_id="srv-test"))
    app = create_app()
    client = TestClient(app)
    with client.websocket_connect("/api/v1/ws/trading") as ws:
        ws.send_json({"action": "subscribe", "channels": ["orders"]})
        ws.receive_json()  # account_state snapshot (seq=1)
        ws.receive_json()  # subscribed

        ws.send_json({"action": "resync", "channels": ["orders"]})
        snap = ws.receive_json()
        assert snap["type"] == "account_state"
        assert snap["seq"] == 2  # counter advanced by the resync snapshot

        ack = ws.receive_json()
        assert ack["type"] == "resynced"
        assert ack["channels"] == ["orders"]


def test_multi_channel_only_first_channel_gets_snapshot() -> None:
    seq_mgr = ChannelSequenceManager(session_id="srv-test")
    set_channel_sequence(seq_mgr)
    app = create_app()
    client = TestClient(app)
    with client.websocket_connect("/api/v1/ws/trading") as ws:
        ws.send_json({"action": "subscribe", "channels": ["orders", "positions"]})
        snap = ws.receive_json()
        assert snap["type"] == "account_state"
        ack = ws.receive_json()
        assert ack["type"] == "subscribed"

        # Only the first subscribed channel's counter advanced. The positions
        # channel received no snapshot — its first delta is the client's P3
        # baseline (no gap check), not a gap.
        assert seq_mgr.watermark(orders="local:orders") == AccountStateSeq(orders=1)
        assert seq_mgr.watermark(orders="local:positions") == AccountStateSeq()


def test_unknown_action_still_rejected() -> None:
    set_channel_sequence(ChannelSequenceManager(session_id="srv-test"))
    app = create_app()
    client = TestClient(app)
    with client.websocket_connect("/api/v1/ws/trading") as ws:
        ws.send_json({"action": "bogus"})
        resp = ws.receive_json()
        assert resp["type"] == "error"
        assert "Unknown action" in resp["message"]
