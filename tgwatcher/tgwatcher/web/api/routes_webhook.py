"""Webhook management routes (Phase 2B batch 5).

Extracted from `._legacy`. Routes:
  GET  /webhook/config — webhook dispatcher status (no secrets)
  POST /webhook/test   — send test payload to enabled endpoints
"""
import logging

from flask import Blueprint, jsonify, request

from ._legacy import _app_state, require_auth

logger = logging.getLogger(__name__)

bp = Blueprint("webhook", __name__, url_prefix="")


@bp.route("/webhook/config", methods=["GET"])
@require_auth
def get_webhook_config():
    """Return current webhook dispatcher status (secrets never exposed)."""
    _webhook_dispatcher = _app_state.webhook_dispatcher
    if not _webhook_dispatcher:
        return jsonify({"enabled": False, "endpoints": []})
    return jsonify(_webhook_dispatcher.get_status())


@bp.route("/webhook/test", methods=["POST"])
@require_auth
def test_webhook():
    """Send a test payload to all enabled webhook endpoints (or a specific url)."""
    _webhook_dispatcher = _app_state.webhook_dispatcher
    if not _webhook_dispatcher:
        return jsonify({"error": "Webhook not initialized"}), 500
    data = request.get_json(silent=True) or {}
    target_url = data.get("url")
    result = _webhook_dispatcher.send_test(target_url)
    return jsonify(result)
