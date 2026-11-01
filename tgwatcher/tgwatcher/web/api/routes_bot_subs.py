"""Bot subscription CRUD routes.

Manage Telegram chat subscriptions for signal push.
Routes:
  GET    /api/bot/subscriptions         — list all subscriptions
  POST   /api/bot/subscriptions         — create a subscription
  PATCH  /api/bot/subscriptions/<id>     — update subscription
  DELETE /api/bot/subscriptions/<id>     — delete subscription
"""
import json
import logging

from datetime import datetime
from flask import Blueprint, jsonify, request

from tgwatcher.models import BotSubscription
from ._legacy import _app_state, require_auth

logger = logging.getLogger(__name__)

bp = Blueprint("bot_subscriptions", __name__, url_prefix="")


def _parse_expires_at(value) -> datetime | None:
    """Parse expires_at from ISO string or None."""
    if value is None:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except (ValueError, TypeError):
        return None


@bp.route("/api/bot/subscriptions", methods=["GET"])
@require_auth
def list_subscriptions():
    """Return all bot subscriptions."""
    storage = _app_state.storage
    if not storage:
        return jsonify({"error": "Storage not initialized"}), 500

    with storage.get_session() as sess:
        subs = sess.query(BotSubscription).order_by(BotSubscription.id).all()
        return jsonify([
            {
                "id": s.id,
                "chat_id": s.chat_id,
                "enabled": s.enabled,
                "min_score": s.min_score,
                "event_types": json.loads(s.event_types) if s.event_types else None,
                "plan_id": s.plan_id,
                "status": s.status,
                "expires_at": s.expires_at.isoformat() if s.expires_at else None,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "updated_at": s.updated_at.isoformat() if s.updated_at else None,
            }
            for s in subs
        ])


@bp.route("/api/bot/subscriptions", methods=["POST"])
@require_auth
def create_subscription():
    """Create a new bot subscription."""
    storage = _app_state.storage
    if not storage:
        return jsonify({"error": "Storage not initialized"}), 500

    data = request.get_json(silent=True) or {}
    chat_id = data.get("chat_id")
    if chat_id is None:
        return jsonify({"error": "chat_id is required"}), 400

    try:
        chat_id = int(chat_id)
    except (ValueError, TypeError):
        return jsonify({"error": "chat_id must be an integer"}), 400

    min_score = float(data.get("min_score", 0.0))
    event_types = data.get("event_types")  # list or None
    if event_types is not None:
        if not isinstance(event_types, list):
            return jsonify({"error": "event_types must be a list"}), 400
        event_types_json = json.dumps(event_types)
    else:
        event_types_json = None

    sub = BotSubscription(
        chat_id=chat_id,
        enabled=bool(data.get("enabled", True)),
        min_score=min_score,
        event_types=event_types_json,
        plan_id=int(data["plan_id"]) if data.get("plan_id") else None,
        status=str(data.get("status", "active")),
        expires_at=_parse_expires_at(data.get("expires_at")),
    )

    try:
        with storage.get_session() as sess:
            sess.add(sub)
            sess.commit()
            sess.refresh(sub)
            return jsonify({
                "id": sub.id,
                "chat_id": sub.chat_id,
                "enabled": sub.enabled,
                "min_score": sub.min_score,
                "event_types": json.loads(sub.event_types) if sub.event_types else None,
                "plan_id": sub.plan_id,
                "status": sub.status,
                "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
            }), 201
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            return jsonify({"error": f"chat_id {chat_id} already subscribed"}), 409
        logger.exception("Create subscription failed: %s", e)
        return jsonify({"error": str(e)}), 500


@bp.route("/api/bot/subscriptions/<int:sub_id>", methods=["PATCH"])
@require_auth
def update_subscription(sub_id: int):
    """Update a bot subscription."""
    storage = _app_state.storage
    if not storage:
        return jsonify({"error": "Storage not initialized"}), 500

    data = request.get_json(silent=True) or {}

    with storage.get_session() as sess:
        sub = sess.query(BotSubscription).get(sub_id)
        if not sub:
            return jsonify({"error": "Subscription not found"}), 404

        if "enabled" in data:
            sub.enabled = bool(data["enabled"])
        if "min_score" in data:
            sub.min_score = float(data["min_score"])
        if "event_types" in data:
            et = data["event_types"]
            sub.event_types = json.dumps(et) if et is not None else None
        if "plan_id" in data:
            sub.plan_id = int(data["plan_id"]) if data["plan_id"] else None
        if "status" in data:
            sub.status = str(data["status"])
        if "expires_at" in data:
            sub.expires_at = _parse_expires_at(data["expires_at"])
        sub.updated_at = _app_state.storage._now() if hasattr(_app_state.storage, '_now') else None

        sess.commit()
        sess.refresh(sub)
        return jsonify({
            "id": sub.id,
            "chat_id": sub.chat_id,
            "enabled": sub.enabled,
            "min_score": sub.min_score,
            "event_types": json.loads(sub.event_types) if sub.event_types else None,
            "plan_id": sub.plan_id,
            "status": sub.status,
            "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
        })


@bp.route("/api/bot/subscriptions/<int:sub_id>", methods=["DELETE"])
@require_auth
def delete_subscription(sub_id: int):
    """Delete a bot subscription."""
    storage = _app_state.storage
    if not storage:
        return jsonify({"error": "Storage not initialized"}), 500

    with storage.get_session() as sess:
        sub = sess.query(BotSubscription).get(sub_id)
        if not sub:
            return jsonify({"error": "Subscription not found"}), 404
        sess.delete(sub)
        sess.commit()
        return jsonify({"deleted": sub_id})
