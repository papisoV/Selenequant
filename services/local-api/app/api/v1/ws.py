"""WebSocket gateway for real-time market data and paper trading updates.

Two WebSocket endpoints:
- ``/api/v1/ws/markets`` — subscribe to ticker/kline/depth events
- ``/api/v1/ws/paper`` — receive order/position/PnL state changes

Protocol:
- Client sends JSON messages to subscribe/unsubscribe
- Server pushes JSON event frames
- Heartbeat via WebSocket ping/pong (handled by Starlette)
"""

from __future__ import annotations

import asyncio
import json
import os as _os
import time
from typing import Any, cast

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.core.dependencies import get_market_service

logger = structlog.get_logger(__name__)

router = APIRouter(tags=["websocket"])


def _now_ms() -> int:
    """Epoch milliseconds — every WS frame carries one."""
    return int(time.time() * 1000)


async def _recv_json(raw: str, ws: WebSocket) -> dict[str, Any] | None:
    """解析一条 WS 文本为 dict; 不是合法 JSON 就回 error 帧并返回 None。

    调用方收到 None 时应当 ``continue``(跳过本条消息)。
    """
    try:
        return cast("dict[str, Any]", json.loads(raw))
    except json.JSONDecodeError:
        await ws.send_json({"type": "error", "message": "Invalid JSON"})
        return None


# Polymarket WS bridge — shared connection per backend instance
_poly_ws_client = None
_poly_ws_lock = asyncio.Lock()


# ---------------------------------------------------------------------------
# Connection manager
# ---------------------------------------------------------------------------


_ALLOWED_ORIGINS: set[str] = {
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "tauri://localhost",
    "https://tauri.localhost",
    "http://tauri.localhost",
    "http://tauri.localhost:8088",
}

# 多副本并行开发:worktree 副本用不同前端端口(如 3001),经 env 注入扩展白名单
for _extra in filter(None, _os.environ.get("SELENE_WS_EXTRA_ORIGINS", "").split(",")):
    _ALLOWED_ORIGINS.add(_extra.strip())

_MAX_WS_CONNECTIONS_PER_IP = int(_os.environ.get("SELENE_WS_MAX_PER_IP", "50"))
_MAX_WS_CONNECTIONS_GLOBAL = int(_os.environ.get("SELENE_WS_MAX_GLOBAL", "5000"))
_ip_connection_counts: dict[str, int] = {}


def _check_origin(ws: WebSocket) -> bool:
    """Validate Origin header against the allow-list (CSWSH protection)."""
    origin = ws.headers.get("origin", "")
    if not origin:
        return True
    return origin in _ALLOWED_ORIGINS


def _get_client_ip(ws: WebSocket) -> str:
    """Return the direct client IP. Does NOT trust x-forwarded-for."""
    return ws.client.host if ws.client else "unknown"


def _release_ip(ws: WebSocket) -> None:
    """一个连接断开时递减其 IP 计数, 归零则移除。"""
    ip = _get_client_ip(ws)
    cnt = _ip_connection_counts.get(ip, 1) - 1
    if cnt <= 0:
        _ip_connection_counts.pop(ip, None)
    else:
        _ip_connection_counts[ip] = cnt


class ConnectionManager:
    """Track active WebSocket connections and their subscriptions."""

    def __init__(self) -> None:
        self._connections: dict[WebSocket, set[str]] = {}

    def _within_limits(self, ws: WebSocket) -> bool:
        ip_count = _ip_connection_counts.get(_get_client_ip(ws), 0)
        total = sum(m.active_count for m in _ALL_MANAGERS)
        return (
            ip_count < _MAX_WS_CONNECTIONS_PER_IP and total < _MAX_WS_CONNECTIONS_GLOBAL
        )

    async def connect(self, ws: WebSocket) -> bool:
        """Accept connection if within limits. Returns False if rejected."""
        if not _check_origin(ws):
            await ws.close(code=4003, reason="Origin not allowed")
            return False
        if not self._within_limits(ws):
            await ws.close(code=4029, reason="Too many connections")
            return False
        await ws.accept()
        self._connections[ws] = set()
        ip = _get_client_ip(ws)
        _ip_connection_counts[ip] = _ip_connection_counts.get(ip, 0) + 1
        return True

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self._connections:
            del self._connections[ws]
            _release_ip(ws)

    def subscribe(self, ws: WebSocket, channel: str) -> None:
        if ws in self._connections:
            self._connections[ws].add(channel)

    def unsubscribe(self, ws: WebSocket, channel: str) -> None:
        if ws in self._connections:
            self._connections[ws].discard(channel)

    def get_subscriptions(self, ws: WebSocket) -> set[str]:
        return self._connections.get(ws, set())

    def _subscribers(self, channel: str) -> list[WebSocket]:
        return [ws for ws, chans in self._connections.items() if channel in chans]

    async def _send_to_subscribers(self, channel: str, text: str) -> None:
        """把已序列化的文本发给订阅者; 发失败的连接断开。"""
        dead: list[WebSocket] = []
        for ws in self._subscribers(channel):
            try:
                await ws.send_text(text)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)

    async def broadcast(self, channel: str, data: dict[str, Any]) -> None:
        """Send data to all connections subscribed to *channel*.

        Pre-serializes JSON once and sends the same text to all clients,
        avoiding redundant json.dumps() per connection (N clients = 1 serialize).
        """
        if not self.has_subscribers(channel):
            return
        text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        await self._send_to_subscribers(channel, text)

    async def broadcast_raw(self, channel: str, text: str) -> None:
        """Send a pre-serialized JSON string to all subscribers (zero-copy)."""
        await self._send_to_subscribers(channel, text)

    def has_subscribers(self, channel: str) -> bool:
        """Check if any connection is subscribed to the given channel."""
        return any(channel in chans for chans in self._connections.values())

    @property
    def active_count(self) -> int:
        return len(self._connections)


# Singleton managers
_market_manager = ConnectionManager()
_paper_manager = ConnectionManager()
_trading_manager = ConnectionManager()
_polymarket_manager = ConnectionManager()
_agent_manager = ConnectionManager()
_strategy_manager = ConnectionManager()
_research_manager = ConnectionManager()
_intelligence_manager = ConnectionManager()
_ALL_MANAGERS = [
    _market_manager,
    _paper_manager,
    _trading_manager,
    _polymarket_manager,
    _agent_manager,
    _strategy_manager,
    _research_manager,
    _intelligence_manager,
]

# Per-WS user_id tracking (for user-scoped channels)
_ws_user_ids: dict[WebSocket, str] = {}

