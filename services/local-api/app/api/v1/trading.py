"""Unified trading endpoints — mode-agnostic order, position, and balance API.

All endpoints delegate to the current ExecutionEngine (Paper or Live).
The mode is selected at startup or switched via PUT /trading/mode.
Risk engine pre-checks are applied before every order placement.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.core.dependencies import (
    AuthenticatedUser,
    build_response_meta,
    get_execution_engine,
    get_execution_mode,
    get_live_engine,
    get_market_service,
    get_order_journal,
    get_risk_engine,
    get_shadow_engine,
    optional_user,
    set_execution_mode,
)
from selene_space_types.common import APIResponse
from selene_space_types.errors import SeleneSpaceError, ErrorCode
from selene_space_types.latency import LatencyStats
from selene_space_types.trading import (
    BalanceUpdate,
    ExecutionMode,
    Fill,
    MarginMode,
    Order,
    OrderSide,
    OrderStatus,
    OrderType,
    Position,
    PositionSide,
)

if TYPE_CHECKING:
    from selene_space_exchange.execution.confirmation import OrderConfirmationManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/trading", tags=["trading"])

DEFAULT_ACCOUNT = "paper-default"


def _resolve_mark_price(symbol: str) -> float | None:
    """Pull the most recent close price for a symbol from market_svc.

    Used to convert USDT notional → base asset quantity for market orders
    (LiveExecutionEngine.place_order expects base qty, not USDT notional).
    Returns None if no market data is available for the symbol.
    """
    try:
        svc = get_market_service()
        if svc is None:
            return None
        # Try a few symbol variants — market_svc may store "BTCUSDT" or "BTC-USDT".
        candidates = [
            symbol,
            symbol.replace("/", "-"),
            symbol.replace("/", ""),
            symbol.replace("-", "/"),
        ]
        for sym in candidates:
            for ex, mt in getattr(svc, "sources", {}).keys() or [("binance", "spot")]:
                klines = svc.get_klines(sym, exchange=ex, market_type=mt, limit=1)
                if klines:
                    return float(klines[-1].close)
        return None
    except Exception:
        logger.debug("mark price resolution failed for %s", symbol, exc_info=True)
        return None


def _verify_trading_ownership(account_id: str, user: AuthenticatedUser) -> None:
    """Verify the user owns the account. Delegates to paper module's registry.

    Fail-closed (C2 fix): ``ImportError`` re-raises rather than silently
    passing. ``SELENE_AUTH_DISABLED=1`` audited bypass for ``local`` user.
    """
    from app.core.auth_flags import (
        audit_auth_disabled_bypass,
        is_auth_disabled,
        is_auth_failclosed,
    )

    if user.id == "local":
        if is_auth_disabled():
            audit_auth_disabled_bypass(user.id, f"trading:{account_id}")
            return
        # Without the audited bypass, "local" still needs the ownership
        # check (fall through). Fail-open rollback path below.
        if not is_auth_failclosed():
            return

    try:
        from app.api.v1.paper import _owner_of
    except ImportError as exc:
        # Fail-closed: missing ownership resolver is a denial, not a grant.
        if is_auth_failclosed():
            logger.error(
                "trading_ownership_check_failclosed import_error account=%s user=%s err=%s",
                account_id,
                user.id,
                exc,
            )
            raise
        # Fail-open rollback path (emergency only) — preserves v1 behavior.
        return

    owner = _owner_of(account_id)
    if owner is not None and owner != user.id and owner != "local":
        raise SeleneSpaceError(
            ErrorCode.PERMISSION_DENIED, "You do not have access to this account"
        )


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------


class TradingModeResponse(BaseModel):
    mode: str = Field(..., description="Current execution mode: 'paper' or 'live'")


class TradingModeRequest(BaseModel):
    mode: ExecutionMode = Field(..., description="Target mode: 'paper' or 'live'")


class PlaceOrderRequest(BaseModel):
    symbol: str = Field(..., description="Instrument, e.g. 'BTC-USDT-SWAP'")
    side: OrderSide
    order_type: OrderType
    quantity: float = Field(..., gt=0, description="Order size in USDT")
    price: float | None = Field(None, ge=0)
    stop_price: float | None = Field(None, ge=0)
    leverage: int = Field(1, ge=1, le=125, description="Leverage")
    margin_mode: MarginMode = Field(MarginMode.CROSS)
    pos_side: PositionSide = Field(PositionSide.LONG)
    reduce_only: bool = Field(False)
    account_id: str = Field(DEFAULT_ACCOUNT, description="Account ID")
    confirm_risk: bool = Field(
        False, description="User confirmed they saw and accept risk alerts"
    )
    time_in_force: str | None = Field(None, description="Time in force: GTC, IOC, FOK")
    client_order_id: str | None = Field(
        None, description="Client-supplied order ID for idempotency"
    )
    tp_price: float | None = Field(None, gt=0, description="Take-profit trigger price")
    sl_price: float | None = Field(None, gt=0, description="Stop-loss trigger price")


class CancelOrderRequest(BaseModel):
    order_id: str


# ---------------------------------------------------------------------------
# Mode endpoints
# ---------------------------------------------------------------------------


@router.get("/mode", response_model=TradingModeResponse)
async def get_mode(
    mode: str = Depends(get_execution_mode),
    user: AuthenticatedUser = Depends(optional_user),
) -> TradingModeResponse:
    """Get the current trading execution mode."""
    return TradingModeResponse(mode=mode)


@router.put("/mode", response_model=TradingModeResponse)
async def set_mode(
    req: TradingModeRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> TradingModeResponse:
    """Switch trading execution mode.

    Five graduated modes:
      - PAPER        — simulation only
      - SHADOW       — intercept REST + journal, no real fills
      - CONFIRM      — requires preview_id + manual confirmation
      - LIMITED_AUTO — hard limits (daily notional, position, frequency)
      - FULL_AUTO    — only risk engine checks

    LIVE-gated modes (CONFIRM, LIMITED_AUTO, FULL_AUTO, plus legacy LIVE
    alias) require the LiveExecutionEngine to be configured (API keys in
    keyring) and reachable via a connectivity check. PAPER and SHADOW
    are always allowed.
    """
    # Treat legacy "live" as CONFIRM — safest real-money default.
    if req.mode == ExecutionMode.PAPER or req.mode == ExecutionMode.SHADOW:
        # Always allowed — no live engine required.
        pass
    else:
        # CONFIRM / LIMITED_AUTO / FULL_AUTO (and any LIVE alias) require live engine.
        live_engine = get_live_engine()
        if live_engine is None:
            raise HTTPException(
                status_code=400,
                detail="Live engine not configured. Add exchange API keys to keyring first.",
            )
        try:
            client = getattr(live_engine, "_client", None)
            if client is None or not await client.test_connectivity():
                raise HTTPException(
                    status_code=400,
                    detail="Live engine connectivity check failed. Check API keys and network.",
                )
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Live engine not ready: {exc}",
            ) from exc
    set_execution_mode(req.mode.value)
    # Sync ExecutionModeGuard singleton so _execute_signal's guard.check() uses
    # the new mode's hard limits (e.g. LIMITED_AUTO's $200/symbol, $2000/day cap).
    try:
        from app.core.dependencies import get_mode_guard

        _guard = get_mode_guard()
        if _guard is not None:
            _guard.mode = req.mode  # ExecutionModeGuard exposes `mode` setter
    except Exception:
        logger.warning("Failed to sync ExecutionModeGuard mode", exc_info=True)
    logger.info("Execution mode set to %s by user=%s", req.mode.value, user.id)
    return TradingModeResponse(mode=req.mode.value)


# ---------------------------------------------------------------------------
# Kill Switch endpoints
# ---------------------------------------------------------------------------


class KillSwitchActivateRequest(BaseModel):
    reason: str = Field(
        default="Manual activation", description="Why kill switch is being engaged"
    )
    close_positions: bool = Field(
        default=False,
        description=(
            "If true, also market-close all open positions on the exchange. "
            "Defaults to False (cancel open orders only — see registered callbacks)."
        ),
    )


class KillSwitchResponse(BaseModel):
    active: bool
    activated_at: float | None
    reason: str


@router.post("/kill-switch", response_model=KillSwitchResponse)
async def activate_kill_switch(
    req: KillSwitchActivateRequest,
    user: AuthenticatedUser = Depends(optional_user),
) -> KillSwitchResponse:
    """Activate the global kill switch — blocks all new orders immediately.

    Side effects:
      - Persists active state to disk (survives restart)
      - Fires registered on_activate callbacks (e.g. cancel-all-open-orders
        on the exchange)
      - If ``close_positions=True``, also market-closes all open positions
        on the configured live trading client

    The kill switch is a global singleton — activating it affects every
    strategy and every future order placement path (paper, shadow, live).
    """
    from selene_space_risk.kill_switch import KillSwitch

    ks = KillSwitch()  # singleton
    ks.activate(reason=req.reason)

    if req.close_positions:
        live_engine = get_live_engine()
        client = getattr(live_engine, "_client", None) if live_engine else None
        if client is None:
            logger.warning(
                "Kill switch activated with close_positions=True but no live client; "
                "only open-order cancellation callbacks will run",
            )
        else:
            try:
                # Best-effort market close of all positions.
                # Most adapters expose close_all_positions(); guard with getattr.
                close_all = getattr(client, "close_all_positions", None)
                if callable(close_all):
                    await close_all()
                    logger.info(
                        "Kill switch: closed all open positions via live client"
                    )
                else:
                    logger.warning(
                        "Kill switch: client has no close_all_positions(); "
                        "positions not auto-closed",
                    )
            except Exception:
                logger.exception("Kill switch: close_all_positions failed")

    # Fire trade alert so operator is notified out-of-band.
    try:
        from app.core.notification_dispatch import dispatch

        dispatch(
            "kill_switch_activated",
            f"KILL SWITCH ACTIVATED\nReason: {req.reason}\n"
            f"Close positions: {req.close_positions}\nUser: {user.id}",
        )
    except Exception:
        logger.debug(
            "send_trade_alert failed during kill-switch activation", exc_info=True
        )

    status = ks.status()
    return KillSwitchResponse(
        active=bool(status["active"]),
        activated_at=status["activated_at"]
        if isinstance(status["activated_at"], (int, float))
        else None,
        reason=str(status["reason"] or ""),
    )


@router.get("/trailing-status")
async def get_trailing_status(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """Tiered trailing stop state for every tracked symbol.

    Empty list when the tracker is not wired (e.g. runner not started).
    """
    from app.core.dependencies import get_tiered_trailing_tracker

    tracker = get_tiered_trailing_tracker()
    statuses: list[dict[str, Any]] = []
    if tracker is not None:
        for sym in tracker.symbols():
            s = tracker.status(sym)
            if s is not None:
                statuses.append(s)
    return APIResponse(data=statuses, meta=build_response_meta(request), error=None)


@router.post("/kill-switch/reset", response_model=KillSwitchResponse)
async def reset_kill_switch(
    user: AuthenticatedUser = Depends(optional_user),
) -> KillSwitchResponse:
    """Reset (deactivate) the kill switch — resume normal operations.

    Use with caution: this allows new orders to flow again. Typically
    only called after the operator has verified the root-cause condition
    that triggered the kill switch has been resolved.
    """
    from selene_space_risk.kill_switch import KillSwitch

    ks = KillSwitch()  # singleton
    ks.deactivate()
    logger.info("Kill switch reset by user=%s", user.id)

    try:
        from app.core.notification_dispatch import dispatch

        dispatch(
            "kill_switch_released",
            f"Kill switch RESET by user={user.id}. Operations resumed.",
        )
    except Exception:
        logger.debug("send_trade_alert failed during kill-switch reset", exc_info=True)

    status = ks.status()
    return KillSwitchResponse(
        active=bool(status["active"]),
        activated_at=status["activated_at"]
        if isinstance(status["activated_at"], (int, float))
        else None,
        reason=str(status["reason"] or ""),
    )


@router.get("/kill-switch", response_model=KillSwitchResponse)
async def get_kill_switch_status(
    user: AuthenticatedUser = Depends(optional_user),
) -> KillSwitchResponse:
    """Return the current kill switch state."""
    from selene_space_risk.kill_switch import KillSwitch

    ks = KillSwitch()  # singleton
    status = ks.status()
    return KillSwitchResponse(
        active=bool(status["active"]),
        activated_at=status["activated_at"]
        if isinstance(status["activated_at"], (int, float))
        else None,
        reason=str(status["reason"] or ""),
    )


# ---------------------------------------------------------------------------
# Order endpoints
# ---------------------------------------------------------------------------


@router.post("/orders")
async def place_order(
    req: PlaceOrderRequest,
    request: Request,
    engine: object = Depends(get_live_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Place a new order. Risk alerts are returned as advisory — only CRITICAL blocks.

    If risk alerts are triggered (non-CRITICAL), the order still goes through
    but the response includes `risk_alerts` for the UI to show as popups.
    CRITICAL alerts (blacklist, honeypot) hard-block the order with 403.
    """
    _verify_trading_ownership(req.account_id, user)
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Live execution engine not configured. Please add API keys.",
        )

    # Kill switch — hard block when active.
    try:
        from selene_space_risk.kill_switch import KillSwitch

        ks = KillSwitch()
        if ks.is_active:
            raise HTTPException(
                status_code=403,
                detail=f"Kill switch active: {ks.reason}. Reset via POST /trading/kill-switch/reset.",
            )
    except HTTPException:
        raise
    except Exception:
        logger.debug("Kill switch check failed", exc_info=True)

    # Execution mode guard — confirm mode allows direct /orders placement.
    # CONFIRM mode requires the preview+confirm flow; reject bare /orders.
    mode_str = get_execution_mode()
    if mode_str == "confirm":
        raise HTTPException(
            status_code=403,
            detail="CONFIRM mode requires POST /trading/orders-preview then /trading/orders-confirm.",
        )

    # ExecutionModeGuard hard limits for LIMITED_AUTO / FULL_AUTO.
    # Strategy auto-trades go through StrategyRunner._execute_signal which
    # calls guard.check() + record_order() — but manual /trading/orders
    # bypasses that path, so we enforce here too. Without this, a manual
    # 500 USDT order in LIMITED_AUTO would bypass the $200/symbol cap.
    if mode_str in ("limited_auto", "full_auto"):
        try:
            from app.core.dependencies import get_mode_guard
            from selene_space_exchange.execution.mode_guard import ModeGuardError

            guard = get_mode_guard()
            if guard is not None:
                guard.check(
                    symbol=req.symbol,
                    side=req.side,
                    order_type=req.order_type,
                    quantity=req.quantity,  # USDT notional per master API
                    leverage=req.leverage or 1,
                    has_stop_loss=bool(req.stop_price),
                )
                guard.record_order(symbol=req.symbol, quantity=req.quantity)
        except ModeGuardError as exc:
            raise HTTPException(
                status_code=403,
                detail=f"Mode guard blocked order: {exc.reason}",
            )

    risk_alerts_data: list[dict] = []
    risk_blocked = False
    risk_reason = ""

    # Risk pre-check (advisory, not hard-block)
    risk_engine = get_risk_engine()
    if risk_engine is not None:
        try:
            import time

            from selene_space_types.agent import TradeIntent

            intent = TradeIntent(
                symbol=req.symbol,
                side=req.side,
                quantity=req.quantity,
                price=req.price,
                order_type=req.order_type,
                reasoning="User manual order",
                confidence=1.0,
                risk_params={"stop_loss": req.stop_price} if req.stop_price else {},
                timestamp=int(time.time() * 1000),
            )

            # Build context from current account state
            ctx: dict = {}
            try:
                balances = await engine.get_balances(req.account_id)  # type: ignore[attr-defined]
                if balances:
                    ctx["total_equity"] = sum(b.free + b.locked for b in balances)
                positions = await engine.get_positions(req.account_id)  # type: ignore[attr-defined]
                if positions:
                    ctx["positions"] = {
                        p.symbol: p.quantity * p.avg_entry_price for p in positions
                    }
            except Exception:
                logger.debug(
                    "Risk pre-check: failed to load balances or positions for context",
                    exc_info=True,
                )

            decision = risk_engine.pre_check(intent, ctx)

            # Serialize alerts for response
            risk_alerts_data = [
                {
                    "rule_id": a.rule_id,
                    "level": a.level.value,
                    "message": a.message,
                    "context": a.context,
                    "timestamp": a.timestamp,
                }
                for a in decision.alerts
            ]

            if not decision.allowed:
                # CRITICAL — hard block
                risk_blocked = True
                risk_reason = decision.reason
            elif decision.alerts and not req.confirm_risk:
                # Non-critical alerts but user hasn't confirmed —
                # don't place order yet, return alerts for UI confirmation
                return APIResponse(
                    data={
                        "order": None,
                        "risk_alerts": risk_alerts_data,
                        "has_risk_alerts": True,
                    },
                    meta=build_response_meta(request),
                    error=None,
                )

        except ImportError:
            logger.debug("selene_space_types.agent not available for risk check")
        except Exception:
            logger.warning("Risk pre-check error (allowing trade)", exc_info=True)

    if risk_blocked:
        raise HTTPException(
            status_code=403,
            detail=f"Risk check failed: {risk_reason}",
        )

    # Convert USDT notional → base asset quantity.
    # PlaceOrderRequest.quantity is documented as "Order size in USDT" (line 78),
    # but LiveExecutionEngine.place_order passes quantity directly to Binance
    # /api/v3/order's `quantity` param, which expects BASE asset amount (e.g. BTC
    # qty, not USDT notional). Without conversion, a 50 USDT order gets sent as
    # 50 BTC (~$3.5M) and fails with Insufficient balance.
    # StrategyRunner._execute_signal does this conversion at line 905-908; this
    # endpoint must do the same for manual one-shot orders.
    order_qty_base = req.quantity
    ot = req.order_type
    order_type_str = (ot.value if hasattr(ot, "value") else str(ot)).upper()
    if order_type_str == "MARKET":
        # Market order: pull mark price from market_svc recent close.
        mark_price = _resolve_mark_price(req.symbol)
        if mark_price is None or mark_price <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resolve mark price for {req.symbol} — market data unavailable. "
                "Retry with a limit order (price field) instead.",
            )
        order_qty_base = req.quantity / mark_price
    elif (
        order_type_str in ("LIMIT", "LIMIT_MAKER")
        and req.price is not None
        and req.price > 0
    ):
        # Limit order: use the caller-supplied price.
        order_qty_base = req.quantity / req.price
    elif (
        order_type_str in ("STOP_LIMIT", "TAKE_PROFIT_LIMIT")
        and req.price is not None
        and req.price > 0
    ):
        # Stop-limit / take-profit-limit: use the caller-supplied limit price.
        order_qty_base = req.quantity / req.price
    elif order_type_str in (
        "STOP_MARKET",
        "TAKE_PROFIT_MARKET",
        "TRAILING_STOP_MARKET",
        "IOC",
    ):
        # Market-triggered variants: same as market — need mark price.
        mark_price = _resolve_mark_price(req.symbol)
        if mark_price is None or mark_price <= 0:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resolve mark price for {req.symbol} — market data unavailable. "
                "Retry with a limit order (price field) instead.",
            )
        order_qty_base = req.quantity / mark_price
    else:
        # Unknown order type — refuse rather than silently passing USDT as base.
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported order_type {order_type_str!r} for USDT→base conversion "
            "(expected market / limit / stop_market / stop_limit / ioc).",
        )

    order = await engine.place_order(  # type: ignore[attr-defined]
        account_id=req.account_id,
        symbol=req.symbol,
        side=req.side,
        order_type=req.order_type,
        quantity=order_qty_base,
        price=req.price,
        stop_price=req.stop_price,
        leverage=req.leverage,
        margin_mode=req.margin_mode,
        pos_side=req.pos_side,
        reduce_only=req.reduce_only,
        time_in_force=req.time_in_force,
        tp_price=req.tp_price,
        sl_price=req.sl_price,
        client_order_id=req.client_order_id,
    )

    # Build response with order data + risk alerts
    order_data = order.model_dump(mode="json") if hasattr(order, "model_dump") else {}
    result = {
        "order": order_data,
        "risk_alerts": risk_alerts_data,
        "has_risk_alerts": len(risk_alerts_data) > 0,
    }

    return APIResponse(data=result, meta=build_response_meta(request), error=None)


