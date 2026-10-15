"""H9: API endpoints must not leak internal exception details in 5xx responses.

5xx errors return generic "Internal server error" detail while the full
exception (potentially containing DB paths, API key fragments, stack info)
is logged server-side via ``logger.error(..., exc_info=True)``.

4xx business errors (404 "not found", 400 "invalid weights") keep useful
client-facing messages.
"""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest
from app.main import create_app
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_500_error_no_internal_leak(monkeypatch, caplog):
    """5xx response must not contain internal exception details (H9)."""
    import app.api.v1.vibe_channels as mod

    monkeypatch.setattr(mod, "_VIBE_CHANNELS_ENABLED", True)

    sensitive = "DB path /secret/api_key_abc123"

    def _boom(channel: str, message: str) -> bool:
        raise RuntimeError(sensitive)

    app = create_app()
    transport = ASGITransport(app=app)
    with caplog.at_level(logging.ERROR, logger="app.api.v1.vibe_channels"), patch(
        "subsystems.vibe.bridge.channels.preset.send_via_vibe", side_effect=_boom
    ):
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            resp = await c.post(
                "/api/v1/vibe/channels/telegram/send",
                json={"message": "hi"},
            )

    assert resp.status_code == 500
    body = resp.json()
    detail = body.get("detail", "")
    # Generic message — no sensitive internal info leaked
    assert "Internal server error" in str(detail)
    assert sensitive not in str(detail)
    assert "/secret/" not in str(detail)
    assert "api_key_abc123" not in str(detail)
    # Server-side log captured the full exception
    assert any(
        sensitive in rec.getMessage() for rec in caplog.records
    ), "full exception must be logged server-side"


@pytest.mark.asyncio
async def test_404_error_keeps_useful_message():
    """4xx errors keep useful client-facing messages (e.g. 404 not found)."""
    from app.core.dependencies import get_decision_workflow_service

    class _StubService:
        async def get_record(self, record_id: str):
            return None  # triggers 404 "record 'X' not found" path

    app = create_app()
    app.dependency_overrides[get_decision_workflow_service] = lambda: _StubService()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        # decision_workflow GET /records/{record_id} returns 404 with
        # useful "record 'X' not found" message.
        resp = await c.get("/api/v1/decision-workflow/records/nonexistent-id-xyz")

    assert resp.status_code == 404
    body = resp.json()
    detail = str(body.get("detail", ""))
    assert (
        "nonexistent-id-xyz" in detail or "not found" in detail.lower()
    ), "404 must keep useful 'not found' client message"
