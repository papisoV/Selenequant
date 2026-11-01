"""§7.4 smoke tests — the subset provable with the real WS transport offline.

The full §7.4 manual smoke needs a live server: real market data (fills come
from the paper engine matching against market prices) and a process restart
(session_id is a fresh uuid4 per lifespan). This file automates the parts that
hold with the real /api/v1/ws/trading transport but no external network:

- subscribe -> account_state snapshot pushed
- disconnect -> reconnect -> snapshot re-pushed; the per-channel counter is
  process-global, so its seq keeps advancing (no per-connection reset) while
  session_id stays stable (same process, same session).

Still manual after this (documented in the QA notes):
  1. a real paper order filling and advancing seq — the bridge is exercised
     by async unit tests (test_ws_seq_resync.py) with a fake engine, and the
     real-transport snapshot/resync path by test_ws_account_state.py; the
     combined real-engine + real-transport path needs live price data.
  2. a server restart changing session_id and rebuilding the paper ledger.
"""

from __future__ import annotations

import pytest
import app.api.v1.ws as ws_mod
from app.core.sequence import ChannelSequenceManager, set_channel_sequence
from app.main import create_app
from starlette.testclient import TestClient


@pytest.fixture(autouse=True)
def _reset_state():
    ws_mod._trading_manager = ws_mod.ConnectionManager()
    ws_mod._paper_manager = ws_mod.ConnectionManager()
    ws_mod._ip_connection_counts.clear()
    set_channel_sequence(None)
    yield


def test_reconnect_repushes_snapshot_seq_advances() -> None:
    """§7.4: disconnect/reconnect re-pushes a snapshot.

    The counter is process-global (single-writer ``ChannelSequenceManager``),
    not per-connection, so the reconnect snapshot's seq continues from the
    first connection's (1 -> 2), and session_id is unchanged.
    """
    set_channel_sequence(ChannelSequenceManager(session_id="srv-test"))
    app = create_app()
    client = TestClient(app)

    with client.websocket_connect("/api/v1/ws/trading") as ws:
        ws.send_json({"action": "subscribe", "channels": ["orders"]})
        snap1 = ws.receive_json()
        assert snap1["type"] == "account_state"
        assert snap1["seq"] == 1
        assert snap1["session_id"] == "srv-test"
        ws.receive_json()  # subscribed

    # Reconnect in the same process: snapshot re-pushed, seq continued.
    with client.websocket_connect("/api/v1/ws/trading") as ws2:
        ws2.send_json({"action": "subscribe", "channels": ["orders"]})
        snap2 = ws2.receive_json()
        assert snap2["type"] == "account_state"
        assert snap2["seq"] == 2  # continued, not reset to 1
        assert snap2["session_id"] == "srv-test"
        ws2.receive_json()  # subscribed


def test_reconnect_resync_keeps_seq_advancing() -> None:
    """§7.4: reconnect + resync on a fresh connection still advances the
    same process-global counter (3 snapshots: conn1, conn2, resync)."""
    set_channel_sequence(ChannelSequenceManager(session_id="srv-test"))
    app = create_app()
    client = TestClient(app)

    with client.websocket_connect("/api/v1/ws/trading") as ws:
        ws.send_json({"action": "subscribe", "channels": ["orders"]})
        assert ws.receive_json()["seq"] == 1  # snapshot
        ws.receive_json()  # subscribed

    with client.websocket_connect("/api/v1/ws/trading") as ws2:
        ws2.send_json({"action": "subscribe", "channels": ["orders"]})
        assert ws2.receive_json()["seq"] == 2  # reconnect snapshot
        ws2.receive_json()  # subscribed

        # A gap-driven resync on the same connection pushes seq 3.
        ws2.send_json({"action": "resync", "channels": ["orders"]})
        snap = ws2.receive_json()
        assert snap["type"] == "account_state"
        assert snap["seq"] == 3
        ws2.receive_json()  # resynced
