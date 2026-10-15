"""US-1A: outcome / backtest_verification must survive the API layer.

The record has carried both audit keys since W2 (T2a backtest attachment,
T3a outcome state machine) but ``AnalyzeResponse`` did not declare them and
all three construction sites dropped them, so the win/loss loop was
invisible to the frontend no matter how many times the heartbeat judged it.

These tests pin the wire contract: declared fields, passthrough on every
read path, and a four-value ``outcome.status`` enumeration.
"""

from __future__ import annotations

from typing import Any

import pytest
from app.core.decision_workflow_service import DecisionWorkflowService
from app.core.dependencies import get_decision_workflow_service
from app.main import create_app
from httpx import ASGITransport, AsyncClient

from app.api.v1.decision_workflow import _to_response

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

_CANNED_INPUT: dict[str, Any] = {
    "symbol": "BTC/USDT",
    "interval": "1h",
    "summary": {"atr14": 5.0},
}


def _seed_record(
    input_id: str, outcome: dict[str, Any] | None
) -> DecisionWorkflowService:
    """A service holding one canned record, optionally carrying an outcome."""
    svc = DecisionWorkflowService()
    svc.analyze(
        "trading_decision",
        _CANNED_INPUT,
        input_id=input_id,
        session_id="s1",
    )
    svc._records[input_id].outcome = outcome
    return svc


def _make_app(svc: DecisionWorkflowService):
    app = create_app()
    app.dependency_overrides[get_decision_workflow_service] = lambda: svc
    return app


async def _get_record_body(
    input_id: str, outcome: dict[str, Any] | None
) -> dict[str, Any]:
    svc = _seed_record(input_id, outcome)
    app = _make_app(svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get(f"/api/v1/decision-workflow/records/{input_id}")
    assert resp.status_code == 200, resp.text
    body: dict[str, Any] = resp.json()
    return body


# ---------------------------------------------------------------------------
# Wire model: both keys declared (the whole point of the fix)
# ---------------------------------------------------------------------------


def test_response_model_declares_both_audit_keys() -> None:
    """Pydantic v2 drops undeclared top-level keys — the declaration is the fix."""
    from app.api.v1.decision_workflow import AnalyzeResponse

    fields = AnalyzeResponse.model_fields
    assert "outcome" in fields
    assert "backtest_verification" in fields
    assert "followups" in fields
    assert fields["outcome"].default is None
    assert fields["backtest_verification"].default is None
    assert fields["followups"].default is None


def test_to_response_passes_audit_keys_through() -> None:
    """Single construction site: no key may be dropped, no stray extras.

    The exact key set is the assertion — a projection that silently stops
    emitting one of the three audit keys is exactly the bug this file pins.
    """
    svc = _seed_record("unit-1", {"status": "win", "pnl_r": 2.0})
    svc._records["unit-1"].backtest_verification = {"trades": 12}
    svc._records["unit-1"].followups = [{"question": "q", "answer": "a"}]

    out = _to_response(svc._records["unit-1"])

    assert out.outcome == {"status": "win", "pnl_r": 2.0}
    assert out.backtest_verification == {"trades": 12}
    assert out.followups == [{"question": "q", "answer": "a"}]
    assert set(out.model_dump()) == {
        "meta",
        "input_data",
        "stage1",
        "stage2",
        "strategy_files_used",
        "usage_total",
        "outcome",
        "backtest_verification",
        "followups",
    }


# ---------------------------------------------------------------------------
# Endpoint read path: GET /records/{id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_record_returns_outcome_when_present() -> None:
    body = await _get_record_body(
        "us1a-win", {"status": "win", "pnl_r": 1.8, "entry_triggered": True}
    )
    assert body["outcome"]["status"] == "win"
    assert body["outcome"]["pnl_r"] == 1.8


@pytest.mark.asyncio
async def test_get_record_returns_backtest_verification_when_present() -> None:
    svc = _seed_record("us1a-bt", None)
    svc._records["us1a-bt"].backtest_verification = {"result": "win", "trades": 9}
    app = _make_app(svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.get("/api/v1/decision-workflow/records/us1a-bt")
    assert resp.status_code == 200, resp.text
    assert resp.json()["backtest_verification"] == {"result": "win", "trades": 9}


@pytest.mark.asyncio
async def test_get_record_without_outcome_yields_null_not_error() -> None:
    """未判定的记录 → 键存在但为 null(前端据此整段不渲染),不 500。"""
    body = await _get_record_body("us1a-none", None)
    assert body["outcome"] is None
    assert body["backtest_verification"] is None


# ---------------------------------------------------------------------------
# Four-value enumeration (the state machine's full output alphabet)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "outcome",
    [
        {"status": "win", "pnl_r": 2.5},
        {"status": "loss", "pnl_r": -1.0},
        {"status": "expired", "pnl_r": None},
        {"status": "timeout_open", "pnl_r": None},
    ],
)
async def test_outcome_status_enumeration_roundtrips(outcome: dict[str, Any]) -> None:
    body = await _get_record_body(f"us1a-{outcome['status']}", outcome)
    assert body["outcome"]["status"] in {"win", "loss", "expired", "timeout_open"}
    assert body["outcome"]["status"] == outcome["status"]
    assert body["outcome"]["pnl_r"] == outcome["pnl_r"]


# ---------------------------------------------------------------------------
# POST /analyze shares the same projection (no second construction site)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_analyze_endpoint_exposes_audit_keys() -> None:
    """The canned POST /analyze path also carries the keys (null for fresh runs)."""
    svc = DecisionWorkflowService()
    app = _make_app(svc)
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        resp = await client.post(
            "/api/v1/decision-workflow/analyze",
            json={
                "tree_id": "trading_decision",
                "input_data": _CANNED_INPUT,
                "input_id": "us1a-analyze",
            },
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "outcome" in body
    assert "backtest_verification" in body
    assert body["outcome"] is None
    assert body["backtest_verification"] is None
