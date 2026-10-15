"""Stats query routes for TGWatcher API.

Phase 2B batch 1: moved verbatim from tgwatcher/web/api/_legacy.py.
Sub-blueprint registered under the parent `api` blueprint.
"""
from flask import Blueprint, jsonify, request

from ._legacy import _app_state, _iso_z, require_auth

bp = Blueprint("stats", __name__, url_prefix="")


@bp.route("/stats", methods=["GET"])
@require_auth
def get_stats():
    storage = _app_state.storage
    stats = storage.get_stats()
    stats["earliest_message"] = _iso_z(stats["earliest_message"])
    stats["latest_message"] = _iso_z(stats["latest_message"])
    return jsonify(stats)


@bp.route("/stats/trend", methods=["GET"])
@require_auth
def get_stats_trend():
    period = request.args.get("period", "day")
    days = request.args.get("days", 30, type=int)
    chat_id = request.args.get("chat_id", type=int)
    days = max(1, min(days, 365))
    result = _app_state.storage.get_message_trend(period=period, days=days, chat_id=chat_id)
    return jsonify(result)


@bp.route("/stats/heatmap", methods=["GET"])
@require_auth
def get_stats_heatmap():
    chat_id = request.args.get("chat_id", type=int)
    result = _app_state.storage.get_activity_heatmap(chat_id=chat_id)
    return jsonify(result)


@bp.route("/stats/comparison", methods=["GET"])
@require_auth
def get_stats_comparison():
    result = _app_state.storage.get_group_comparison()
    return jsonify(result)
