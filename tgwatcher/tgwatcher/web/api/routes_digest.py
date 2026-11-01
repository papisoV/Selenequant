"""Market digest routes (Phase 2B batch 5).

Extracted from `._legacy`. Routes:
  POST /digest/generate — generate new AI market digest (concurrency-locked)
  GET  /digest/latest   — most recent persisted digest (no LLM call)
  GET  /digest/history  — recent digests, newest first
"""
import logging
import threading

from flask import Blueprint, jsonify, request

from ._legacy import _app_state, require_auth

logger = logging.getLogger(__name__)

bp = Blueprint("digest", __name__, url_prefix="")

_digest_lock = threading.Lock()


@bp.route("/digest/generate", methods=["POST"])
@require_auth
def generate_digest():
    """Generate a new market digest. Covers last window (cold start 36h, else
    last_digest_at → now, capped at 36h). Persists to digests table.

    Concurrency: module-level Lock — only one generation at a time. If a
    request is already running, returns 409.

    Query/body params:
        lookback_hours: int (optional). If set, force window to
            (now - lookback_hours, now) instead of incremental. Capped to
            720 (30d). Default: incremental with 7d auto-fallback.
    """
    _storage = _app_state.storage
    if not _storage:
        return jsonify({"error": "Storage not initialized"}), 500
    _signal_engine = _app_state.signal_engine
    if not _signal_engine or not getattr(_signal_engine, "_llm", None):
        return jsonify({"error": "Signal engine / LLM not initialized"}), 500

    # Accept lookback_hours from JSON body or query string.
    lookback_hours = None
    if request.is_json:
        body = request.get_json(silent=True) or {}
        lookback_hours = body.get("lookback_hours")
    if lookback_hours is None:
        lookback_hours = request.args.get("lookback_hours", type=int)

    if not _digest_lock.acquire(blocking=False):
        return jsonify({"error": "Another digest generation is in progress"}), 409

    try:
        from tgwatcher.digest import generate_digest as _gen
        try:
            result = _gen(_storage, _signal_engine._llm,
                          lookback_hours=lookback_hours)
        except Exception as e:
            logger.exception("Digest generation failed")
            return jsonify({"error": f"Generation failed: {e}"}), 500
        return jsonify(result.to_dict())
    finally:
        _digest_lock.release()


@bp.route("/digest/latest", methods=["GET"])
@require_auth
def get_latest_digest():
    """Return most recent digest (does NOT trigger LLM)."""
    _storage = _app_state.storage
    if not _storage:
        return jsonify({"error": "Storage not initialized"}), 500
    from tgwatcher.digest import get_latest_digest as _get
    result = _get(_storage)
    if result is None:
        return jsonify(None), 404
    return jsonify(result.to_dict())


@bp.route("/digest/history", methods=["GET"])
@require_auth
def list_digests():
    """Return recent digests, newest first. ?limit=N (default 20, max 100)."""
    _storage = _app_state.storage
    if not _storage:
        return jsonify({"error": "Storage not initialized"}), 500
    limit = request.args.get("limit", type=int, default=20)
    limit = max(1, min(limit, 100))
    from tgwatcher.digest import list_digests as _list
    rows = _list(_storage, limit=limit)
    return jsonify([r.to_dict() for r in rows])