# Per-WS fusion config (for multi-strategy signal fusion)
_fusion_configs: dict[WebSocket, dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# /api/v1/ws/markets
# ---------------------------------------------------------------------------


async def _push_kline_snapshot(
    ws: WebSocket,
    symbols: list[str],
    exchange: str,
    market_type: str,
    interval: str | None = None,
) -> None:
    """Send cached K-line data to a newly subscribed client for instant rendering.

    Args:
        interval: Optional specific interval to push. If None, pushes all cached intervals.
    """
    try:
        from app.core.redis import get_redis
        from selene_space_market.kline_store import KlineStore

        redis_client = get_redis()
        if redis_client is None:
            return
        store = KlineStore(redis_client)
        ts = _now_ms()
        # Determine which intervals to push
        intervals_to_push = (
            [interval]
            if interval
            else ("1m", "5m", "15m", "30m", "1h", "4h", "8h", "1d")
        )
        for sym in symbols:
            for ivl in intervals_to_push:
                cached = await store.get(exchange, market_type, sym, ivl, limit=200)
                if cached:
                    data_json = (
                        "[" + ",".join(c.model_dump_json() for c in cached) + "]"
                    )
                    text = (
                        f'{{"type":"kline_snapshot","symbol":"{sym}",'
                        f'"exchange":"{exchange}","market_type":"{market_type}",'
                        f'"interval":"{ivl}","data":{data_json},"timestamp":{ts}}}'
                    )
                    await ws.send_text(text)
    except Exception:
        logger.debug("kline_snapshot push failed", exc_info=True)


@router.websocket("/api/v1/ws/markets")
async def ws_markets(ws: WebSocket) -> None:
    """Real-time market data WebSocket.

    Client messages (JSON):
        ``{"action": "subscribe", "symbols": ["BTC/USDT"]}``
        ``{"action": "unsubscribe", "symbols": ["BTC/USDT"]}``

    Server pushes:
        ``{"type": "ticker", "symbol": "BTC/USDT", "data": {...}}``
        ``{"type": "kline", "symbol": "BTC/USDT", "data": {...}}``
        ``{"type": "depth", "symbol": "BTC/USDT", "data": {...}}``
    """
    if not await _market_manager.connect(ws):
        return
    logger.info("ws_market_connected", connections=_market_manager.active_count)
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60.0)
            except TimeoutError:
                # Send ping to keep connection alive
                try:
                    await ws.send_json({"type": "ping", "timestamp": _now_ms()})
                except Exception:
                    break
                continue

            msg = await _recv_json(raw, ws)
            if msg is None:
                continue

            action = msg.get("action")
            symbols = msg.get("symbols", [])

            if action == "subscribe":
                ex = msg.get("exchange", "binance")
                mt = msg.get("market_type", "futures")
                interval = msg.get("interval")  # Optional: specific kline interval
                for sym in symbols:
                    _market_manager.subscribe(ws, f"market:{ex}:{mt}:{sym}")
                svc = get_market_service()
                if svc is not None:
                    for sym in symbols:
                        # "ALL" 是全局频道，不需要订阅特定交易对
                        if sym == "ALL":
                            continue
                        try:
                            await svc.add_symbol(
                                sym,
                                exchange=ex,
                                market_type=mt,
                                kline_interval=interval,
                            )
                        except Exception:
                            logger.warning(
                                "ws_add_symbol_failed", symbol=sym, exc_info=True
                            )
                await ws.send_json(
                    {
                        "type": "subscribed",
                        "symbols": symbols,
                        "exchange": ex,
                        "market_type": mt,
                        "interval": interval,
                        "timestamp": _now_ms(),
                    }
                )

                # Push kline_snapshot from Redis cache for instant chart rendering
                asyncio.ensure_future(
                    _push_kline_snapshot(ws, symbols, ex, mt, interval=interval)
                )
            elif action == "unsubscribe":
                ex = msg.get("exchange", "binance")
                mt = msg.get("market_type", "futures")
                interval = msg.get(
                    "interval"
                )  # Optional: specific kline interval to unsubscribe
                for sym in symbols:
                    _market_manager.unsubscribe(ws, f"market:{ex}:{mt}:{sym}")
                svc = get_market_service()
                if svc is not None:
                    for sym in symbols:
                        if sym == "ALL":
                            continue
                        try:
                            # If interval is provided, only unsubscribe that specific kline interval
                            # Otherwise, unsubscribe all streams (ticker, depth, all klines)
                            await svc.remove_symbol(
                                sym,
                                exchange=ex,
                                market_type=mt,
                                kline_interval=interval,
                            )
                        except Exception:
                            logger.warning(
                                "ws_remove_symbol_failed", symbol=sym, exc_info=True
                            )
                await ws.send_json(
                    {
                        "type": "unsubscribed",
                        "symbols": symbols,
                        "interval": interval,
                        "timestamp": _now_ms(),
                    }
                )
            else:
                await ws.send_json(
                    {"type": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    finally:
        _market_manager.disconnect(ws)
        logger.info("ws_market_disconnected", connections=_market_manager.active_count)


# ---------------------------------------------------------------------------
# /api/v1/ws/paper
# ---------------------------------------------------------------------------


async def _resolve_ws_user(ws: WebSocket, *, token: str | None = None) -> str | None:
    """Extract user_id from a JWT token.

    Accepts token from:
    1. Explicit ``token`` parameter (first-message auth, preferred)
    2. Query param ``?token=`` (legacy, still supported)

    Returns the user_id string if auth is enabled and the token is valid,
    ``"local"`` for localhost connections, or ``None`` on failure.

    Fail-closed (C2 fix): when ``SELENE_AUTH_FAILCLOSED=1`` (default) and
    the user cannot be resolved (remote + no token), the WebSocket is
    closed with code 4401 before returning None. Callers must check for
    None and return immediately. Set ``SELENE_AUTH_FAILCLOSED=0`` to
    revert to v1 fail-open (None → soft allow, subscribe-time rejection).
    """
    from app.core.auth_flags import is_auth_failclosed

    # Local-first trust model: localhost connections are trusted.
    # TestClient uses host "testclient" — treat it as local too.
    client = ws.client
    if client:
        host = client.host or ""
        if host in ("127.0.0.1", "::1", "localhost", "testclient"):
            return "local"

    # Remote connections: require valid token (future: JWT).
    # SELENE_AUTH_DISABLED does NOT grant remote trust — the audited bypass
    # is for localhost dev only (already handled above).
    if not token:
        token = ws.query_params.get("token", "")
    if not token:
        if is_auth_failclosed():
            try:
                await ws.close(code=4401, reason="Authentication required")
            except Exception:
                pass
        return None
    # Future: JWT validation for non-localhost connections
    if is_auth_failclosed():
        try:
            await ws.close(code=4401, reason="Invalid token")
        except Exception:
            pass
    return None


async def _verify_account_ownership(user_id: str, account_id: str) -> bool:
    """Check if the given user owns the paper account (or auth is disabled).

    Fail-close: exceptions deny access rather than granting it.
    """
    if user_id == "local":
        return True
    from app.core.dependencies import get_db_manager

    db = get_db_manager()
    if db is None:
        return False
    try:
        from selene_space_storage.repositories.paper_accounts import (
            PaperAccountRepository,
        )

        repo = PaperAccountRepository(db)
        acct = await repo.get_account(account_id)
        if acct is None:
            return False
        acct_user = str(acct.get("user_id", "local"))
        return acct_user == user_id or acct_user == "local"
    except Exception:
        logger.warning(
            "account_ownership_check_failed", account_id=account_id, exc_info=True
        )
        return False


@router.websocket("/api/v1/ws/paper")
async def ws_paper(ws: WebSocket) -> None:
    """Paper Trading state WebSocket.

    Client messages (JSON):
        ``{"action": "subscribe", "account_id": "pa-xxx"}``
        ``{"action": "unsubscribe", "account_id": "pa-xxx"}``

    Server pushes:
        ``{"type": "order_update", "account_id": "pa-xxx", "data": {...}}``
        ``{"type": "position_update", "account_id": "pa-xxx", "data": {...}}``
        ``{"type": "pnl_update", "account_id": "pa-xxx", "data": {...}}``
    """
    if not await _paper_manager.connect(ws):
        return
    ws_user_id = await _resolve_ws_user(ws)
    logger.info(
        "ws_paper_connected", connections=_paper_manager.active_count, user=ws_user_id
    )
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60.0)
            except TimeoutError:
                try:
                    await ws.send_json({"type": "ping", "timestamp": _now_ms()})
                except Exception:
                    break
                continue

            msg = await _recv_json(raw, ws)
            if msg is None:
                continue

            action = msg.get("action")
            account_id = msg.get("account_id", "")

            if action == "subscribe" and account_id:
                if ws_user_id is None:
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": "Authentication required: provide a valid token query parameter",
                            "timestamp": _now_ms(),
                        }
                    )
                    continue
                if ws_user_id != "local" and not await _verify_account_ownership(
                    ws_user_id, account_id
                ):
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": "Access denied: account does not belong to current user",
                            "timestamp": _now_ms(),
                        }
                    )
                    continue
                _paper_manager.subscribe(ws, f"paper:{account_id}")
                await ws.send_json(
                    {
                        "type": "subscribed",
                        "account_id": account_id,
                        "timestamp": _now_ms(),
                    }
                )
            elif action == "unsubscribe" and account_id:
                _paper_manager.unsubscribe(ws, f"paper:{account_id}")
                await ws.send_json(
                    {
                        "type": "unsubscribed",
                        "account_id": account_id,
                        "timestamp": _now_ms(),
                    }
                )
            else:
                await ws.send_json(
                    {"type": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    finally:
        _paper_manager.disconnect(ws)
        logger.info("ws_paper_disconnected", connections=_paper_manager.active_count)


# ---------------------------------------------------------------------------
# /api/v1/ws/trading
# ---------------------------------------------------------------------------


async def _push_account_state_snapshot(ws_user_id: str, channel: str) -> None:
    """Build an AccountState snapshot and push it on a trading channel.

    A5 (protocol §3.2/§3.3): the envelope seq S is the subscription channel's
    OWN counter, taken AFTER the getter gather — so the snapshot is a normal
    full event of that channel and the client's gap-check needs zero
    special-casing for ``account_state`` events.
    """
    from app.core.account_state import build_account_state, resolve_account_id
    from app.core.dependencies import get_execution_engine
    from app.core.sequence import get_channel_sequence

    engine = get_execution_engine()
    account_id = resolve_account_id()
    seq_mgr = get_channel_sequence()
    session_id = seq_mgr.session_id if seq_mgr is not None else ""
    seq_keys = None
    if seq_mgr is not None:
        seq_keys = {
            "orders": f"{ws_user_id}:orders",
            "positions": f"{ws_user_id}:positions",
            "balances": f"{ws_user_id}:balances",
        }
    state = await build_account_state(
        engine,
        account_id,
        session_id=session_id,
        sequence=seq_mgr,
        seq_keys=seq_keys,
    )
    # Watermark-first ordering invariant: the envelope seq is the channel's
    # next value AFTER the gather, so S > every event stamped during gather.
    seq = seq_mgr.next(f"{ws_user_id}:{channel}") if seq_mgr is not None else None
    envelope: dict[str, Any] = {
        "type": "account_state",
        "data": state.model_dump(mode="json"),
        "timestamp": _now_ms(),
    }
    if seq is not None:
        envelope["seq"] = seq
    if session_id:
        envelope["session_id"] = session_id
    await _trading_manager.broadcast(f"trading:{ws_user_id}:{channel}", envelope)


@router.websocket("/api/v1/ws/trading")
async def ws_trading(ws: WebSocket) -> None:
    """Unified trading WebSocket — receives order, fill, position, balance events.

    Client messages (JSON):
        ``{"action": "subscribe", "channels": ["orders", "positions", "balances"]}``
        ``{"action": "unsubscribe", "channels": ["orders"]}``
        ``{"action": "resync", "channels": ["orders"]}``

    Server pushes:
        ``{"type": "order_update", "data": {...}}``
        ``{"type": "fill", "data": {...}}``
        ``{"type": "position_update", "data": {...}}``
        ``{"type": "balance_update", "data": [...]}``
        ``{"type": "account_state", "data": <AccountState>, "seq": S}`` — full
        snapshot pushed on the first subscribed channel, and on every resync.

    Client bookkeeping (P3, protocol §3.3): the FIRST event received on a
    seq_key is the baseline — ``last[key] = (session_id, seq)`` — and is never
    gap-checked. Only later events with ``seq != last+1``, or a ``session_id``
    change, indicate a gap → request a resync. Events WITHOUT a ``seq`` field
    (e.g. paper-channel ``strategy_signal`` / ``runner_status``) are pure info:
    skip the gap-check, do not update ``last``, and do not participate in
    baseline initialisation (MAJOR-1, protocol §3.3 rule 3b).
    """
    if not await _trading_manager.connect(ws):
        return
    ws_user_id = await _resolve_ws_user(ws)
    if ws_user_id:
        _ws_user_ids[ws] = ws_user_id
    logger.info(
        "ws_trading_connected",
        connections=_trading_manager.active_count,
        user=ws_user_id,
    )
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60.0)
            except TimeoutError:
                try:
                    await ws.send_json({"type": "ping", "timestamp": _now_ms()})
                except Exception:
                    break
                continue

            msg = await _recv_json(raw, ws)
            if msg is None:
                continue

            action = msg.get("action")
            channels = msg.get("channels", [])

            # First-message auth support
            if action == "auth":
                token = msg.get("token", "")
                resolved = await _resolve_ws_user(ws, token=token)
                if resolved:
                    ws_user_id = resolved
                    _ws_user_ids[ws] = ws_user_id
                    await ws.send_json(
                        {"type": "authenticated", "timestamp": _now_ms()}
                    )
                else:
                    await ws.send_json({"type": "error", "message": "Invalid token"})
                continue

            if action == "subscribe":
                if ws_user_id is None:
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": "Authentication required",
                            "timestamp": _now_ms(),
                        }
                    )
                    continue
                for ch in channels:
                    _trading_manager.subscribe(ws, f"trading:{ws_user_id}:{ch}")
                # Subscribe-then-snapshot (A5 / M6): the FIRST subscribed
                # channel immediately gets a full account_state snapshot stamped
                # with its own counter. Remaining channels self-heal via their
                # full-replacement delta events — only one full document per
                # subscription, not one per channel.
                if channels:
                    await _push_account_state_snapshot(ws_user_id, channels[0])
                await ws.send_json(
                    {
                        "type": "subscribed",
                        "channels": channels,
                        "timestamp": _now_ms(),
                    }
                )
            elif action == "unsubscribe":
                for ch in channels:
                    _trading_manager.unsubscribe(
                        ws, f"trading:{ws_user_id or 'local'}:{ch}"
                    )
                await ws.send_json(
                    {
                        "type": "unsubscribed",
                        "channels": channels,
                        "timestamp": _now_ms(),
                    }
                )
            elif action == "resync":
                # Explicit gap recovery (§3.3.5): push a full snapshot on each
                # requested channel, stamped with that channel's own counter.
                if ws_user_id is None:
                    await ws.send_json(
                        {
                            "type": "error",
                            "message": "Authentication required",
                            "timestamp": _now_ms(),
                        }
                    )
                    continue
                for ch in msg.get("channels", []):
                    await _push_account_state_snapshot(ws_user_id, ch)
                await ws.send_json(
                    {
                        "type": "resynced",
                        "channels": msg.get("channels", []),
                        "timestamp": _now_ms(),
                    }
                )
            else:
                await ws.send_json(
                    {"type": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    finally:
        _ws_user_ids.pop(ws, None)
        _trading_manager.disconnect(ws)
        logger.info(
            "ws_trading_disconnected", connections=_trading_manager.active_count
        )


# ---------------------------------------------------------------------------
# /api/v1/ws/agent — Agent reasoning event stream
# ---------------------------------------------------------------------------

# Map internal AgentStreamEventType to WS event names
_AGENT_EVENT_MAP: dict[str, str] = {
    "thinking": "thinking",
    "tool_call": "tool_call",
    "tool_result": "tool_result",
    "reflection": "reflection",
    "text_delta": "final_answer",
    "done": "done",
}


@router.websocket("/api/v1/ws/agent")
async def ws_agent(ws: WebSocket) -> None:
    """Agent reasoning WebSocket — bidirectional agent interaction.

    Client messages (JSON):
        ``{"action": "chat", "message": "BTC 价格多少？", "session_id": "..."}``

    Server pushes (event sequence):
        ``{"type": "reasoning_start", ...}``
        ``{"type": "thinking", "data": {"content": "...", "round": 1}}``
        ``{"type": "tool_call", "data": {"tool": "...", "arguments": {...}}}``
        ``{"type": "tool_result", "data": {"tool": "...", "output": "..."}}``
        ``{"type": "reflection", "data": {"content": "...", "round": 1}}``
        ``{"type": "final_answer", "data": {"text": "..."}}``
        ``{"type": "done", "data": {}}``
    """
    if not await _agent_manager.connect(ws):
        return
    ws_user_id = await _resolve_ws_user(ws)
    if ws_user_id is None:
        await ws.send_json({"type": "error", "message": "Authentication required"})
        _agent_manager.disconnect(ws)
        await ws.close(code=4001, reason="Authentication required")
        return
    _ws_user_ids[ws] = ws_user_id
    _agent_manager.subscribe(ws, f"agent:{ws_user_id}:reasoning")
    logger.info(
        "ws_agent_connected", connections=_agent_manager.active_count, user=ws_user_id
    )
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=120.0)
            except TimeoutError:
                try:
                    await ws.send_json({"type": "ping", "timestamp": _now_ms()})
                except Exception:
                    break
                continue

            msg = await _recv_json(raw, ws)
            if msg is None:
                continue

            action = msg.get("action")

            if action == "chat":
                user_message = msg.get("message", "").strip()
                if not user_message:
                    await ws.send_json({"type": "error", "message": "Empty message"})
                    continue
                await _handle_agent_chat_ws(ws, user_message, msg.get("session_id"))
            else:
                await ws.send_json(
                    {"type": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    finally:
        _ws_user_ids.pop(ws, None)
        _agent_manager.disconnect(ws)
        logger.info("ws_agent_disconnected", connections=_agent_manager.active_count)


async def _handle_agent_chat_ws(
    ws: WebSocket, message: str, session_id: str | None
) -> None:
    """Process a chat message through AgentRuntime and push events over WS."""
    from app.core.dependencies import get_agent_runtime, get_settings_service

    user_id = _ws_user_ids.get(ws, "local")
    uid = user_id if user_id != "local" else None
    runtime = None
    try:
        from app.api.v1.agent import _get_user_runtime

        settings_service = get_settings_service()
        runtime = await _get_user_runtime(settings_service, user_id=uid)
    except Exception:
        pass
    if runtime is None:
        runtime = get_agent_runtime()
    ts = _now_ms()

    if runtime is None:
        await ws.send_json(
            _ws_frame("final_answer", {"text": "Agent runtime is not available."}, ts)
        )
        await ws.send_json(_ws_frame("done", {}, ts))
        return

    await ws.send_json(
        _ws_frame("reasoning_start", {"session_id": session_id or ""}, ts)
    )

    # This socket has no approval round-trip, so it must not run state-writing
    # tools unconfirmed. Deny them with an actionable reason rather than
    # silently bypassing the confirmation gate the HTTP surface enforces.
    async def _deny_write_tool(request_id: str, tool_name: str, arguments: dict) -> str:
        logger.warning("ws_agent_write_tool_denied tool=%s", tool_name)
        return (
            f"'{tool_name}' requires explicit user confirmation, which this "
            "WebSocket surface cannot collect. Use the agent chat panel, "
            "which asks before running it."
        )

    try:
        async for event in runtime.process_message(
            message, approval_gate=_deny_write_tool
        ):
            await _send_agent_event(ws, event)
    except Exception as exc:
        logger.error("ws_agent_error", error=str(exc), exc_info=True)
        await ws.send_json(_ws_frame("error", {"message": str(exc)}, _now_ms()))
        await ws.send_json(_ws_frame("done", {}, _now_ms()))


def _ws_frame(type_: str, data: dict[str, Any], ts: int) -> dict[str, Any]:
    """WS 消息信封 {type, data, timestamp}。"""
    return {"type": type_, "data": data, "timestamp": ts}


async def _send_agent_event(ws: WebSocket, event: Any) -> None:
    """把一个 AgentRuntime 事件转成 WS 帧发出。"""
    ws_type = _AGENT_EVENT_MAP.get(event.type.value, event.type.value)
    await ws.send_json(_ws_frame(ws_type, event.data, event.timestamp))


async def _subscribe_fusion(
    ws: WebSocket, channel_owner: str, msg: dict[str, Any]
) -> None:
    """订阅多策略融合信号: 登记配置 + 回执。"""
    strategy_ids = msg.get("strategy_ids", [])
    symbol = msg.get("symbol", "BTC/USDT")
    min_agreeing = msg.get("min_agreeing", 3)

    _strategy_manager.subscribe(ws, f"fusion:{channel_owner}:{symbol}")
    _fusion_configs[ws] = {
        "strategy_ids": strategy_ids,
        "symbol": symbol,
        "min_agreeing": min_agreeing,
    }
    await ws.send_json(
        {
            "type": "fusion_subscribed",
            "symbol": symbol,
            "strategy_ids": strategy_ids,
            "min_agreeing": min_agreeing,
            "timestamp": _now_ms(),
        }
    )
    logger.info(
        "ws_fusion_subscribed",
        symbol=symbol,
        strategy_count=len(strategy_ids),
        min_agreeing=min_agreeing,
    )


async def _unsubscribe_fusion(
    ws: WebSocket, channel_owner: str, msg: dict[str, Any]
) -> None:
    """退订融合信号并清配置。"""
    symbol = msg.get("symbol", "BTC/USDT")
    _strategy_manager.unsubscribe(ws, f"fusion:{channel_owner}:{symbol}")
    _fusion_configs.pop(ws, None)
    await ws.send_json(
        {"type": "fusion_unsubscribed", "symbol": symbol, "timestamp": _now_ms()}
    )


async def broadcast_agent_event(
    event_type: str,
    data: dict[str, Any],
    *,
    user_id: str = "local",
) -> None:
    """Push an agent reasoning event to the user's subscribed WS clients."""
    await _agent_manager.broadcast(
        f"agent:{user_id}:reasoning",
        {"type": event_type, "data": data, "timestamp": _now_ms()},
    )


# ---------------------------------------------------------------------------
# /api/v1/ws/strategy — Strategy signal stream
# ---------------------------------------------------------------------------


@router.websocket("/api/v1/ws/strategy")
async def ws_strategy(ws: WebSocket) -> None:
    """Strategy signal WebSocket — real-time trading signals from deployed strategies.

    Client messages (JSON):
        ``{"action": "subscribe", "strategy_id": "sma-cross-1"}``
        ``{"action": "unsubscribe", "strategy_id": "sma-cross-1"}``

    Server pushes:
        ``{"type": "strategy_signal", "data": {"strategy_id": "...", "symbol": "BTC/USDT", "side": "buy", "strength": 1.0, "reason": "..."}}``
    """
    if not await _strategy_manager.connect(ws):
        return
    ws_user_id = await _resolve_ws_user(ws)
    if ws_user_id:
        _ws_user_ids[ws] = ws_user_id
    logger.info(
        "ws_strategy_connected",
        connections=_strategy_manager.active_count,
        user=ws_user_id,
    )
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60.0)
            except TimeoutError:
                try:
                    await ws.send_json({"type": "ping", "timestamp": _now_ms()})
                except Exception:
                    break
                continue

            msg = await _recv_json(raw, ws)
            if msg is None:
                continue

            action = msg.get("action")

            if action == "subscribe":
                # Subscribe to strategy signal channel
                _strategy_manager.subscribe(ws, f"strategy:{ws_user_id or 'local'}")
                await ws.send_json(
                    {
                        "type": "subscribed",
                        "timestamp": _now_ms(),
                    }
                )
            elif action == "unsubscribe":
                _strategy_manager.unsubscribe(ws, f"strategy:{ws_user_id or 'local'}")
                await ws.send_json(
                    {
                        "type": "unsubscribed",
                        "timestamp": _now_ms(),
                    }
                )
            elif action == "subscribe_fusion":
                await _subscribe_fusion(ws, ws_user_id or "local", msg)
            elif action == "unsubscribe_fusion":
                await _unsubscribe_fusion(ws, ws_user_id or "local", msg)
            else:
                await ws.send_json(
                    {"type": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    finally:
        _ws_user_ids.pop(ws, None)
        _fusion_configs.pop(ws, None)
        _strategy_manager.disconnect(ws)
        logger.info(
            "ws_strategy_disconnected", connections=_strategy_manager.active_count
        )


# ---------------------------------------------------------------------------
# Helper for broadcasting from other modules
# ---------------------------------------------------------------------------


async def broadcast_market_event(
    symbol: str, event_type: str, data: dict[str, Any]
) -> None:
    """Push a market event to all WebSocket subscribers of that symbol.

    Also publishes to Redis Pub/Sub so other workers receive the event.
    """
    exchange = data.get("exchange", "binance")
    market_type = data.get("market_type", "futures")

    channel = f"market:{exchange}:{market_type}:{symbol}"
    payload = {
        "type": event_type,
        "symbol": symbol,
        "data": data,
        "timestamp": _now_ms(),
    }
    await _market_manager.broadcast(channel, payload)

    # Cross-worker Pub/Sub only when multi-worker is enabled
    if _os.environ.get("SELENE_WS_PUBSUB_ENABLED", "").lower() in ("1", "true"):
        from app.core.redis_pubsub import publish as redis_publish

        await redis_publish(channel, payload)

    if symbol != "ALL" and event_type in (
        "large_trade",
        "large_order",
        "liquidation",
        "liquidation_stats",
        "funding_rate",
        "footprint_bar",
        "vpvr_update",
        "options_ticker",
        # A-share anomalies are per-symbol, but feed-bar clients subscribe to
        # the ALL channel only — without this they would never receive one.
        "anomaly",
    ):
        all_channel = f"market:{exchange}:{market_type}:ALL"
        await _market_manager.broadcast(all_channel, payload)


async def broadcast_market_event_fast(
    symbol: str,
    event_type: str,
    data_json: str,
    *,
    exchange: str = "binance",
    market_type: str = "futures",
) -> None:
    """Zero-copy fast path: accept pre-serialized JSON from model_dump_json().

    Skips model_dump() → dict → json.dumps() double serialization.
    Used for high-frequency ticker/kline events.
    """
    channel = f"market:{exchange}:{market_type}:{symbol}"
    ts = _now_ms()
    text = f'{{"type":"{event_type}","symbol":"{symbol}","data":{data_json},"timestamp":{ts}}}'
    await _market_manager.broadcast_raw(channel, text)

    if symbol != "ALL" and event_type in (
        "large_trade",
        "large_order",
        "liquidation",
        "liquidation_stats",
        "funding_rate",
        "footprint_bar",
        "vpvr_update",
        "options_ticker",
        "anomaly",
    ):
        all_channel = f"market:{exchange}:{market_type}:ALL"
        await _market_manager.broadcast_raw(all_channel, text)


async def broadcast_paper_event(
    account_id: str,
    event_type: str,
    data: dict[str, Any],
    *,
    seq: int | None = None,
    session_id: str | None = None,
) -> None:
    """Push a paper trading event to all WebSocket subscribers of that account.

    ``seq`` is supplied by the single writer (the bridge callback) — this
    function never advances a counter itself. When None the field is omitted
    from the envelope (backward-compatible; old clients ignore unknown keys).
    """
    envelope: dict[str, Any] = {
        "type": event_type,
        "account_id": account_id,
        "data": data,
        "timestamp": _now_ms(),
    }
    if seq is not None:
        envelope["seq"] = seq
    if session_id:
        envelope["session_id"] = session_id
    await _paper_manager.broadcast(f"paper:{account_id}", envelope)


async def broadcast_strategy_signal(signal_data: dict[str, Any]) -> None:
    """Broadcast strategy signal to frontend WebSocket clients.

    Args:
        signal_data: Dict with keys: strategy_id, symbol, side, strength, reason
    """
    message = {
        "type": "strategy_signal",
        "data": signal_data,
        "timestamp": _now_ms(),
    }
    # Broadcast to strategy channel (user-scoped)
    # For now, broadcast to all strategy subscribers (local mode)
    await _strategy_manager.broadcast("strategy:local", message)


async def broadcast_fusion_signal(
    symbol: str,
    side: str,
    strength: float,
    agreeing_count: int,
    total_count: int,
    *,
    user_id: str = "local",
    strategy_ids: list[str] | None = None,
    reason: str = "",
) -> None:
    """Broadcast fused multi-strategy signal to WebSocket subscribers.

    This is called after StrategyEnsemble.fuse_with_qbot_voting() produces a consensus signal.

    Args:
        symbol: Trading pair symbol (e.g., "BTC/USDT").
        side: Signal direction ("buy" or "sell").
        strength: Fused signal strength/confidence (0.0 to 1.0).
        agreeing_count: Number of strategies that agreed on this signal.
        total_count: Total number of strategies in the ensemble.
        user_id: User scope for the broadcast channel.
        strategy_ids: IDs of strategies that contributed to the fusion.
        reason: Human-readable reason for the fused signal.
    """
    message = {
        "type": "fusion_signal",
        "data": {
            "symbol": symbol,
            "side": side,
            "strength": strength,
            "agreeing_count": agreeing_count,
            "total_count": total_count,
            "strategy_ids": strategy_ids or [],
            "reason": reason,
        },
        "timestamp": _now_ms(),
    }
    fusion_channel = f"fusion:{user_id}:{symbol}"
    await _strategy_manager.broadcast(fusion_channel, message)
    logger.debug(
        "broadcast_fusion_signal",
        symbol=symbol,
        side=side,
        strength=strength,
        agreeing_count=agreeing_count,
        total_count=total_count,
    )


async def broadcast_trading_event(
    channel: str,
    event_type: str,
    data: dict[str, Any],
    *,
    user_id: str = "local",
    seq: int | None = None,
    session_id: str | None = None,
) -> None:
    """Push a trading event (order/fill/position/balance) to WS subscribers.

    Args:
        channel: One of 'orders', 'positions', 'balances'.
        event_type: Event type string, e.g. 'order_update', 'fill', 'balance_update'.
        data: Serialized event data.
        user_id: Scope broadcast to this user's channel only.
        seq: Monotonic per-channel sequence, supplied by the single writer
            (bridge callback) — this function never advances a counter itself.
            Omitted from the envelope when None.
        session_id: Server process identity, echoed in the envelope.
    """
    envelope: dict[str, Any] = {
        "type": event_type,
        "data": data,
        "timestamp": _now_ms(),
    }
    if seq is not None:
        envelope["seq"] = seq
    if session_id:
        envelope["session_id"] = session_id
    await _trading_manager.broadcast(
        f"trading:{user_id}:{channel}",
        envelope,
    )


# ---------------------------------------------------------------------------
# /api/v1/ws/research — Tuning progress stream
# ---------------------------------------------------------------------------


@router.websocket("/api/v1/ws/research")
async def ws_research(ws: WebSocket) -> None:
    """Research WebSocket — real-time tuning task progress.

    Client messages (JSON):
        ``{"action": "subscribe", "task_id": "tune-abc123"}``
        ``{"action": "unsubscribe", "task_id": "tune-abc123"}``

    Server pushes:
        ``{"type": "tuning_progress", "task_id": "...", "data": {...}}``
        ``{"type": "tuning_completed", "task_id": "...", "data": {...}}``
        ``{"type": "tuning_failed", "task_id": "...", "data": {...}}``
        ``{"type": "tuning_cancelled", "task_id": "...", "data": {...}}``
    """
    if not await _research_manager.connect(ws):
        return
    logger.info("ws_research_connected", connections=_research_manager.active_count)
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60.0)
            except TimeoutError:
                try:
                    await ws.send_json({"type": "ping", "timestamp": _now_ms()})
                except Exception:
                    break
                continue

            msg = await _recv_json(raw, ws)
            if msg is None:
                continue

            action = msg.get("action")
            task_id = msg.get("task_id", "")

            if action == "subscribe" and task_id:
                _research_manager.subscribe(ws, f"tuning:{task_id}")
                await ws.send_json(
                    {
                        "type": "subscribed",
                        "task_id": task_id,
                        "timestamp": _now_ms(),
                    }
                )
            elif action == "unsubscribe" and task_id:
                _research_manager.unsubscribe(ws, f"tuning:{task_id}")
                await ws.send_json(
                    {
                        "type": "unsubscribed",
                        "task_id": task_id,
                        "timestamp": _now_ms(),
                    }
                )
            else:
                await ws.send_json(
                    {"type": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    finally:
        _research_manager.disconnect(ws)
        logger.info(
            "ws_research_disconnected", connections=_research_manager.active_count
        )


# ---------------------------------------------------------------------------
# /api/v1/ws/polymarket — real-time prediction market data
# ---------------------------------------------------------------------------


def _unique_nonempty(ids: tuple[str, ...]) -> list[str]:
    """去重并丢掉空串, 保持出现顺序。"""
    seen: set[str] = set()
    unique: list[str] = []
    for cid in ids:
        if cid and cid not in seen:
            seen.add(cid)
            unique.append(cid)
    return unique


async def _broadcast_poly(event_type: str, data: dict[str, Any], *ids: str) -> None:
    """Broadcast a Polymarket event to all unique non-empty channel IDs."""
    payload = {"type": event_type, "data": data, "timestamp": _now_ms()}
    for cid in _unique_nonempty(ids):
        await _polymarket_manager.broadcast(f"poly:{cid}", payload)


async def _on_poly_book(data: dict[str, Any]) -> None:
    await _broadcast_poly(
        "book", data, data.get("asset_id", ""), data.get("market", "")
    )


async def _on_poly_price_change(data: dict[str, Any]) -> None:
    market = data.get("market", "")

    # Polymarket sends {market, price_changes: [{asset_id, price, best_bid, best_ask}, ...]}
    # Broadcast per-asset price_change so frontend can key by token_id
    changes = data.get("price_changes", data.get("tokens", data.get("changes", [])))
    if not changes:
        # Fallback: single asset_id at top level
        await _broadcast_poly("price_change", data, data.get("asset_id", ""), market)
        return
    for entry in changes:
        if isinstance(entry, dict) and entry.get("asset_id"):
            await _broadcast_poly("price_change", entry, entry["asset_id"], market)


async def _on_poly_last_trade(data: dict[str, Any]) -> None:
    await _broadcast_poly(
        "last_trade", data, data.get("asset_id", ""), data.get("market", "")
    )


async def _on_poly_disconnect() -> None:
    """Called when the Polymarket WS disconnects — invalidate cached client."""
    global _poly_ws_client
    logger.warning("polymarket_ws_disconnected, will reconnect automatically")
    _poly_ws_client = None


async def _close_quietly(client: Any, *, event: str) -> None:
    """尽力关闭客户端, 失败只记 debug。"""
    try:
        await client.close()
    except Exception:
        logger.debug(event, exc_info=True)


def _new_poly_ws_client(proxy: str | None) -> Any:
    """构造一个 Polymarket WS 客户端(事件桥接到前端)。"""
    from selene_space_exchange.exchanges.polymarket.ws_client import PolymarketWSClient

    return PolymarketWSClient(
        on_book=_on_poly_book,
        on_price_change=_on_poly_price_change,
        on_last_trade=_on_poly_last_trade,
        on_disconnect=_on_poly_disconnect,
        auto_reconnect=True,
        stall_timeout_s=90.0,
        proxy=proxy,
    )


async def _drop_stale_poly_client() -> None:
    """丢弃已断线的旧客户端引用。"""
    global _poly_ws_client
    if _poly_ws_client is None:
        return
    await _close_quietly(
        _poly_ws_client, event="polymarket_stale_ws_client_close_failed"
    )
    _poly_ws_client = None


async def _ensure_poly_ws():
    """Lazy-init a shared PolymarketWSClient that bridges events to frontends.

    The client has built-in auto-reconnect and PING keepalive per Polymarket docs.
    If the previous client is disconnected, a fresh one is created.
    """
    global _poly_ws_client
    async with _poly_ws_lock:
        if _poly_ws_client is not None and _poly_ws_client.is_connected:
            return _poly_ws_client

        await _drop_stale_poly_client()

        from selene_space_exchange.exchanges.polymarket.client import detect_local_proxy

        proxy = detect_local_proxy()
        if proxy:
            logger.info("polymarket_ws_using_proxy", proxy=proxy)

        client = _new_poly_ws_client(proxy)
        try:
            await client.connect()
            _poly_ws_client = client
            logger.info("polymarket_ws_connected", proxy=proxy or "direct")
        except Exception:
            logger.error("polymarket_ws_connect_failed", exc_info=True)
            await _close_quietly(
                client, event="polymarket_ws_client_close_after_connect_failed"
            )
        return _poly_ws_client


# ---------------------------------------------------------------------------
# REST polling fallback when WS to Polymarket is unavailable
# ---------------------------------------------------------------------------

_poll_subscribed_tokens: set[str] = set()
_poll_task: asyncio.Task[None] | None = None
_POLL_INTERVAL_S = 2.0  # poll every 2 seconds
_poll_index = 0


def _rotate_batch(tokens: list[str], start: int, size: int) -> tuple[list[str], int]:
    """从 start 处环绕取 size 个 token, 返回 (批, 新游标)。"""
    n = len(tokens)
    if start + size <= n:
        batch = tokens[start : start + size]
    else:
        batch = tokens[start:n] + tokens[0 : (start + size - n)]
    return batch, (start + size) % n


async def _poll_one_orderbook(client: Any, tid: str) -> None:
    """取单个 token 的订单簿并广播 book(附带 last_trade)。失败只记 debug。"""
    try:
        book = await client.get_orderbook(tid)
        d = book.model_dump()
        d["asset_id"] = tid
        await _broadcast_poly("book", d, tid)
        ltp = d.get("last_trade_price", "")
        if ltp:
            await _broadcast_poly("last_trade", {"asset_id": tid, "price": ltp}, tid)
    except Exception:
        logger.debug(
            "polymarket_rest_poll_orderbook_failed", asset_id=tid, exc_info=True
        )


async def _poll_orderbooks() -> None:
    """Background task: poll REST orderbooks for subscribed tokens and broadcast."""
    from selene_space_exchange.exchanges.polymarket.client import PolymarketClient

    global _poll_index
    client = PolymarketClient()
    logger.info("polymarket_rest_poll_started", interval=_POLL_INTERVAL_S)
    try:
        while True:
            await asyncio.sleep(_POLL_INTERVAL_S)

            # If WS reconnected, stop polling
            if _poly_ws_client is not None and _poly_ws_client.is_connected:
                logger.info("polymarket_ws_recovered, stopping REST poll")
                break

            tokens = list(_poll_subscribed_tokens)
            if not tokens or _polymarket_manager.active_count == 0:
                continue

            # Cycle through tokens instead of always grabbing the first N.
            batch, _poll_index = _rotate_batch(
                tokens, _poll_index % len(tokens), min(15, len(tokens))
            )
            for tid in batch:
                await _poll_one_orderbook(client, tid)

    except asyncio.CancelledError:
        pass
    finally:
        await client.close()
        logger.info("polymarket_rest_poll_stopped")


def _ensure_poll_fallback() -> None:
    """Start the REST polling task if WS is not connected."""
    global _poll_task
    if _poly_ws_client is not None and _poly_ws_client.is_connected:
        return
    if _poll_task is not None and not _poll_task.done():
        return
    _poll_task = asyncio.create_task(_poll_orderbooks(), name="polymarket-rest-poll")


@router.websocket("/api/v1/ws/polymarket")
async def ws_polymarket(ws: WebSocket) -> None:
    """Real-time Polymarket data WebSocket.

    Client messages (JSON):
        ``{"action": "subscribe", "token_ids": ["abc123..."]}``
        ``{"action": "unsubscribe", "token_ids": ["abc123..."]}``

    Server pushes:
        ``{"type": "book", "data": {...}}``
        ``{"type": "price_change", "data": {...}}``
        ``{"type": "last_trade", "data": {...}}``
    """
    if not await _polymarket_manager.connect(ws):
        return
    logger.info("ws_polymarket_connected", connections=_polymarket_manager.active_count)

    poly_ws = await _ensure_poly_ws()

    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60.0)
            except TimeoutError:
                try:
                    await ws.send_json({"type": "ping", "timestamp": _now_ms()})
                except Exception:
                    break
                continue

            msg = await _recv_json(raw, ws)
            if msg is None:
                continue

            action = msg.get("action")
            token_ids: list[str] = msg.get("token_ids", [])

            if action == "subscribe" and token_ids:
                for tid in token_ids:
                    _polymarket_manager.subscribe(ws, f"poly:{tid}")
                    _poll_subscribed_tokens.add(tid)

                if poly_ws is not None:
                    try:
                        await poly_ws.subscribe_market(token_ids)
                    except Exception:
                        logger.warning("poly_ws_subscribe_failed", exc_info=True)
                else:
                    _ensure_poll_fallback()

                await ws.send_json(
                    {
                        "type": "subscribed",
                        "token_ids": token_ids,
                        "timestamp": _now_ms(),
                    }
                )
            elif action == "unsubscribe" and token_ids:
                for tid in token_ids:
                    _polymarket_manager.unsubscribe(ws, f"poly:{tid}")
                    _poll_subscribed_tokens.discard(tid)
                await ws.send_json(
                    {
                        "type": "unsubscribed",
                        "token_ids": token_ids,
                        "timestamp": _now_ms(),
                    }
                )
            else:
                await ws.send_json(
                    {"type": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    finally:
        _polymarket_manager.disconnect(ws)
        logger.info(
            "ws_polymarket_disconnected", connections=_polymarket_manager.active_count
        )


# ---------------------------------------------------------------------------
# /api/v1/ws/intelligence — real-time news/event/pulse updates
# ---------------------------------------------------------------------------

_INTELLIGENCE_CHANNELS = {"news", "events", "pulse", "alerts"}


@router.websocket("/api/v1/ws/intelligence")
async def ws_intelligence(ws: WebSocket) -> None:
    """Real-time intelligence WebSocket — news, event signals, pulse, alerts.

    Client messages (JSON):
        ``{"action": "subscribe", "channels": ["news", "events", "pulse", "alerts"]}``
        ``{"action": "unsubscribe", "channels": ["events"]}``

    Server pushes:
        ``{"type": "news_update", "data": {...}}``
        ``{"type": "event_signal", "data": {...}}``
        ``{"type": "pulse_update", "data": {...}}``
        ``{"type": "alert", "data": {...}}``
    """
    if not await _intelligence_manager.connect(ws):
        return
    logger.info(
        "ws_intelligence_connected", connections=_intelligence_manager.active_count
    )
    try:
        while True:
            try:
                raw = await asyncio.wait_for(ws.receive_text(), timeout=60.0)
            except TimeoutError:
                try:
                    await ws.send_json({"type": "ping", "timestamp": _now_ms()})
                except Exception:
                    break
                continue

            msg = await _recv_json(raw, ws)
            if msg is None:
                continue

            action = msg.get("action")
            channels = msg.get("channels", [])

            if action == "subscribe":
                for ch in channels:
                    if ch in _INTELLIGENCE_CHANNELS:
                        _intelligence_manager.subscribe(ws, f"intel:{ch}")
                await ws.send_json(
                    {
                        "type": "subscribed",
                        "channels": [
                            ch for ch in channels if ch in _INTELLIGENCE_CHANNELS
                        ],
                        "timestamp": _now_ms(),
                    }
                )
            elif action == "unsubscribe":
                for ch in channels:
                    _intelligence_manager.unsubscribe(ws, f"intel:{ch}")
                await ws.send_json(
                    {
                        "type": "unsubscribed",
                        "channels": channels,
                        "timestamp": _now_ms(),
                    }
                )
            else:
                await ws.send_json(
                    {"type": "error", "message": f"Unknown action: {action}"}
                )

    except WebSocketDisconnect:
        pass
    finally:
        _intelligence_manager.disconnect(ws)
        logger.info(
            "ws_intelligence_disconnected",
            connections=_intelligence_manager.active_count,
        )


async def broadcast_intelligence_event(
    channel: str,
    event_type: str,
    data: dict[str, Any],
) -> None:
    """Push an intelligence event (news/event/pulse/alert) to WS subscribers.

    Args:
        channel: One of 'news', 'events', 'pulse', 'alerts'.
        event_type: Event type string, e.g. 'news_update', 'event_signal', 'pulse_update', 'alert'.
        data: Serialized event data.
    """
    if channel not in _INTELLIGENCE_CHANNELS:
        return
    await _intelligence_manager.broadcast(
        f"intel:{channel}",
        {"type": event_type, "data": data, "timestamp": _now_ms()},
    )


# ---------------------------------------------------------------------------
# Footprint / VPVR / Options broadcast helpers (from selene-master1)
# ---------------------------------------------------------------------------


async def broadcast_footprint_bar(
    symbol: str,
    data: dict[str, Any],
    *,
    exchange: str = "binance",
    market_type: str = "futures",
) -> None:
    """Push a footprint bar update to market WS subscribers."""
    channel = f"market:{exchange}:{market_type}:{symbol}"
    payload = {
        "type": "footprint_bar",
        "symbol": symbol,
        "data": data,
        "timestamp": _now_ms(),
    }
    await _market_manager.broadcast(channel, payload)
    all_channel = f"market:{exchange}:{market_type}:ALL"
    await _market_manager.broadcast(all_channel, payload)


async def broadcast_vpvr_update(
    symbol: str,
    data: dict[str, Any],
    *,
    exchange: str = "binance",
    market_type: str = "futures",
) -> None:
    """Push a VPVR profile update to market WS subscribers."""
    channel = f"market:{exchange}:{market_type}:{symbol}"
    payload = {
        "type": "vpvr_update",
        "symbol": symbol,
        "data": data,
        "timestamp": _now_ms(),
    }
    await _market_manager.broadcast(channel, payload)
    all_channel = f"market:{exchange}:{market_type}:ALL"
    await _market_manager.broadcast(all_channel, payload)


async def broadcast_options_ticker(
    data: dict[str, Any],
) -> None:
    """Push an options ticker update to market WS subscribers."""
    instrument_name = data.get("instrument_name", "")
    base_currency = instrument_name.split("-")[0] if "-" in instrument_name else "BTC"
    exchange = "deribit"
    market_type = "options"
    channel = f"market:{exchange}:{market_type}:{base_currency}"
    payload = {
        "type": "options_ticker",
        "data": data,
        "timestamp": _now_ms(),
    }
    await _market_manager.broadcast(channel, payload)
    all_channel = f"market:{exchange}:{market_type}:ALL"
    await _market_manager.broadcast(all_channel, payload)


# ---------------------------------------------------------------------------
# TG signal poller — bridge tg_events DB → intel:events WS channel
# ---------------------------------------------------------------------------

_tg_signal_poller_task: asyncio.Task | None = None
_tg_signal_last_seen_ts: int = 0


async def broadcast_tg_signal(event_row: dict[str, Any]) -> None:
    """Broadcast a single tg_events row to the intel:events WS channel."""
    await broadcast_intelligence_event(
        "events",
        "tg_signal",
        event_row,
    )


async def _tg_signal_poller_loop(db_path: str, interval_s: float = 5.0) -> None:
    """Poll tg_events for new rows since last seen, broadcast each.

    Runs as a background asyncio task. Exits cleanly on cancellation.
    Uses a monotonic last-seen-ts cursor so restarted processes re-broadcast
    the latest event once (acceptable for a low-frequency feed).

    H7: sqlite3.connect + execute are blocking I/O — wrapped in
    asyncio.to_thread to avoid blocking the event loop.
    """
    global _tg_signal_last_seen_ts
    import sqlite3 as _sqlite
    from selene_space_alpha.event_guard.tg_source import _ensure_schema

    def _init_cursor() -> int | None:
        """Sync helper: open conn, ensure schema, return max timestamp."""
        conn = _sqlite.connect(db_path)
        try:
            _ensure_schema(conn)
            row = conn.execute("SELECT MAX(timestamp) FROM tg_events").fetchone()
            if row is None or row[0] is None:
                return None
            return int(row[0])
        finally:
            conn.close()

    def _fetch_new_rows(cursor_ts: int) -> list:
        """Sync helper: fetch rows newer than cursor_ts."""
        conn = _sqlite.connect(db_path)
        try:
            _ensure_schema(conn)
            return conn.execute(
                "SELECT timestamp, event_type, alert_level, affected, half_life_h, "
                "magnitude, urgency, llm_event_type, message "
                "FROM tg_events WHERE timestamp > ? "
                "ORDER BY timestamp ASC LIMIT 50",
                (cursor_ts,),
            ).fetchall()
        finally:
            conn.close()

    if not _os.path.exists(db_path):
        logger.info("tg_signal_poller: DB not found at %s, will retry", db_path)
        return

    # Initialize cursor to the most recent existing event so we only broadcast
    # events that arrive after startup (not replay the entire history).
    # Call _ensure_schema to migrate older DBs that predate the magnitude /
    # urgency / llm_event_type columns — otherwise the SELECT below raises
    # "no such column: magnitude" on legacy macro_events.db files.
    try:
        max_ts = await asyncio.to_thread(_init_cursor)
        if max_ts:
            _tg_signal_last_seen_ts = int(max_ts)
    except Exception as e:
        logger.warning("tg_signal_poller: init cursor failed: %s", e)

    logger.info(
        "tg_signal_poller started: db=%s interval=%ss cursor=%d",
        db_path,
        interval_s,
        _tg_signal_last_seen_ts,
    )

    while True:
        try:
            await asyncio.sleep(interval_s)
            if not _os.path.exists(db_path):
                continue
            rows = await asyncio.to_thread(_fetch_new_rows, _tg_signal_last_seen_ts)

            for row in rows:
                (
                    ts_ms,
                    eg_type,
                    level,
                    affected,
                    half_life,
                    mag,
                    urg,
                    llm_type,
                    message,
                ) = row
                _tg_signal_last_seen_ts = max(_tg_signal_last_seen_ts, int(ts_ms))
                await broadcast_tg_signal(
                    {
                        "timestamp": int(ts_ms),
                        "event_type": eg_type,
                        "alert_level": level,
                        "affected": affected,
                        "half_life_h": half_life,
                        "magnitude": mag,
                        "urgency": urg,
                        "llm_event_type": llm_type,
                        "message": message,
                    }
                )
        except asyncio.CancelledError:
            logger.info("tg_signal_poller cancelled")
            break
        except Exception:
            logger.warning("tg_signal_poller iteration failed", exc_info=True)
            await asyncio.sleep(interval_s)


def start_tg_signal_poller(
    db_path: str | None = None,
    interval_s: float = 5.0,
) -> None:
    """Start the TG signal DB poller as a background task."""
    global _tg_signal_poller_task
    if _tg_signal_poller_task is not None and not _tg_signal_poller_task.done():
        return
    path = db_path or _os.environ.get("SELENE_TG_MACRO_DB", "data/macro_events.db")
    _tg_signal_poller_task = asyncio.create_task(
        _tg_signal_poller_loop(path, interval_s),
        name="tg-signal-poller",
    )


async def stop_tg_signal_poller() -> None:
    """Stop the TG signal poller. Safe to call multiple times."""
    global _tg_signal_poller_task
    if _tg_signal_poller_task is None:
        return
    _tg_signal_poller_task.cancel()
    try:
        await _tg_signal_poller_task
    except asyncio.CancelledError:
        pass
    _tg_signal_poller_task = None
