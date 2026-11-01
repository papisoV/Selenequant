"""AccountState aggregation service — builds serializable full-account snapshots.

This is the single "build on demand" service behind the AccountState resync
primitive. It aggregates the execution engine's in-memory state through the
existing ``ExecutionEngine`` getter protocol (paper/live/shadow unified), so
REST and WS consumers all get the same document shape.

Ordering invariant (CRITICAL #2 / §3.3): the watermark is read synchronously
BEFORE the getter gather, so ``seq`` is a conservative lower bound — the
snapshot covers every event with seq <= watermark. The envelope seq S is
stamped by the subscription channel's own counter AFTER the gather (Step 5),
giving the stronger guarantee "snapshot covers every event with seq <= S".
"""

from __future__ import annotations

import asyncio
import logging
import time
from decimal import Decimal
from typing import Any

from selene_space_types.trading import AccountState, AccountStateSeq, ExecutionMode

logger = logging.getLogger(__name__)

MAX_ACCOUNT_TRADES = 200


async def _safe_get(engine: Any, method: str, account_id: str) -> list[Any]:
    """Run a single engine getter; any failure empties that domain (N1).

    Mirrors the tolerant ``engine is None -> []`` semantics of the existing
    ``/trading/positions`` endpoint: one broken getter must not 5xx the whole
    document.
    """
    fn = getattr(engine, method, None)
    if fn is None:
        return []
    try:
        result = await fn(account_id)
        return list(result) if result is not None else []
    except Exception:
        logger.warning("AccountState gather: %s(%s) failed", method, account_id, exc_info=True)
        return []


def _resolve_exchange(engine: Any, mode: ExecutionMode) -> str:
    if engine is not None:
        exchange = getattr(engine, "exchange", None)
        if exchange:
            return exchange
    return "paper" if mode == ExecutionMode.PAPER else "unknown"


def resolve_account_id() -> str:
    """Resolve the account_id for AccountState queries (§3.5).

    Prefer the first paper account when the paper account manager is
    configured; fall back to the default paper account otherwise. Shared by
    the WS snapshot path, paper REST and the agent trading_workspace endpoint
    so multi-account paper setups resolve consistently everywhere.
    """
    from app.api.v1.trading import DEFAULT_ACCOUNT
    from app.core.dependencies import get_paper_account_manager

    acct_mgr = get_paper_account_manager()
    list_accounts = getattr(acct_mgr, "list_accounts", None)
    if callable(list_accounts):
        accounts = list_accounts()
        if accounts:
            first_id = getattr(accounts[0], "id", None)
            if first_id:
                return first_id
    return DEFAULT_ACCOUNT


def _paper_equity(engine: Any, account_id: str) -> Decimal | None:
    """Compute paper equity (F11: initial + realized - fee + unrealized).

    Only possible when the engine exposes paper internals; otherwise None
    ("unavailable", e.g. live/shadow without an equity tracker).
    """
    acct_mgr = getattr(engine, "_account_mgr", None)
    pos_mgr = getattr(engine, "_position_mgr", None)
    if acct_mgr is None or pos_mgr is None:
        return None
    account = acct_mgr.get_account(account_id)
    if account is None:
        return None
    wallet = (
        Decimal(str(account.initial_balance))
        + Decimal(str(account.total_realized_pnl))
        - Decimal(str(account.total_fee))
    )
    unrealized = sum(
        Decimal(str(p.unrealized_pnl)) for p in pos_mgr.get_open_positions(account_id)
    )
    return wallet + unrealized


async def build_account_state(
    engine: Any,
    account_id: str,
    *,
    session_id: str = "",
    sequence: Any = None,
    seq_keys: dict[str, str] | None = None,
    mode: ExecutionMode | None = None,
) -> AccountState:
    """Build an AccountState document from an execution engine's getters.

    Args:
        engine: An object implementing the ExecutionEngine getter protocol
            (or None -> empty-state document, never 5xx).
        account_id: Account identifier.
        session_id: Server process identity (client uses it to detect restarts).
        sequence: ChannelSequenceManager (or None -> zero watermark). When
            provided, ``seq_keys`` maps the orders/positions/balances channels
            to their seq_key names for the watermark.
        seq_keys: ``{"orders": key, "positions": key, "balances": key}`` used
            to read the watermark. Paper passes one key for all three; trading
            passes per-channel keys.
        mode: Override the execution mode (defaults to ``engine.mode``).
    """
    # 1. Watermark FIRST — conservative lower bound, before any getter gather.
    if sequence is not None and seq_keys:
        watermark = sequence.watermark(
            orders=seq_keys.get("orders"),
            positions=seq_keys.get("positions"),
            balances=seq_keys.get("balances"),
        )
    else:
        watermark = AccountStateSeq()

    # 2. Resolve mode/exchange.
    resolved_mode = mode
    if resolved_mode is None and engine is not None:
        resolved_mode = getattr(engine, "mode", None)
    if resolved_mode is None:
        resolved_mode = ExecutionMode.PAPER
    exchange = _resolve_exchange(engine, resolved_mode)

    # 3. Gather getters — single getter failure empties that domain (N1).
    balances: list[Any] = []
    positions: list[Any] = []
    orders: list[Any] = []
    fills: list[Any] = []
    if engine is not None:
        gathered = await asyncio.gather(
            _safe_get(engine, "get_balances", account_id),
            _safe_get(engine, "get_positions", account_id),
            _safe_get(engine, "get_orders", account_id),
            _safe_get(engine, "get_fills", account_id),
        )
        balances, positions, orders, fills = gathered

    # 4. Trades truncated to the most recent MAX_ACCOUNT_TRADES (M5).
    if len(fills) > MAX_ACCOUNT_TRADES:
        fills = sorted(fills, key=lambda f: getattr(f, "timestamp", 0), reverse=True)[
            :MAX_ACCOUNT_TRADES
        ]

    equity = _paper_equity(engine, account_id) if engine is not None else None

    state = AccountState(
        session_id=session_id,
        seq=watermark,
        exchange=exchange,
        account_id=account_id,
        mode=resolved_mode,
        balances=balances,
        positions=positions,
        orders=orders,
        trades=fills,
        equity=equity,
        timestamp=int(time.time() * 1000),
    )

    # 5. Best-effort audit (M1 — write failure only warns, never raises).
    try:
        from app.core.audit import audit_event

        await audit_event(
            event_type="account.state_snapshot",
            actor="local_api",
            action="state_snapshot",
            resource=account_id,
            outcome="success",
            severity="info",
            details={
                "account_id": account_id,
                "mode": resolved_mode.value,
                "exchange": exchange,
                "session_id": session_id,
                "seq": watermark.model_dump(),
            },
        )
    except Exception:
        logger.debug("AccountState audit write failed", exc_info=True)

    return state
