"""Notification endpoints — channel management, event types, and preferences.

渠道持久化到 ~/.selenespace/notification_channels.json；test 端点真发
（webhook 五方言 + telegram，见 app.core.notify_channels）。
"""

from __future__ import annotations

import json
import logging
import time
import uuid

import httpx
from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import build_response_meta
from app.core.notify_channels import (
    channels_path,
    load_channels,
    save_channels,
    send_channel_test,
)
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, NotFoundError, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/notifications", tags=["notifications"])

# Static event type catalog
_EVENT_TYPES: list[str] = [
    "order_filled",
    "position_opened",
    "position_closed",
    "kill_switch_activated",
    "kill_switch_released",
    "strategy_signal",
    "risk_alert",
    "smart_money_alert",
    "price_alert",
    # A4: user-created rules. Distinct from ``price_alert`` (the PA one-shot
    # trade alerts) so a user can mute one without muting the other.
    "alert_rule_hit",
]


def _find_channel(channel_id: str) -> dict | None:
    return next((c for c in load_channels() if c.get("id") == channel_id), None)


def _upsert_channel(channel: dict) -> None:
    channels = [c for c in load_channels() if c.get("id") != channel["id"]]
    channels.append(channel)
    save_channels(channels)


def _persist_update(channel_id: str, mutate) -> dict:
    channels = load_channels()
    for ch in channels:
        if ch.get("id") == channel_id:
            mutate(ch)
            save_channels(channels)
            return ch
    raise NotFoundError(f"Channel '{channel_id}' not found")


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class CreateChannelRequest(BaseModel):
    type: str = Field(
        ...,
        min_length=1,
        description="Channel type, e.g. 'webhook', 'telegram', 'email'",
    )
    name: str = Field(..., min_length=1, description="Human-readable channel name")
    config: dict = Field(
        default_factory=dict, description="Channel-specific configuration"
    )


class UpdateChannelRequest(BaseModel):
    name: str | None = Field(None, description="Updated channel name")
    config: dict | None = Field(None, description="Updated channel configuration")
    enabled: bool | None = Field(None, description="Enable or disable the channel")


class UpdatePreferencesRequest(BaseModel):
    items: list[dict] = Field(
        ...,
        description="List of {event_type, channel_ids} preference entries",
    )


# ---------------------------------------------------------------------------
# Channel endpoints
# ---------------------------------------------------------------------------


@router.get("/channels")
async def list_channels(
    request: Request,
) -> APIResponse[dict]:
    """Return all configured notification channels (secrets masked)."""
    channels = []
    for ch in load_channels():
        cfg = dict(ch.get("config") or {})
        for key in ("url", "bot_token", "secret"):
            if key in cfg and cfg[key]:
                cfg[key] = "***"
        channels.append({**ch, "config": cfg})
    data = {"channels": channels}
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.post("/channels")
async def create_channel(
    request: Request,
    body: CreateChannelRequest,
) -> APIResponse[dict]:
    """Create a new notification channel."""
    channel_id = str(uuid.uuid4())
    channel = {
        "id": channel_id,
        "type": body.type,
        "name": body.name,
        "config": body.config,
        "enabled": True,
        "created_at": int(time.time() * 1000),
    }
    _upsert_channel(channel)

    logger.info("Notification channel created: %s (%s)", body.name, body.type)

    return APIResponse(data=channel, meta=build_response_meta(request), error=None)


@router.patch("/channels/{channel_id}")
async def update_channel(
    request: Request,
    channel_id: str,
    body: UpdateChannelRequest,
) -> APIResponse[dict]:
    """Update an existing notification channel."""

    def _apply(ch: dict) -> None:
        if body.name is not None:
            ch["name"] = body.name
        if body.config is not None:
            ch["config"] = body.config
        if body.enabled is not None:
            ch["enabled"] = body.enabled

    channel = _persist_update(channel_id, _apply)

    logger.info("Notification channel updated: %s", channel_id)

    return APIResponse(data=channel, meta=build_response_meta(request), error=None)