@router.delete("/orders/{order_id}", response_model=Order)
async def cancel_order(
    order_id: str,
    engine: object = Depends(get_live_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> Order:
    """Cancel an open order."""
    if engine is None:
        raise HTTPException(
            status_code=503,
            detail="Live execution engine not configured. Please add API keys.",
        )

    try:
        return await engine.cancel_order(order_id)  # type: ignore[attr-defined]
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/orders", response_model=list[Order])
async def get_orders(
    account_id: str = DEFAULT_ACCOUNT,
    status: OrderStatus | None = None,
    engine: object = Depends(get_live_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> list[Order]:
    """Get orders, optionally filtered by status.

    In shadow execution mode, orders route to the ShadowExecutionEngine (not
    LiveExecutionEngine). Fall back to shadow engine when live is not
    configured so callers can verify shadow run output via this endpoint.
    """
    _verify_trading_ownership(account_id, user)
    active_engine = engine
    if active_engine is None:
        # Fall back to shadow engine if live is not configured (shadow mode).
        from app.core.dependencies import get_shadow_engine

        active_engine = get_shadow_engine()
    if active_engine is None:
        return []

    return await active_engine.get_orders(account_id, status=status)  # type: ignore[attr-defined, no-any-return]


# ---------------------------------------------------------------------------
# Position endpoints
# ---------------------------------------------------------------------------


@router.get("/positions", response_model=list[Position])
async def get_positions(
    account_id: str = DEFAULT_ACCOUNT,
    engine: object = Depends(get_live_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> list[Position]:
    """Get all open positions.

    Returns an empty list when no live execution engine is configured (e.g. no
    API keys registered). This lets the frontend render an empty state instead
    of receiving a 503 error during demo / paper-trading use.
    """
    _verify_trading_ownership(account_id, user)
    if engine is None:
        return []

    return await engine.get_positions(account_id)  # type: ignore[attr-defined, no-any-return]


# ---------------------------------------------------------------------------
# Account-state endpoint
# ---------------------------------------------------------------------------


@router.get("/account-state")
async def get_account_state(
    request: Request,
    account_id: str = DEFAULT_ACCOUNT,
    engine: object = Depends(get_execution_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Return the full account state document (resync primitive).

    Aggregates orders/positions/trades/balances/equity from the current
    execution engine's getters via ``build_account_state``. Returns an
    empty-state document (zero watermark, mode="paper") when no engine is
    configured — never a 5xx (mirrors ``/trading/positions``).
    """
    _verify_trading_ownership(account_id, user)

    from app.core.account_state import build_account_state
    from app.core.sequence import get_channel_sequence

    sequence = get_channel_sequence()
    seq_keys: dict[str, str] | None = None
    session_id = ""
    if sequence is not None:
        session_id = sequence.session_id
        seq_keys = {
            "orders": "local:orders",
            "positions": "local:positions",
            "balances": "local:balances",
        }

    state = await build_account_state(
        engine,
        account_id,
        session_id=session_id,
        sequence=sequence,
        seq_keys=seq_keys,
    )
    return APIResponse(
        data=state.model_dump(mode="json"),
        meta=build_response_meta(request),
        error=None,
    )


# ---------------------------------------------------------------------------
# PnL endpoints
# ---------------------------------------------------------------------------


@router.get("/pnl/{symbol}")
async def get_symbol_pnl(
    symbol: str,
    account_id: str = DEFAULT_ACCOUNT,
    engine: object = Depends(get_live_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> dict[str, Any]:
    """Get realized + unrealized PnL for a single symbol.

    Aggregates from the live engine's position + fill history.
    Returns zeros when the engine is unavailable (paper/demo mode).
    """
    _verify_trading_ownership(account_id, user)
    sym = symbol.upper().replace("-", "/")
    realized = 0.0
    unrealized = 0.0
    fees = 0.0

    if engine is not None:
        try:
            positions = await engine.get_positions(account_id)  # type: ignore[attr-defined]
            for p in positions:
                if p.symbol.upper() == sym:
                    unrealized += float(p.unrealized_pnl or 0.0)
                    realized += float(p.realized_pnl or 0.0)
                    fees += float(getattr(p, "accrued_funding", 0.0) or 0.0)
        except Exception:
            logger.warning("Failed to aggregate PnL for %s", sym, exc_info=True)

    total = realized + unrealized
    return {
        "symbol": sym,
        "realized_pnl": round(realized, 4),
        "unrealized_pnl": round(unrealized, 4),
        "total_pnl": round(total, 4),
        "fees": round(fees, 4),
        "timestamp": int(__import__("time").time() * 1000),
    }


# ---------------------------------------------------------------------------
# Balance endpoints
# ---------------------------------------------------------------------------


@router.get("/balances", response_model=list[BalanceUpdate])
async def get_balances(
    account_id: str = DEFAULT_ACCOUNT,
    engine: object = Depends(get_live_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> list[BalanceUpdate]:
    """Get account balances.

    Returns an empty list when no live execution engine is configured (e.g. no
    API keys registered). This lets the frontend render an empty state instead
    of receiving a 503 error during demo / paper-trading use.
    """
    _verify_trading_ownership(account_id, user)
    if engine is None:
        return []

    return await engine.get_balances(account_id)  # type: ignore[attr-defined, no-any-return]


# ---------------------------------------------------------------------------
# History endpoints
# ---------------------------------------------------------------------------


@router.get("/history", response_model=list[Fill])
async def get_trade_history(
    account_id: str = DEFAULT_ACCOUNT,
    engine: object = Depends(get_live_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> list[Fill]:
    """Get trade execution history (fills).

    Returns an empty list when no execution engine is configured, consistent
    with /positions and /balances empty-state behavior.
    """
    _verify_trading_ownership(account_id, user)
    active_engine = engine
    if active_engine is None:
        # Fall back to shadow engine, then paper engine.
        from app.core.dependencies import get_shadow_engine, get_strategy_runner

        active_engine = get_shadow_engine()
        if active_engine is None:
            runner = get_strategy_runner()
            if runner is not None:
                active_engine = getattr(runner, "_paper", None)
    if active_engine is None:
        return []

    return await active_engine.get_fills(account_id)  # type: ignore[attr-defined, no-any-return]


# ---------------------------------------------------------------------------
# Trading Pro endpoints
# ---------------------------------------------------------------------------

_leverage_settings: dict[str, int] = {}  # symbol -> leverage


class AmendOrderRequest(BaseModel):
    price: float | None = Field(None, gt=0, description="New price for the order")
    quantity: float | None = Field(None, gt=0, description="New quantity for the order")


class SetLeverageRequest(BaseModel):
    symbol: str = Field(..., min_length=1, description="Trading pair symbol")
    leverage: int = Field(..., ge=1, le=125, description="Leverage multiplier")
    account_id: str | None = Field(None, description="Optional account ID")


class ClosePositionRequest(BaseModel):
    symbol: str = Field(..., min_length=1, description="Position symbol to close")
    account_id: str | None = Field(None, description="Optional account ID")
    amount: float | None = Field(
        None, gt=0, description="Partial close amount; None = full close"
    )


@router.put("/orders/{order_id}")
async def amend_order(
    order_id: str,
    request: Request,
    body: AmendOrderRequest,
    engine: object = Depends(get_live_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Amend an existing order (cancel old + place new in paper mode)."""
    if engine is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Execution engine not configured. Please add API keys.",
        )

    if body.price is None and body.quantity is None:
        raise SeleneSpaceError(
            code=ErrorCode.VALIDATION_ERROR,
            message="At least one of 'price' or 'quantity' must be provided to amend the order",
        )

    try:
        # Cancel the existing order
        cancelled = await engine.cancel_order(order_id)  # type: ignore[attr-defined]

        # Place a new order with updated parameters
        new_order = await engine.place_order(  # type: ignore[attr-defined]
            account_id=cancelled.account_id
            if hasattr(cancelled, "account_id")
            else DEFAULT_ACCOUNT,
            symbol=cancelled.symbol if hasattr(cancelled, "symbol") else "",
            side=cancelled.side if hasattr(cancelled, "side") else "buy",
            order_type=cancelled.order_type
            if hasattr(cancelled, "order_type")
            else "limit",
            quantity=body.quantity
            if body.quantity is not None
            else (cancelled.quantity if hasattr(cancelled, "quantity") else 0),
            price=body.price
            if body.price is not None
            else (cancelled.price if hasattr(cancelled, "price") else None),
            leverage=cancelled.leverage if hasattr(cancelled, "leverage") else 1,
        )

        logger.info("Order amended: %s -> new order placed", order_id)

        data = {
            "amended": True,
            "order_id": str(new_order.id) if hasattr(new_order, "id") else order_id,
        }
    except ValueError as exc:
        raise SeleneSpaceError(
            code=ErrorCode.NOT_FOUND,
            message=f"Order '{order_id}' not found",
        ) from exc
    except Exception as exc:
        logger.error("Failed to amend order %s: %s", order_id, exc, exc_info=True)
        raise SeleneSpaceError(
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to amend order",
        ) from exc

    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.post("/leverage")
async def set_leverage(
    request: Request,
    body: SetLeverageRequest,
    engine: object = Depends(get_live_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Set leverage for a symbol."""
    _leverage_settings[body.symbol] = body.leverage

    # If engine is available, apply leverage on the exchange side
    if engine is not None:
        try:
            if hasattr(engine, "set_leverage"):
                await engine.set_leverage(  # type: ignore[attr-defined]
                    symbol=body.symbol,
                    leverage=body.leverage,
                    account_id=body.account_id or DEFAULT_ACCOUNT,
                )
        except Exception:
            logger.warning(
                "Failed to set leverage on engine for %s, stored locally only",
                body.symbol,
                exc_info=True,
            )

    logger.info("Leverage set: %s -> %dx", body.symbol, body.leverage)

    data = {
        "symbol": body.symbol,
        "leverage": body.leverage,
        "account_id": body.account_id,
    }
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


@router.post("/positions/close")
async def close_position(
    request: Request,
    body: ClosePositionRequest,
    engine: object = Depends(get_live_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Close a position by placing an opposite market order."""
    if engine is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Execution engine not configured. Please add API keys.",
        )

    account_id = body.account_id or DEFAULT_ACCOUNT

    try:
        # Find the current position
        positions = await engine.get_positions(account_id)  # type: ignore[attr-defined]
        target = None
        for pos in positions:
            if hasattr(pos, "symbol") and pos.symbol == body.symbol:
                target = pos
                break

        if target is None:
            raise SeleneSpaceError(
                code=ErrorCode.NOT_FOUND,
                message=f"No open position found for '{body.symbol}'",
            )

        # Determine opposite side
        current_side = target.side if hasattr(target, "side") else "long"
        close_side = "sell" if current_side in ("long", "buy") else "buy"
        close_qty = (
            body.amount
            if body.amount is not None
            else (target.quantity if hasattr(target, "quantity") else 0)
        )

        # Place opposite market order
        await engine.place_order(  # type: ignore[attr-defined]
            account_id=account_id,
            symbol=body.symbol,
            side=close_side,
            order_type="market",
            quantity=close_qty,
            reduce_only=True,
        )

        logger.info(
            "Position closed: %s (side: %s, qty: %s)",
            body.symbol,
            current_side,
            close_qty,
        )

        data = {
            "closed": True,
            "symbol": body.symbol,
            "side": current_side,
        }
    except SeleneSpaceError:
        raise
    except Exception as exc:
        logger.error(
            "Failed to close position for %s: %s", body.symbol, exc, exc_info=True
        )
        raise SeleneSpaceError(
            code=ErrorCode.INTERNAL_ERROR,
            message="Failed to close position",
        ) from exc

    return APIResponse(data=data, meta=build_response_meta(request), error=None)


# ---------------------------------------------------------------------------
# Order Preview / Confirm (Phase 2 — confirm-mode safety gate)
# ---------------------------------------------------------------------------

_confirmation_manager: Any | None = None


def _get_confirmation_manager() -> OrderConfirmationManager:
    global _confirmation_manager
    if _confirmation_manager is None:
        from selene_space_exchange.execution.confirmation import (
            OrderConfirmationManager,
        )

        _confirmation_manager = OrderConfirmationManager()
    return _confirmation_manager


class PreviewOrderRequest(BaseModel):
    symbol: str = Field(..., description="Instrument, e.g. 'BTC-USDT-SWAP'")
    side: OrderSide
    order_type: OrderType
    quantity: float = Field(..., gt=0)
    price: float | None = Field(None, ge=0)
    stop_price: float | None = Field(None, ge=0)
    leverage: int = Field(1, ge=1, le=125)
    margin_mode: MarginMode = Field(MarginMode.CROSS)
    pos_side: PositionSide = Field(PositionSide.LONG)
    account_id: str = Field(DEFAULT_ACCOUNT)


class ConfirmOrderRequest(BaseModel):
    preview_id: str = Field(..., description="Preview ID from /orders/preview")


@router.post("/orders-preview")
async def preview_order(
    req: PreviewOrderRequest,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Preview an order before execution. Returns estimated costs and risk alerts.

    In confirm mode, orders must be previewed first. The preview expires after 30s.
    """
    _verify_trading_ownership(req.account_id, user)

    # In CONFIRM mode, /orders-preview is the entry point of the preview+confirm
    # flow — it MUST NOT require a preview_id (it CREATES one). ExecutionModeGuard
    # check is enforced at /orders-confirm (with the preview_id) and at /orders
    # (which rejects CONFIRM mode outright above).

    # Estimate fill price from shadow engine if available
    estimated_fill_price = 0.0
    estimated_slippage_bps = 0.0
    estimated_fee = req.quantity * 0.0005  # 0.05% taker fee estimate
    required_margin = 0.0
    available_balance = 0.0

    shadow = get_shadow_engine()
    if shadow is not None:
        try:
            balances = await shadow.get_balances(req.account_id)
            if balances:
                available_balance = sum(b.free + b.locked for b in balances)
        except Exception:
            pass

    # Risk pre-check
    risk_alerts: list[dict] = []
    risk_engine = get_risk_engine()
    if risk_engine is not None:
        try:
            import time
            from selene_space_types.agent import TradeIntent

            intent = TradeIntent(
                symbol=req.symbol,
                side=req.side,
                quantity=req.quantity,
                price=req.price,
                order_type=req.order_type,
                reasoning="User preview order",
                confidence=1.0,
                risk_params={"stop_loss": req.stop_price} if req.stop_price else {},
                timestamp=int(time.time() * 1000),
            )
            decision = risk_engine.pre_check(intent, {})
            risk_alerts = [
                {"rule_id": a.rule_id, "level": a.level.value, "message": a.message}
                for a in decision.alerts
            ]
        except Exception:
            logger.debug("Risk pre-check failed during preview", exc_info=True)

    required_margin = (
        (req.quantity * (req.price or 1.0)) / req.leverage if req.leverage > 0 else 0.0
    )

    mgr = _get_confirmation_manager()
    preview = await mgr.create_preview(
        symbol=req.symbol,
        side=req.side.value,
        order_type=req.order_type.value,
        quantity=req.quantity,
        price=req.price,
        stop_price=req.stop_price,
        leverage=req.leverage,
        margin_mode=req.margin_mode.value,
        pos_side=req.pos_side.value,
        account_id=req.account_id,
        estimated_fill_price=estimated_fill_price,
        estimated_slippage_bps=estimated_slippage_bps,
        estimated_fee=estimated_fee,
        required_margin=required_margin,
        available_balance=available_balance,
        risk_alerts=risk_alerts,
    )

    return APIResponse(
        data={
            "preview_id": preview.preview_id,
            "symbol": preview.symbol,
            "side": preview.side,
            "order_type": preview.order_type,
            "quantity": preview.quantity,
            "price": preview.price,
            "estimated_fill_price": preview.estimated_fill_price,
            "estimated_slippage_bps": preview.estimated_slippage_bps,
            "estimated_fee": preview.estimated_fee,
            "required_margin": preview.required_margin,
            "available_balance": preview.available_balance,
            "risk_alerts": preview.risk_alerts,
            "expires_at": preview.expires_at,
        },
        meta=build_response_meta(request),
        error=None,
    )


@router.post("/orders-confirm")
async def confirm_order(
    req: ConfirmOrderRequest,
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict]:
    """Confirm a previewed order for execution.

    The preview must be valid (not expired, not already used).
    After confirmation, the order is placed on the active engine.
    """
    mgr = _get_confirmation_manager()
    preview = await mgr.confirm_preview(req.preview_id)
    if preview is None:
        raise HTTPException(
            status_code=410,
            detail="Preview expired, already used, or not found. Create a new preview.",
        )

    # Route to the active engine
    engine = get_live_engine()
    if engine is None:
        engine = get_shadow_engine()
    if engine is None:
        raise HTTPException(status_code=503, detail="No execution engine available.")

    _verify_trading_ownership(preview.account_id, user)

    order = await engine.place_order(
        account_id=preview.account_id,
        symbol=preview.symbol,
        side=OrderSide(preview.side),
        order_type=OrderType(preview.order_type),
        quantity=preview.quantity,
        price=preview.price,
        stop_price=preview.stop_price,
        leverage=preview.leverage,
        margin_mode=MarginMode(preview.margin_mode),
        pos_side=PositionSide(preview.pos_side),
    )

    order_data = order.model_dump(mode="json") if hasattr(order, "model_dump") else {}
    return APIResponse(
        data={"order": order_data, "preview_id": req.preview_id},
        meta=build_response_meta(request),
        error=None,
    )


# ---------------------------------------------------------------------------
# Live log — SSE stream of order/fill events (Phase 4)
# ---------------------------------------------------------------------------


@router.get("/live-log")
async def live_log(
    request: Request,
    user: AuthenticatedUser = Depends(optional_user),
):
    """SSE stream of recent order/fill events for the trading dashboard.

    Streams ``LiveLogEntry``-shaped JSON events. Pulls from the recent fill
    history + open orders. When the client disconnects (Request.is_disconnected),
    the stream terminates.
    """
    import asyncio
    import json
    import time

    from fastapi.responses import StreamingResponse

    async def _event_source() -> Any:
        last_ts = int(time.time() * 1000) - 60_000  # start 60s in the past
        # Send an initial hello so EventSource opens immediately.
        yield f"event: hello\ndata: {json.dumps({'ts': int(time.time()*1000)})}\n\n"

        while True:
            if await request.is_disconnected():
                break

            events: list[dict[str, Any]] = []

            # 1) Recent fills
            try:
                engine = get_live_engine() or get_shadow_engine()
                if engine is not None:
                    fills = getattr(engine, "recent_fills", None)
                    if fills:
                        for fill in list(fills)[-20:]:
                            ts = int(
                                getattr(fill, "ts", 0)
                                or getattr(fill, "timestamp", 0)
                                or 0
                            )
                            if ts <= last_ts:
                                continue
                            events.append(
                                {
                                    "ts": ts,
                                    "event_type": "order_filled",
                                    "symbol": getattr(fill, "symbol", ""),
                                    "side": getattr(fill, "side", None),
                                    "qty": float(getattr(fill, "quantity", 0) or 0)
                                    or None,
                                    "price": float(getattr(fill, "price", 0) or 0)
                                    or None,
                                    "order_id": getattr(fill, "order_id", None),
                                    "account_id": getattr(fill, "account_id", None),
                                }
                            )
                            last_ts = max(last_ts, ts)
            except Exception:
                logger.debug("live-log fills fetch failed", exc_info=True)

            # 2) Open orders snapshot (no ts filter — emit as periodic snapshot)
            try:
                engine = get_live_engine() or get_shadow_engine()
                if engine is not None:
                    orders = getattr(engine, "open_orders", None)
                    if orders:
                        for order in list(orders)[-5:]:
                            ts = int(time.time() * 1000)
                            events.append(
                                {
                                    "ts": ts,
                                    "event_type": "order_placed",
                                    "symbol": getattr(order, "symbol", ""),
                                    "side": getattr(order, "side", None),
                                    "qty": float(getattr(order, "quantity", 0) or 0)
                                    or None,
                                    "price": float(getattr(order, "price", 0) or 0)
                                    or None,
                                    "order_id": getattr(order, "id", None)
                                    or getattr(order, "order_id", None),
                                    "account_id": getattr(order, "account_id", None),
                                }
                            )
            except Exception:
                logger.debug("live-log orders fetch failed", exc_info=True)

            for ev in events:
                yield f"event: log\ndata: {json.dumps(ev)}\n\n"

            await asyncio.sleep(2.0)

    return StreamingResponse(
        _event_source(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ---------------------------------------------------------------------------
# Latency monitoring — /latency-stats
# ---------------------------------------------------------------------------


@router.get("/latency-stats")
async def get_latency_stats(
    request: Request,
    window: int = 3600,
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[LatencyStats]:
    """Compute p50/p95/p99 order latency percentiles from order_journal.

    Three buckets are reported:
    - intent_to_submit: INTENT journal write → mark_submitted
    - submit_to_ack: mark_submitted → mark_confirmed (exchange WS ack)
    - ack_to_fill: mark_confirmed → mark_filled

    Rows with NULL timestamps in either endpoint of a bucket are excluded
    from that bucket's percentile (partial rows still count toward sample_count).
    """
    import asyncio
    import sqlite3
    import time as _time

    journal = get_order_journal()
    if journal is None:
        return APIResponse(
            data=LatencyStats(window_seconds=window, sample_count=0),
            meta=build_response_meta(request=request),
            error=None,
        )

    now_ms = int(_time.time() * 1000)
    cutoff_ms = now_ms - window * 1000

    # H7: wrap blocking sqlite3 connect+execute in asyncio.to_thread to avoid
    # blocking the event loop under concurrent /latency-stats requests.
    def _fetch_latency_rows() -> list:
        con = sqlite3.connect(str(journal._db_path))  # noqa: SLF001 — read-only access to journal path
        try:
            cur = con.execute(
                """
                SELECT created_at, submitted_at, acknowledged_at, filled_at
                FROM order_journal
                WHERE created_at >= ?
                """,
                (cutoff_ms,),
            )
            return cur.fetchall()
        finally:
            con.close()

    rows = await asyncio.to_thread(_fetch_latency_rows)

    sample_count = len(rows)

    intent_submit = [r[1] - r[0] for r in rows if r[0] and r[1]]
    submit_ack = [r[2] - r[1] for r in rows if r[1] and r[2]]
    ack_fill = [r[3] - r[2] for r in rows if r[2] and r[3]]

    def _pcts(values: list[int]) -> tuple[float | None, float | None, float | None]:
        if not values:
            return None, None, None
        sorted_vals = sorted(values)
        n = len(sorted_vals)

        def _pct(p: float) -> float:
            if n == 1:
                return float(sorted_vals[0])
            k = max(0, min(n - 1, int(round(p / 100 * (n - 1)))))
            return float(sorted_vals[k])

        return _pct(50), _pct(95), _pct(99)

    p50_is, p95_is, p99_is = _pcts(intent_submit)
    p50_sa, p95_sa, p99_sa = _pcts(submit_ack)
    p50_af, p95_af, p99_af = _pcts(ack_fill)

    return APIResponse(
        data=LatencyStats(
            window_seconds=window,
            sample_count=sample_count,
            intent_to_submit_p50_ms=p50_is,
            intent_to_submit_p95_ms=p95_is,
            intent_to_submit_p99_ms=p99_is,
            submit_to_ack_p50_ms=p50_sa,
            submit_to_ack_p95_ms=p95_sa,
            submit_to_ack_p99_ms=p99_sa,
            ack_to_fill_p50_ms=p50_af,
            ack_to_fill_p95_ms=p95_af,
            ack_to_fill_p99_ms=p99_af,
        ),
        meta=build_response_meta(request=request),
        error=None,
    )
