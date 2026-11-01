"""Crawl + data control routes (Phase 2B batch 4).

Extracted from `._legacy`. Routes:
  DELETE /config/groups/<chat_id>   — delete group + chat data
  POST   /data/purge                — purge all messages
  POST   /crawl/start               — start crawl (incremental/full/date_range/catchup)
  POST   /crawl/stop                — stop crawl + suspend auto-poll
  GET    /crawl/status              — crawl service status
  GET    /crawl/auto-poll           — per-group auto-poll state
  PATCH  /crawl/auto-poll/<chat_id> — update per-group auto-poll settings
"""
import logging
import os
import time
from pathlib import Path

from flask import Blueprint, jsonify, request

from ._legacy import (
    _app_state,
    _atomic_write_config,
    _auto_poll_lock,
    _auto_poll_state,
    _auto_poll_stop_requested,
    require_auth,
    push_sse_event,
)

logger = logging.getLogger(__name__)

bp = Blueprint("crawl", __name__, url_prefix="")


@bp.route("/config/groups/<int:chat_id>", methods=["DELETE"])
@require_auth
def delete_group(chat_id):
    # Remove from config if present
    _config = _app_state.config
    groups = _config.get("groups") or []
    new_groups = [g for g in groups if g.get("id") != chat_id]
    config_changed = len(new_groups) < len(groups)
    if config_changed:
        _config["groups"] = new_groups
        config_path = os.environ.get("TGWATCHER_CONFIG", str(Path.cwd() / "config.yaml"))
        _atomic_write_config(_config, config_path)
    # Always delete database data regardless of config state
    deleted = _app_state.storage.delete_chat_data(chat_id)
    if not config_changed and deleted == 0:
        return jsonify({"error": "Group not found"}), 404
    return jsonify({"status": "removed", "groups": _config["groups"], "messages_deleted": deleted})


@bp.route("/data/purge", methods=["POST"])
@require_auth
def purge_all_data():
    deleted = _app_state.storage.delete_all_data()
    return jsonify({"status": "purged", "messages_deleted": deleted})


# --- Crawl Control APIs ---

@bp.route("/crawl/start", methods=["POST"])
@require_auth
def start_crawl():
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "incremental")
    if mode not in ("incremental", "full", "date_range", "catchup"):
        return jsonify({"error": "Invalid mode. Use: incremental, full, date_range, catchup"}), 400
    extra: dict = {}
    if mode == "date_range":
        offset_date = data.get("offset_date")
        until_date = data.get("until_date")
        if not offset_date or not until_date:
            return jsonify({"error": "date_range mode requires offset_date and until_date"}), 400
        extra["offset_date"] = offset_date
        extra["until_date"] = until_date
    _config = _app_state.config
    ok = _app_state.crawl_service.start(mode=mode, **extra)
    if not ok:
        if mode == "catchup" and not [g for g in _config.get("groups", []) if g.get("auto_catchup", False)]:
            return jsonify({"error": "没有启用自动补爬的群组，请先在群组页面开启"}), 400
        return jsonify({"error": "Crawl already running"}), 409
    return jsonify({"status": "started", "mode": mode})


@bp.route("/crawl/stop", methods=["POST"])
@require_auth
def stop_crawl():
    ok = _app_state.crawl_service.stop()
    if not ok:
        return jsonify({"error": "No crawl running"}), 409
    # Signal auto-poll daemon to suspend triggering new crawls. Without this,
    # the daemon would fire the next tick (often within seconds) and restart
    # a crawl immediately after the current one stops — making the user's
    # "stop" feel ignored. Cleared when auto-poll is re-enabled via
    # update_auto_poll() or on fresh startup via _init_auto_poll().
    _auto_poll_stop_requested.set()
    logger.info("Stop requested by user — auto-poll daemon suspended until re-enabled")
    return jsonify({"status": "stopping"})


@bp.route("/crawl/status", methods=["GET"])
@require_auth
def crawl_status():
    return jsonify(_app_state.crawl_service.status)


@bp.route("/crawl/auto-poll", methods=["GET"])
@require_auth
def get_auto_poll():
    """Return per-group auto-poll state with countdown to next tick."""
    now = time.time()
    with _auto_poll_lock:
        result = []
        for cid, s in _auto_poll_state.items():
            result.append({
                "chat_id": cid,
                "name": s.get("name", str(cid)),
                "enabled": s["enabled"],
                "interval_seconds": s["interval"],
                "remaining_seconds": max(0, int(s["next_tick_at"] - now)) if s["enabled"] else None,
            })
    return jsonify(result)


@bp.route("/crawl/auto-poll/<int:chat_id>", methods=["PATCH"])
@require_auth
def update_auto_poll(chat_id: int):
    """Update per-group auto_poll settings and persist to config.yaml."""
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    interval = data.get("interval_seconds")
    if enabled is None and interval is None:
        return jsonify({"error": "Provide 'enabled' and/or 'interval_seconds'"}), 400

    _config = _app_state.config
    # Update in-memory config
    found = False
    for g in _config.get("groups", []):
        if g.get("id") == chat_id:
            found = True
            if enabled is not None:
                g["auto_poll"] = bool(enabled)
            if interval is not None:
                try:
                    iv = int(interval)
                except (TypeError, ValueError):
                    return jsonify({"error": "interval_seconds must be int"}), 400
                if iv < 5 or iv > 3600:
                    return jsonify({"error": "interval_seconds must be 5-3600"}), 400
                g["poll_interval_seconds"] = iv
            break
    if not found:
        return jsonify({"error": "Group not found in config"}), 404

    # Persist
    config_path = os.environ.get("TGWATCHER_CONFIG", str(Path.cwd() / "config.yaml"))
    _atomic_write_config(_config, config_path)

    # Update live state
    with _auto_poll_lock:
        s = _auto_poll_state.get(chat_id)
        if s is None:
            s = {"name": str(chat_id)}
            _auto_poll_state[chat_id] = s
        if enabled is not None:
            s["enabled"] = bool(enabled)
        if interval is not None:
            s["interval"] = iv
        s["next_tick_at"] = time.time() + s["interval"]
        s["name"] = next((g.get("name", str(chat_id)) for g in _config["groups"] if g.get("id") == chat_id), str(chat_id))

    # Re-enabling auto-poll clears any user-initiated stop signal so the
    # daemon resumes triggering ticks. This is the only path that clears
    # _auto_poll_stop_requested besides a fresh startup (_init_auto_poll).
    if enabled is True:
        _auto_poll_stop_requested.clear()
        logger.info("Auto-poll re-enabled by user — stop signal cleared, daemon resumed")

    push_sse_event("auto_poll_tick", {
        "chat_id": chat_id,
        "name": s.get("name"),
        "next_tick_at": s["next_tick_at"],
        "interval": s["interval"],
        "enabled": s["enabled"],
    })
    return jsonify({"status": "updated", "chat_id": chat_id,
                    "enabled": s["enabled"], "interval_seconds": s["interval"]})
