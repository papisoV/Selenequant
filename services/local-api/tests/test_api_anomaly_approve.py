"""Suggest-tier HTTP surface — GET /anomaly/proposals and POST /anomaly/approve.

The consumer tests (``test_astock_anomaly_decision.py``) prove the registry and
verdict semantics. This file proves the *endpoints*: the read list renders a
pending proposal, the write records a verdict and echoes ``executes_order:
False``, an unknown id is refused (not a false success), and a deployment with
no consumer answers empty/unavailable instead of raising.

The override seam is ``get_anomaly_decision_consumer`` — the same getter the
lifespan populates, so the test exercises the real route wiring, not a mock of
it.

Business failures (``no_pending_proposal``, ``unavailable``) are **HTTP 200
envelopes carrying an ``error`` object**, not 4xx responses — the client routes
on ``error.code``. Every business-error case below pins both the 200 status and
the code, so a change to either half fails loudly instead of silently breaking
the panel's error branches.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.dependencies import (
    get_anomaly_decision_consumer,
)
from app.main import create_app

from selene_space_paper.audit_ledger import HashChainLedger
from selene_space_types.market import AnomalyEvent

_CST = timezone(timedelta(hours=8))


def _cst_insession_ts() -> int:
    now_cst = datetime.now(_CST)
    stamped = now_cst.replace(hour=10, minute=0, second=0, microsecond=0)
    return int(stamped.timestamp() * 1000)


def _anomaly(kind: str = "limit_up", **overrides: Any) -> AnomalyEvent:
    base: dict[str, Any] = {
        "exchange": "astock",
        "symbol": "600519.SH",
        "timestamp": _cst_insession_ts(),
        "kind": kind,
        "price": 11.0,
        "prev_close": 10.0,
        "name": "贵州茅台",
    }
    base.update(overrides)
    return AnomalyEvent(**base)


class _MemConsumer:
    """A real consumer over a temp ledger — the endpoints talk to the real API."""

    def __init__(self, tmp_path: Any, mode: str = "suggest") -> None:
        self._ledger = HashChainLedger(tmp_path / "chain.jsonl")
        from app.astock_anomaly_decision import AnomalyDecisionConsumer

        self._inner = AnomalyDecisionConsumer(
            self._ledger, capital_base=100_000, quantity_pct=0.1, mode=mode
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    @property
    def ledger(self) -> HashChainLedger:
        return self._ledger


def _app(consumer: Any) -> Any:
    app = create_app()
    app.dependency_overrides[get_anomaly_decision_consumer] = lambda: consumer
    return app


async def _get(app: Any, path: str, **kw: Any) -> Any:
    transport = ASGITransport(app=app, client=kw.pop("client", ("127.0.0.1", 123)))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.get(path)


async def _post(app: Any, path: str, body: dict[str, Any], **kw: Any) -> Any:
    transport = ASGITransport(app=app, client=kw.pop("client", ("127.0.0.1", 123)))
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        return await c.post(path, json=body)


@pytest.fixture
def consumer(tmp_path: Any) -> _MemConsumer:
    return _MemConsumer(tmp_path)


# ---------------------------------------------------------------------------
# GET /anomaly/proposals
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_renders_pending_proposal(consumer: _MemConsumer):
    consumer.handle(_anomaly("limit_up"))
    resp = await _get(_app(consumer), "/api/v1/anomaly/proposals")
    assert resp.status_code == 200
    body = resp.json()
    assert body["error"] is None
    data = body["data"]
    assert data["mode"] == "suggest"
    assert data["wired"] is True
    assert len(data["proposals"]) == 1
    p = data["proposals"][0]
    assert p["kind"] == "limit_up"
    assert p["symbol"] == "600519.SH"
    assert p["side"] == "buy"
    assert p["quantity"] == 900.0


@pytest.mark.asyncio
async def test_list_empty_when_nothing_pending(consumer: _MemConsumer):
    resp = await _get(_app(consumer), "/api/v1/anomaly/proposals")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["proposals"] == []
    assert data["wired"] is True


@pytest.mark.asyncio
async def test_list_unwired_deployment_is_honest():
    # No consumer (crypto-only): wired=False so the panel can tell "nothing
    # pending" apart from "not available here".
    resp = await _get(_app(None), "/api/v1/anomaly/proposals")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["wired"] is False
    assert data["proposals"] == []


# ---------------------------------------------------------------------------
# POST /anomaly/approve
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_approve_records_and_echoes_no_order(consumer: _MemConsumer):
    consumer.handle(_anomaly("limit_up"))
    dedup_id = consumer.list_pending()[0].dedup_id

    resp = await _post(
        _app(consumer),
        "/api/v1/anomaly/approve",
        {"dedup_id": dedup_id, "approve": True},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["error"] is None
    assert body["data"]["resolved"] is True
    assert body["data"]["result"] == "approved"
    # The wire must carry the no-order truth, not just the ledger.
    assert body["data"]["executes_order"] is False

    # And the ledger really holds the confirmation.
    records = [e.payload for e in consumer.ledger.read_chain()]
    approvals = [r for r in records if r.get("stage") == "anomaly_approval"]
    assert len(approvals) == 1
    assert approvals[0]["executes_order"] is False


@pytest.mark.asyncio
async def test_approve_unknown_id_is_refused(consumer: _MemConsumer):
    resp = await _post(
        _app(consumer),
        "/api/v1/anomaly/approve",
        {"dedup_id": "2099-01-01|X|limit_up", "approve": True},
    )
    body = resp.json()
    # A business miss is a 200 envelope with an error field, never a 4xx —
    # the client routes on ``error.code``, so a status change here would
    # silently break the panel's "unknown id" branch.
    assert resp.status_code == 200
    assert body["data"] is None
    assert body["error"]["code"] == "no_pending_proposal"
    # Nothing was written.
    records = [e.payload for e in consumer.ledger.read_chain()]
    assert [r for r in records if r.get("stage") == "anomaly_approval"] == []


@pytest.mark.asyncio
async def test_approve_replay_is_refused(consumer: _MemConsumer):
    consumer.handle(_anomaly("limit_up"))
    dedup_id = consumer.list_pending()[0].dedup_id
    app = _app(consumer)

    first = await _post(
        app, "/api/v1/anomaly/approve", {"dedup_id": dedup_id, "approve": True}
    )
    assert first.status_code == 200
    assert first.json()["error"] is None

    second = await _post(
        app, "/api/v1/anomaly/approve", {"dedup_id": dedup_id, "approve": True}
    )
    assert second.status_code == 200
    assert second.json()["error"]["code"] == "no_pending_proposal"
    records = [e.payload for e in consumer.ledger.read_chain()]
    assert len([r for r in records if r.get("stage") == "anomaly_approval"]) == 1


@pytest.mark.asyncio
async def test_approve_unwired_deployment_is_refused():
    resp = await _post(
        _app(None),
        "/api/v1/anomaly/approve",
        {"dedup_id": "x", "approve": True},
    )
    body = resp.json()
    assert resp.status_code == 200
    assert body["data"] is None
    assert body["error"]["code"] == "unavailable"


@pytest.mark.asyncio
async def test_approve_rejects_remote_unauthenticated(consumer: _MemConsumer):
    # require_user rejects non-localhost callers — an approval is a security
    # action, so it must not be castable from off-box.
    resp = await _post(
        _app(consumer),
        "/api/v1/anomaly/approve",
        {"dedup_id": "x", "approve": True},
        client=("8.8.8.8", 4000),
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_approve_missing_field_is_422(consumer: _MemConsumer):
    resp = await _post(_app(consumer), "/api/v1/anomaly/approve", {"approve": True})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_proposals_payload_is_json_serializable(consumer: _MemConsumer):
    # A leaked enum anywhere in the payload would break the client; pin it.
    consumer.handle(_anomaly("limit_up"))
    resp = await _get(_app(consumer), "/api/v1/anomaly/proposals")
    json.dumps(resp.json())  # raises if not JSON-safe
