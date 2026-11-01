"""Tests for Phase 3.9: Vibe IM channels API endpoints.

Verifies:
- 404 when VIBE_CHANNELS_ENABLED is not set
- 200 with channels list when enabled
- send/send_alert endpoints return {"sent": false} (Phase 3.9 stubs)
- Fault tolerance: import failures don't crash the API
Pattern follows test_vibe_mcp_autoenable.py and existing selene API test patterns.
"""

from __future__ import annotations

import importlib
import os
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _reload_router():
    """Reload the vibe_channels router so env var changes take effect."""
    import app.api.v1.vibe_channels as vc

    return importlib.reload(vc)


# ---------------------------------------------------------------------------
# Test 1: env gating -- 404 when disabled
# ---------------------------------------------------------------------------


class TestEnvGating:
    @pytest.mark.asyncio
    async def test_list_channels_404_when_disabled(self):
        """GET /api/v1/vibe/channels returns 404 when VIBE_CHANNELS_ENABLED is not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("VIBE_CHANNELS_ENABLED", None)
            _reload_router()
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/vibe/channels")
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_send_404_when_disabled(self):
        """POST /api/v1/vibe/channels/telegram/send returns 404 when disabled."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("VIBE_CHANNELS_ENABLED", None)
            _reload_router()
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/vibe/channels/telegram/send",
                    json={"message": "hello"},
                )
            assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_alert_404_when_disabled(self):
        """POST /api/v1/vibe/channels/telegram/alert returns 404 when disabled."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("VIBE_CHANNELS_ENABLED", None)
            _reload_router()
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/vibe/channels/telegram/alert",
                    json={"message": "urgent"},
                )
            assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Test 2: 200 when enabled
# ---------------------------------------------------------------------------


class TestListChannelsEnabled:
    @pytest.mark.asyncio
    async def test_list_channels_returns_200(self):
        """GET /api/v1/vibe/channels returns 200 with channels when enabled."""
        with patch.dict(os.environ, {"VIBE_CHANNELS_ENABLED": "true"}):
            _reload_router()
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/vibe/channels")
            assert resp.status_code == 200
            data = resp.json()
            assert "channels" in data
            assert isinstance(data["channels"], list)
            assert len(data["channels"]) >= 16, f"expected >=16 channels, got {len(data['channels'])}"
            assert data["send_is_stub"] is True

    @pytest.mark.asyncio
    async def test_list_channels_contains_known_channels(self):
        """Known vibe channels (telegram, discord, slack) are in the list."""
        with patch.dict(os.environ, {"VIBE_CHANNELS_ENABLED": "true"}):
            _reload_router()
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/vibe/channels")
            data = resp.json()
            for expected in ("telegram", "discord", "slack"):
                assert expected in data["channels"], f"missing channel: {expected}"


# ---------------------------------------------------------------------------
# Test 3: send/send_alert are Phase 3.9 stubs (return False)
# ---------------------------------------------------------------------------


class TestSendEndpoints:
    @pytest.mark.asyncio
    async def test_send_returns_false(self):
        """POST /api/v1/vibe/channels/telegram/send returns {"sent": false} (stub)."""
        with patch.dict(os.environ, {"VIBE_CHANNELS_ENABLED": "true"}):
            _reload_router()
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/vibe/channels/telegram/send",
                    json={"message": "hello world"},
                )
            assert resp.status_code == 200
            data = resp.json()
            assert "sent" in data
            assert data["sent"] is False, "send_is_stub=True means sent is always False"

    @pytest.mark.asyncio
    async def test_alert_returns_false(self):
        """POST /api/v1/vibe/channels/telegram/alert returns {"sent": false} (stub)."""
        with patch.dict(os.environ, {"VIBE_CHANNELS_ENABLED": "true"}):
            _reload_router()
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/vibe/channels/telegram/alert",
                    json={"message": "urgent alert"},
                )
            assert resp.status_code == 200
            data = resp.json()
            assert "sent" in data
            assert data["sent"] is False, "send_is_stub=True means sent is always False"

    @pytest.mark.asyncio
    async def test_send_requires_message_body(self):
        """POST /api/v1/vibe/channels/telegram/send with empty body returns 422."""
        with patch.dict(os.environ, {"VIBE_CHANNELS_ENABLED": "true"}):
            _reload_router()
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.post(
                    "/api/v1/vibe/channels/telegram/send",
                    json={},
                )
            assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Test 4: case-insensitive env parsing
# ---------------------------------------------------------------------------


class TestCaseInsensitiveEnv:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("value", ["true", "TRUE", "True", "Yes", "YES"])
    async def test_case_insensitive_list_channel_access(self, value: str):
        """VIBE_CHANNELS_ENABLED parsing is case-insensitive."""
        with patch.dict(os.environ, {"VIBE_CHANNELS_ENABLED": value}):
            _reload_router()
            app = create_app()
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/api/v1/vibe/channels")
            assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Test 5: fault tolerance
# ---------------------------------------------------------------------------


class TestFaultTolerance:
    @pytest.mark.asyncio
    async def test_list_channels_500_when_bridge_fails(self):
        """When vibe_channels_preset raises, the endpoint returns 500."""
        with patch.dict(os.environ, {"VIBE_CHANNELS_ENABLED": "true"}):
            _reload_router()
            with patch(
                "subsystems.vibe.bridge.channels.preset.vibe_channels_preset",
                side_effect=RuntimeError("simulated bridge failure"),
            ):
                app = create_app()
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.get("/api/v1/vibe/channels")
                assert resp.status_code == 500

    @pytest.mark.asyncio
    async def test_send_500_when_send_via_vibe_fails(self):
        """When send_via_vibe raises, the endpoint returns 500."""
        with patch.dict(os.environ, {"VIBE_CHANNELS_ENABLED": "true"}):
            _reload_router()
            with patch(
                "subsystems.vibe.bridge.channels.preset.send_via_vibe",
                side_effect=RuntimeError("simulated send failure"),
            ):
                app = create_app()
                transport = ASGITransport(app=app)
                async with AsyncClient(transport=transport, base_url="http://test") as client:
                    resp = await client.post(
                        "/api/v1/vibe/channels/telegram/send",
                        json={"message": "test"},
                    )
                assert resp.status_code == 500
