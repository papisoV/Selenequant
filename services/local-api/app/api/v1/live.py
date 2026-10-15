"""Phase 5 Observability: /api/v1/live/status endpoint.

Returns 7 observability indicators for live trading:
  - ws_health: heartbeat_age_s / reconnect_count_1h / is_healthy / reason
  - wal_lag: pending journal entries (INTENT/SUBMITTED)
  - killswitch_state: is_active / reason / activated_at
  - reconciliation_diff_count: last reconciliation diff
  - shadow_mode_diff: 0 (Phase 5.5 placeholder)
  - daily_pnl: None (Phase 2a FillsLedger pending)
  - peak_equity: None (Phase 2a EquityTracker pending)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.core.dependencies import AuthenticatedUser, build_response_meta, require_user
from selene_space_types.common import APIResponse

router = APIRouter(prefix="/live", tags=["live"])


class LivePlaceOrderRequest(BaseModel):
    """Request body for POST /api/v1/live/place_order.

    Wraps LiveExecutionEngine.place_order with a simple HTTP interface.
    Approval gate (per-session first-trade) is enforced by the engine itself.
    """

    symbol: str = Field(..., description="Instrument, e.g. 'SOL/USDT' or 'SOLUSDT'")
    side: str = Field(..., description="buy or sell")
    order_type: str = Field(..., description="MARKET / LIMIT / LIMIT_MAKER")
    quantity: float = Field(
        ..., gt=0, description="Order size in BASE asset qty (e.g. SOL qty, not USDT)"
    )
    price: float | None = Field(
        None, gt=0, description="Limit price (required for LIMIT)"
    )
    leverage: int = Field(
        1,
        ge=1,
        le=125,
        description="Leverage multiplier (routed to risk_engine via risk_params)",
    )
    reduce_only: bool = Field(False, description="Close position only")
    stop_price: float | None = Field(
        None,
        gt=0,
        description="Stop-loss price (Bug C: triggers StopManager registration after fill)",
    )


def _build_ws_health(app: Any) -> dict[str, Any]:
    monitor = getattr(app.state, "ws_health_monitor", None)
    if monitor is None:
        return {
            "is_healthy": False,
            "heartbeat_age_s": None,
            "reconnect_count_1h": 0,
            "reason": "monitor_not_wired",
        }
    return {
        "is_healthy": bool(monitor.is_healthy()),
        "heartbeat_age_s": monitor.heartbeat_age_s,
        "reconnect_count_1h": int(monitor.reconnect_count_1h),
        "reason": monitor.reason,
    }


def _build_wal_lag(app: Any) -> int:
    from app.core.dependencies import get_order_journal

    journal = get_order_journal()
    if journal is None:
        return 0
    try:
        return int(journal.get_pending_count())
    except Exception:
        return 0


def _build_killswitch_state() -> dict[str, Any]:
    try:
        from selene_space_risk.kill_switch import KillSwitch

        ks = KillSwitch()  # singleton
        return {
            "is_active": bool(ks.is_active),
            "reason": ks.reason,
            "activated_at": ks.activated_at,
        }
    except Exception:
        return {"is_active": False, "reason": "", "activated_at": None}


def _build_reconciliation_diff(app: Any) -> int:
    from app.core.dependencies import get_live_engine

    engine = get_live_engine()
    if engine is None:
        return 0
    recon = getattr(engine, "_reconciliation", None)
    if recon is None:
        return 0
    try:
        return int(recon.get_last_diff_count())
    except Exception:
        return 0


def _build_audit_chain_health() -> dict[str, Any]:
    """C1 observability: hash-chain ledger state (entries/chain_ok/latest).

    Reads the pipeline's ledger if the live engine exists, else the default
    path directly; any failure degrades to available=False rather than
    breaking /status.
    """
    ledger = None
    try:
        from app.core.dependencies import get_live_engine

        engine = get_live_engine()
        ledger = getattr(
            getattr(engine, "_decision_pipeline", None), "_audit_ledger", None
        )
    except Exception:
        ledger = None
    if ledger is None:
        try:
            from selene_space_paper.audit_ledger import HashChainLedger

            ledger = HashChainLedger(
                Path.home() / ".selenespace" / "audit" / "live_decisions.jsonl"
            )
        except Exception:
            return {
                "available": False,
                "entries": 0,
                "chain_ok": False,
                "latest_action": None,
            }
    try:
        ok, _bad_seq = ledger.verify_chain()
        entries = ledger.read_chain()
        latest = entries[-1] if entries else None
        return {
            "available": True,
            "entries": len(entries),
            "chain_ok": bool(ok),
            "latest_action": (latest.payload or {}).get("action") if latest else None,
        }
    except Exception:
        return {
            "available": True,
            "entries": 0,
            "chain_ok": False,
            "latest_action": None,
        }


def _build_shadow_health(app: Any) -> dict[str, Any]:
    """Phase 5.5-P0: Aggregate 6 shadow health indicators from ShadowExecutionEngine.

    Returns a dict with all 6 gate metrics + daily_pnl + peak_equity.
    Each metric is annotated with its current value; pass/fail evaluation
    is downstream (Phase 5.5 3-day gate).
    """
    from app.core.dependencies import get_shadow_engine

    engine = get_shadow_engine()
    if engine is None:
        return {
            "ws_messages_30min": 0,
            "ws_reconnect_24h": 0,
            "strategy_signals_fired": 0,
            "killswitch_gate_ok": True,
            "wal_lag": 0,
            "equity_tracker_peak_updated": False,
            "daily_pnl": None,
            "peak_equity": None,
        }

    # ws_messages_30min: read from shadow engine ticker counter
    ws_msgs = getattr(engine, "ticker_msg_count_30min", 0) or 0

    # ws_reconnect_24h: shadow engine doesn't have its own WS connection;
    # read from WsHealthMonitor (shared, if wired). Fallback 0.
    monitor = getattr(app.state, "ws_health_monitor", None)
    ws_reconnect = (
        (getattr(monitor, "reconnect_count_1h", 0) or 0) * 24 if monitor else 0
    )

    # strategy_signals_fired: counter incremented in shadow place_order()
    signals = getattr(engine, "strategy_signals_fired", 0) or 0

    # killswitch_gate_ok: KillSwitch is singleton shared across shadow/live.
    # Shadow mode manual trigger test deactivates after test — doesn't affect live.
    try:
        from selene_space_risk.kill_switch import KillSwitch

        ks = KillSwitch()
        ks_ok = not ks.is_active
    except Exception:
        ks_ok = True

    # wal_lag: read from shadow engine's own OrderJournal
    wal = 0
    journal = getattr(engine, "_journal", None)
    if journal is not None:
        try:
            wal = int(journal.get_pending_count())
        except Exception:
            wal = 0

    # equity_tracker_peak_updated: True if EquityTracker has been updated at least once
    et_peak = getattr(engine, "equity_tracker_peak", None)
    et_updated = et_peak is not None and et_peak > 0.0

    # daily_pnl / peak_equity from shadow engine's FillsLedger/EquityTracker
    from datetime import date

    daily_pnl = None
    peak_equity = None
    fills_ledger = getattr(engine, "_fills_ledger", None)
    if fills_ledger is not None:
        try:
            daily_pnl = fills_ledger.get_daily_realized_pnl("shadow", date.today())
        except Exception:
            daily_pnl = None
    eq_tracker = getattr(engine, "_equity_tracker", None)
    if eq_tracker is not None:
        peak_equity = getattr(eq_tracker, "peak", None)

    return {
        "ws_messages_30min": ws_msgs,
        "ws_reconnect_24h": ws_reconnect,
        "strategy_signals_fired": signals,
        "killswitch_gate_ok": ks_ok,
        "wal_lag": wal,
        "equity_tracker_peak_updated": et_updated,
        "daily_pnl": daily_pnl,
        "peak_equity": peak_equity,
    }


@router.get("/status")
async def get_live_status(request: Request) -> APIResponse[dict[str, Any]]:
    """Return live trading observability snapshot."""
    app = request.app

    # Determine execution mode from settings + env var (consistent with main.py).
    import os
    from app.core.dependencies import get_settings_service

    try:
        settings_service = get_settings_service()
        settings = (
            await settings_service.get_settings()
            if settings_service is not None
            else None
        )
        exec_mode = (
            settings.get("general", {}).get("execution_mode", "paper")
            if settings
            else "paper"
        )
    except Exception:
        exec_mode = "paper"
    env_mode = os.environ.get("SELENE_EXECUTION_MODE", "").strip().lower()
    if env_mode in ("shadow", "live", "paper"):
        exec_mode = env_mode

    if exec_mode == "shadow":
        # Shadow mode: use shadow engine observability, not live engine
        shadow_health = _build_shadow_health(app)
        payload: dict[str, Any] = {
            "ws_health": _build_ws_health(app),
            "wal_lag": shadow_health["wal_lag"],
            "killswitch_state": _build_killswitch_state(),
            "reconciliation_diff_count": 0,  # not applicable in shadow mode
            "shadow_mode_diff": {
                "ws_messages_30min": shadow_health["ws_messages_30min"],
                "ws_reconnect_24h": shadow_health["ws_reconnect_24h"],
                "strategy_signals_fired": shadow_health["strategy_signals_fired"],
                "killswitch_gate_ok": shadow_health["killswitch_gate_ok"],
                "wal_lag": shadow_health["wal_lag"],
                "equity_tracker_peak_updated": shadow_health[
                    "equity_tracker_peak_updated"
                ],
            },
            "daily_pnl": shadow_health["daily_pnl"],
            "peak_equity": shadow_health["peak_equity"],
            "audit_chain_health": _build_audit_chain_health(),
        }
    else:
        # Live/paper mode: existing behavior + Phase 2b FillsLedger/EquityTracker wire
        payload = {
            "ws_health": _build_ws_health(app),
            "wal_lag": _build_wal_lag(app),
            "killswitch_state": _build_killswitch_state(),
            "reconciliation_diff_count": _build_reconciliation_diff(app),
            "shadow_mode_diff": 0,
            "daily_pnl": None,
            "peak_equity": None,
            "audit_chain_health": _build_audit_chain_health(),
        }
        # Live mode: wire FillsLedger/EquityTracker if available
        if exec_mode == "live":
            from app.core.dependencies import get_live_engine

            engine = get_live_engine()
            if engine is not None:
                fills_ledger = getattr(engine, "_fills_ledger", None)
                if fills_ledger is not None:
                    try:
                        from datetime import date

                        payload["daily_pnl"] = fills_ledger.get_daily_realized_pnl(
                            "live", date.today()
                        )
                    except Exception:
                        pass
                eq_tracker = getattr(engine, "_equity_tracker", None)
                if eq_tracker is not None:
                    peak = getattr(eq_tracker, "peak", None)
                    if peak is not None:
                        payload["peak_equity"] = peak
    return APIResponse(
        success=True,
        data=payload,
        meta=build_response_meta(request),
    )


# ---------------------------------------------------------------------------
# Phase 6: per-session first-trade approval gate
# ---------------------------------------------------------------------------


@router.get("/approval-status")
async def get_approval_status(request: Request) -> APIResponse[dict[str, Any]]:
    """Return current first-trade approval state for the live engine.

    Frontend polls this to decide whether to show the confirmation dialog.
    Approval resets on every engine restart.
    """
    from app.core.dependencies import get_live_engine

    engine = get_live_engine()
    approved = bool(getattr(engine, "first_trade_approved", False)) if engine else False
    return APIResponse(
        success=True,
        data={"approved": approved, "engine_wired": engine is not None},
        meta=build_response_meta(request),
    )


@router.post("/approve")
async def approve_first_trade(
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
) -> APIResponse[dict[str, Any]]:
    """Grant per-session first-trade approval for the live engine.

    Two-step confirmation: frontend confirmation dialog calls this endpoint,
    then LiveExecutionEngine.place_order will be allowed for this session.
    Approval is per-session — resets on every engine restart.

    Safety: this endpoint ONLY flips the approval flag. It does NOT place any
    order. The user must still call place_order separately after approval.

    Per-user approve (C3 fix): the engine tracks ``_approved_users`` set;
    approve_first_trade(user.id) adds the user, place_order checks
    user.id in set. A singleton engine no longer grants global approval.
    """
    from app.core.dependencies import get_live_engine

    engine = get_live_engine()
    if engine is None:
        return APIResponse(
            success=False,
            data={"approved": False, "error": "live_engine_not_wired"},
            meta=build_response_meta(request),
        )
    # Idempotent — calling twice is fine.
    approve_fn = getattr(engine, "approve_first_trade", None)
    if approve_fn is None or not callable(approve_fn):
        return APIResponse(
            success=False,
            data={"approved": False, "error": "approve_method_not_available"},
            meta=build_response_meta(request),
        )
    # Per-user approve: pass user.id so the engine can add to _approved_users.
    try:
        approve_fn(user.id)
    except TypeError:
        # Backward compat: engine signature still takes no args.
        approve_fn()
    return APIResponse(
        success=True,
        data={"approved": True, "engine_wired": True, "user": user.id},
        meta=build_response_meta(request),
    )


@router.post("/place_order")
async def place_order(
    req: LivePlaceOrderRequest,
    request: Request,
    user: AuthenticatedUser = Depends(require_user),
) -> APIResponse[dict[str, Any]]:
    """Place an order via LiveExecutionEngine.place_order (full prod path).

    Routes through: live_engine → risk_engine (incl. MarginUtilizationRule) →
    KillSwitch → BinanceRESTClient. Approval gate (per-session first-trade)
    is enforced by the engine — call POST /api/v1/live/approve first.

    This endpoint exists for retest scripts and manual one-shot orders. For
    normal trading UI flows, use POST /api/v1/trading/orders instead (which
    converts USDT notional → base qty automatically).

    quantity is BASE asset qty (e.g. SOL qty, not USDT notional).

    Per-user approve (C3 fix): the engine checks ``user.id in
    _approved_users`` before submitting. Cross-user approve → 403-equivalent
    RuntimeError from engine, surfaced as place_order_blocked.
    """
    from app.core.dependencies import get_live_engine

    engine = get_live_engine()
    if engine is None:
        return APIResponse(
            success=False,
            data={"error": "live_engine_not_wired"},
            meta=build_response_meta(request),
            error={
                "code": "live_engine_not_wired",
                "message": "Live engine not initialized",
            },
        )

    # Translate str → enum for LiveExecutionEngine.place_order
    from selene_space_types.trading import OrderSide, OrderType

    try:
        side_enum = OrderSide(req.side.lower())
    except Exception:
        return APIResponse(
            success=False,
            data={"error": f"invalid side: {req.side}"},
            meta=build_response_meta(request),
            error={
                "code": "invalid_side",
                "message": f"side must be buy/sell, got {req.side}",
            },
        )
    try:
        ot_enum = OrderType(req.order_type.lower())
    except Exception:
        return APIResponse(
            success=False,
            data={"error": f"invalid order_type: {req.order_type}"},
            meta=build_response_meta(request),
            error={
                "code": "invalid_order_type",
                "message": f"order_type invalid: {req.order_type}",
            },
        )

    # Per-user approve gate (C3 fix): check user.id in engine._approved_users
    # BEFORE calling place_order. Cross-user approve → blocked here. The
    # engine also enforces this internally (defense-in-depth) but the HTTP
    # layer returns a structured error instead of a RuntimeError.
    approved_users = getattr(engine, "_approved_users", None)
    if approved_users is not None and user.id not in approved_users:
        return APIResponse(
            success=False,
            data={"error": f"first-trade approval not granted for user {user.id}"},
            meta=build_response_meta(request),
            error={
                "code": "approval_required",
                "message": f"Call POST /api/v1/live/approve first (user {user.id} not approved)",
            },
        )

    try:
        order = await engine.place_order(
            account_id="live",
            symbol=req.symbol,
            side=side_enum,
            order_type=ot_enum,
            quantity=req.quantity,
            price=req.price,
            leverage=req.leverage,
            reduce_only=req.reduce_only,
            stop_price=req.stop_price,
            user_id=user.id,
        )
    except RuntimeError as e:
        # Approval gate / KillSwitch / SELENE_ENABLE_REAL_TRADING blocks
        msg = str(e)
        return APIResponse(
            success=False,
            data={"error": msg},
            meta=build_response_meta(request),
            error={"code": "place_order_blocked", "message": msg},
        )
    except Exception as e:
        # risk_engine CRITICAL, exchange API errors, etc.
        return APIResponse(
            success=False,
            data={"error": str(e)},
            meta=build_response_meta(request),
            error={"code": "place_order_failed", "message": str(e)},
        )

    # order is an Order dataclass — convert to dict for JSON response
    order_dict: dict[str, Any]
    if hasattr(order, "model_dump"):
        order_dict = order.model_dump(mode="json")
    elif hasattr(order, "__dict__"):
        order_dict = {k: v for k, v in vars(order).items() if not k.startswith("_")}
    else:
        order_dict = {"raw": str(order)}

    return APIResponse(
        success=True,
        data={
            "order": order_dict,
            "approved": getattr(engine, "first_trade_approved", False),
        },
        meta=build_response_meta(request),
    )


def _test_endpoints_disabled_response(request: Request) -> APIResponse[dict[str, Any]]:
    """Standard 403-style response when SELENE_ENABLE_TEST_ENDPOINTS is off."""
    return APIResponse(
        success=False,
        data={"error": "test_endpoints_disabled"},
        meta=build_response_meta(request),
        error={
            "code": "test_endpoints_disabled",
            "message": "Set SELENE_ENABLE_TEST_ENDPOINTS=true to enable test endpoints",
        },
    )


def _is_test_endpoints_enabled() -> bool:
    import os

    return os.getenv("SELENE_ENABLE_TEST_ENDPOINTS", "").strip().lower() in (
        "true",
        "1",
        "yes",
        "on",
    )


class SimulateFillRequest(BaseModel):
    """Request body for POST /api/v1/live/test/simulate_fill (test-only).

    Manually marks an order FILLED + triggers StopManager registration.
    Bypasses WS user data stream which can drop fill events when proxy
    is unstable. Gate via SELENE_ENABLE_TEST_ENDPOINTS env var.
    """

    order_id: str = Field(..., description="Internal order ID (e.g. 'live-xxx')")
    filled_quantity: float | None = Field(
        None, gt=0, description="Override filled qty; defaults to order.quantity"
    )
    avg_price: float | None = Field(
        None, gt=0, description="Override avg fill price; defaults to order.price or 0"
    )


@router.post("/test/simulate_fill")
async def simulate_fill(
    req: SimulateFillRequest, request: Request
) -> APIResponse[dict[str, Any]]:
    """Mark an order FILLED + trigger stop registration (test-only).

    Gate: SELENE_ENABLE_TEST_ENDPOINTS must be 'true' / '1' / 'yes'.
    Returns 403 if disabled. Used by e2e tests to verify StopManager
    trigger chain without relying on WS user data stream.
    """
    if not _is_test_endpoints_enabled():
        return _test_endpoints_disabled_response(request)

    from app.core.dependencies import get_live_engine

    engine = get_live_engine()
    if engine is None:
        return APIResponse(
            success=False,
            data={"error": "live_engine_not_wired"},
            meta=build_response_meta(request),
            error={
                "code": "live_engine_not_wired",
                "message": "Live engine not initialized",
            },
        )

    try:
        order = await engine.simulate_fill(
            req.order_id,
            filled_quantity=req.filled_quantity,
            avg_price=req.avg_price,
        )
    except ValueError as e:
        return APIResponse(
            success=False,
            data={"error": str(e)},
            meta=build_response_meta(request),
            error={"code": "order_not_found", "message": str(e)},
        )

    order_dict: dict[str, Any]
    if hasattr(order, "model_dump"):
        order_dict = order.model_dump()
    else:
        order_dict = {"raw": str(order)}

    return APIResponse(
        success=True,
        data={
            "order": order_dict,
            "stop_registered": getattr(engine, "_stop_manager", None) is not None,
        },
        meta=build_response_meta(request),
    )


class PushTickRequest(BaseModel):
    """Request body for POST /api/v1/live/test/push_tick (test-only).

    Manually push a price tick into LiveEngine.on_price_tick to verify
    StopManager trigger chain. Gate via SELENE_ENABLE_TEST_ENDPOINTS.
    """

    symbol: str = Field(..., description="Instrument, e.g. 'ETHUSDT' or 'ETH/USDT'")
    price: float = Field(..., gt=0, description="Price tick to push")


@router.post("/test/push_tick")
async def push_tick(
    req: PushTickRequest, request: Request
) -> APIResponse[dict[str, Any]]:
    """Push a price tick into LiveEngine.on_price_tick (test-only).

    Gate: SELENE_ENABLE_TEST_ENDPOINTS must be 'true' / '1' / 'yes'.
    Returns the count of stops triggered by this tick.
    """
    if not _is_test_endpoints_enabled():
        return _test_endpoints_disabled_response(request)

    from app.core.dependencies import get_live_engine

    engine = get_live_engine()
    if engine is None:
        return APIResponse(
            success=False,
            data={"error": "live_engine_not_wired"},
            meta=build_response_meta(request),
            error={
                "code": "live_engine_not_wired",
                "message": "Live engine not initialized",
            },
        )

    triggered_before = (
        len(engine._pending_stop_tasks) if hasattr(engine, "_pending_stop_tasks") else 0
    )
    await engine.on_price_tick(req.symbol, req.price)
    import asyncio

    # Give triggered tasks a moment to start
    await asyncio.sleep(0.05)
    triggered_after = (
        len(engine._pending_stop_tasks) if hasattr(engine, "_pending_stop_tasks") else 0
    )

    return APIResponse(
        success=True,
        data={
            "symbol": req.symbol,
            "price": req.price,
            "pending_tasks_before": triggered_before,
            "pending_tasks_after": triggered_after,
            "stop_manager_active": len(engine._stop_manager.list_active_stops())
            if getattr(engine, "_stop_manager", None)
            else 0,
        },
        meta=build_response_meta(request),
    )
