"""Tests for Cookie + HttpOnly SSE auth fallback (optimization #13).

Verifies:
- Cookie auth fallback works when Authorization header absent
- Cookie attributes are secure (HttpOnly, SameSite=Strict, Path=/api)
- Login/bootstrap/logout endpoints set/clear cookie correctly
- Authorization header still takes priority over cookie
"""
from __future__ import annotations

import pytest

from flask import Flask

from tgwatcher.web.api._helpers import (
    _clear_auth_cookie,
    _extract_auth_token,
    _set_auth_cookie,
)


@pytest.fixture()
def app() -> Flask:
    """Minimal Flask app with auth cookie helpers wired to test routes."""
    app = Flask(__name__)
    app.config["TESTING"] = True

    # Wire _app_state.auth_token to a known value for testing.
    from tgwatcher.web.api import _app_state
    _app_state.auth_token = "test-secret-token"

    @app.route("/api/_test/sse")
    def _sse():
        token = _extract_auth_token()
        if _app_state.auth_token and token != _app_state.auth_token:
            return {"error": "Unauthorized"}, 401
        return {"ok": True}

    @app.route("/api/_test/login", methods=["POST"])
    def _login():
        from flask import jsonify, make_response
        resp = make_response(jsonify({"ok": True}))
        _set_auth_cookie(resp, _app_state.auth_token)
        return resp

    @app.route("/api/_test/logout", methods=["POST"])
    def _logout():
        from flask import jsonify, make_response
        resp = make_response(jsonify({"ok": True}))
        _clear_auth_cookie(resp)
        return resp

    yield app

    _app_state.auth_token = None


@pytest.fixture()
def client(app: Flask):
    return app.test_client()


class TestCookieAuthFallback:
    def test_no_auth_returns_401(self, client):
        resp = client.get("/api/_test/sse")
        assert resp.status_code == 401

    def test_cookie_auth_returns_200(self, client):
        client.set_cookie("auth_token", "test-secret-token", path="/api")
        resp = client.get("/api/_test/sse")
        assert resp.status_code == 200

    def test_mismatched_cookie_returns_401(self, client):
        client.set_cookie("auth_token", "wrong-token", path="/api")
        resp = client.get("/api/_test/sse")
        assert resp.status_code == 401

    def test_authorization_header_priority_over_cookie(self, client):
        # cookie is wrong, header is right → should pass
        client.set_cookie("auth_token", "wrong-token", path="/api")
        resp = client.get(
            "/api/_test/sse",
            headers={"Authorization": "Bearer test-secret-token"},
        )
        assert resp.status_code == 200

    def test_header_auth_still_works_without_cookie(self, client):
        resp = client.get(
            "/api/_test/sse",
            headers={"Authorization": "Bearer test-secret-token"},
        )
        assert resp.status_code == 200


class TestCookieAttributes:
    def test_login_sets_cookie_with_secure_attributes(self, client):
        resp = client.post("/api/_test/login")
        # Flask test_client exposes Set-Cookie via resp.headers
        set_cookie = resp.headers.get("Set-Cookie", "")
        assert "auth_token=test-secret-token" in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Path=/api" in set_cookie
        assert "SameSite=Strict" in set_cookie
        assert "Max-Age=86400" in set_cookie

    def test_logout_clears_cookie(self, client):
        client.set_cookie("auth_token", "test-secret-token", path="/api")
        resp = client.post("/api/_test/logout")
        set_cookie = resp.headers.get("Set-Cookie", "")
        # delete_cookie sends an expired empty cookie
        assert "auth_token=" in set_cookie
        # Max-Age=0 or expires in the past indicates deletion
        assert "Max-Age=0" in set_cookie or "expires=" in set_cookie.lower()

    def test_cookie_not_readable_by_js(self, client):
        """HttpOnly means document.cookie cannot read auth_token — verify attribute set."""
        resp = client.post("/api/_test/login")
        set_cookie = resp.headers.get("Set-Cookie", "")
        # HttpOnly must be present (capital H, no spaces around)
        assert "HttpOnly" in set_cookie
