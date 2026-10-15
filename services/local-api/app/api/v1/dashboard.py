"""Dashboard aggregator — /api/v1/dashboard.

Single endpoint that fan-outs to markets + paper + trading + signals with
800ms per-sub-call timeout and a 5s overall cache. Returns a unified
DashboardSnapshot so the dashboard page can replace 6 network calls with 1.

Phase 4 deliverable per the integration plan.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from fastapi import APIRouter, Request

from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse, Timestamp
from selene_space_types.phase4 import (
    DashboardMarketSummary,
    DashboardPaperSummary,
    DashboardSignalsSummary,
    DashboardSnapshot,
    DashboardTradingSummary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

# Per-sub-call timeout (seconds). Slow calls are dropped and recorded.
_SUBCALL_TIMEOUT = 0.8
# Overall cache TTL (seconds).
_CACHE_TTL = 5.0

_cache: dict[str, tuple[float, DashboardSnapshot]] = {}
_CACHE_KEY = "default"


def _now_ms() -> Timestamp:
    return int(time.time() * 1000)


# ---------------------------------------------------------------------------
# Sub-call implementations (fan-out targets)
# ---------------------------------------------------------------------------


async def _fetch_market_summary() -> list[DashboardMarketSummary]:
    """Fetch top markets snapshot via MarketDataService.get_ticker.

    Uses settings-backed (exchange, market_type) resolution to match the
    markets endpoint. Returns empty list on failure.
    """
    try:
        from app.core.dependencies import get_market_service, get_settings_service
        from app.api.v1.markets import _resolve_source

        svc = get_market_service()
        if svc is None:
            return []

        settings_service = get_settings_service()
        if settings_service is None:
            return []
        exchange, market_type = _resolve_source(None, None, settings_service)

        symbols = svc.get_symbols(exchange, market_type)
        if not symbols:
            return []

        summaries: list[DashboardMarketSummary] = []
        for sym in symbols[:8]:
            ticker = svc.get_ticker(sym, exchange, market_type)
            if ticker is None:
                # Still emit a placeholder row so the UI knows the symbol is
                # subscribed but the first ticker hasn't arrived yet.
                summaries.append(
                    DashboardMarketSummary(
                        symbol=sym,
                        last_price=None,
                        change_24h_pct=None,
                        volume_24h=None,
                    )
                )
                continue
            summaries.append(
                DashboardMarketSummary(
                    symbol=ticker.symbol,
                    last_price=float(ticker.last_price),
                    change_24h_pct=float(getattr(ticker, "change_24h_pct", 0.0) or 0.0),
                    volume_24h=float(getattr(ticker, "volume_24h", 0.0) or 0.0),
                )
            )
        return summaries
    except Exception as exc:
        logger.debug("market summary fetch failed: %s", exc, exc_info=True)
        return []


async def _fetch_paper_summary() -> DashboardPaperSummary | None:
    """Fetch paper account summary."""
    try:
        from app.api.v1.paper import _accounts_lock, _paper_accounts  # type: ignore

        with _accounts_lock:
            if not _paper_accounts:
                return None
            account_id, acct = next(iter(_paper_accounts.items()))
            return DashboardPaperSummary(
                account_id=account_id,
                equity=float(getattr(acct, "equity", 0.0) or 0.0),
                unrealized_pnl=float(getattr(acct, "unrealized_pnl", 0.0) or 0.0),
                open_positions=len(getattr(acct, "positions", {}) or {}),
            )
    except Exception as exc:
        logger.debug("paper summary fetch failed: %s", exc, exc_info=True)
        return None


async def _fetch_trading_summary() -> DashboardTradingSummary | None:
    """Fetch trading mode + open orders/positions summary."""
    try:
        from app.core.dependencies import get_execution_mode, get_live_engine, get_shadow_engine

        mode = get_execution_mode()
        open_orders = 0
        open_positions = 0
        kill_switch = False

        for engine_getter in (get_live_engine, get_shadow_engine):
            engine = engine_getter()
            if engine is None:
                continue
            orders = getattr(engine, "open_orders", None)
            if orders is not None:
                try:
                    open_orders = sum(1 for _ in orders) if orders else 0
                except TypeError:
                    open_orders = 0
            positions = getattr(engine, "positions", None)
            if positions is not None:
                try:
                    open_positions = sum(1 for _ in positions) if positions else 0
                except TypeError:
                    open_positions = 0
            kill_switch = bool(getattr(engine, "kill_switch_engaged", False))
            break

        return DashboardTradingSummary(
            mode=str(mode),
            open_orders=open_orders,
            open_positions=open_positions,
            kill_switch=kill_switch,
        )
    except Exception as exc:
        logger.debug("trading summary fetch failed: %s", exc, exc_info=True)
        return None


async def _fetch_signals_summary() -> DashboardSignalsSummary | None:
    """Fetch active signal counts by severity."""
    try:
        from app.api.v1.signals import _signals_lock, _signals_store  # type: ignore

        total = 0
        high = 0
        medium = 0
        low = 0
        with _signals_lock:
            for sig in list(_signals_store.values()):
                total += 1
                sev = (getattr(sig, "severity", "low") or "low").lower()
                if sev == "high":
                    high += 1
                elif sev == "medium":
                    medium += 1
                else:
                    low += 1
        return DashboardSignalsSummary(
            total=total,
            high_severity=high,
            medium_severity=medium,
            low_severity=low,
        )
    except Exception as exc:
        logger.debug("signals summary fetch failed: %s", exc, exc_info=True)
        return None


# ---------------------------------------------------------------------------
# Fan-out with 800ms per-sub-call timeout
# ---------------------------------------------------------------------------


async def _gather_with_timeout(
    label: str,
    coro: Any,
) -> tuple[str, Any, bool]:
    """Run a sub-call with the 800ms budget. Returns (label, result, was_slow)."""
    try:
        result = await asyncio.wait_for(coro, timeout=_SUBCALL_TIMEOUT)
        return label, result, False
    except asyncio.TimeoutError:
        logger.warning("dashboard sub-call %s timed out", label)
        return label, None, True
    except Exception as exc:
        logger.warning("dashboard sub-call %s failed: %s", label, exc, exc_info=True)
        return label, None, True


async def _build_snapshot() -> DashboardSnapshot:
    """Fan-out to all 4 sub-calls concurrently, with 800ms per call."""
    slow: list[str] = []

    results = await asyncio.gather(
        _gather_with_timeout("markets", _fetch_market_summary()),
        _gather_with_timeout("paper", _fetch_paper_summary()),
        _gather_with_timeout("trading", _fetch_trading_summary()),
        _gather_with_timeout("signals", _fetch_signals_summary()),
    )

    markets: list[DashboardMarketSummary] = []
    paper: DashboardPaperSummary | None = None
    trading: DashboardTradingSummary | None = None
    signals: DashboardSignalsSummary | None = None

    for label, result, was_slow in results:
        if was_slow:
            slow.append(label)
            continue
        if label == "markets" and isinstance(result, list):
            markets = result
        elif label == "paper" and isinstance(result, DashboardPaperSummary):
            paper = result
        elif label == "trading" and isinstance(result, DashboardTradingSummary):
            trading = result
        elif label == "signals" and isinstance(result, DashboardSignalsSummary):
            signals = result

    return DashboardSnapshot(
        ts=_now_ms(),
        markets=markets,
        paper=paper,
        trading=trading,
        signals=signals,
        slow_subcalls=slow,
    )


def _read_cache() -> DashboardSnapshot | None:
    if _CACHE_KEY in _cache:
        ts, snap = _cache[_CACHE_KEY]
        if time.time() - ts < _CACHE_TTL:
            return snap
    return None


def _write_cache(snap: DashboardSnapshot) -> None:
    _cache[_CACHE_KEY] = (time.time(), snap)


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------


@router.get("", response_model=None)
async def get_dashboard_snapshot(
    request: Request,
    force: bool = False,
) -> APIResponse[DashboardSnapshot]:
    """Aggregate snapshot for the dashboard page.

    Fan-outs to markets/paper/trading/signals with 800ms per-sub-call
    timeout. Slow sub-calls are dropped and listed in `slow_subcalls`.
    Cached for 5 seconds unless `force=true`.
    """
    if not force:
        cached = _read_cache()
        if cached is not None:
            return APIResponse(data=cached, meta=build_response_meta(request), error=None)

    snap = await _build_snapshot()
    _write_cache(snap)
    return APIResponse(data=snap, meta=build_response_meta(request), error=None)
