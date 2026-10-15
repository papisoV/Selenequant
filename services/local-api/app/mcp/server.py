"""Outbound MCP channel — read-only + low-risk tools for external AI clients.

Each tool is a plain async function returning a JSON-serializable dict, so
callers (FastAPI router, tests, future MCP SDK mounts) can invoke them without
HTTP ceremony.  Every tool:

- declares ``risk_level`` / ``read_only`` metadata (registered, not inferred)
- clamps parameters (limit/interval whitelists)
- redacts secrets from the payload before returning

Auth is enforced at the HTTP layer via ``_require_token``.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.v1 import backtests as bt
from app.core.dependencies import AuthenticatedUser
from selene_space_agent.redact import redact_secrets
from selene_space_types.common import APIResponse, ResponseMeta

logger = logging.getLogger(__name__)

# Valid intervals are clamped to a whitelist; unknown values are rejected
# rather than passed to the exchange (fail closed).
_VALID_INTERVALS = ("1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w")
_MAX_BACKTEST_CAPITAL = 100_000_000.0
_MAX_TUNING_LIMIT = 100
_MAX_KLINE_CANDLES = 1000


# ---------------------------------------------------------------------------
# Tool metadata (registered declarations, per spec)
# ---------------------------------------------------------------------------


class ToolMeta:
    """Declared safety metadata for an MCP tool."""

    __slots__ = ("risk_level", "read_only", "requires_auth")

    def __init__(self, risk_level: str, read_only: bool, requires_auth: bool) -> None:
        self.risk_level = risk_level
        self.read_only = read_only
        self.requires_auth = requires_auth


RUN_BACKTEST_META = ToolMeta(risk_level="medium", read_only=False, requires_auth=True)
GET_TUNING_META = ToolMeta(risk_level="low", read_only=True, requires_auth=False)
ANALYZE_KLINES_META = ToolMeta(risk_level="low", read_only=True, requires_auth=False)


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

_SECRET_KEY_ENV = "SELENE_MCP_TOKEN"
_TENANT = "selene"


async def _require_token(request: Request) -> AuthenticatedUser:
    """Reject unauthenticated / wrong-tenant MCP calls.

    The MCP outbound channel is explicitly token-gated (external AI clients),
    so unlike the local-first API it never trusts the socket address.  Every
    call must present ``Authorization: Bearer <SELENE_MCP_TOKEN>``:
    - missing/malformed token → 401
    - token present but not matching → 401
    - ``X-Tenant`` header set to anything other than the channel tenant → 403
    """
    creds = request.headers.get("Authorization", "")
    if not creds.startswith("Bearer "):
        raise HTTPException(401, "Missing bearer token")
    supplied = creds[len("Bearer ") :].strip()
    expected = _mcp_token()
    if not expected:
        raise HTTPException(503, "MCP token not configured (SELENE_MCP_TOKEN)")
    if not supplied or supplied != expected:
        raise HTTPException(401, "Invalid bearer token")
    tenant = request.headers.get("X-Tenant", "")
    if tenant and tenant != _TENANT:
        raise HTTPException(403, "Tenant not authorized for MCP channel")
    return AuthenticatedUser(id="mcp", role="user")


def _mcp_token() -> str:
    """Return the configured MCP bearer token (empty when unset)."""
    import os

    return os.environ.get(_SECRET_KEY_ENV, "")


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


def _clamp_interval(interval: str | None, default: str = "1h") -> str:
    if interval is None:
        return default
    lowered = interval.lower()
    if lowered not in _VALID_INTERVALS:
        raise HTTPException(
            400, f"Invalid interval '{interval}' (valid: {', '.join(_VALID_INTERVALS)})"
        )
    return lowered


def _clamp_capital(capital: float | None, default: float = 10_000.0) -> float:
    if capital is None:
        return default
    if capital <= 0:
        raise HTTPException(400, "initial_capital must be > 0")
    return min(capital, _MAX_BACKTEST_CAPITAL)


async def run_backtest(
    strategy_id: str,
    symbol: str | None = None,
    interval: str | None = None,
    initial_capital: float | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Low-risk: start a backtest (reuses /backtests task creation).

    No order is placed; this only spawns a background analysis task and returns
    its ``task_id``.  Execution can be mocked in tests by monkeypatching the
    ``_spawn_backtest`` hook below.
    """
    if not strategy_id:
        raise HTTPException(400, "strategy_id is required")
    interval_ok = _clamp_interval(interval)
    capital_ok = _clamp_capital(initial_capital)

    body = bt.RunBacktestRequest(
        strategy_id=strategy_id,
        symbol=symbol,
        interval=interval_ok,
        initial_capital=capital_ok,
        parameters=parameters or {},
    )
    task_id = await _spawn_backtest(body, user_id="local")
    return cast(
        dict[str, Any],
        redact_secrets(
            {
                "task_id": task_id,
                "status": "pending",
                "strategy_id": strategy_id,
                "interval": interval_ok,
                "risk_level": RUN_BACKTEST_META.risk_level,
                "read_only": RUN_BACKTEST_META.read_only,
            }
        ),
    )


