"""Bot push management routes.

Extracted pattern from routes_webhook. Routes:
  GET    /api/bot/status — bot pusher status
  POST   /api/bot/test   — send test signal to all configured chats
  PATCH  /api/bot/config — update enabled/chat_ids at runtime
"""
import logging

from flask import Blueprint, jsonify, request

from ._legacy import _app_state, require_auth

logger = logging.getLogger(__name__)

bp = Blueprint("bot", __name__, url_prefix="")


@bp.route("/api/bot/status", methods=["GET"])
@require_auth
def get_bot_status():
    """Return current bot pusher status."""
    bot_pusher = _app_state.bot_pusher
    if not bot_pusher:
        return jsonify({"enabled": False, "chat_ids": []})
    return jsonify(bot_pusher.get_status())


@bp.route("/api/bot/test", methods=["POST"])
@require_auth
def test_bot_push():
    """Send a test signal to all configured chat_ids."""
    bot_pusher = _app_state.bot_pusher
    if not bot_pusher:
        return jsonify({"error": "Bot pusher not initialized"}), 500
    result = bot_pusher.send_test()
    return jsonify(result)


@bp.route("/api/bot/config", methods=["PATCH"])
@require_auth
def update_bot_config():
    """Update bot pusher config at runtime (enabled, chat_ids)."""
    bot_pusher = _app_state.bot_pusher
    if not bot_pusher:
        return jsonify({"error": "Bot pusher not initialized"}), 500

    data = request.get_json(silent=True) or {}
    enabled = data.get("enabled")
    chat_ids = data.get("chat_ids")

    if enabled is not None and not isinstance(enabled, bool):
        return jsonify({"error": "enabled must be boolean"}), 400
    if chat_ids is not None and not isinstance(chat_ids, list):
        return jsonify({"error": "chat_ids must be a list"}), 400

    try:
        bot_pusher.update_config(
            enabled=enabled if enabled is not None else None,
            chat_ids=chat_ids if chat_ids is not None else None,
        )
    except Exception as e:
        logger.exception("Bot config update failed: %s", e)
        return jsonify({"error": str(e)}), 500

    return jsonify(bot_pusher.get_status())
