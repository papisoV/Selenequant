"""Real-time listener control routes (Phase 2B batch 5).

Extracted from `._legacy`. Routes:
  GET   /listen/status — current listener state
  PATCH /listen/status — start/stop listener manually
"""
import logging

from flask import Blueprint, jsonify, request

from ._legacy import (
    _config,
    _get_listen_groups,
    _listener_daemon,
    _start_listener_thread,
    _stop_listener,
    require_auth,
)

logger = logging.getLogger(__name__)

bp = Blueprint("listen", __name__, url_prefix="")


@bp.route("/listen/status", methods=["GET"])
@require_auth
def get_listen_status():
    """Return current listener state: enabled (running), groups being listened to."""
    listen_groups = _get_listen_groups(_config)
    return jsonify({
        "enabled": _listener_daemon.is_running,
        "groups": [{"chat_id": g.get("id"), "name": g.get("name", g.get("id"))} for g in listen_groups],
    })


@bp.route("/listen/status", methods=["PATCH"])
@require_auth
def patch_listen_status():
    """Start or stop the listener manually.
    Body: {"enabled": true/false}. When starting, uses all groups with auto_listen=true.
    If no groups have auto_listen=true, returns 400."""
    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    if enabled is None:
        return jsonify({"error": "Missing 'enabled' in body"}), 400
    if enabled:
        listen_groups = _get_listen_groups(_config)
        if not listen_groups:
            return jsonify({"error": "No groups with auto_listen=true. Enable auto_listen on at least one group first."}), 400
        if _listener_daemon.is_running:
            return jsonify({"status": "already_running", "groups": [g.get("name") for g in listen_groups]})
        ok = _start_listener_thread(listen_groups)
        if not ok:
            return jsonify({"error": "Failed to start listener (check logs)"}), 500
        return jsonify({"status": "started", "groups": [g.get("name") for g in listen_groups]})
    else:
        if not _listener_daemon.is_running:
            return jsonify({"status": "already_stopped"})
        _stop_listener()
        return jsonify({"status": "stopping"})
