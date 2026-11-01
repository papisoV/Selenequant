"""Paper Trading endpoints.

Provides account management, order placement, position queries,
and PnL calculation through the ``selene_space_paper`` package managers.
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.core.dependencies import (
    AuthenticatedUser,
    build_response_meta,
    get_db_manager,
    get_execution_engine,
    get_market_service,
    get_paper_account_manager,
    get_paper_order_manager,
    get_paper_position_manager,
    optional_user,
)
from app.core.sequence import get_channel_sequence
from selene_space_types.common import APIResponse, Pagination
from selene_space_types.errors import ErrorCode, NotFoundError, SeleneSpaceError
from selene_space_types.trading import (
    ExecutionMode,
    MarginMode,
    OrderSide,
    OrderStatus,
    OrderType,
    PositionSide,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/paper", tags=["paper-trading"])


def _ok(request: Request, data: Any) -> APIResponse[Any]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


def _bad_request(exc: Exception) -> HTTPException:
    """把 ValueError 统一转成 400。"""
    return HTTPException(status_code=400, detail=str(exc))


def _account_repo(db: Any) -> Any:
    from selene_space_storage.repositories.paper_accounts import PaperAccountRepository

    return PaperAccountRepository(db)


async def _persist_account(account: Any, user_id: str, note: str) -> None:
    """有 DB 就把账户存一份; 失败只记 debug。"""
    db = get_db_manager()
    if db is None:
        return
    try:
        await _account_repo(db).save_account(account.model_dump(), user_id=user_id)
    except Exception:
        logger.debug(note, exc_info=True)


def _account_or_404(accounts: Any, account_id: str) -> Any:
    account = accounts.get_account(account_id)
    if account is None:
        raise NotFoundError(f"Account '{account_id}' not found")
    return account


def _unrealized_of(pos_mgr: Any, account_id: str) -> float:
    """未实现盈亏; 无仓管则为 0。"""
    if pos_mgr is None:
        return 0.0
    total: float = sum(
        float(p.unrealized_pnl) for p in pos_mgr.get_open_positions(account_id)
    )
    return total


def _enrich_account(data: dict[str, Any], account: Any, unrealized: float) -> None:
    """补上 equity/balance/未实现/已实现四个派生字段(H19: 统一 float)。"""
    data["equity"] = _wallet_balance(account) + unrealized
    data["balance"] = float(account.current_balance)
    data["unrealized_pnl"] = unrealized
    data["realized_pnl"] = float(account.total_realized_pnl)


def _snapshot_equity_async(account_id: str, account: Any) -> None:
    """按持仓算当前权益并异步落一笔快照(尽力而为)。"""
    try:
        pos_mgr = get_paper_position_manager()
        unrealized = _unrealized_of(pos_mgr, account_id)
        wallet_bal = _wallet_balance(account)
        asyncio.create_task(_record_equity(account_id, wallet_bal + unrealized))
    except Exception:
        logger.debug("Post-mutation equity snapshot failed", exc_info=True)


_equity_record_timestamps: dict[str, float] = {}
EQUITY_RECORD_INTERVAL = 30.0


def _paper_dec(v: float | Decimal | int | str) -> Decimal:
    """Coerce to Decimal via str to avoid float precision pollution (H19)."""
    if isinstance(v, Decimal):
        return v
    if isinstance(v, int):
        return Decimal(v)
    if isinstance(v, float):
        return Decimal(str(v))
    return Decimal(v)


def _wallet_balance(account: Any) -> float:
    """Compute wallet_balance as float (H19 — funding fields are Decimal)."""
    return (
        float(account.initial_balance)
        + float(account.total_realized_pnl)
        - float(account.total_fee)
    )


def _floatify_account_snapshot(data: dict[str, Any]) -> dict[str, Any]:
    """Coerce Decimal funding fields to float in a model_dump dict for JSON output (H19)."""
    for k in (
        "initial_balance",
        "current_balance",
        "equity",
        "total_realized_pnl",
        "total_fee",
    ):
        if k in data and data[k] is not None and isinstance(data[k], Decimal):
            data[k] = float(data[k])
    return data


# ---------------------------------------------------------------------------
# Account ownership registry  (account_id → user_id)
#
# The in-memory PaperExecutionEngine / AccountManager has no concept of
# "user".  We track ownership at the API layer so each user can only see
# and operate on their own accounts.
# ---------------------------------------------------------------------------

_account_owners: dict[str, str] = {}
_owners_loaded = False


def _register_owner(account_id: str, user_id: str) -> None:
    """Record that *account_id* belongs to *user_id*."""
    _account_owners[account_id] = user_id


def _owner_of(account_id: str) -> str | None:
    """Return the user_id that owns *account_id*, or None."""
    return _account_owners.get(account_id)


async def _ensure_owners_loaded() -> None:
    """One-time load of ownership from the DB (paper_accounts.user_id)."""
    global _owners_loaded
    if _owners_loaded:
        return
    _owners_loaded = True
    try:
        db = get_db_manager()
        if db is None:
            return
        # P0 fix: was db.query() — a method AsyncSQLiteManager does not have.
        # Every load raised AttributeError and the debug-level except buried
        # it, so _account_owners stayed empty and non-local users got 403 on
        # their own accounts (tests/test_paper_owner_mapping.py).
        rows = await db.execute(
            "SELECT id, user_id FROM paper_accounts WHERE user_id IS NOT NULL", ()
        )
        for r in rows:
            _account_owners[r["id"]] = r["user_id"]
        logger.info("Loaded %d paper account owner mappings from DB", len(rows))
    except Exception:
        logger.warning("Could not preload paper account owners", exc_info=True)


async def _verify_ownership(account_id: str, user: AuthenticatedUser) -> None:
    """Raise 403 if the current user does not own *account_id*.

    In Community mode (user.id == "local") ownership checks are skipped.
    Non-local users are denied access to unregistered accounts (owner is None).

    Loads the ownership mapping itself: previously only list_accounts called
    _ensure_owners_loaded, so every other endpoint (get account, orders,
    positions, ...) checked against a permanently empty mapping and 403'd
    legitimate owners.
    """
    if user.id == "local":
        return
    await _ensure_owners_loaded()
    owner = _owner_of(account_id)
    if owner is None or (owner != user.id and owner != "local"):
        raise SeleneSpaceError(
            ErrorCode.PERMISSION_DENIED,
            "You do not have access to this account",
        )


# ---------------------------------------------------------------------------
# Fallback in-memory managers (used when packages not injected via DI)
# ---------------------------------------------------------------------------

_fallback_initialized = False
_fallback_accounts: Any = None
_fallback_orders: Any = None
_fallback_positions: Any = None


def _ensure_fallback() -> tuple[Any, Any, Any]:
    """Lazily create fallback managers from selene_space_paper."""
    global \
        _fallback_initialized, \
        _fallback_accounts, \
        _fallback_orders, \
        _fallback_positions
    if not _fallback_initialized:
        try:
            from selene_space_paper.accounts import AccountManager
            from selene_space_paper.orders import PaperOrderManager
            from selene_space_paper.positions import PositionManager

            _fallback_accounts = AccountManager()
            _fallback_orders = PaperOrderManager()
            _fallback_positions = PositionManager()
        except ImportError:
            pass
        _fallback_initialized = True
    return _fallback_accounts, _fallback_orders, _fallback_positions


def _pick_manager(mgr: Any, index: int, label: str) -> Any:
    """注入优先; 否则用 fallback 里第 index 个; 都没有则 503。"""
    if mgr is not None:
        return mgr
    fallback = _ensure_fallback()[index]
    if fallback is None:
        raise SeleneSpaceError(
            ErrorCode.SERVICE_UNAVAILABLE, f"Paper {label} manager unavailable"
        )
    return fallback


def _get_accounts(mgr: Any = Depends(get_paper_account_manager)) -> Any:
    return _pick_manager(mgr, 0, "account")


def _get_orders(mgr: Any = Depends(get_paper_order_manager)) -> Any:
    return _pick_manager(mgr, 1, "order")


def _get_positions(mgr: Any = Depends(get_paper_position_manager)) -> Any:
    return _pick_manager(mgr, 2, "position")


# ---------------------------------------------------------------------------
# Request bodies
# ---------------------------------------------------------------------------


class CreateAccountRequest(BaseModel):
    name: str = Field(..., min_length=1, description="Account name")
    initial_balance: float = Field(10_000.0, gt=0, description="Starting balance")
    account_type: str = Field(
        "manual", description="Account type: strategy, agent, or manual"
    )
    strategy_id: str | None = Field(
        None, description="Linked strategy ID (for strategy accounts)"
    )


class PlaceOrderRequest(BaseModel):
    account_id: str = Field(..., description="Paper account ID")
    symbol: str = Field(..., description="Instrument, e.g. BTC-USDT-SWAP")
    side: OrderSide = Field(..., description="buy or sell")
    order_type: OrderType = Field(OrderType.MARKET, description="Order type")
    quantity: float = Field(..., gt=0, description="Order size in USDT")
    price: float | None = Field(None, gt=0, description="Limit price")
    stop_price: float | None = Field(None, gt=0, description="Stop price")
    leverage: int = Field(1, ge=1, le=125, description="Leverage multiplier")
    margin_mode: MarginMode = Field(MarginMode.CROSS, description="cross or isolated")
    pos_side: PositionSide = Field(PositionSide.LONG, description="long or short")
    reduce_only: bool = Field(False, description="Close position only")
    mark_price: float | None = Field(
        None, gt=0, description="Frontend mark price for fill simulation"
    )
    tp_price: float | None = Field(None, gt=0, description="Take-profit trigger price")
    sl_price: float | None = Field(None, gt=0, description="Stop-loss trigger price")


class ClosePositionRequest(BaseModel):
    account_id: str = Field(..., description="Paper account ID")
    symbol: str = Field(..., description="Instrument")
    pos_side: PositionSide = Field(..., description="Position side to close")
    quantity: float | None = Field(
        None, gt=0, description="Close amount in USDT (None=close all)"
    )
    mark_price: float | None = Field(
        None, gt=0, description="Mark price for market close"
    )


# ---------------------------------------------------------------------------
# Account endpoints
# ---------------------------------------------------------------------------


def _refresh_unrealized_pnl(account_id: str, pos_mgr: Any) -> None:
    """Update unrealized PnL on all open positions with latest market prices."""
    if pos_mgr is None:
        return
    engine = get_execution_engine()
    market_svc = get_market_service()
    for p in pos_mgr.get_open_positions(account_id):
        price = _get_live_price(p.symbol, engine, market_svc)
        if price is not None and price > 0:
            pos_mgr.update_unrealized_pnl(account_id, p.symbol, price)


async def _visible_accounts(mgr: Any, user: AuthenticatedUser) -> list[Any]:
    """本机用户见全部; 其余见自己的, 一个都没有则自动建一个默认账户。"""
    all_accounts: list[Any] = list(mgr.list_accounts())
    if user.id == "local":
        return all_accounts
    owned = [a for a in all_accounts if _owner_of(a.id) == user.id]
    if owned:
        return owned
    new_acct = mgr.create_account(
        name="Default Paper Account",
        initial_balance=100_000.0,
    )
    _register_owner(new_acct.id, user.id)
    await _persist_account(
        new_acct, user.id, "Failed to persist auto-created paper account"
    )
    return [new_acct]


@router.get("/accounts")
async def list_accounts(
    request: Request,
    mgr: Any = Depends(_get_accounts),
    pos_mgr: Any = Depends(_get_positions),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List paper trading accounts owned by the current user.

    Equity = wallet_balance + unrealized PnL.
    wallet_balance = initial_balance + total_realized_pnl - total_fee.
    """
    import time as _time

    await _ensure_owners_loaded()
    accounts = await _visible_accounts(mgr, user)

    result = []
    now = _time.time()
    for a in accounts:
        _refresh_unrealized_pnl(a.id, pos_mgr)
        data = _floatify_account_snapshot(a.model_dump(mode="json"))
        # H19: Position.unrealized_pnl 是 Decimal,sum 后与 wallet_balance(float) 相加
        # 会抛 float+Decimal TypeError。统一转 float 后再求和。
        unrealized = sum(
            float(p.unrealized_pnl) for p in pos_mgr.get_open_positions(a.id)
        )
        _enrich_account(data, a, unrealized)
        result.append(data)

        last_ts = _equity_record_timestamps.get(a.id, 0.0)
        if now - last_ts >= EQUITY_RECORD_INTERVAL:
            _equity_record_timestamps[a.id] = now
            asyncio.create_task(_record_equity(a.id, data["equity"]))

    return _ok(request, result)


