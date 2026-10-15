"""Tests for GET /api/v1/decision-workflow/heartbeat/status (T1 小-1).

Covers the two branches the PA heartbeat gate can be in:
  - 关态: SELENE_PA_HEARTBEAT unset → enabled:false, fields at defaults
  - 开态: gate on + scheduler present (stub) → response mirrors status()
"""

from __future__ import annotations

import time

import app.api.v1.decision_workflow as dw_module
import pytest
from app.core.pa_heartbeat import PaHeartbeatConfig, PaHeartbeatScheduler
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_heartbeat_status_disabled_by_default(monkeypatch):
    """Gate unset → endpoint reports enabled:false without a scheduler."""
    monkeypatch.delenv("SELENE_PA_HEARTBEAT", raising=False)
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/decision-workflow/heartbeat/status")
    assert resp.status_code == 200
    assert resp.json() == {
        "enabled": False,
        "last_round": None,
        "attempts_today": 0,
        "budget_day": None,
        "busy": False,
        "next_fire_at": None,
        "config": None,
    }


class _StubScheduler:
    """Duck-typed PaHeartbeatScheduler.status() for the 开态 branch."""

    def status(self) -> dict:
        return {
            "last_round": {"day": "2026-09-13", "n": 2},
            "attempts_today": 3,
            "budget_day": "2026-09-13",
            "busy": True,
            "next_fire_at": 1_900_000_000.0,
            "config": {
                "hour_utc": 2,
                "daily_max": 30,
                "interval": "1h",
                "htf_interval": None,
            },
        }


@pytest.mark.asyncio
async def test_heartbeat_status_enabled_projects_status(monkeypatch):
    """Gate on + scheduler present → response mirrors the status() projection."""
    monkeypatch.setattr(dw_module, "heartbeat_enabled", lambda: True)
    app = create_app()
    app.state._pa_heartbeat = _StubScheduler()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/decision-workflow/heartbeat/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["enabled"] is True
    assert body["busy"] is True
    assert body["attempts_today"] == 3
    assert body["budget_day"] == "2026-09-13"
    assert body["last_round"] == {"day": "2026-09-13", "n": 2}
    assert body["next_fire_at"] == 1_900_000_000.0
    assert body["config"] == {
        "hour_utc": 2,
        "daily_max": 30,
        "interval": "1h",
        "htf_interval": None,
    }


@pytest.mark.asyncio
async def test_heartbeat_status_next_fire_at_is_in_the_future(monkeypatch):
    """Real scheduler (not a stub): next_fire_at must be a future epoch second.

    Guards the honest-derivation rule — a value derived from the tick period or
    the staleness tolerance could land in the past, which is what the frontend
    would then render as a countdown that never fires.

    The trigger hour is pinned three hours ahead of the current UTC hour so the
    window is closed, hence strictly in the future, whenever this runs.
    """
    monkeypatch.setattr(dw_module, "heartbeat_enabled", lambda: True)
    app = create_app()
    app.state._pa_heartbeat = PaHeartbeatScheduler(
        lambda: None,
        lambda: None,
        config=PaHeartbeatConfig(hour_utc=(time.gmtime().tm_hour + 3) % 24),
    )
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/decision-workflow/heartbeat/status")
    assert resp.status_code == 200
    next_fire_at = resp.json()["next_fire_at"]
    assert isinstance(next_fire_at, (int, float))
    assert next_fire_at > time.time()
    # 一天之内必到(每天触发一次,不存在跨多日等待)
    assert next_fire_at <= time.time() + 86_400 + 60
