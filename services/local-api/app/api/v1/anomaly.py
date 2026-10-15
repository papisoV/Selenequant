"""A-share anomaly proposal endpoints — list pending proposals, cast a verdict.

The suggest tier parks a mapped ``TradeIntent`` for every fresh A-share anomaly
as a pending proposal (see :mod:`app.astock_anomaly_decision`). These endpoints
are the human side of that loop: read what is pending, then approve or reject.

**Approving does not place an order.** It appends an ``anomaly_approval``
record (``executes_order=False``) to the production hash-chain ledger — a human
confirming the read, nothing more. There is deliberately no execution path
behind this endpoint; wiring one is the plan §5 preflight (A-share limit/T+1
rules in the production RiskEngine roster + a real-money gate + a second
security review), not a change to make here.

Auth mirrors ``POST /agent/approve``: approval is a security action, so the
write endpoint demands a real user (``require_user``); the read endpoint takes
``optional_user``. Under ``SELENE_AUTH_DISABLED=1`` the local-only bypass
applies, same as the rest of the API.

The consumer is resolved lazily per request via the dependency getter so a
test can override it, and a crypto-only deployment (consumer ``None``) answers
with an empty list / ``unavailable`` rather than raising.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.dependencies import (
    AuthenticatedUser,
    build_response_meta,
    get_anomaly_decision_consumer,
    optional_user,
    require_user,
)
from selene_space_types.common import APIResponse, ErrorInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/anomaly", tags=["anomaly"])


def _ok(request: Request, data: Any) -> APIResponse[Any]:
    return APIResponse(data=data, meta=build_response_meta(request))


class AnomalyApproveRequest(BaseModel):
    """Body for POST /anomaly/approve — resolve one pending proposal."""

    dedup_id: str = Field(
        ..., min_length=1, description="Proposal id from GET /anomaly/proposals"
    )
    approve: bool = Field(
        ...,
        description="True to record approval, False to reject (no order either way)",
    )


@router.get("/proposals")
async def list_proposals(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
    consumer: Any = Depends(get_anomaly_decision_consumer),
) -> APIResponse[dict[str, Any]]:
    """List pending A-share anomaly proposals (oldest first).

    ``mode`` reports the consumer's resolved ``SELENE_ANOMALY_MODE``;
    ``wired`` is False on a crypto-only deployment where no consumer exists, so
    the panel can distinguish "nothing pending" from "not available here".
    """
    if consumer is None:
        return _ok(request, {"proposals": [], "mode": None, "wired": False})
    proposals = [p.to_payload() for p in consumer.list_pending()]
    return _ok(request, {"proposals": proposals, "mode": consumer.mode, "wired": True})


@router.post("/approve")
async def approve_proposal(
    req: AnomalyApproveRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
    consumer: Any = Depends(get_anomaly_decision_consumer),
) -> APIResponse[dict[str, Any]]:
    """Record a verdict on one pending proposal.

    The response echoes ``executes_order: False`` so a client (and an auditor
    reading the wire) can never mistake the call for placing an order. An
    unknown or already-resolved id is a ``no_pending_proposal`` error, not a
    success — a replayed click must not read as a second approval.
    """
    if consumer is None:
        return APIResponse(
            error=ErrorInfo(
                code="unavailable",
                message="anomaly decision consumer is not wired in this deployment",
            ),
            meta=build_response_meta(request),
        )

    result = consumer.decide(req.dedup_id, approve=req.approve)
    if result == "no_pending_proposal":
        return APIResponse(
            error=ErrorInfo(
                code="no_pending_proposal",
                message="no pending proposal for that id (unknown, expired, or already resolved)",
            ),
            meta=build_response_meta(request),
        )

    logger.info(
        "anomaly_proposal_http dedup_id=%s approve=%s result=%s",
        req.dedup_id,
        req.approve,
        result,
    )
    return _ok(
        request,
        {"resolved": True, "result": result, "executes_order": False},
    )