@router.post("/accounts")
async def create_account(
    request: Request,
    body: CreateAccountRequest,
    mgr: Any = Depends(_get_accounts),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Create a new paper trading account owned by the current user."""
    kwargs: dict[str, Any] = {
        "name": body.name,
        "initial_balance": body.initial_balance,
    }
    try:
        from selene_space_paper.accounts import AccountType

        type_map = {t.value: t for t in AccountType}
        if body.account_type in type_map:
            kwargs["account_type"] = type_map[body.account_type]
        if body.strategy_id:
            kwargs["strategy_id"] = body.strategy_id
    except ImportError:
        pass

    account = mgr.create_account(**kwargs)

    _register_owner(account.id, user.id)

    data = _floatify_account_snapshot(account.model_dump(mode="json"))
    data["equity"] = float(account.initial_balance)
    data["balance"] = float(account.current_balance)
    data["unrealized_pnl"] = 0.0
    data["realized_pnl"] = 0.0

    await _persist_account(
        account, user.id, "Failed to persist paper account with user_id"
    )

    return _ok(request, data)


@router.delete("/accounts/{account_id}")
async def delete_account(
    account_id: str,
    request: Request,
    mgr: Any = Depends(_get_accounts),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Delete a paper trading account (must be owned by current user)."""
    await _verify_ownership(account_id, user)
    deleted = mgr.delete_account(account_id)
    if not deleted:
        raise NotFoundError(f"Paper account '{account_id}' not found")
    _account_owners.pop(account_id, None)
    return _ok(request, {"deleted": account_id})


@router.post("/accounts/{account_id}/reset")
async def reset_account(
    account_id: str,
    request: Request,
    mgr: Any = Depends(_get_accounts),
    engine: Any = Depends(get_execution_engine),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Reset a paper trading account to initial state."""
    await _verify_ownership(account_id, user)
    if engine is not None and hasattr(engine, "reset_account"):
        success = engine.reset_account(account_id)
        if not success:
            raise NotFoundError(f"Paper account '{account_id}' not found")
        account = mgr.get_account(account_id)
    else:
        account = mgr.reset_account(account_id)
    if account is None:
        raise NotFoundError(f"Paper account '{account_id}' not found")

    try:
        db = request.app.state.db if hasattr(request.app.state, "db") else None
        if db:
            from selene_space_storage.repositories.paper_accounts import (
                PaperAccountRepository,
            )

            repo = PaperAccountRepository(db)
            await repo.clear_equity_history(account_id)
            await repo.save_equity_point(account_id, account.initial_balance)
    except Exception:
        logger.warning(
            "Failed to clear equity history on reset for %s", account_id, exc_info=True
        )

    data = _floatify_account_snapshot(account.model_dump(mode="json"))
    data["equity"] = float(account.initial_balance)
    data["balance"] = float(account.current_balance)
    data["unrealized_pnl"] = 0.0
    data["realized_pnl"] = 0.0
    return _ok(request, data)


@router.get("/accounts/{account_id}")
async def get_account(
    account_id: str,
    request: Request,
    mgr: Any = Depends(_get_accounts),
    pos_mgr: Any = Depends(_get_positions),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Get account details with live equity."""
    await _verify_ownership(account_id, user)
    account = _account_or_404(mgr, account_id)
    _refresh_unrealized_pnl(account_id, pos_mgr)
    data = _floatify_account_snapshot(account.model_dump(mode="json"))
    _enrich_account(data, account, _unrealized_of(pos_mgr, account_id))
    asyncio.create_task(_record_equity(account_id, data["equity"]))
    return APIResponse(data=data)


@router.get("/accounts/{account_id}/state")
async def get_account_state(
    account_id: str,
    request: Request,
    engine: Any = Depends(get_execution_engine),
    acct_mgr: Any = Depends(get_paper_account_manager),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Return a serializable full account state document (resync primitive).

    Aggregates orders/positions/trades/balances/equity from the execution
    engine's getters via ``build_account_state``. Paper clients use this as
    their recovery primitive (§3.3.7): on a detected seq gap they fetch the
    full document and replay it on top of the account_snapshot mixed mode.
    """
    await _verify_ownership(account_id, user)

    if acct_mgr is not None and acct_mgr.get_account(account_id) is None:
        raise NotFoundError(f"Account '{account_id}' not found")

    from app.core.account_state import build_account_state

    sequence = get_channel_sequence()
    seq_keys: dict[str, str] | None = None
    session_id = ""
    if sequence is not None:
        session_id = sequence.session_id
        seq_keys = {
            "orders": f"paper:{account_id}",
            "positions": f"paper:{account_id}",
            "balances": f"paper:{account_id}",
        }

    state = await build_account_state(
        engine,
        account_id,
        session_id=session_id,
        sequence=sequence,
        seq_keys=seq_keys,
        mode=getattr(engine, "mode", ExecutionMode.PAPER),
    )
    return _ok(request, state.model_dump(mode="json"))


# ---------------------------------------------------------------------------
# Order endpoints
# ---------------------------------------------------------------------------


def _swap_symbol_to_ticker(symbol: str) -> str:
    """Convert SWAP symbol to ticker format for market data lookup.

    ``BTC-USDT-SWAP`` → ``BTC/USDT``
    ``ETH-USDT-SWAP`` → ``ETH/USDT``
    Already-normalized symbols like ``BTC/USDT`` pass through unchanged.
    """
    s = symbol.upper().replace("-SWAP", "")
    if "/" in s:
        return s
    if "-" in s:
        parts = s.split("-", 1)
        return f"{parts[0]}/{parts[1]}"
    return s


def _order_args(body: Any) -> dict[str, Any]:
    """请求体 → 下单关键字实参(engine 与 order manager 共用; 不含 mark_price)。"""
    return {
        "account_id": body.account_id,
        "symbol": body.symbol,
        "side": body.side,
        "order_type": body.order_type,
        "quantity": body.quantity,
        "price": body.price,
        "stop_price": body.stop_price,
        "leverage": body.leverage,
        "margin_mode": body.margin_mode,
        "pos_side": body.pos_side,
        "reduce_only": body.reduce_only,
    }


def _fill_market_order_fallback(orders: Any, order: Any, body: Any) -> None:
    """引擎缺席时: 给市价单补一次直接撮合(用 mark/limit/现价当成交价)。"""
    if body.order_type != OrderType.MARKET:
        return
    fill_price = body.mark_price or body.price or _ticker_price(body.symbol)
    if not fill_price or fill_price <= 0:
        return
    try:
        from selene_space_paper.fills import try_fill

        fill = try_fill(order, fill_price, fee_rate=0.001)
        if fill is not None:
            orders.update_fill(order.id, fill.quantity, fill.price)
    except Exception:
        logger.debug("Fallback fill failed", exc_info=True)


@router.post("/orders")
async def place_order(
    request: Request,
    body: PlaceOrderRequest,
    accounts: Any = Depends(_get_accounts),
    orders: Any = Depends(_get_orders),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Place a paper trading order.

    Always delegates to PaperExecutionEngine which guarantees immediate fill
    for market orders.  The engine accepts ``mark_price`` from the frontend
    as a fallback fill price when its internal price cache is empty.
    """
    await _verify_ownership(body.account_id, user)
    account = _account_or_404(accounts, body.account_id)

    engine = get_execution_engine()
    order_args = _order_args(body)

    if engine is not None:
        try:
            order = await engine.place_order(**order_args, mark_price=body.mark_price)
            return _ok(request, order.model_dump(mode="json"))
        except ValueError as exc:
            raise _bad_request(exc) from exc

    # Engine not available — direct order manager fallback
    logger.warning("Execution engine not available, using raw order manager")
    order = orders.place_order(**order_args)
    _fill_market_order_fallback(orders, order, body)

    # Record equity point after order placement
    _snapshot_equity_async(body.account_id, account)

    return _ok(request, order.model_dump(mode="json"))


@router.get("/orders")
async def list_orders(
    request: Request,
    account_id: str = Query(..., description="Paper account ID"),
    status: OrderStatus | None = Query(None, description="Filter by status"),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=1000),
    orders_mgr: Any = Depends(_get_orders),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List orders for an account (must be owned by current user)."""
    await _verify_ownership(account_id, user)
    all_orders = orders_mgr.get_orders(account_id, status=status)
    total = len(all_orders)
    page = all_orders[offset : offset + limit]
    return APIResponse(
        data=[o.model_dump(mode="json") for o in page],
        meta=build_response_meta(
            request,
            pagination=Pagination(offset=offset, limit=limit, total=total),
        ),
        error=None,
    )


@router.delete("/orders/{order_id}")
def _account_of_order(orders_mgr: Any, order_id: str) -> str | None:
    """在订单管理器的 账户→订单 映射里找该订单所属账户。"""
    for acct_id, oids in orders_mgr._account_orders.items():
        if order_id in oids:
            return cast("str", acct_id)
    return None


def _release_margin(account: Any, order: Any, order_id: str) -> None:
    """撤销挂单时把剩余委托量对应的保证金退回账户。"""
    remaining_qty = order.quantity - order.filled_quantity
    if remaining_qty <= 0 or not order.leverage or order.leverage <= 0:
        return
    released = remaining_qty / order.leverage
    account.current_balance += _paper_dec(released)
    logger.info("Released %.2f USDT margin for cancelled order %s", released, order_id)


async def cancel_order_endpoint(
    order_id: str,
    request: Request,
    orders_mgr: Any = Depends(_get_orders),
    accounts: Any = Depends(_get_accounts),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Cancel a pending paper trading order and release frozen margin."""
    engine = get_execution_engine()

    try:
        if engine is not None and hasattr(engine, "cancel_order"):
            order = await engine.cancel_order(order_id)
        else:
            order = orders_mgr.cancel_order(order_id)

        account = None
        account_id = (
            _account_of_order(orders_mgr, order_id) if order is not None else None
        )

        if account_id:
            await _verify_ownership(account_id, user)
            account = accounts.get_account(account_id)
            if account is not None:
                _release_margin(account, order, order_id)

        if account is not None and account_id is not None:
            _snapshot_equity_async(account_id, account)

        return APIResponse(
            data=order.model_dump(mode="json")
            if order
            else {"id": order_id, "status": "cancelled"}
        )
    except KeyError:
        raise NotFoundError(f"Order '{order_id}' not found")
    except Exception as exc:
        raise _bad_request(exc) from exc


# ---------------------------------------------------------------------------
# Close position endpoint
# ---------------------------------------------------------------------------


def _require_engine(engine: Any) -> Any:
    if engine is None:
        raise SeleneSpaceError(
            ErrorCode.SERVICE_UNAVAILABLE, "Execution engine unavailable"
        )
    return engine


def _find_position(pos_mgr: Any, account_id: str, symbol: str, pos_side: Any) -> Any:
    """找 (symbol, pos_side) 对应的持仓。"""
    for p in pos_mgr.get_positions(account_id):
        if (
            _symbols_match(p.symbol, symbol)
            and getattr(p, "pos_side", None) == pos_side
        ):
            return p
    return None


@router.post("/close-position")
async def close_position(
    request: Request,
    body: ClosePositionRequest,
    accounts: Any = Depends(_get_accounts),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Close an open position (market close).

    Places a reduce-only market order in the opposite direction.
    """
    await _verify_ownership(body.account_id, user)
    account = _account_or_404(accounts, body.account_id)

    engine = _require_engine(get_execution_engine())
    pos_mgr = getattr(engine, "_position_mgr", None)
    if pos_mgr is None:
        raise SeleneSpaceError(
            ErrorCode.SERVICE_UNAVAILABLE, "Position manager unavailable"
        )

    target = _find_position(pos_mgr, body.account_id, body.symbol, body.pos_side)
    if target is None or target.quantity <= 0:
        raise NotFoundError("No open position to close")

    close_qty = body.quantity if body.quantity else target.quantity
    close_side = OrderSide.SELL if body.pos_side == PositionSide.LONG else OrderSide.BUY

    try:
        order = await engine.place_order(
            account_id=body.account_id,
            symbol=target.symbol,
            side=close_side,
            order_type=OrderType.MARKET,
            quantity=close_qty,
            price=None,
            leverage=target.leverage,
            margin_mode=target.margin_mode,
            pos_side=body.pos_side,
            reduce_only=True,
            mark_price=body.mark_price,
        )

        try:
            _refresh_unrealized_pnl(
                body.account_id, getattr(engine, "_position_mgr", None)
            )
            unrealized = sum(
                p.unrealized_pnl
                for p in engine._position_mgr.get_open_positions(body.account_id)
            )
            wallet_bal = _wallet_balance(account)
            asyncio.create_task(
                _record_equity(body.account_id, wallet_bal + unrealized)
            )
        except Exception:
            pass

        return _ok(request, order.model_dump(mode="json"))
    except ValueError as exc:
        raise _bad_request(exc) from exc


def _symbols_match(a: str, b: str) -> bool:
    """Flexible symbol comparison across formats."""
    na = a.upper().replace("-SWAP", "").replace("-", "/")
    nb = b.upper().replace("-SWAP", "").replace("-", "/")
    return na == nb


# ---------------------------------------------------------------------------
# Position endpoints
# ---------------------------------------------------------------------------


def _account_balance(engine: Any, account_id: str) -> float | None:
    """从引擎的账户管理器取余额; 取不到则 None。"""
    acct_mgr = getattr(engine, "_account_mgr", None) if engine is not None else None
    acct = acct_mgr.get_account(account_id) if acct_mgr else None
    return float(acct.current_balance) if acct else None


def _apply_liquidation_price(p: Any, balance: float | None) -> None:
    """杠杆仓补算强平价。"""
    if p.leverage <= 1 or p.avg_entry_price <= 0:
        return
    from selene_space_paper.positions import _estimate_liquidation_price

    p.liquidation_price = _estimate_liquidation_price(
        p.avg_entry_price,
        p.leverage,
        p.side,
        p.margin_mode,
        available_balance=balance,
        position_usdt=p.quantity,
    )


@router.get("/positions")
async def list_positions(
    request: Request,
    account_id: str = Query(..., description="Paper account ID"),
    pos_mgr: Any = Depends(_get_positions),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """List positions for an account with live unrealized PnL."""
    await _verify_ownership(account_id, user)
    positions = pos_mgr.get_positions(account_id)

    engine = get_execution_engine()
    market_svc = get_market_service()
    acct_balance = _account_balance(engine, account_id)

    for p in positions:
        if p.quantity <= 0:
            continue
        price = _get_live_price(p.symbol, engine, market_svc)
        if price is not None and price > 0:
            pos_mgr.update_unrealized_pnl(account_id, p.symbol, price)
        _apply_liquidation_price(p, acct_balance)

    positions = pos_mgr.get_positions(account_id)
    return _ok(request, [p.model_dump(mode="json") for p in positions])


def _get_live_price(symbol: str, engine: Any, market_svc: Any) -> float | None:
    """Best-effort current price from engine cache or market data service."""
    if engine is not None:
        prices = getattr(engine, "_last_prices", {})
        if symbol in prices:
            return float(prices[symbol])
        ticker_sym = _swap_symbol_to_ticker(symbol)
        if ticker_sym in prices:
            return float(prices[ticker_sym])

    if market_svc is not None:
        try:
            ticker = market_svc.get_ticker(_swap_symbol_to_ticker(symbol))
            if ticker is not None:
                return float(ticker.last_price)
        except Exception:
            logger.debug(
                "_get_live_price: market service get_ticker failed",
                exc_info=True,
            )
    return None


# ---------------------------------------------------------------------------
# Fills / trade history endpoint
# ---------------------------------------------------------------------------


@router.get("/fills")
async def list_fills(
    request: Request,
    account_id: str = Query(..., description="Paper account ID"),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """Return actual Fill records (not orders) as trade history.

    Mirrors OKX ``/api/v5/trade/fills-history``: each record includes
    execution price, fee, fee_rate, realized PnL, exec_type, etc.
    """
    await _verify_ownership(account_id, user)
    engine = get_execution_engine()
    if engine is None:
        return _ok(request, [])

    fills = await engine.get_fills(account_id)
    data = [f.model_dump(mode="json") for f in fills]
    return _ok(request, data)


# ---------------------------------------------------------------------------
# Fee settings endpoints
# ---------------------------------------------------------------------------


def _fee_settings(account: Any) -> dict[str, Any]:
    """费率设置的响应体。"""
    return {
        "account_id": account.id,
        "maker_fee_rate": account.maker_fee_rate,
        "taker_fee_rate": account.taker_fee_rate,
    }


class FeeSettingsRequest(BaseModel):
    maker_fee_rate: float = Field(
        ..., ge=0, le=0.01, description="Maker fee rate (e.g. 0.0002)"
    )
    taker_fee_rate: float = Field(
        ..., ge=0, le=0.01, description="Taker fee rate (e.g. 0.0005)"
    )


@router.get("/settings")
async def get_paper_settings(
    request: Request,
    account_id: str = Query("paper-default", description="Paper account ID"),
    mgr: Any = Depends(_get_accounts),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Return current paper trading settings (fee rates etc)."""
    await _verify_ownership(account_id, user)
    account = _account_or_404(mgr, account_id)
    return _ok(request, _fee_settings(account))


@router.post("/settings")
async def update_paper_settings(
    request: Request,
    body: FeeSettingsRequest,
    account_id: str = Query("paper-default", description="Paper account ID"),
    mgr: Any = Depends(_get_accounts),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[dict[str, Any]]:
    """Update paper trading fee rates."""
    await _verify_ownership(account_id, user)
    account = _account_or_404(mgr, account_id)

    engine = get_execution_engine()
    if engine is not None and hasattr(engine, "update_fee_rates"):
        engine.update_fee_rates(account_id, body.maker_fee_rate, body.taker_fee_rate)
    else:
        account.maker_fee_rate = body.maker_fee_rate
        account.taker_fee_rate = body.taker_fee_rate

    return _ok(request, _fee_settings(account))


# ---------------------------------------------------------------------------
# PnL endpoint
# ---------------------------------------------------------------------------


def _ticker_price(symbol: str) -> float | None:
    """行情服务的现价; 无服务/取不到则 None。"""
    market_svc = get_market_service()
    if market_svc is None:
        return None
    try:
        ticker = market_svc.get_ticker(_swap_symbol_to_ticker(symbol))
    except Exception:
        logger.debug("Failed to fetch ticker for %s", symbol, exc_info=True)
        return None
    return ticker.last_price if ticker is not None else None


def _pnl_prices(positions: list[Any]) -> dict[str, float]:
    """每个持仓的定价: 优先实时 ticker, 退回持仓自身 current_price。"""
    prices: dict[str, float] = {}
    for p in positions:
        live = _ticker_price(p.symbol)
        prices[p.symbol] = (
            live if live is not None else (getattr(p, "current_price", 0.0) or 0.0)
        )
    return prices


@router.get("/pnl")
async def get_pnl(
    request: Request,
    account_id: str = Query(..., description="Paper account ID"),
    pos_mgr: Any = Depends(_get_positions),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """Calculate PnL for all positions of an account."""
    await _verify_ownership(account_id, user)
    positions = pos_mgr.get_positions(account_id)
    prices = _pnl_prices(positions)

    try:
        from selene_space_paper.pnl import calculate_account_pnl

        records = calculate_account_pnl(positions, prices)
        return _ok(request, [r.model_dump(mode="json") for r in records])
    except ImportError:
        return _ok(
            request,
            [
                {
                    "symbol": p.symbol,
                    "realized_pnl": p.realized_pnl,
                    "unrealized_pnl": p.unrealized_pnl,
                }
                for p in positions
            ],
        )


@router.get("/accounts/{account_id}/equity-history")
async def get_equity_history_endpoint(
    account_id: str,
    request: Request,
    limit: int = Query(100, ge=1, le=1000),
    user: AuthenticatedUser = Depends(optional_user),
) -> APIResponse[list[dict[str, Any]]]:
    """Retrieve historical equity points for an account."""
    await _verify_ownership(account_id, user)
    db = get_db_manager()
    if not db:
        return APIResponse(data=[], meta=build_response_meta(request))
    history = await _account_repo(db).get_equity_history(account_id, limit)
    return APIResponse(data=history, meta=build_response_meta(request))


async def _sync_account_for_equity(repo: Any, account_id: str) -> None:
    """落库前先把账户同步进 DB, 并在首次时补一笔初始权益点(满足外键)。"""
    try:
        acct_mgr = get_paper_account_manager()
        if not acct_mgr:
            return
        account = acct_mgr.get_account(account_id)
        if not account:
            return
        await repo.save_account(
            account.model_dump(), user_id=_owner_of(account_id) or "local"
        )
        if await repo.has_equity_history(account_id):
            return
        initial_equity = float(getattr(account, "initial_balance", 100000.0))
        await repo.save_equity_point(account_id, initial_equity)
        logger.info("Recorded initial equity for %s: %.2f", account_id, initial_equity)
    except Exception as e:
        logger.warning("Account sync/history check failed for %s: %s", account_id, e)


async def _record_equity(account_id: str, equity: float):
    """Internal helper to save a snapshot of account equity.

    Ensures the account exists in the database first to satisfy foreign key constraints,
    as the PaperExecutionEngine only persists to file by default.
    """
    try:
        db = get_db_manager()
        if not db:
            logger.debug("Record equity skipped: No DB manager")
            return

        repo = _account_repo(db)
        await _sync_account_for_equity(repo, account_id)
        await repo.save_equity_point(account_id, equity)
        logger.info("Recorded current equity for %s: %.2f", account_id, equity)
    except Exception as e:
        logger.warning("Error recording equity point for %s: %s", account_id, e)
