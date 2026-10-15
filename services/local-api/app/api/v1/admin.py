"""Admin endpoints — kill switch control for emergency trading halt."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.dependencies import AuthenticatedUser, build_response_meta, require_user
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])

# ---------------------------------------------------------------------------
# Module-level kill switch state
# ---------------------------------------------------------------------------

_kill_switch_active: bool = False
_kill_switch_reason: str | None = None
_kill_switch_tripped_at: int | None = None


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class TripKillSwitchRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Reason for tripping the kill switch")


class ReleaseKillSwitchRequest(BaseModel):
    reason: str = Field(..., min_length=1, description="Reason for releasing the kill switch")
    force: bool = Field(False, description="Force release even if still in danger")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/kill-switch")
async def get_kill_switch(
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
) -> APIResponse[dict]:
    """Return the current kill switch status."""
    data = {
        "active": _kill_switch_active,
        "reason": _kill_switch_reason,
        "tripped_at": _kill_switch_tripped_at,
    }
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.post("/kill-switch/trip")
async def trip_kill_switch(
    request: Request,
    body: TripKillSwitchRequest,
    user: AuthenticatedUser = Depends(require_user),
) -> APIResponse[dict]:
    """Activate the kill switch, halting all trading operations."""
    if user.role != "admin":
        raise SeleneSpaceError(ErrorCode.PERMISSION_DENIED, "Admin role required")
    global _kill_switch_active, _kill_switch_reason, _kill_switch_tripped_at

    _kill_switch_active = True
    _kill_switch_reason = body.reason
    _kill_switch_tripped_at = int(time.time() * 1000)

    logger.warning("Kill switch TRIPPED: %s", body.reason)

    # Activate the KillSwitch singleton so on_activate callbacks fire
    # (auto-cancel-all-orders, etc.) and a trade alert is sent.
    try:
        from selene_space_risk.kill_switch import KillSwitch

        KillSwitch().activate(reason=f"admin: {body.reason}")
    except Exception:
        logger.exception("Failed to activate KillSwitch singleton from admin endpoint")

    try:
        from app.core.notification_dispatch import dispatch

        dispatch("kill_switch_activated", f"KILL SWITCH ACTIVATED: {body.reason}")
    except Exception:
        pass

    # Audit the manual trip
    try:
        from app.core.audit import audit_event

        await audit_event(
            event_type="admin.kill_switch_trip",
            actor="admin",
            action="kill_switch_trip",
            resource="kill_switch",
            outcome="success",
            severity="critical",
            details={"reason": body.reason},
        )
    except Exception:
        pass

    data = {
        "active": True,
        "reason": _kill_switch_reason,
    }
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.post("/kill-switch/release")
async def release_kill_switch(
    request: Request,
    body: ReleaseKillSwitchRequest,
    user: AuthenticatedUser = Depends(require_user),
) -> APIResponse[dict]:
    """Release the kill switch, resuming trading operations."""
    if user.role != "admin":
        raise SeleneSpaceError(ErrorCode.PERMISSION_DENIED, "Admin role required")
    global _kill_switch_active, _kill_switch_reason, _kill_switch_tripped_at

    # C7 fix: force gate. If the switch is active and force=False, refuse
    # to release — return early BEFORE the unset block. The caller must
    # explicitly acknowledge the danger by setting force=True.
    if _kill_switch_active and not body.force:
        logger.warning(
            "Kill switch release REFUSED (force=False, still active): reason=%s user=%s",
            body.reason, user.id,
        )
        return APIResponse(
            data={
                "active": True,
                "error": "force_required",
                "message": "Kill switch is active; set force=true to release",
                "reason": _kill_switch_reason,
            },
            meta=build_response_meta(request),
            error={
                "code": "force_required",
                "message": "Kill switch is active; set force=true to release",
            },
        )

    if _kill_switch_active and not body.force and _kill_switch_reason:
        logger.info(
            "Kill switch release requested (reason: %s), but force=False and switch is active for: %s",
            body.reason,
            _kill_switch_reason,
        )

    _kill_switch_active = False
    _kill_switch_reason = None
    _kill_switch_tripped_at = None

    logger.info("Kill switch RELEASED: %s", body.reason)

    try:
        from app.core.notification_dispatch import dispatch

        dispatch(
            "kill_switch_released",
            f"Kill switch released: {body.reason} (force={body.force})",
        )
    except Exception:
        pass

    try:
        from app.core.audit import audit_event

        await audit_event(
            event_type="admin.kill_switch_release",
            actor="admin",
            action="kill_switch_release",
            resource="kill_switch",
            outcome="success",
            severity="warn",
            details={"reason": body.reason, "force": body.force},
        )
    except Exception:
        pass

    data = {
        "active": False,
        "reason": "released",
    }
    return APIResponse(data=data, meta=build_response_meta(request), error=None)
