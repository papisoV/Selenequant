"""Grid trading API routes — start/stop/adjust grids, sentinel, chat control.

All grid engine instances are managed in-process via a module-level
registry.  The sentinel scheduler is created lazily on first use; its
background loops auto-start only when SELENE_GRID_SENTINEL_AUTO is set
(see _sentinel_auto_enabled) and are stopped at app shutdown (H10).

Known gap: the scheduler is not stopped when the last grid stops — the
"stopped when the last grid stops" behavior described in earlier revisions
was never implemented.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse, ErrorInfo

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/grid", tags=["grid"])


def _err(msg: str, code: str = "GRID_ERROR") -> ErrorInfo:
    return ErrorInfo(code=code, message=msg)

# ---------------------------------------------------------------------------
# In-process grid engine registry
# ---------------------------------------------------------------------------

# {exchange_key: GridExecutionEngine}
_engines: dict[str, Any] = {}
# {exchange_key: PerpetualDEXMixin adapter}
_adapters: dict[str, Any] = {}
# Sentinel scheduler (singleton)
_scheduler: Any = None


def _get_or_create_engine(key: str) -> Any:
    """Lazily create a GridExecutionEngine for the given exchange key."""
    if key in _engines:
        return _engines[key]
    from selene_space_exchange.dex.factory import create_dex_adapter
    from selene_space_paper.grid_engine import GridExecutionEngine

    adapter = create_dex_adapter(key, {})
    engine = GridExecutionEngine(adapter)
    _engines[key] = engine
    _adapters[key] = adapter
    return engine


def _ensure_scheduler(llm: Any | None = None) -> Any:
    """Return (and lazily create) the sentinel scheduler.

    Stays synchronous on purpose: callers resolve the LLM provider
    themselves (``_ensure_scheduler(llm=await _resolve_llm_provider())``),
    so a caller that forgets degrades to env-only config rather than
    silently handing a coroutine to the scheduler.
    """
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    from app.sentinel_scheduler import SentinelScheduler, SentinelConfig

    if llm is None:
        # Lazily create LLM provider from env vars (H17 path).
        llm = _create_llm_provider()
    if llm is None:
        logger.warning(
            "grid sentinel has no LLM provider; AI tools will fail until "
            "LLM settings (keyring or SELENE_LLM_* env) are configured"
        )
    config = SentinelConfig()
    _scheduler = SentinelScheduler(_engines, llm, config)
    return _scheduler


def _provider_has_key(provider: Any) -> bool:
    """True when the provider carries a non-empty API key.

    ``OpenAICompatProvider`` stores its config as ``self.config`` (base.py);
    check falsy so both ``None`` and ``""`` count as "no key".
    """
    cfg = getattr(provider, "config", None)
    return bool(getattr(cfg, "api_key", None))


async def _resolve_llm_provider() -> Any:
    """Resolve the sentinel LLM provider: env vars first, keyring fallback.

    The H17 env-var path (``_create_llm_provider``) wins when it yields a
    usable key; otherwise fall back to the keyring-backed settings chain
    so grid sentinel follows the same LLM config as the rest of the app.
    """
    provider = _create_llm_provider()
    if provider is not None and _provider_has_key(provider):
        return provider
    from app.core.dependencies import get_settings_service
    from app.core.llm_helper import build_provider

    return await build_provider(get_settings_service())


def _sentinel_auto_enabled() -> bool:
    """Whether the sentinel scheduler should auto-start its background loops.

    Default OFF: enabling it starts periodic LLM calls (and grid push
    notifications), so it must be opt-in via SELENE_GRID_SENTINEL_AUTO=1.
    Manual triggers (/sentinel/run, /chat) are unaffected.
    """
    import os

    return os.environ.get("SELENE_GRID_SENTINEL_AUTO", "").lower() in ("1", "true", "yes")


def _create_llm_provider() -> Any:
    """Create an LLM provider from application settings."""
    try:
        from selene_space_llm.openai_compat import OpenAICompatProvider
        from selene_space_llm.base import LLMConfig
        import os

        # H17: use project-namespaced SELENE_LLM_* env vars (not generic AI_*).
        # Falls back to AI_* for backward compat (logged once if used).
        api_key = os.getenv("SELENE_LLM_API_KEY", "") or os.getenv("AI_API_KEY", "")
        base_url = (
            os.getenv("SELENE_LLM_BASE_URL", "")
            or os.getenv("AI_BASE_URL", "")
            or "https://api.openai.com/v1"
        )
        # No fabricated model: a key with no model named used to resolve to a
        # pinned cloud model, which paired against whichever base_url was set
        # and made every call fail with a mismatch the user could not see. The
        # same "let the endpoint decide" convention already lives in
        # ``llm_helper.build_provider``; grid now agrees with it.
        model = (
            os.getenv("SELENE_LLM_MODEL", "") or os.getenv("AI_MODEL", "") or "default"
        )

        if not os.getenv("SELENE_LLM_API_KEY") and os.getenv("AI_API_KEY"):
            logger.warning(
                "AI_API_KEY env var is deprecated; rename to SELENE_LLM_API_KEY (H17)."
            )

        return OpenAICompatProvider(LLMConfig(
            model=model,
            api_key=api_key or None,
            base_url=base_url,
        ))
    except Exception:
        logger.warning("LLM provider creation failed; sentinel tools will not work")
        return None


# ---------------------------------------------------------------------------
# Grid control endpoints
# ---------------------------------------------------------------------------


@router.post("/start")
async def start_grid(
    request: Request,
    exchange: str = Query(..., description="Exchange key: de, ex, rs"),
    market_id: int = Query(...),
    mode: str = Query("neutral", description="neutral | long | short"),
    lower: float = Query(...),
    upper: float = Query(...),
    grid_count: int = Query(...),
    size_base: float = Query(...),
    leverage: int = Query(10),
    out_of_range_action: str = Query("close"),
) -> APIResponse[dict[str, Any]]:
    """Start a grid on the specified exchange."""
    if exchange not in ("de", "ex", "rs"):
        return APIResponse(data=None, error=_err(f"Unknown exchange: {exchange}"), meta=build_response_meta(request))

    engine = _get_or_create_engine(exchange)
    cfg = {
        "marketId": market_id,
        "mode": mode,
        "lower": lower,
        "upper": upper,
        "gridCount": grid_count,
        "sizeBase": size_base,
        "leverage": leverage,
        "outOfRangeAction": out_of_range_action,
    }
    try:
        state = await engine.start_grid(cfg)
        # Ensure the scheduler exists; only auto-start its background loops
        # when explicitly opted in (default off keeps periodic LLM calls off).
        sched = _ensure_scheduler(llm=await _resolve_llm_provider())
        if _sentinel_auto_enabled():
            if not sched._running:
                await sched.start()
        else:
            logger.info(
                "grid sentinel auto-patrol disabled; set SELENE_GRID_SENTINEL_AUTO=1 "
                "to enable periodic AI patrol and grid notifications"
            )
        return APIResponse(data=state.model_dump(), error=None, meta=build_response_meta(request))
    except Exception as exc:
        return APIResponse(data=None, error=_err(exc), meta=build_response_meta(request))


@router.post("/stop")
async def stop_grid(
    request: Request,
    exchange: str = Query(...),
    close_position: bool = Query(True),
) -> APIResponse[dict[str, Any]]:
    """Stop a running grid."""
    engine = _engines.get(exchange)
    if not engine:
        return APIResponse(data=None, error=_err(f"No grid engine for {exchange}"), meta=build_response_meta(request))
    try:
        state = await engine.stop_grid(close_position=close_position)
        return APIResponse(data=state.model_dump(), error=None, meta=build_response_meta(request))
    except Exception as exc:
        return APIResponse(data=None, error=_err(exc), meta=build_response_meta(request))


@router.post("/cancel-orders")
async def cancel_orders(
    request: Request,
    exchange: str = Query(...),
) -> APIResponse[dict[str, Any]]:
    """Cancel all orders without closing position."""
    engine = _engines.get(exchange)
    if not engine:
        return APIResponse(data=None, error=_err(f"No grid engine for {exchange}"), meta=build_response_meta(request))
    try:
        state = await engine.cancel_all_orders()
        return APIResponse(data=state.model_dump(), error=None, meta=build_response_meta(request))
    except Exception as exc:
        return APIResponse(data=None, error=_err(exc), meta=build_response_meta(request))


@router.post("/adjust-range")
async def adjust_range(
    request: Request,
    exchange: str = Query(...),
    lower: float = Query(...),
    upper: float = Query(...),
) -> APIResponse[dict[str, Any]]:
    """Adjust the grid price range."""
    engine = _engines.get(exchange)
    if not engine:
        return APIResponse(data=None, error=_err(f"No grid engine for {exchange}"), meta=build_response_meta(request))
    try:
        from selene_space_paper.grid_types import RangeAdjustment
        state = await engine.adjust_range(RangeAdjustment(lower=lower, upper=upper))
        return APIResponse(data=state.model_dump(), error=None, meta=build_response_meta(request))
    except Exception as exc:
        return APIResponse(data=None, error=_err(exc), meta=build_response_meta(request))


@router.post("/recovery")
async def start_recovery(
    request: Request,
    exchange: str = Query(...),
    above_entry_only: bool = Query(False),
) -> APIResponse[dict[str, Any]]:
    """Start a reduce-only recovery ladder."""
    engine = _engines.get(exchange)
    if not engine:
        return APIResponse(data=None, error=_err(f"No grid engine for {exchange}"), meta=build_response_meta(request))
    try:
        cfg = {"aboveEntryOnly": above_entry_only}
        state = await engine.start_recovery(cfg)
        return APIResponse(data=state.model_dump(), error=None, meta=build_response_meta(request))
    except Exception as exc:
        return APIResponse(data=None, error=_err(exc), meta=build_response_meta(request))


@router.post("/close-position")
async def close_position(
    request: Request,
    exchange: str = Query(...),
) -> APIResponse[dict[str, Any]]:
    """Cancel all orders and market-close the position."""
    engine = _engines.get(exchange)
    if not engine:
        return APIResponse(data=None, error=_err(f"No grid engine for {exchange}"), meta=build_response_meta(request))
    try:
        state = await engine.close_position_now()
        return APIResponse(data=state.model_dump(), error=None, meta=build_response_meta(request))
    except Exception as exc:
        return APIResponse(data=None, error=_err(exc), meta=build_response_meta(request))


@router.get("/state")
async def get_state(
    request: Request,
    exchange: str | None = Query(None),
) -> APIResponse[dict[str, Any]]:
    """Get grid state for one or all exchanges."""
    if exchange:
        engine = _engines.get(exchange)
        if not engine:
            return APIResponse(data=None, error=_err(f"No grid engine for {exchange}"), meta=build_response_meta(request))
        state = engine.get_state()
        return APIResponse(data=state.model_dump(), error=None, meta=build_response_meta(request))

    all_states = {}
    for key in ("de", "ex", "rs"):
        eng = _engines.get(key)
        all_states[key] = eng.get_state().model_dump() if eng else {"running": False}
    return APIResponse(data=all_states, error=None, meta=build_response_meta(request))


@router.post("/reset-stats")
async def reset_stats(
    request: Request,
    exchange: str = Query(...),
) -> APIResponse[dict[str, Any]]:
    """Reset cumulative stats for a grid engine."""
    engine = _engines.get(exchange)
    if not engine:
        return APIResponse(data=None, error=_err(f"No grid engine for {exchange}"), meta=build_response_meta(request))
    state = engine.reset_stats()
    return APIResponse(data=state.model_dump(), error=None, meta=build_response_meta(request))


# ---------------------------------------------------------------------------
# AI sentinel / chat endpoints
# ---------------------------------------------------------------------------


@router.get("/sentinel")
async def get_sentinel_status(
    request: Request,
) -> APIResponse[dict[str, Any]]:
    """Get sentinel scheduler status."""
    sched = _scheduler
    if sched is None:
        return APIResponse(data={"running": False}, error=None, meta=build_response_meta(request))
    return APIResponse(data=sched.status(), error=None, meta=build_response_meta(request))


@router.post("/sentinel/run")
async def run_sentinel(
    request: Request,
    tool: str = Query("sentinel", description="sentinel | market | report"),
    exchange: str = Query("ex", description="Exchange key for market analysis"),
) -> APIResponse[dict[str, Any]]:
    """Manually trigger a sentinel tool run."""
    sched = _ensure_scheduler(llm=await _resolve_llm_provider())
    if sched is None:
        return APIResponse(data=None, error=_err("Sentinel not available"), meta=build_response_meta(request))

    try:
        if tool == "sentinel":
            result = await sched.sentinel_tool.async_execute({})
        elif tool == "market":
            result = await sched.market_tool.async_execute({"exchange": exchange})
        elif tool == "report":
            result = await sched.report_tool.async_execute({})
        else:
            return APIResponse(data=None, error=_err(f"Unknown tool: {tool}"), meta=build_response_meta(request))

        output = result.output if result else ""
        error = result.error if result else None
        return APIResponse(data={"output": output}, error=error, meta=build_response_meta(request))
    except Exception as exc:
        return APIResponse(data=None, error=_err(exc), meta=build_response_meta(request))


@router.post("/chat")
async def grid_chat(
    request: Request,
    body: dict[str, Any] = None,
) -> APIResponse[dict[str, Any]]:
    """Chat with the grid AI control assistant."""
    if body is None:
        body = {}
    message = body.get("message", "")
    history = body.get("history", [])
    if not message:
        return APIResponse(data=None, error=_err("Missing 'message'"), meta=build_response_meta(request))

    sched = _ensure_scheduler(llm=await _resolve_llm_provider())
    if sched is None:
        return APIResponse(data=None, error=_err("Sentinel not available"), meta=build_response_meta(request))

    try:
        result = await sched.chat_tool.async_execute({
            "message": message,
            "history": history,
        })
        output = result.output if result else ""
        error = result.error if result else None
        return APIResponse(data={"output": output}, error=error, meta=build_response_meta(request))
    except Exception as exc:
        return APIResponse(data=None, error=_err(exc), meta=build_response_meta(request))


@router.get("/markets/{exchange}")
async def get_markets(
    request: Request,
    exchange: str,
) -> APIResponse[dict[str, Any]]:
    """Get available markets for a DEX exchange."""
    adapter = _adapters.get(exchange)
    if not adapter:
        try:
            from selene_space_exchange.dex.factory import create_dex_adapter
            adapter = create_dex_adapter(exchange, {})
            _adapters[exchange] = adapter
        except Exception as exc:
            return APIResponse(data=None, error=_err(exc), meta=build_response_meta(request))

    try:
        markets = await adapter.get_markets()
        return APIResponse(
            data={"markets": [m.model_dump() if hasattr(m, "model_dump") else str(m) for m in markets]},
            error=None,
            meta=build_response_meta(request),
        )
    except Exception as exc:
        return APIResponse(data=None, error=_err(exc), meta=build_response_meta(request))
