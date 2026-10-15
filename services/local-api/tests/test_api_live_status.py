"""Tests for /api/v1/live/status endpoint (Phase 5 observability).

Verifies the endpoint returns all 7 observability fields:
  - ws_health (is_healthy, heartbeat_age_s, reconnect_count_1h, reason)
  - wal_lag (int)
  - killswitch_state (is_active, reason, activated_at)
  - reconciliation_diff_count (int)
  - shadow_mode_diff (int, Phase 5.5 placeholder = 0)
  - daily_pnl (None — Phase 2a FillsLedger pending)
  - peak_equity (None — Phase 2a EquityTracker pending)
"""

from __future__ import annotations

import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_live_status_returns_ws_health_field():
    """GET /api/v1/live/status returns ws_health with all 4 sub-fields."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/live/status")
    assert resp.status_code == 200
    body = resp.json()
    ws_health = body["data"]["ws_health"]
    assert "is_healthy" in ws_health
    assert "heartbeat_age_s" in ws_health
    assert "reconnect_count_1h" in ws_health
    assert "reason" in ws_health


@pytest.mark.asyncio
async def test_live_status_returns_wal_lag_int():
    """GET /api/v1/live/status returns wal_lag as int."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/live/status")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["data"]["wal_lag"], int)


@pytest.mark.asyncio
async def test_live_status_returns_killswitch_state():
    """GET /api/v1/live/status returns killswitch_state with 3 sub-fields."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/live/status")
    assert resp.status_code == 200
    body = resp.json()
    ks = body["data"]["killswitch_state"]
    assert "is_active" in ks
    assert "reason" in ks
    assert "activated_at" in ks


@pytest.mark.asyncio
async def test_live_status_returns_reconciliation_diff_count():
    """GET /api/v1/live/status returns reconciliation_diff_count as int."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/live/status")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["data"]["reconciliation_diff_count"], int)


@pytest.mark.asyncio
async def test_live_status_daily_pnl_none_when_phase2a_pending():
    """daily_pnl and peak_equity are None — Phase 2a FillsLedger/EquityTracker not yet built."""
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/live/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["daily_pnl"] is None
    assert body["data"]["peak_equity"] is None
    assert body["data"]["shadow_mode_diff"] == 0  # Phase 5.5 placeholder


@pytest.mark.asyncio
async def test_live_status_returns_audit_chain_health():
    """C1 observability: audit_chain_health present with 4 sub-fields."""
    from app.api.v1.live import _build_audit_chain_health

    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/live/status")
    assert resp.status_code == 200
    body = resp.json()["data"]
    a = body["audit_chain_health"]
    assert isinstance(a, dict)
    assert set(a.keys()) == {"available", "entries", "chain_ok", "latest_action"}
    assert isinstance(a["entries"], int)

    # helper degrades gracefully when nothing fails: available reflects ledger read
    health = _build_audit_chain_health()
    assert isinstance(health["entries"], int)
    assert isinstance(health["chain_ok"], bool)