async def _spawn_backtest(body: bt.RunBacktestRequest, user_id: str) -> str:
    """Create a backtest task in the shared in-memory store.

    Mirrors ``backtests.start_backtest``'s task creation but returns the id
    directly.  Tests can monkeypatch this to avoid touching the engine.
    """
    import asyncio
    import uuid

    task_id = f"bt-{uuid.uuid4().hex[:8]}"
    task = bt.BacktestTask(
        task_id=task_id,
        strategy_id=body.strategy_id,
        user_id=user_id,
        symbol=body.symbol or "",
        interval=body.interval or "",
    )
    bt._tasks[task_id] = task
    bt._evict_oldest_tasks()
    asyncio.create_task(bt._run_backtest(task, body))
    return task_id


async def get_tuning_results(limit: int = 20) -> list[dict[str, Any]]:
    """Read-only: list tuning results from data/tuning_results/*.json."""
    limit = max(0, min(limit, _MAX_TUNING_LIMIT))
    try:
        from app.api.v1.research import list_tuning_results

        resp = await list_tuning_results(
            request=cast(Request, _FakeRequest()),
            user=None,  # type: ignore[arg-type]
        )
        data = resp.data or []
        # Dump to plain dicts so redact_secrets can traverse nested payloads.
        dumped = [
            item.model_dump(mode="json") if hasattr(item, "model_dump") else item
            for item in data[:limit]
        ]
        return cast(list[dict[str, Any]], redact_secrets(dumped))
    except Exception as exc:  # noqa: BLE001 — surface as tool-level failure
        logger.warning("get_tuning_results failed: %s", exc)
        return cast(
            list[dict[str, Any]],
            redact_secrets([{"error": f"tuning lookup failed: {exc}"}]),
        )


async def analyze_klines(symbol: str, timeframe: str = "1h") -> dict[str, Any]:
    """Read-only: fetch recent K-lines and run price-action structure analysis.

    This does NOT place orders.  It pulls the last ``_MAX_KLINE_CANDLES``
    candles and returns a compact summary (last close, high/low, bar count,
    direction hint) suitable for external clients.
    """
    if not symbol:
        raise HTTPException(400, "symbol is required")
    timeframe = _clamp_interval(timeframe, default="1h")

    try:
        from app.core.dependencies import get_kline_manager

        km = get_kline_manager()
        if km is None:
            return cast(
                dict[str, Any],
                redact_secrets(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "error": "kline manager not available",
                        "risk_level": ANALYZE_KLINES_META.risk_level,
                    }
                ),
            )
        df = await km.get_klines(
            exchange="binance",
            market_type="futures",
            symbol=symbol,
            interval=timeframe,
            source="auto",
            min_candles=1,
        )
        if df is None or len(df) == 0:
            return cast(
                dict[str, Any],
                redact_secrets(
                    {
                        "symbol": symbol,
                        "timeframe": timeframe,
                        "bars": 0,
                        "risk_level": ANALYZE_KLINES_META.risk_level,
                    }
                ),
            )
        tail = df.tail(_MAX_KLINE_CANDLES)
        last = tail.iloc[-1]
        direction = _trend_hint(tail["close"].tolist() if "close" in tail else [])
        return cast(
            dict[str, Any],
            redact_secrets(
                {
                    "symbol": symbol,
                    "timeframe": timeframe,
                    "bars": int(len(tail)),
                    "last_close": float(last.get("close", 0.0)),
                    "high": float(tail["high"].max()) if "high" in tail else None,
                    "low": float(tail["low"].min()) if "low" in tail else None,
                    "direction": direction,
                    "risk_level": ANALYZE_KLINES_META.risk_level,
                    "read_only": ANALYZE_KLINES_META.read_only,
                }
            ),
        )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        logger.warning("analyze_klines failed: %s", exc)
        return cast(
            dict[str, Any],
            redact_secrets(
                {"symbol": symbol, "timeframe": timeframe, "error": str(exc)}
            ),
        )


