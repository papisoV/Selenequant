"""Event -> channel notification dispatcher.

Single delivery path for out-of-band alerts (kill switch, price, grid, fills).
The settings page is the source of truth:

  ~/.selenespace/notification_channels.json     channel credentials
  ~/.selenespace/notification_preferences.json  event_type -> [channel_id]

``dispatch(event_type, text)`` resolves the subscribed channels, converts them
to pulse ``NotificationTarget``s, and sends through ``selene_space_pulse`` (the
transport layer: 12 channels, SSRF guard, vendor dialect adaptation).

Fallback contract: when no enabled channel is subscribed to the event, delivery
falls back to the pulse env-var targets (TELEGRAM_BOT_TOKEN / NOTIFY_WEBHOOK).
That keeps a mis-migrated or not-yet-configured install from silently losing
kill-switch alerts. Delivery never raises into the caller.
"""

from __future__ import annotations

import json
import logging

from app.core.notify_channels import channels_path, load_channels

logger = logging.getLogger(__name__)

# Settings-page channel type -> pulse target type. Two settings-page aliases
# collapse onto one pulse type (serverchan); unknown types pass through so a
# newly added channel still reaches the transport layer.
_PULSE_TARGET_TYPE = {
    "webhook": "webhook",
    "telegram": "telegram",
    "dingtalk": "dingtalk",
    "serverchan3": "serverchan3",
    "serverchan": "serverchan3",
    "pushplus": "pushplus",
    "ntfy": "ntfy",
    "gotify": "gotify",
    "pushover": "pushover",
}


def _load_preferences() -> dict[str, list[str]]:
    """Read the event_type -> channel_ids subscription map (never raises)."""
    prefs_file = channels_path().parent / "notification_preferences.json"
    if not prefs_file.exists():
        return {}
    try:
        loaded = json.loads(prefs_file.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        logger.warning("Failed to read notification preferences", exc_info=True)
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        str(k): [str(c) for c in v]
        for k, v in loaded.items()
        if isinstance(v, list)
    }


def _resolve_targets(event_type: str):
    """Return the pulse NotificationTargets subscribed to *event_type*.

    Unknown channel ids are skipped with a warning (the channel may have been
    hard-deleted after the subscription was written).
    """
    from selene_space_types.stock import NotificationTarget

    prefs = _load_preferences()
    channel_ids = prefs.get(event_type) or []
    if not channel_ids:
        return []

    by_id = {c.get("id"): c for c in load_channels()}
    targets = []
    for cid in channel_ids:
        channel = by_id.get(cid)
        if channel is None:
            logger.warning(
                "Notification subscription references unknown channel %s", cid
            )
            continue
        if not channel.get("enabled", True) or channel.get("deleted"):
            continue
        ch_type = str(channel.get("type", "webhook"))
        targets.append(
            NotificationTarget(
                type=_PULSE_TARGET_TYPE.get(ch_type, ch_type),
                name=str(channel.get("name") or channel.get("id") or ch_type),
                enabled=True,
                settings=dict(channel.get("config") or {}),
            )
        )
    return targets


def _load_quiet_hours() -> str | None:
    """Read the quiet_hours field from notification_preferences.json (never raises)."""
    prefs_file = channels_path().parent / "notification_preferences.json"
    if not prefs_file.exists():
        return None
    try:
        loaded = json.loads(prefs_file.read_text(encoding="utf-8-sig"))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(loaded, dict):
        return None
    value = loaded.get("quiet_hours")
    return str(value) if value else None


def _build_noise():
    """Build the pulse noise gate, or None when construction fails (fail-open)."""
    from selene_space_pulse.noise import NoiseStore, NotificationNoise, resolve_noise_db_path

    try:
        return NotificationNoise(
            NoiseStore(resolve_noise_db_path()), _load_quiet_hours()
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "Noise guard construction failed; delivering without suppression",
            exc_info=True,
        )
        return None


def dispatch(event_type: str, text: str) -> list[str]:
    """Deliver *text* for *event_type* to its subscribed channels.

    Returns per-target delivery status strings. Never raises — an alert path
    must not break the caller's control flow (e.g. kill-switch activation).
    """
    try:
        targets = _resolve_targets(event_type)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Notification dispatch resolution failed for %s", event_type, exc_info=True
        )
        targets = []

    try:
        if targets:
            from selene_space_pulse.notify import send_to_targets

            try:
                noise = _build_noise()
            except Exception:  # noqa: BLE001  fail-open: broken guard never blocks
                noise = None
            return send_to_targets(targets, text, noise=noise)

        # No subscribed channel — fall back to env-configured targets so a
        # not-yet-configured install still receives alerts.
        from selene_space_pulse.notify import send_trade_alert

        return send_trade_alert(text)
    except Exception:  # noqa: BLE001
        logger.warning(
            "Notification dispatch failed for %s", event_type, exc_info=True
        )
        return []


__all__ = ["dispatch"]
