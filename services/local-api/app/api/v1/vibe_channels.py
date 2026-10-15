"""Vibe IM channels API (Phase 3.9).

Exposes the vendored 16 IM channels from the vibe bridge as REST endpoints.
All endpoints are env-gated by ``VIBE_CHANNELS_ENABLED`` — return 404 when
the feature is not explicitly opted in.

NOTE: send/send_alert are Phase 1 stubs that return ``{"sent": false}``.
Actual message delivery requires Phase 4 ChannelManager lifecycle integration.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

logger = logging.getLogger(__name__)

router = APIRouter(tags=["vibe-channels"])

_VIBE_CHANNELS_ENABLED = os.environ.get("VIBE_CHANNELS_ENABLED", "").lower() in ("1", "true", "yes")


def _check_enabled() -> None:
    """Raise 404 when VIBE_CHANNELS_ENABLED is not set to a truthy value."""
    if not _VIBE_CHANNELS_ENABLED:
        raise HTTPException(status_code=404, detail="Vibe channels not enabled. Set VIBE_CHANNELS_ENABLED=true.")


class SendRequest(BaseModel):
    message: str


# ---------------------------------------------------------------------------
# GET /api/v1/vibe/channels
# ---------------------------------------------------------------------------


@router.get("/vibe/channels")
async def list_channels():
    """List available vibe IM channels.

    Returns the channel names and a ``send_is_stub`` flag indicating whether
    send/send_alert are live (False in Phase 3.9 — actual delivery requires
    Phase 4 ChannelManager wiring).
    """
    _check_enabled()
    try:
        from subsystems.vibe.bridge.channels.preset import vibe_channels_preset

        descriptor = vibe_channels_preset()
        return {
            "channels": list(descriptor.channel_names),
            "send_is_stub": descriptor.send_is_stub,
        }
    except Exception as exc:
        logger.error("Failed to list vibe channels: %s", exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# POST /api/v1/vibe/channels/{channel}/send
# ---------------------------------------------------------------------------


@router.post("/vibe/channels/{channel}/send")
async def send_to_channel(channel: str, body: SendRequest):
    """Send a message to a vibe IM channel.

    Phase 3.9 stub: returns ``{"sent": false}`` because the vibe
    ChannelManager is not yet wired into the selene lifespan.
    Actual delivery is Phase 4 work.
    """
    _check_enabled()
    try:
        from subsystems.vibe.bridge.channels.preset import send_via_vibe

        sent = send_via_vibe(channel, body.message)
        return {"sent": sent}
    except Exception as exc:
        logger.error("Failed to send to channel '%s': %s", channel, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")


# ---------------------------------------------------------------------------
# POST /api/v1/vibe/channels/{channel}/alert
# ---------------------------------------------------------------------------


@router.post("/vibe/channels/{channel}/alert")
async def send_alert_to_channel(channel: str, body: SendRequest):
    """Send an alert (higher priority) to a vibe IM channel.

    Phase 3.9 stub: returns ``{"sent": false}`` because the vibe
    ChannelManager is not yet wired into the selene lifespan.
    Actual delivery is Phase 4 work.
    """
    _check_enabled()
    try:
        from subsystems.vibe.bridge.channels.preset import send_alert_via_vibe

        sent = send_alert_via_vibe(channel, body.message)
        return {"sent": sent}
    except Exception as exc:
        logger.error("Failed to send alert to channel '%s': %s", channel, exc, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal server error")
