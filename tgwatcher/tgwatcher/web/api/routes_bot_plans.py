"""Subscription plan CRUD routes.

Manage pricing plans for bot signal push subscriptions.
Routes:
  GET    /api/bot/plans                    — list all plans
  POST   /api/bot/plans                    — create a plan
  PATCH  /api/bot/plans/<id>               — update plan
  DELETE /api/bot/plans/<id>               — soft-disable plan
  GET    /api/bot/subscriptions/<id>/status — subscription status + expiry
"""
import json
import logging

from flask import Blueprint, jsonify, request

from tgwatcher.models import SubscriptionPlan, BotSubscription
from ._legacy import _app_state, require_auth

logger = logging.getLogger(__name__)

bp = Blueprint("bot_plans", __name__, url_prefix="")


@bp.route("/api/bot/plans", methods=["GET"])
@require_auth
def list_plans():
    """Return all subscription plans."""
    storage = _app_state.storage
    if not storage:
        return jsonify({"error": "Storage not initialized"}), 500

    with storage.get_session() as sess:
        plans = sess.query(SubscriptionPlan).order_by(SubscriptionPlan.id).all()
        return jsonify([
            {
                "id": p.id,
                "name": p.name,
                "price_cents": p.price_cents,
                "currency": p.currency,
                "interval_days": p.interval_days,
                "max_signals_per_day": p.max_signals_per_day,
                "features": json.loads(p.features_json) if p.features_json else None,
                "enabled": p.enabled,
                "created_at": p.created_at.isoformat() if p.created_at else None,
                "updated_at": p.updated_at.isoformat() if p.updated_at else None,
            }
            for p in plans
        ])


@bp.route("/api/bot/plans", methods=["POST"])
@require_auth
def create_plan():
    """Create a new subscription plan."""
    storage = _app_state.storage
    if not storage:
        return jsonify({"error": "Storage not initialized"}), 500

    data = request.get_json(silent=True) or {}
    name = data.get("name")
    if not name:
        return jsonify({"error": "name is required"}), 400

    plan = SubscriptionPlan(
        name=name,
        price_cents=int(data.get("price_cents", 0)),
        currency=str(data.get("currency", "CNY")),
        interval_days=int(data.get("interval_days", 30)),
        max_signals_per_day=int(data.get("max_signals_per_day", 0)),
        features_json=json.dumps(data["features"]) if "features" in data and data["features"] else None,
        enabled=bool(data.get("enabled", True)),
    )

    try:
        with storage.get_session() as sess:
            sess.add(plan)
            sess.commit()
            sess.refresh(plan)
            return jsonify({
                "id": plan.id,
                "name": plan.name,
                "price_cents": plan.price_cents,
                "currency": plan.currency,
                "interval_days": plan.interval_days,
                "max_signals_per_day": plan.max_signals_per_day,
                "features": json.loads(plan.features_json) if plan.features_json else None,
                "enabled": plan.enabled,
            }), 201
    except Exception as e:
        if "UNIQUE constraint" in str(e):
            return jsonify({"error": f"Plan name '{name}' already exists"}), 409
        logger.exception("Create plan failed: %s", e)
        return jsonify({"error": str(e)}), 500


@bp.route("/api/bot/plans/<int:plan_id>", methods=["PATCH"])
@require_auth
def update_plan(plan_id: int):
    """Update a subscription plan."""
    storage = _app_state.storage
    if not storage:
        return jsonify({"error": "Storage not initialized"}), 500

    data = request.get_json(silent=True) or {}

    with storage.get_session() as sess:
        plan = sess.query(SubscriptionPlan).get(plan_id)
        if not plan:
            return jsonify({"error": "Plan not found"}), 404

        if "name" in data:
            plan.name = str(data["name"])
        if "price_cents" in data:
            plan.price_cents = int(data["price_cents"])
        if "currency" in data:
            plan.currency = str(data["currency"])
        if "interval_days" in data:
            plan.interval_days = int(data["interval_days"])
        if "max_signals_per_day" in data:
            plan.max_signals_per_day = int(data["max_signals_per_day"])
        if "features" in data:
            plan.features_json = json.dumps(data["features"]) if data["features"] else None
        if "enabled" in data:
            plan.enabled = bool(data["enabled"])

        sess.commit()
        sess.refresh(plan)
        return jsonify({
            "id": plan.id,
            "name": plan.name,
            "price_cents": plan.price_cents,
            "currency": plan.currency,
            "interval_days": plan.interval_days,
            "max_signals_per_day": plan.max_signals_per_day,
            "features": json.loads(plan.features_json) if plan.features_json else None,
            "enabled": plan.enabled,
        })


@bp.route("/api/bot/plans/<int:plan_id>", methods=["DELETE"])
@require_auth
def delete_plan(plan_id: int):
    """Soft-disable a subscription plan (sets enabled=False)."""
    storage = _app_state.storage
    if not storage:
        return jsonify({"error": "Storage not initialized"}), 500

    with storage.get_session() as sess:
        plan = sess.query(SubscriptionPlan).get(plan_id)
        if not plan:
            return jsonify({"error": "Plan not found"}), 404
        plan.enabled = False
        sess.commit()
        return jsonify({"disabled": plan_id})


@bp.route("/api/bot/subscriptions/<int:sub_id>/status", methods=["GET"])
@require_auth
def subscription_status(sub_id: int):
    """Return subscription status with plan and expiry info."""
    storage = _app_state.storage
    if not storage:
        return jsonify({"error": "Storage not initialized"}), 500

    with storage.get_session() as sess:
        sub = sess.query(BotSubscription).get(sub_id)
        if not sub:
            return jsonify({"error": "Subscription not found"}), 404

        result = {
            "id": sub.id,
            "chat_id": sub.chat_id,
            "enabled": sub.enabled,
            "status": sub.status,
            "expires_at": sub.expires_at.isoformat() if sub.expires_at else None,
            "plan_id": sub.plan_id,
            "plan": None,
        }

        if sub.plan_id:
            plan = sess.query(SubscriptionPlan).get(sub.plan_id)
            if plan:
                result["plan"] = {
                    "id": plan.id,
                    "name": plan.name,
                    "price_cents": plan.price_cents,
                    "currency": plan.currency,
                    "interval_days": plan.interval_days,
                    "max_signals_per_day": plan.max_signals_per_day,
                    "features": json.loads(plan.features_json) if plan.features_json else None,
                }

        # Check if expired
        from datetime import datetime, timezone
        if sub.expires_at and sub.expires_at.replace(tzinfo=None) < datetime.now(timezone.utc).replace(tzinfo=None):
            result["is_expired"] = True
        else:
            result["is_expired"] = False

        return jsonify(result)
