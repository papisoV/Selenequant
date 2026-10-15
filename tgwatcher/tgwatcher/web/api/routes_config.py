"""Config routes (Phase 2B batch 4).

Extracted from `._legacy`. Routes:
  GET   /config                                — get safe config snapshot
  PUT   /config/groups                         — replace groups list
  PATCH /config/groups/<chat_id>/auto_catchup  — toggle per-group auto_catchup
  PATCH /config/groups/<chat_id>/auto_listen   — toggle per-group auto_listen
"""
import logging
import os
from pathlib import Path

from flask import Blueprint, jsonify, request

from ._legacy import (
    _app_state,
    _atomic_write_config,
    _get_listen_groups,
    _listener_daemon,
    _start_listener_thread,
    _stop_listener,
    require_auth,
)

logger = logging.getLogger(__name__)

bp = Blueprint("config", __name__, url_prefix="")


@bp.route("/config", methods=["GET"])
@require_auth
def get_config():
    _config = _app_state.config
    safe_config = {}
    safe_config["groups"] = _config.get("groups", [])
    safe_config["crawl"] = _config.get("crawl", {})
    safe_config["proxy"] = {"enabled": _config.get("proxy", {}).get("enabled", False)}
    safe_config["storage"] = _config.get("storage", {})
    safe_config["telegram"] = {
        "phone": _config["telegram"]["phone"],
        "session_dir": _config["telegram"].get("session_dir", "./sessions"),
    }
    safe_config["web"] = _config.get("web", {})
    safe_config["catchup"] = _config.get("catchup", {"enabled": True, "limit": 1000})
    return jsonify(safe_config)


@bp.route("/config/groups", methods=["PUT"])
@require_auth
def update_groups():
    data = request.get_json(silent=True)
    if not data or "groups" not in data:
        return jsonify({"error": "Missing 'groups' in body"}), 400
    for g in data["groups"]:
        if not g.get("id") and not g.get("username"):
            return jsonify({"error": "Each group must have 'id' or 'username'"}), 400
    _config = _app_state.config
    # Preserve per-group flags from existing config for groups that still exist
    existing_map = {g.get("id"): g for g in _config.get("groups", [])}
    for g in data["groups"]:
        gid = g.get("id")
        if gid in existing_map:
            ex = existing_map[gid]
            g.setdefault("auto_catchup", ex.get("auto_catchup", False))
            g.setdefault("auto_poll", ex.get("auto_poll", False))
            g.setdefault("poll_interval_seconds", ex.get("poll_interval_seconds", 15))
            g.setdefault("auto_listen", ex.get("auto_listen", False))
    _config["groups"] = data["groups"]
    config_path = os.environ.get("TGWATCHER_CONFIG", str(Path.cwd() / "config.yaml"))
    _atomic_write_config(_config, config_path)
    return jsonify({"status": "updated", "groups": _config["groups"]})


@bp.route("/config/groups/<int:chat_id>/auto_catchup", methods=["PATCH"])
@require_auth
def toggle_group_auto_catchup(chat_id):
    data = request.get_json(silent=True) or {}
    auto_catchup = data.get("auto_catchup")
    if auto_catchup is None:
        return jsonify({"error": "Missing 'auto_catchup' in body"}), 400
    _config = _app_state.config
    groups = _config.get("groups", [])
    found = False
    for g in groups:
        if g.get("id") == chat_id:
            g["auto_catchup"] = bool(auto_catchup)
            found = True
            break
    if not found:
        return jsonify({"error": "Group not found in config"}), 404
    config_path = os.environ.get("TGWATCHER_CONFIG", str(Path.cwd() / "config.yaml"))
    _atomic_write_config(_config, config_path)
    return jsonify({"status": "updated", "chat_id": chat_id, "auto_catchup": bool(auto_catchup)})


@bp.route("/config/groups/<int:chat_id>/auto_listen", methods=["PATCH"])
@require_auth
def toggle_group_auto_listen(chat_id: int):
    """Toggle per-group auto_listen. If turning on and listener not running, start it.
    If turning off and no groups remain, stop the listener."""
    data = request.get_json(silent=True) or {}
    auto_listen = data.get("auto_listen")
    if auto_listen is None:
        return jsonify({"error": "Missing 'auto_listen' in body"}), 400
    _config = _app_state.config
    groups = _config.get("groups", [])
    found = False
    for g in groups:
        if g.get("id") == chat_id:
            g["auto_listen"] = bool(auto_listen)
            found = True
            break
    if not found:
        return jsonify({"error": "Group not found in config"}), 404
    config_path = os.environ.get("TGWATCHER_CONFIG", str(Path.cwd() / "config.yaml"))
    _atomic_write_config(_config, config_path)

    # Side-effect: start/stop listener based on remaining auto_listen groups
    listen_groups = _get_listen_groups(_config)
    if auto_listen and not _listener_daemon.is_running and listen_groups:
        _start_listener_thread(listen_groups)
    elif not auto_listen and not listen_groups and _listener_daemon.is_running:
        _stop_listener()

    return jsonify({"status": "updated", "chat_id": chat_id,
                    "auto_listen": bool(auto_listen),
                    "listener_running": _listener_daemon.is_running})
