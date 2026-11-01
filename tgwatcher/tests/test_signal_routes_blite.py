"""Tests for /api/signal/daemon endpoint and _signal_lock (Option B-lite).

Verifies:
- GET /api/signal/daemon requires auth (401 without token)
- POST /api/signal/process returns 409 with {"lock": "signal"} when the
  module-level _signal_lock is already held by another caller
- GET /api/signal/daemon returns the 5-field status shape when daemon
  is not initialized (all zeros / None)
"""
from __future__ import annotations

import pytest
from flask import Flask

from tgwatcher.web.api.routes_signals import _signal_lock, bp


@pytest.fixture()
def app() -> Flask:
    """Flask app with the signals blueprint registered under /api.

    Uses a plain app (no real _app_state wiring) — _app_state.* reads
    will return defaults from the AppState dataclass (e.g. signal_service
    is None, auto_llm_daemon is None) so routes return their early-exit
    responses, which is what we want to test.
    """
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(bp, url_prefix="/api")

    # Wire a known auth token so require_auth lets requests through.
    from tgwatcher.web.api import _app_state
    _app_state.auth_token = "test-secret-token"
    # For /signal/process: signal_service must be truthy to pass the
    # first check and reach the _signal_lock acquisition. Use a bare
    # object — start() is never called because the test holds the lock,
    # so the route returns 409 before invoking _app_state.signal_service.
    _app_state.signal_service = object()

    yield app

    _app_state.auth_token = None
    _app_state.signal_service = None


@pytest.fixture()
def test_client(app: Flask):
    return app.test_client()


def _auth_headers() -> dict:
    return {"Authorization": "Bearer test-secret-token"}


class TestSignalDaemonRoute:
    def test_daemon_status_route_requires_auth(self, test_client):
        rv = test_client.get("/api/signal/daemon")
        assert rv.status_code == 401

    def test_daemon_status_returns_5_fields_when_no_daemon(self, test_client):
        rv = test_client.get("/api/signal/daemon", headers=_auth_headers())
        assert rv.status_code == 200
        data = rv.get_json()
        assert set(data.keys()) == {
            "running", "pending", "last_batch_at",
            "last_batch_count", "last_digest_at",
        }
        assert data["running"] is False
        assert data["pending"] == 0
        assert data["last_batch_at"] is None
        assert data["last_batch_count"] is None
        assert data["last_digest_at"] is None


class TestSignalProcessLock:
    def test_signal_process_returns_409_when_lock_held(self, test_client):
        # Acquire lock in the test thread, then POST — expect 409
        acquired = _signal_lock.acquire(blocking=False)
        assert acquired, "lock should be free at test start"
        try:
            rv = test_client.post("/api/signal/process", json={}, headers=_auth_headers())
            assert rv.status_code == 409
            data = rv.get_json()
            assert data.get("lock") == "signal"
        finally:
            _signal_lock.release()