def _trend_hint(closes: list[float]) -> str:
    """Crude direction hint from the last 20 closes (first vs last)."""
    if len(closes) < 2:
        return "flat"
    window = closes[-20:]
    if window[-1] > window[0]:
        return "up"
    if window[-1] < window[0]:
        return "down"
    return "flat"


class _FakeRequest:
    """Minimal Request stand-in for reusing research's list_tuning_results."""

    @property
    def state(self) -> Any:
        return _FakeState()


class _FakeState:
    request_id: str | None = None


# ---------------------------------------------------------------------------
# FastAPI router — auth-gated MCP channel endpoints
# ---------------------------------------------------------------------------

router = APIRouter(prefix="/mcp/tools", tags=["mcp-tools"])


class RunBacktestParams(BaseModel):
    strategy_id: str = Field(..., description="Strategy or template id")
    symbol: str | None = Field(None, description="Trading pair, e.g. BTC/USDT")
    interval: str | None = Field(None, description="Kline interval (1m..1w)")
    initial_capital: float | None = Field(None, gt=0)
    parameters: dict[str, Any] = Field(default_factory=dict)


@router.post("/run_backtest")
async def http_run_backtest(
    body: RunBacktestParams,
    request: Request,
    user: Any = Depends(_require_token),
) -> APIResponse[dict[str, Any]]:
    """Start a backtest via the MCP channel (auth-gated)."""
    payload = await run_backtest(
        strategy_id=body.strategy_id,
        symbol=body.symbol,
        interval=body.interval,
        initial_capital=body.initial_capital,
        parameters=body.parameters,
    )
    return APIResponse(
        data=payload,
        meta=_meta(request),
        error=None,
    )


@router.get("/get_tuning_results")
async def http_get_tuning_results(
    request: Request,
    limit: int = 20,
    user: Any = Depends(_require_token),
) -> APIResponse[list[dict[str, Any]]]:
    """List tuning history (auth-gated, read-only)."""
    limit = max(0, min(limit, _MAX_TUNING_LIMIT))
    data = await get_tuning_results(limit=limit)
    return APIResponse(data=data, meta=_meta(request), error=None)


class AnalyzeParams(BaseModel):
    symbol: str = Field(..., description="Trading pair, e.g. BTC/USDT")
    timeframe: str = Field("1h", description="Kline interval")


@router.post("/analyze_klines")
async def http_analyze_klines(
    body: AnalyzeParams,
    request: Request,
    user: Any = Depends(_require_token),
) -> APIResponse[dict[str, Any]]:
    """Analyze recent K-lines (auth-gated, read-only, no orders)."""
    payload = await analyze_klines(symbol=body.symbol, timeframe=body.timeframe)
    return APIResponse(data=payload, meta=_meta(request), error=None)


def _meta(request: Request) -> ResponseMeta:
    return ResponseMeta(request_id=getattr(request.state, "request_id", None))
