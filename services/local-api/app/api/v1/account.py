"""Account endpoints — data export, deletion request, and cancellation."""

from __future__ import annotations

import logging
import time

from fastapi import APIRouter, Depends, Request

from app.core.dependencies import (
    AuthenticatedUser,
    build_response_meta,
    get_chat_session_repo,
    get_db_manager,
    get_strategy_repo,
    optional_user,
)
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/me", tags=["account"])

# ---------------------------------------------------------------------------
# Module-level deletion state
# ---------------------------------------------------------------------------

_deletion_requested: bool = False
_deletion_requested_at: int | None = None

GRACE_PERIOD_DAYS: int = 7


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/export")
async def export_user_data(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Collect and return all user data for export (GDPR-style)."""
    strategies_count = 0
    backtests_count = 0
    chat_sessions_count = 0
    data: dict = {}

    # Strategies
    strategy_repo = get_strategy_repo()
    if strategy_repo is not None:
        try:
            strategies = await strategy_repo.list_strategies(user_id=user.id)
            strategies_count = len(strategies) if strategies else 0
            data["strategies"] = [
                s if isinstance(s, dict) else str(s) for s in (strategies or [])
            ]
        except Exception:
            logger.debug("Failed to export strategies", exc_info=True)

    # Backtests
    db_manager = get_db_manager()
    if db_manager is not None:
        try:
            backtests_count = 0  # placeholder — backtest counting via db_manager
            data["backtests"] = []
        except Exception:
            logger.debug("Failed to export backtests", exc_info=True)

    # Chat sessions
    chat_repo = get_chat_session_repo()
    if chat_repo is not None:
        try:
            sessions = await chat_repo.list_sessions(user_id=user.id)
            chat_sessions_count = len(sessions) if sessions else 0
            data["chat_sessions"] = [s if isinstance(s, dict) else str(s) for s in (sessions or [])]
        except Exception:
            logger.debug("Failed to export chat sessions", exc_info=True)

    result = {
        "user_id": user.id,
        "exported_at": int(time.time() * 1000),
        "strategies_count": strategies_count,
        "backtests_count": backtests_count,
        "chat_sessions_count": chat_sessions_count,
        "data": data,
    }
    return APIResponse(data=result, meta=build_response_meta(request), error=None)


@router.get("/deletion-status")
async def get_deletion_status(
    request: Request,
) -> APIResponse[dict]:
    """Return the current account deletion request status."""
    data = {
        "deletion_requested": _deletion_requested,
        "requested_at": _deletion_requested_at,
        "grace_period_days": GRACE_PERIOD_DAYS,
    }
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.delete("")
async def request_deletion(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Mark the account for deletion. Grace period allows cancellation."""
    global _deletion_requested, _deletion_requested_at

    if _deletion_requested:
        raise SeleneSpaceError(
            code=ErrorCode.CONFLICT,
            message="Account deletion already requested",
            details={"requested_at": _deletion_requested_at},
        )

    _deletion_requested = True
    _deletion_requested_at = int(time.time() * 1000)

    logger.info("Account deletion requested by user %s", user.id)

    data = {
        "deletion_requested": True,
        "grace_period_days": GRACE_PERIOD_DAYS,
        "message": f"Account deletion requested. You have {GRACE_PERIOD_DAYS} days to cancel.",
    }
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.put("/cancel-deletion")
async def cancel_deletion(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Cancel a pending account deletion request."""
    global _deletion_requested, _deletion_requested_at

    if not _deletion_requested:
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message="No deletion request is pending",
        )

    _deletion_requested = False
    _deletion_requested_at = None

    logger.info("Account deletion cancelled by user %s", user.id)

    data = {
        "deletion_requested": False,
        "message": "Account deletion cancelled.",
    }
    return APIResponse(data=data, meta=build_response_meta(request), error=None)
