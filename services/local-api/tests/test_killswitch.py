"""C7 fix: kill-switch force gate.

release_kill_switch with force=False while switch is active → REFUSES
to release (returns force_required error, switch stays active).
force=True → released.

Before C7: force=False still released (only logged a warning).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

_SERVICE_ROOT = Path(__file__).resolve().parent.parent
if str(_SERVICE_ROOT) not in sys.path:
    sys.path.insert(0, str(_SERVICE_ROOT))


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("SELENE_AUTH_FAILCLOSED", "1")
    monkeypatch.delenv("SELENE_AUTH_DISABLED", raising=False)
    from app.main import create_app

    return create_app()


@pytest.fixture(autouse=True)
def _reset_kill_switch():
    """Reset module-level kill switch state between tests."""
    from app.api.v1 import admin as admin_mod

    admin_mod._kill_switch_active = False
    admin_mod._kill_switch_reason = None
    admin_mod._kill_switch_tripped_at = None
    yield
    admin_mod._kill_switch_active = False
    admin_mod._kill_switch_reason = None
    admin_mod._kill_switch_tripped_at = None


def test_release_force_false_when_active_refused(app):
    """force=False + active → refused (still active)."""
    from app.api.v1 import admin as admin_mod

    admin_mod._kill_switch_active = True
    admin_mod._kill_switch_reason = "drawdown breach"

    client = TestClient(app)
    resp = client.post(
        "/api/v1/admin/kill-switch/release",
        json={"reason": "manual", "force": False},
    )
    assert resp.status_code == 200  # APIResponse wraps error
    body = resp.json()
    assert body["error"] is not None
    assert body["error"]["code"] == "force_required"
    # Switch must STILL be active.
    assert admin_mod._kill_switch_active is True


def test_release_force_true_when_active_released(app):
    """force=True + active → released."""
    from app.api.v1 import admin as admin_mod

    admin_mod._kill_switch_active = True
    admin_mod._kill_switch_reason = "drawdown breach"

    client = TestClient(app)
    resp = client.post(
        "/api/v1/admin/kill-switch/release",
        json={"reason": "manual override", "force": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert admin_mod._kill_switch_active is False
    assert admin_mod._kill_switch_reason is None


def test_release_force_false_when_inactive_released(app):
    """force=False + inactive → released (no-op, allowed)."""
    from app.api.v1 import admin as admin_mod

    admin_mod._kill_switch_active = False

    client = TestClient(app)
    resp = client.post(
        "/api/v1/admin/kill-switch/release",
        json={"reason": "cleanup", "force": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    assert admin_mod._kill_switch_active is False


def test_trip_then_release_force_false_still_active(app):
    """End-to-end: trip → release force=False → still active → release force=True → released."""
    from app.api.v1 import admin as admin_mod

    client = TestClient(app)

    # Trip.
    resp = client.post(
        "/api/v1/admin/kill-switch/trip",
        json={"reason": "test trip"},
    )
    assert resp.status_code == 200
    assert admin_mod._kill_switch_active is True

    # Release with force=False → refused.
    resp2 = client.post(
        "/api/v1/admin/kill-switch/release",
        json={"reason": "try release", "force": False},
    )
    body2 = resp2.json()
    assert body2["error"] is not None
    assert body2["error"]["code"] == "force_required"
    assert admin_mod._kill_switch_active is True

    # Release with force=True → released.
    resp3 = client.post(
        "/api/v1/admin/kill-switch/release",
        json={"reason": "force release", "force": True},
    )
    body3 = resp3.json()
    assert body3["error"] is None
    assert admin_mod._kill_switch_active is False


def test_trip_activates_risk_engine_singleton(app):
    """trip 必须把风控单例也置活 —— 拦交易的是它,不是 admin 的模块变量。"""
    from selene_space_risk.kill_switch import KillSwitch

    client = TestClient(app)
    resp = client.post(
        "/api/v1/admin/kill-switch/trip",
        json={"reason": "singleton probe"},
    )
    assert resp.status_code == 200
    assert KillSwitch().is_active is True


def test_release_deactivates_risk_engine_singleton(app):
    """release 必须解除风控单例 —— 否则 admin 显示已释放而系统仍在拒单。

    2026-10-25 实测缺陷:release 只清 admin 模块变量,单例仍 active:true
    (磁盘 kill_switch.json 也是 true),运维以为已恢复、实际下单全被拦。
    """
    from selene_space_risk.kill_switch import KillSwitch

    client = TestClient(app)
    resp = client.post(
        "/api/v1/admin/kill-switch/trip",
        json={"reason": "singleton release probe"},
    )
    assert resp.status_code == 200
    assert KillSwitch().is_active is True

    resp2 = client.post(
        "/api/v1/admin/kill-switch/release",
        json={"reason": "force release", "force": True},
    )
    assert resp2.status_code == 200
    assert resp2.json()["error"] is None
    # 拦交易的那个状态必须真的解除,不能只看 admin 变量。
    assert KillSwitch().is_active is False
