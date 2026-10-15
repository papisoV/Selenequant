"""Auth + login routes for TGWatcher API.

Phase 2B batch 1: moved verbatim from tgwatcher/web/api/_legacy.py.
Sub-blueprint registered under the parent `api` blueprint.
"""
import logging

from flask import Blueprint, jsonify, make_response, request

from ._legacy import (
    _app_state,
    _check_rate_limit,
    _clear_auth_cookie,
    _run_coro,
    _set_auth_cookie,
    _tg_client_guard,
)

logger = logging.getLogger(__name__)

bp = Blueprint("auth", __name__, url_prefix="")


@bp.route("/auth/bootstrap", methods=["GET"])
def auth_bootstrap():
    """Auto-login for localhost: returns the auth token so the browser can
    store it in localStorage, skipping the manual token entry step.

    Only responds to loopback / same-host requests — the token file already
    lives on the user's machine, so this just removes the copy-paste step.
    Remote requests get 403.
    """
    ip = request.remote_addr or "unknown"
    if not _check_rate_limit(f"auth_bootstrap:{ip}", max_requests=10, window=60):
        return jsonify({"error": "Rate limited"}), 429

    auth_token = _app_state.auth_token
    if auth_token is None:
        return jsonify({"token": None})

    loopback = {"127.0.0.1", "::1", "localhost"}
    if ip not in loopback:
        return jsonify({"error": "Forbidden"}), 403

    resp = make_response(jsonify({"token": auth_token}))
    _set_auth_cookie(resp, auth_token)
    return resp


@bp.route("/login/status", methods=["GET"])
def login_status():
    ip = request.remote_addr or "unknown"
    if not _check_rate_limit(f"login_status:{ip}", max_requests=30, window=60):
        return jsonify({"error": "Rate limited"}), 429

    auth_token = _app_state.auth_token
    if auth_token is not None:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token:
            token = request.args.get("token", "")
        if token != auth_token:
            return jsonify({"error": "Unauthorized"}), 401

    phone = _app_state.config["telegram"]["phone"]

    # Session-file fast path: if the Telethon session file exists, treat
    # the user as logged in without touching Telethon. This avoids blocking
    # on _tg_lock during catchup crawl (the root cause of login_status
    # TimeoutError that kicked users back to the login overlay).
    session_logged_in = _session_file_exists(_app_state.config, phone)

    return jsonify({"logged_in": session_logged_in, "phone": phone})


def _session_file_exists(config: dict, phone: str) -> bool:
    """Return True if the Telethon session file for `phone` exists.

    Mirrors TGClient._session_path() logic. Telethon appends `.session`
    to the path passed to TelegramClient(session=...), so we check for
    `<session_dir>/tgwatcher_<phone>.session`. A present session file
    means Telethon successfully called start() in a prior process —
    sufficient signal for the UI to skip the login overlay.
    """
    from pathlib import Path
    tg = config.get("telegram", {})
    session_dir = Path(tg.get("session_dir", "./sessions"))
    safe_phone = phone.replace("+", "")
    return (session_dir / f"tgwatcher_{safe_phone}.session").exists()


@bp.route("/login", methods=["POST"])
def do_login():
    ip = request.remote_addr or "unknown"
    if not _check_rate_limit(f"login:{ip}"):
        return jsonify({"error": "Rate limited"}), 429

    auth_token = _app_state.auth_token
    if auth_token is not None:
        token = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if token != auth_token:
            return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json(silent=True) or {}
    code = data.get("code")
    phone_code_hash = data.get("phone_code_hash")

    phone = _app_state.config["telegram"]["phone"]

    try:
        with _tg_client_guard() as tg:
            authorized = _run_coro(tg.client.is_user_authorized())

            if authorized:
                resp = make_response(jsonify({"status": "already_logged_in"}))
                if _app_state.auth_token is not None:
                    _set_auth_cookie(resp, _app_state.auth_token)
                return resp

            if code and phone_code_hash:
                try:
                    _run_coro(tg.client.sign_in(phone, code, phone_code_hash=phone_code_hash))
                    resp = make_response(jsonify({"status": "logged_in"}))
                    if _app_state.auth_token is not None:
                        _set_auth_cookie(resp, _app_state.auth_token)
                    return resp
                except Exception as e:
                    return jsonify({"error": str(e)}), 400
            else:
                try:
                    result = _run_coro(tg.client.send_code_request(phone))
                    return jsonify({"status": "code_sent", "phone_code_hash": result.phone_code_hash})
                except Exception as e:
                    return jsonify({"error": str(e)}), 400

    except Exception as e:
        logger.error("Login error: %s", e, exc_info=True)
        return jsonify({"error": str(e)}), 500


@bp.route("/logout", methods=["POST"])
def do_logout():
    """Clear auth cookie. Stateless — no server-side session to invalidate."""
    resp = make_response(jsonify({"ok": True}))
    _clear_auth_cookie(resp)
    return resp
