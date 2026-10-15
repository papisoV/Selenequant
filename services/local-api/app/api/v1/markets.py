"""Market data endpoints.

Provides ticker, kline, and L2 orderbook data for subscribed symbols
via the ``MarketDataService`` from ``selene_space_market``.

Supports multiple exchange sources (Binance spot/futures, OKX spot/futures)
selected via query parameters.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from typing import Any, cast

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse

from app.core.dependencies import (
    build_response_meta,
    get_market_service,
    get_settings_service,
)
from app.core.settings_service import SettingsService
from selene_space_core.kline_paths import default_klines_db_path
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, NotFoundError, SeleneSpaceError
from selene_space_types.market import KlineEvent

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/markets", tags=["markets"])


def _ok(request: Request, data: dict[str, Any]) -> APIResponse[dict[str, Any]]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


_ALLOWED_EXCHANGES = {"binance", "okx"}
_ALLOWED_MARKET_TYPES = {"spot", "futures"}

# Throttle derivatives writes: only persist once per symbol per 8h window
_deriv_write_cache: dict[str, int] = {}
_DERIV_WRITE_INTERVAL_MS = 8 * 3600 * 1000  # 8 hours

# In-flight SQLite gap-repair tasks, keyed by partition, so concurrent kline
# requests for the same symbol/interval don't each spawn their own backfill.
_repair_tasks: dict[str, asyncio.Task[Any]] = {}
# Cooldown: same partition won't re-trigger repair within this window.
_repair_last_trigger: dict[str, float] = {}
_REPAIR_COOLDOWN_S = 300  # 5 min


# K 线周期 → 毫秒。多处"新鲜度/窗口"计算共用，避免重复字面量。
_INTERVAL_MS: dict[str, int] = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
    "4h": 14_400_000,
    "1d": 86_400_000,
    "1w": 604_800_000,
}


def _launch_gap_repair(km: Any, ex: str, mt: str, sym: str, interval: str) -> None:
    """Fire-and-forget a background repair_gaps for a SQLite partition.

    One task per partition: if a repair is already running for this key, don't
    start another. Cooldown prevents every kline request from re-triggering a
    scan once the partition is healthy. The endpoint keeps serving local data;
    once the task fills the hole, subsequent requests return a continuous series.
    """
    import time as _t

    key = f"{ex}:{mt}:{sym}:{interval}"
    now = _t.time()
    if key in _repair_tasks and not _repair_tasks[key].done():
        return
    if now - _repair_last_trigger.get(key, 0) < _REPAIR_COOLDOWN_S:
        return
    _repair_last_trigger[key] = now
    task = asyncio.create_task(km.repair_gaps(ex, mt, sym, interval, window_days=10))
    _repair_tasks[key] = task
    task.add_done_callback(lambda t: _repair_tasks.pop(key, None))


def _persist_derivatives_snapshot(
    deriv_db: Any, symbol: str, exchange: str, interval: str
) -> None:
    """Persist current derivatives snapshot (funding rate) to derivatives.db.

    Throttled: only writes once per symbol per 8h to avoid redundant DB writes
    on every kline request (funding rate only changes every 8h anyway).
    """
    import time

    now_ms = int(time.time() * 1000)
    cache_key = f"{exchange}:{symbol}"
    last_write = _deriv_write_cache.get(cache_key, 0)
    if now_ms - last_write < _DERIV_WRITE_INTERVAL_MS:
        return

    # Try to get current funding rate from the service's store
    from app.core.dependencies import get_market_service

    svc = get_market_service()
    if svc is None:
        return

    fr_store = getattr(svc, "_funding_rate_store", None)
    if fr_store is None:
        return

    # Find funding rate for this symbol
    fr_event = None
    for key, event in fr_store.items():
        if symbol in key or key.endswith(symbol):
            fr_event = event
            break

    if fr_event is None:
        return

    # Write to derivatives.db
    try:
        row = (
            exchange,
            symbol,
            int(fr_event.funding_time)
            if hasattr(fr_event, "funding_time") and fr_event.funding_time
            else now_ms,
            float(fr_event.funding_rate),
            float(getattr(fr_event, "mark_price", 0) or 0),
            float(getattr(fr_event, "index_price", 0) or 0),
            int(getattr(fr_event, "next_funding_time", 0) or 0),
        )
        deriv_db.put_funding_rates([row])
        _deriv_write_cache[cache_key] = now_ms
    except Exception:
        logger.debug("Derivatives funding rate write failed", exc_info=True)


def _normalize_symbol(raw: str) -> str:
    """Normalize URL-friendly symbol to internal format.

    Accepts ``BTCUSDT``, ``BTC-USDT``, ``btc_usdt``, ``BTC/USDT`` etc.
    and returns the internal format (passed as-is to MarketDataService,
    which uses the format registered at subscription time).

    Bare base symbols like ``BTC`` are normalized to ``BTC/USDT`` so that
    callers passing only the base asset still get a valid trading pair.
    """
    for sep in ("-", "_"):
        if sep in raw:
            sym = raw.replace(sep, "/").upper()
            # OKX-style "-SWAP" suffix (BTC-USDT-SWAP) → unified BASE/QUOTE;
            # perp semantics are carried by market_type, not the symbol.
            if sym.endswith("/SWAP"):
                sym = sym[: -len("/SWAP")]
            return sym
    if "/" not in raw:
        upper = raw.upper()
        # Already-joined pair without separator, e.g. "BTCUSDT" → "BTC/USDT".
        # Must check this BEFORE the bare-base branch, otherwise "BTCUSDT"
        # gets treated as a base and mangled into "BTCUSDT/USDT".
        for quote in ("USDT", "USD", "PERP", "BUSD", "TUSD", "USDC"):
            if upper.endswith(quote) and len(upper) > len(quote):
                base = upper[: -len(quote)]
                return f"{base}/{quote}"
        # Bare base like "BTC" → "BTC/USDT". USDT is the default quote
        # for the futures/spot pairs this platform trades.
        return f"{upper}/USDT"
    return raw


def _df_to_kline_events(
    df: Any,
    exchange: str,
    market_type: str,
    symbol: str,
    interval: str,
) -> list[KlineEvent]:
    """Convert a SQLite KlineDb DataFrame to KlineEvent list.

    KlineDb.get_klines returns columns ``[timestamp, open, high, low, close,
    volume]``; this fills in the metadata fields (exchange/market_type/symbol/
    interval/closed) that KlineEvent requires. ``closed`` is approximated as
    True for all rows since SQLite history is finalized candles.
    """
    if df is None or len(df) == 0:
        return []
    import time as _time

    now_ms = int(_time.time() * 1000)
    result: list[KlineEvent] = []
    for row in df.itertuples(index=False):
        ts = int(getattr(row, "timestamp", 0))
        result.append(
            KlineEvent(
                exchange=exchange,
                market_type=market_type,
                symbol=symbol,
                timestamp=ts,
                interval=interval,
                open=float(getattr(row, "open", 0)),
                high=float(getattr(row, "high", 0)),
                low=float(getattr(row, "low", 0)),
                close=float(getattr(row, "close", 0)),
                volume=float(getattr(row, "volume", 0)),
                closed=ts > 0 and now_ms >= ts,
            )
        )
    return result


def _resolve_source(
    exchange: str | None,
    market_type: str | None,
    settings_service: SettingsService,
) -> tuple[str, str]:
    """Determine (exchange, market_type) from query params or persisted settings."""
    settings = settings_service._load_non_sensitive()
    exchange_settings = (
        settings.get("exchange", {}) if isinstance(settings, dict) else {}
    )

    resolved_exchange = (
        (exchange or str(exchange_settings.get("provider", "binance"))).strip().lower()
    )
    resolved_market_type = (
        (market_type or str(exchange_settings.get("market_type", "futures")))
        .strip()
        .lower()
    )

    if resolved_exchange not in _ALLOWED_EXCHANGES:
        raise SeleneSpaceError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"Invalid exchange '{resolved_exchange}', expected one of: {', '.join(sorted(_ALLOWED_EXCHANGES))}",
        )

    if resolved_market_type not in _ALLOWED_MARKET_TYPES:
        raise SeleneSpaceError(
            code=ErrorCode.INVALID_PARAMETER,
            message=f"Invalid market_type '{resolved_market_type}', expected one of: {', '.join(sorted(_ALLOWED_MARKET_TYPES))}",
        )

    return resolved_exchange, resolved_market_type


def _require_market_service(svc: Any = Depends(get_market_service)) -> Any:
    """Raise 503 if MarketDataService is not available."""
    if svc is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message="Market data service is not available",
        )
    return svc


async def _ensure_subscribed(
    svc: Any,
    symbol: str,
    exchange: str,
    market_type: str,
) -> None:
    """Auto-subscribe to a symbol on first access if not yet subscribed."""
    source = svc.get_source(exchange, market_type)
    if source is None:
        raise SeleneSpaceError(
            code=ErrorCode.SERVICE_UNAVAILABLE,
            message=f"No data source registered for {exchange}/{market_type}",
        )
    if symbol not in source.get_symbols():
        try:
            await svc.add_symbol(symbol, exchange=exchange, market_type=market_type)
        except Exception:
            logger.debug(
                "Auto-subscribe add_symbol failed for %s on %s/%s",
                symbol,
                exchange,
                market_type,
                exc_info=True,
            )


@router.get("")
async def list_symbols(
    request: Request,
    exchange: str | None = Query(None, description="Exchange provider"),
    market_type: str | None = Query(None, description="Market type"),
    svc: Any = Depends(_require_market_service),
    settings_service: SettingsService = Depends(get_settings_service),
) -> APIResponse[dict[str, Any]]:
    """List all currently subscribed symbols for a given source."""
    ex, mt = _resolve_source(exchange, market_type, settings_service)
    symbols: list[str] = svc.get_symbols(ex, mt)
    return _ok(
        request,
        {"symbols": symbols, "count": len(symbols), "exchange": ex, "market_type": mt},
    )


@router.get("/l1-gaps")
async def list_l1_gaps(
    request: Request,
    since: int | None = Query(
        None, description="Only episodes opened at or after this epoch-ms"
    ),
    limit: int = Query(200, ge=1, le=2000, description="Max episodes to return"),
) -> APIResponse[dict[str, Any]]:
    """Recorded A-share intraday L1 outage episodes (S2 ledger).

    S2's guarantee is "no *invisible* hole": a network gap in the A-share
    intraday poll is recorded as a bounded episode (open/close timestamps,
    duration, affected symbols). This is the read side of that ledger — without
    a reader the rows exist but no operator can act on them.

    An episode with ``closed_at_ms == null`` is still open — either live, or the
    process died mid-outage. It is surfaced rather than hidden: ``open`` in the
    response counts them, so "the recorder itself went blind" is visible.
    """
    from selene_space_market.kline_db import KlineDB, L1SnapshotGap

    db_path = default_klines_db_path()
    if not db_path.exists():
        raise NotFoundError(f"klines.db not found at {db_path}")

    def _read() -> list[L1SnapshotGap]:
        db = KlineDB(str(db_path))
        try:
            # cast: mypy resolves selene_space_market as Any (the package is not
            # on its search path), so the annotated ``-> list[L1SnapshotGap]``
            # would otherwise trip warn_return_any. The method is really declared
            # ``-> list[L1SnapshotGap]``; the annotation is exact, not a
            # suppression.
            return cast(list[L1SnapshotGap], db.list_l1_gaps(since_ms=since))
        finally:
            db.close()

    # Blocking sqlite read off the event loop (same reason as the replay path).
    rows = await asyncio.to_thread(_read)
    # `list_l1_gaps` orders ascending by open time; an ops view wants the most
    # recent first, then cap.
    rows = sorted(rows, key=lambda r: (r.opened_at_ms, r.source), reverse=True)[:limit]

    episodes = [
        {
            "source": r.source,
            "opened_at_ms": r.opened_at_ms,
            "closed_at_ms": r.closed_at_ms,
            "duration_sec": r.duration_sec,
            "symbols": list(r.symbols),
            "is_open": r.closed_at_ms is None,
            # Option A′ bracket. `volume_delta` is null when the episode could
            # not be bracketed — a missing measurement, not a zero. It is a
            # *cumulative-shares* difference over `volume_symbols` symbols, so
            # it is an upper-bracket on turnover, not a reconstruction of the
            # lost ticks.
            "volume_before": r.volume_before,
            "volume_after": r.volume_after,
            "volume_delta": r.volume_delta,
            "volume_symbols": r.volume_symbols,
        }
        for r in rows
    ]
    return _ok(
        request,
        {
            "episodes": episodes,
            "count": len(episodes),
            "open_count": sum(1 for e in episodes if e["is_open"]),
        },
    )


@router.get("/{symbol:path}/ticker")
async def get_ticker(
    symbol: str,
    request: Request,
    exchange: str | None = Query(None, description="Exchange provider: binance or okx"),
    market_type: str | None = Query(None, description="Market type: spot or futures"),
    svc: Any = Depends(_require_market_service),
    settings_service: SettingsService = Depends(get_settings_service),
) -> APIResponse[dict[str, Any]]:
    """Get the latest ticker for *symbol*."""
    sym = _normalize_symbol(symbol)
    ex, mt = _resolve_source(exchange, market_type, settings_service)
    await _ensure_subscribed(svc, sym, ex, mt)
    ticker = svc.get_ticker(sym, ex, mt)
    if ticker is None:
        raise NotFoundError(f"No ticker data for symbol '{sym}' on {ex}/{mt}")
    return _ok(request, {**ticker.model_dump(), "market_type": mt})


@router.get("/{symbol:path}/kline")
async def get_kline(
    symbol: str,
    request: Request,
    interval: str = Query(
        "1h", description="Kline interval, e.g. 1m, 5m, 15m, 30m, 1h, 4h, 1d"
    ),
    limit: int = Query(200, ge=1, le=1500, description="Max number of klines"),
    end_time: int | None = Query(
        None, description="Fetch candles before this timestamp (ms) for pagination"
    ),
    since: int | None = Query(
        None,
        description="Return candles with timestamp >= since (ms) for incremental fetch",
    ),
    exchange: str | None = Query(None, description="Exchange provider: binance or okx"),
    market_type: str | None = Query(None, description="Market type: spot or futures"),
    svc: Any = Depends(_require_market_service),
    settings_service: SettingsService = Depends(get_settings_service),
) -> APIResponse[dict[str, Any]]:
    """Get K-line (candlestick) data for *symbol*.

    Fetches from exchange REST API with the requested interval, ensuring
    multi-timeframe support regardless of the WS subscription interval.

    - ``end_time``: paginate backwards into history
    - ``since``: incremental fetch — return only candles newer than this timestamp
    """
    from app.core.redis import get_redis
    from selene_space_market.kline_store import KlineStore

    sym = _normalize_symbol(symbol)
    ex, mt = _resolve_source(exchange, market_type, settings_service)
    await _ensure_subscribed(svc, sym, ex, mt)

    redis_client = get_redis()
    kline_store = KlineStore(redis_client) if redis_client else None

    # Try Redis cache first (skip for historical pagination with end_time)
    source = "exchange"
    klines = None
    if kline_store and end_time is None:
        cached = await kline_store.get(ex, mt, sym, interval, since=since, limit=limit)
        # 缓存必须达到请求深度才命中:否则旧缓存(端点早年默认 limit=200 写入的 200 根)
        # 会在请求 limit=1500 时短路,图表永远停在最新 200 根(=="K 线缺口")。深度不足
        # 时放行到 REST 拉满,顺带把 Redis + SQLite 刷新到最新行情。
        if cached and len(cached) >= limit:
            klines = cached
            source = "cache"

    # 本地 SQLite 优先:CN 网络下 Binance REST 走 GFW 慢链路,每次切周期/轮询都
    # 现拉 1500 根就是"卡"的主因。启动同步/回填已把本周期历史灌进本地,只要最近
    # 一根 bar 够新(阈值=3 倍周期、最低 30 分钟、最高 2 天),直接毫秒级出本地,
    # REST 完全不发。落后时才放行到 REST 拉尾部,顺带刷新本地。
    import time as _time

    if klines is None and end_time is None:
        _interval_ms = _INTERVAL_MS.get(interval, 3_600_000)
        _fresh_ms = max(30 * 60_000, min(3 * _interval_ms, 2 * 86_400_000))
        try:
            from app.core.dependencies import get_kline_manager

            km = get_kline_manager()
            if km is not None:
                # 只读尾部窗口即可:全分区读出(15m=36k 行)再逐行转 KlineEvent
                # 是本接口主要耗时(实测随分区体积涨到秒级)。用 start_date 把
                # SQL 限定在 needs 量内,转 2 千行 vs 3.6 万行差两个数量级。
                import datetime as _dt

                _need = (limit or 1500) * _interval_ms * 2
                _start = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(
                    milliseconds=_need
                )
                df = await km.get_klines(
                    exchange=ex,
                    market_type=mt,
                    symbol=sym,
                    interval=interval,
                    start_date=_start,
                    source="local",
                )
                if df is not None and len(df) > 0:
                    local_ts = int(df["timestamp"].iloc[-1])
                    _fresh = int(_time.time() * 1000) - local_ts <= _fresh_ms
                    # 只要本地有数据就触发后台 repair_gaps(带 5min 冷却):
                    # repair 用 10 天窗口巡检,能检测到本请求 50h 窗口外/内的
                    # 任何中段洞(如 1m 分区长期未看停在旧点,REST 增量只补尾、
                    # 中段洞永留)。本端点检测不了窗口外的洞,所以统一交给
                    # repair 判断。新鲜度只决定本次是否用 local 返回。
                    _launch_gap_repair(km, ex, mt, sym, interval)

                    if _fresh:
                        local_ev = _df_to_kline_events(df, ex, mt, sym, interval)
                        if since is not None:
                            local_ev = [e for e in local_ev if e.timestamp >= since]
                        if local_ev:
                            klines = local_ev[-limit:] if limit else local_ev
                            source = "local"
        except Exception:
            logger.debug("Kline local-first read failed for %s", sym, exc_info=True)

    # end_time 向后翻页(前端滚动加载历史):本地 SQLite 优先。
    # CN 网络下 Binance REST 走 GFW 慢链路,每次翻页都现拉慢。
    # 本地有 end_time 之前的历史直接毫秒级回;本地没有/不够才走 REST 兜底。
    if klines is None and end_time is not None:
        _interval_ms = _INTERVAL_MS.get(interval, 3_600_000)
        try:
            from app.core.dependencies import get_kline_manager
            import datetime as _dt

            km = get_kline_manager()
            if km is not None:
                _end_dt = _dt.datetime.fromtimestamp(
                    end_time / 1000, tz=_dt.timezone.utc
                )
                _start = _end_dt - _dt.timedelta(
                    milliseconds=(limit or 1500) * _interval_ms * 2
                )
                df = await km.get_klines(
                    exchange=ex,
                    market_type=mt,
                    symbol=sym,
                    interval=interval,
                    start_date=_start,
                    end_date=_end_dt,
                    source="local",
                )
                if df is not None and len(df) > 0:
                    local_ev = _df_to_kline_events(df, ex, mt, sym, interval)
                    local_ev = [e for e in local_ev if e.timestamp < end_time]
                    if local_ev:
                        klines = local_ev[-limit:] if limit else local_ev
                        source = "local"
        except Exception:
            logger.debug("Kline end_time local read failed for %s", sym, exc_info=True)

    if klines is None:
        try:
            klines = await svc.fetch_klines_rest(
                sym,
                ex,
                mt,
                interval=interval,
                limit=limit,
                end_time=end_time,
                start_time=since,
            )
        except Exception:
            logger.warning(
                "REST kline fetch failed for %s on %s/%s", sym, ex, mt, exc_info=True
            )
            klines = []
        if klines and kline_store and end_time is None:
            await kline_store.put(ex, mt, sym, interval, klines)
        # Persist to SQLite for backtesting / offline use
        if klines:
            try:
                from app.core.dependencies import get_kline_manager

                km = get_kline_manager()
                if km is not None:
                    km._db.put_klines(klines)
            except Exception:
                logger.debug("Kline SQLite write skipped", exc_info=True)
            # Persist derivatives data for futures klines
            if mt == "futures":
                try:
                    from app.core.dependencies import get_derivatives_db

                    deriv_db = get_derivatives_db()
                    if deriv_db is not None:
                        _persist_derivatives_snapshot(deriv_db, sym, ex, interval)
                except Exception:
                    logger.debug("Derivatives write skipped", exc_info=True)
        if since is not None and klines:
            klines = [k for k in klines if k.timestamp >= since]
        if not klines:
            # Fallback to SQLite historical klines when exchange is unreachable.
            # SQLite is populated on prior successful fetches (see put_klines
            # above), so this returns stale-but-real data instead of an empty
            # array, keeping the chart visible during network outages.
            try:
                from app.core.dependencies import get_kline_manager

                km = get_kline_manager()
                if km is not None:
                    df = await km.get_klines(
                        exchange=ex,
                        market_type=mt,
                        symbol=sym,
                        interval=interval,
                        source="local",
                    )
                    fallback = _df_to_kline_events(df, ex, mt, sym, interval)
                    if fallback:
                        # Trim to requested limit (SQLite may return more)
                        klines = fallback[-limit:]
                        source = "stale"
                        logger.info(
                            "Kline fallback to SQLite (stale) for %s %s on %s/%s: %d candles",
                            sym,
                            interval,
                            ex,
                            mt,
                            len(klines),
                        )
            except Exception:
                logger.debug("Kline SQLite fallback failed for %s", sym, exc_info=True)
            if not klines:
                klines = []

    kline_dicts = [k.model_dump() for k in klines]
    body = APIResponse(
        data={
            "symbol": sym,
            "interval": interval,
            "exchange": ex,
            "market_type": mt,
            "klines": kline_dicts,
            "source": source,
        },
        meta=build_response_meta(request),
        error=None,
    )

    import json as _json

    content = body.model_dump()
    raw = _json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    etag = hashlib.md5(raw.encode()).hexdigest()  # noqa: S324

    if_none_match = request.headers.get("if-none-match")
    if if_none_match and if_none_match.strip('"') == etag:
        return JSONResponse(
            status_code=304, content=None, headers={"ETag": f'"{etag}"'}
        )

    return JSONResponse(
        content=content,
        headers={
            "ETag": f'"{etag}"',
            "Cache-Control": "public, max-age=5, stale-while-revalidate=30",
        },
    )


@router.post("/replay-klines")
async def replay_klines(
    request: Request,
    symbol: str = Query("BTC/USDT", description="Symbol to replay"),
    interval: str = Query("1h", description="Kline interval"),
    limit: int = Query(500, ge=1, le=20000, description="Max klines to replay"),
    delay_ms: int = Query(100, ge=0, le=5000, description="Delay between ticks"),
    feed_shadow: bool = Query(True, description="Also feed shadow engine price ticks"),
    feed_paper: bool = Query(
        False,
        description="Also feed paper engine price ticks (for shadow vs paper equity comparison)",
    ),
    exchange: str | None = Query(
        None, description="Filter by exchange (binance, okx). Default: any"
    ),
    market_type: str | None = Query(
        None, description="Filter by market_type (spot, futures). Default: any"
    ),
    order: str = Query(
        "asc", description="Sort order: asc (oldest first) or desc (newest first)"
    ),
    svc: Any = Depends(_require_market_service),
) -> APIResponse[dict[str, Any]]:
    """Replay historical klines from local SQLite through the market service.

    Reads klines from data/klines.db and publishes each as a KlineEvent via
    the market service's event bus. Used for shadow run validation when live
    WS feeds are unavailable. If feed_shadow=True, also calls
    shadow_engine.on_price_tick(symbol, close) to trigger fills.

    Filters by exchange/market_type let callers replay only binance/spot (the
    source StrategyRunner._matches accepts) or okx/futures. Default sort asc
    replays oldest-first (chronological); desc replays most-recent first.
    """
    import asyncio
    import sqlite3

    from selene_space_types.market import KlineEvent

    db_path = default_klines_db_path()
    if not db_path.exists():
        raise NotFoundError(f"klines.db not found at {db_path}")

    where_clauses = ["symbol=?", "interval=?"]
    params: list[Any] = [symbol, interval]
    if exchange:
        where_clauses.append("exchange=?")
        params.append(exchange)
    if market_type:
        where_clauses.append("market_type=?")
        params.append(market_type)
    where_sql = " AND ".join(where_clauses)

    sort_direction = "DESC" if order.lower() == "desc" else "ASC"

    # H7: wrap blocking sqlite3 connect+execute in asyncio.to_thread to avoid
    # blocking the event loop under concurrent /klines/replay requests.
    def _fetch_klines() -> list:
        conn = sqlite3.connect(str(db_path))
        try:
            return conn.execute(
                f"SELECT exchange, market_type, symbol, interval, timestamp, open, high, low, close, volume, closed "
                f"FROM klines WHERE {where_sql} ORDER BY timestamp {sort_direction} LIMIT ?",
                (*params, limit),
            ).fetchall()
        finally:
            conn.close()

    rows = await asyncio.to_thread(_fetch_klines)

    if not rows:
        raise NotFoundError(
            f"No klines found for {symbol}/{interval} "
            f"(exchange={exchange or 'any'}, market_type={market_type or 'any'}) in {db_path}"
        )

    if order.lower() == "desc":
        rows = list(reversed(rows))

    from app.core.dependencies import get_execution_engine, get_shadow_engine

    shadow_engine = get_shadow_engine() if feed_shadow else None
    paper_engine = get_execution_engine() if feed_paper else None

    event_bus = getattr(svc, "_event_bus", None)
    if event_bus is None:
        raise NotFoundError("Market service event bus not available")

    fed = 0
    for row in rows:
        ex, mt, sym, kint, ts, o, hi, lo, c, v, closed = row
        kline = KlineEvent(
            exchange=ex,
            market_type=mt,
            symbol=sym,
            timestamp=ts,
            interval=kint,
            open=o,
            high=hi,
            low=lo,
            close=c,
            volume=v,
            closed=bool(closed) if closed is not None else True,
        )
        event_bus.publish(kline)
        if shadow_engine is not None:
            await shadow_engine.on_price_tick(sym, c)
        if paper_engine is not None:
            await paper_engine.on_price_tick(sym, c)
        fed += 1
        if delay_ms > 0:
            await asyncio.sleep(delay_ms / 1000.0)

    return APIResponse(
        data={
            "symbol": symbol,
            "interval": interval,
            "exchange": exchange,
            "market_type": market_type,
            "fed": fed,
            "shadow_engine_wired": shadow_engine is not None,
            "paper_engine_wired": paper_engine is not None,
            "last_close": rows[-1][7] if rows else None,
        },
        meta=build_response_meta(request),
        error=None,
    )


@router.get("/{symbol:path}/orderbook")
async def get_orderbook(
    symbol: str,
    request: Request,
    depth: int = Query(
        20, ge=1, le=100, description="Orderbook depth (number of levels)"
    ),
    exchange: str | None = Query(None, description="Exchange provider: binance or okx"),
    market_type: str | None = Query(None, description="Market type: spot or futures"),
    svc: Any = Depends(_require_market_service),
    settings_service: SettingsService = Depends(get_settings_service),
) -> APIResponse[dict[str, Any]]:
    """Get L2 orderbook snapshot for *symbol*."""
    sym = _normalize_symbol(symbol)
    ex, mt = _resolve_source(exchange, market_type, settings_service)
    await _ensure_subscribed(svc, sym, ex, mt)
    book = svc.get_orderbook(sym, ex, mt)
    if book is None:
        raise NotFoundError(f"No orderbook data for symbol '{sym}' on {ex}/{mt}")
    data = book.model_dump()
    data["bids"] = data["bids"][:depth]
    data["asks"] = data["asks"][:depth]
    return _ok(request, data)