@router.delete("/channels/{channel_id}")
async def delete_channel(
    request: Request,
    channel_id: str,
    mode: str = Query("soft", description="Deletion mode: 'soft' or 'hard'"),
) -> APIResponse[dict]:
    """Delete a notification channel."""
    if _find_channel(channel_id) is None:
        raise NotFoundError(f"Channel '{channel_id}' not found")

    if mode == "hard":
        save_channels([c for c in load_channels() if c.get("id") != channel_id])
        logger.info("Notification channel HARD deleted: %s", channel_id)
    else:

        def _soft_delete(ch: dict) -> None:
            ch["enabled"] = False
            ch["deleted"] = True

        _persist_update(channel_id, _soft_delete)
        logger.info("Notification channel SOFT deleted: %s", channel_id)

    data = {"deleted": True, "mode": mode}
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.post("/channels/{channel_id}/test")
async def test_channel(
    request: Request,
    channel_id: str,
) -> APIResponse[dict]:
    """Send a test notification through the specified channel (real send)."""
    channel = _find_channel(channel_id)
    if channel is None:
        raise NotFoundError(f"Channel '{channel_id}' not found")

    if not channel.get("enabled", True):
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message="Channel is disabled — enable it before testing",
        )

    from selene_space_core.resilience import OutboundGateway

    client = OutboundGateway(
        default_timeout=httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0),
        default_circuit=False,
    )
    try:
        status = await send_channel_test(client, channel)
    finally:
        await client.aclose()

    ok = status == "ok"
    if not ok:
        logger.warning("Channel test failed for %s: %s", channel_id, status)
    data = {
        "tested": ok,
        "channel_id": channel_id,
        "status": status,
        "message": "Test notification sent" if ok else f"Send failed: {status}",
    }
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


# ---------------------------------------------------------------------------
# Event type endpoints
# ---------------------------------------------------------------------------


@router.get("/event-types")
async def list_event_types(
    request: Request,
) -> APIResponse[dict]:
    """Return the static list of supported notification event types."""
    data = {"event_types": _EVENT_TYPES, "total": len(_EVENT_TYPES)}
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


# ---------------------------------------------------------------------------
# Preference endpoints
# ---------------------------------------------------------------------------


@router.get("/preferences")
async def get_preferences(
    request: Request,
) -> APIResponse[dict]:
    """Return current notification preferences mapping event types to channels."""
    prefs_file = channels_path().parent / "notification_preferences.json"
    preferences: dict[str, list[str]] = {}
    if prefs_file.exists():
        try:
            preferences = json.loads(prefs_file.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            logger.warning("Failed to read notification preferences file")
    data = {"preferences": preferences}
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.put("/preferences")
async def update_preferences(
    request: Request,
    body: UpdatePreferencesRequest,
) -> APIResponse[dict]:
    """Update notification preferences — map event types to channel IDs."""
    known_ids = {c.get("id") for c in load_channels()}
    for item in body.items:
        event_type = item.get("event_type")
        channel_ids = item.get("channel_ids", [])
        if not event_type:
            raise SeleneSpaceError(
                code=ErrorCode.VALIDATION_ERROR,
                message="Each preference item must include 'event_type'",
            )
        for cid in channel_ids:
            if cid not in known_ids:
                raise SeleneSpaceError(
                    code=ErrorCode.NOT_FOUND,
                    message=f"Channel '{cid}' not found",
                )

    prefs_file = channels_path().parent / "notification_preferences.json"
    preferences: dict[str, list[str]] = {}
    if prefs_file.exists():
        try:
            preferences = json.loads(prefs_file.read_text(encoding="utf-8-sig"))
        except Exception:  # noqa: BLE001
            logger.warning("Failed to read notification preferences file")
    for item in body.items:
        preferences[item["event_type"]] = item.get("channel_ids", [])
    prefs_file.parent.mkdir(parents=True, exist_ok=True)
    prefs_file.write_text(
        json.dumps(preferences, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    logger.info(
        "Notification preferences updated: %d event types mapped", len(preferences)
    )

    data = {"updated": True, "preferences": preferences}
    return APIResponse(data=data, meta=build_response_meta(request), error=None)
