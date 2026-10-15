"""Health, metrics, and Telegram dialog routes for TGWatcher API.

Phase 2B batch 6 (final): moved verbatim from tgwatcher/web/api/_legacy.py.
Sub-blueprint registered under the parent `api` blueprint.

Note on AppState access: the original functions in _legacy.py used bare-name
lookups (``_storage``, ``_signal_engine``, ``_tg_client``) which resolved via
PEP 562 ``__getattr__`` forwarding to ``_app_state``. Bare-name lookups inside
*this* module would NOT trigger that forwarding (PEP 562 only fires on external
attribute access), so we access these via ``_app_state.<attr>`` directly. This
matches the pattern used by routes_stats / routes_signals / routes_messages.
"""
import logging
from datetime import datetime, timezone

from flask import Blueprint, jsonify, Response

from ._legacy import (
    _app_state,
    _iso_z,
    _run_coro,
    _tg_client_guard,
    require_auth,
)

logger = logging.getLogger(__name__)

bp = Blueprint("health", __name__, url_prefix="")


# --- Telegram Dialog API ---

@bp.route("/dialogs", methods=["GET"])
@require_auth
def get_dialogs():
    try:
        with _tg_client_guard() as tg:
            dialogs = _run_coro(tg.list_dialogs())
            return jsonify(dialogs)
    except Exception as e:
        logger.error("Dialogs error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


# --- Service Health ---

@bp.route("/health", methods=["GET"])
def health_check():
    """Lightweight service health endpoint for Docker/k8s probes.

    No auth: liveness/readiness probes must work without a token. Does NOT
    hit the DB or TG network — only verifies module-level singletons are
    populated. Returns 200 for ok/degraded, 503 for down.
    """
    storage = _app_state.storage
    signal_engine = _app_state.signal_engine
    tg_client = _app_state.tg_client

    storage_status = "ok" if storage is not None else "down"

    if signal_engine is not None and getattr(signal_engine, "_llm", None) is not None:
        llm_status = "ok"
    elif signal_engine is None:
        llm_status = "disabled"
    else:
        llm_status = "down"

    tg_status = "ok" if tg_client is not None else "unknown"

    if storage_status == "down":
        overall = "down"
    elif llm_status == "down":
        overall = "degraded"
    else:
        overall = "ok"

    payload = {
        "status": overall,
        "storage": storage_status,
        "llm": llm_status,
        "tg_client": tg_status,
        "timestamp": _iso_z(datetime.now(timezone.utc)),
    }
    code = 503 if overall == "down" else 200
    return jsonify(payload), code


# --- Prometheus Metrics ---

@bp.route("/metrics", methods=["GET"])
def prometheus_metrics():
    """Prometheus text-format exposition endpoint for scraping.

    No auth: Prometheus scrapers must reach this without a token. Mirrors
    the /health policy. Returns ``text/plain; version=0.0.4`` per the
    Prometheus exposition spec.
    """
    from tgwatcher.web.metrics import collect_metrics

    return Response(collect_metrics(), mimetype="text/plain; version=0.0.4")
