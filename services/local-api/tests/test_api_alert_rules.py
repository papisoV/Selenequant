"""Alert-rule API: CRUD, validation, and the one-time seed migration.

The endpoint this replaces returned five hardcoded demo rules. Two things are
pinned here because they were the actual defects: that list must not come back,
and an empty store must be distinguishable from a never-migrated one.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.api.v1.intelligence as intelligence_module
from app.api.v1.intelligence import router as intelligence_router
from app.middleware.error_handler import install_error_handlers
from app.services.alert_rules_store import AlertRulesStore


@pytest.fixture()
def client(tmp_path, monkeypatch):
    store = AlertRulesStore(tmp_path / "alert_rules.db")
    monkeypatch.setattr(intelligence_module, "get_alert_rules_store", lambda: store)
    app = FastAPI()
    # Domain errors (validation / not found) only become their HTTP status
    # through this middleware; without it they surface as raw exceptions.
    install_error_handlers(app)
    app.include_router(intelligence_router, prefix="/api/v1")
    return TestClient(app)


def _draft(**overrides):
    body = {
        "name": "BTC 突破",
        "type": "price",
        "threshold": {"operator": "gt", "value": 100000},
        "enabled": True,
        "symbol": "BTC/USDT",
    }
    body.update(overrides)
    return body


def test_empty_store_returns_empty_rules_and_not_seeded(client):
    resp = client.get("/api/v1/intelligence/alerts/rules")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["rules"] == []
    assert data["seeded"] is False


def test_demo_rules_are_gone(client):
    """The five literal demo rules must not reappear.

    They were not the user's rules and could never fire, so a client merging
    them in displayed monitoring that did not exist.
    """
    data = client.get("/api/v1/intelligence/alerts/rules").json()["data"]
    assert data["rules"] == []


def test_create_then_list_round_trips(client):
    created = client.post("/api/v1/intelligence/alerts/rules", json=_draft())
    assert created.status_code == 200
    rule = created.json()["data"]
    assert rule["id"]
    assert rule["symbol"] == "BTC/USDT"

    listed = client.get("/api/v1/intelligence/alerts/rules").json()["data"]["rules"]
    assert [r["id"] for r in listed] == [rule["id"]]


def test_create_rejects_unknown_type(client):
    resp = client.post(
        "/api/v1/intelligence/alerts/rules",
        json=_draft(type="onchain", threshold={"min_value": 1}),
    )
    assert resp.status_code == 400


def test_create_rejects_missing_primary_threshold_field(client):
    resp = client.post(
        "/api/v1/intelligence/alerts/rules",
        json=_draft(threshold={"operator": "gt"}),
    )
    assert resp.status_code == 400


def test_price_rule_requires_a_symbol(client):
    """Without a symbol a price threshold has nothing to compare against."""
    resp = client.post("/api/v1/intelligence/alerts/rules", json=_draft(symbol=""))
    assert resp.status_code == 400
    assert "symbol" in resp.json()["error"]["message"]


def test_indicator_rule_requires_a_symbol_too(client):
    body = _draft(
        type="indicator", threshold={"indicator": "RSI", "value": 70}, symbol=""
    )
    assert (
        client.post("/api/v1/intelligence/alerts/rules", json=body).status_code == 400
    )


def test_large_trade_rule_does_not_require_a_symbol(client):
    """It matches every instrument the detector reports."""
    body = _draft(
        type="large_trade", threshold={"min_notional_usd": 1_000_000}, symbol=""
    )
    assert (
        client.post("/api/v1/intelligence/alerts/rules", json=body).status_code == 200
    )


def test_create_rejects_uncomputable_indicator(client):
    """SOPR/NVT are Glassnode metrics — they cannot be computed from bars."""
    body = _draft(
        type="indicator",
        threshold={"indicator": "SOPR", "value": 1},
        symbol="BTC/USDT",
    )
    assert (
        client.post("/api/v1/intelligence/alerts/rules", json=body).status_code == 400
    )


def test_update_replaces_the_whole_rule(client):
    rule = client.post("/api/v1/intelligence/alerts/rules", json=_draft()).json()[
        "data"
    ]
    updated = client.put(
        f"/api/v1/intelligence/alerts/rules/{rule['id']}",
        json=_draft(name="改名", threshold={"operator": "lt", "value": 50000}),
    )
    assert updated.status_code == 200
    body = updated.json()["data"]
    assert body["name"] == "改名"
    assert body["threshold"] == {"operator": "lt", "value": 50000}


def test_update_rejects_type_change_without_matching_threshold(client):
    """Switching type but keeping the old threshold must not be stored."""
    rule = client.post("/api/v1/intelligence/alerts/rules", json=_draft()).json()[
        "data"
    ]
    resp = client.put(
        f"/api/v1/intelligence/alerts/rules/{rule['id']}",
        json=_draft(type="large_trade"),  # still has {"operator","value"}
    )
    assert resp.status_code == 400


def test_update_of_unknown_rule_is_404(client):
    resp = client.put("/api/v1/intelligence/alerts/rules/nope", json=_draft())
    assert resp.status_code == 404


def test_delete_removes_from_list_and_is_404_when_absent(client):
    rule = client.post("/api/v1/intelligence/alerts/rules", json=_draft()).json()[
        "data"
    ]
    assert (
        client.delete(f"/api/v1/intelligence/alerts/rules/{rule['id']}").status_code
        == 200
    )
    assert client.get("/api/v1/intelligence/alerts/rules").json()["data"]["rules"] == []
    assert (
        client.delete(f"/api/v1/intelligence/alerts/rules/{rule['id']}").status_code
        == 404
    )


def test_disabled_rule_stays_listed(client):
    client.post("/api/v1/intelligence/alerts/rules", json=_draft(enabled=False))
    rules = client.get("/api/v1/intelligence/alerts/rules").json()["data"]["rules"]
    assert len(rules) == 1
    assert rules[0]["enabled"] is False


# ---- seed ------------------------------------------------------------------


def test_seed_migrates_local_rules_and_flips_the_flag(client):
    resp = client.post(
        "/api/v1/intelligence/alerts/rules/seed",
        json={"rules": [_draft(name="本地一"), _draft(name="本地二")]},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["created"] == 2
    assert data["skipped"] == []

    listed = client.get("/api/v1/intelligence/alerts/rules").json()["data"]
    assert listed["seeded"] is True
    assert len(listed["rules"]) == 2


def test_seed_is_idempotent(client):
    client.post("/api/v1/intelligence/alerts/rules/seed", json={"rules": [_draft()]})
    again = client.post(
        "/api/v1/intelligence/alerts/rules/seed", json={"rules": [_draft()]}
    )
    data = again.json()["data"]
    assert data["created"] == 0
    assert len(data["rules"]) == 1


def test_seed_skips_invalid_rules_but_migrates_the_valid_ones(client):
    """A legacy ``onchain`` rule must not block the rest of the migration."""
    resp = client.post(
        "/api/v1/intelligence/alerts/rules/seed",
        json={
            "rules": [
                _draft(name="有效"),
                _draft(name="旧链上", type="onchain", threshold={"min_value": 1}),
            ]
        },
    )
    data = resp.json()["data"]
    assert data["created"] == 1
    assert len(data["skipped"]) == 1
    assert [r["name"] for r in data["rules"]] == ["有效"]


def test_delete_all_after_seed_stays_empty(client):
    """Empty must mean empty — not "migrate my rules again"."""
    client.post("/api/v1/intelligence/alerts/rules/seed", json={"rules": [_draft()]})
    rules = client.get("/api/v1/intelligence/alerts/rules").json()["data"]["rules"]
    for rule in rules:
        client.delete(f"/api/v1/intelligence/alerts/rules/{rule['id']}")

    data = client.get("/api/v1/intelligence/alerts/rules").json()["data"]
    assert data["rules"] == []
    assert data["seeded"] is True


def test_seed_rejects_non_list_payload(client):
    resp = client.post("/api/v1/intelligence/alerts/rules/seed", json={"rules": "nope"})
    assert resp.status_code == 400


def test_created_rule_drops_unknown_fields(client):
    """The evaluator reads these rules every 30s; extra keys are not let through."""
    body = _draft(evil="payload", threshold={"operator": "gt", "value": 1, "extra": 2})
    rule = client.post("/api/v1/intelligence/alerts/rules", json=body).json()["data"]
    assert "evil" not in rule
    assert "extra" not in rule["threshold"]
