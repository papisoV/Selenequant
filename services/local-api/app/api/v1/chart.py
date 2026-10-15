"""Unified bars gateway — TradingView-style chart data for 4 REST markets.

``GET /chart/bars`` serves us/commodities/ashare/taiwan with a single
``{t,o,h,l,c,v}`` bar contract (``t`` = Unix seconds UTC). Crypto is
intentionally NOT served here — it keeps its WebSocket realtime path
(Decision A). ``GET /chart/capabilities`` reports per-market intervals.

422 mechanisms (empirically verified on FastAPI 0.138.1):
- invalid ``market``/``interval`` -> ``Literal[...]`` -> FastAPI literal_error
- interval valid globally but unsupported for the market -> manual
  ``RequestValidationError`` -> error_handler.py returns 422
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Query, Request
from fastapi.exceptions import RequestValidationError

from app.api.v1.us_radar import _bars_from_chart, _get_client
from app.core.dependencies import build_response_meta
from selene_space_types.common import APIResponse
from selene_space_types.errors import ErrorCode, SeleneSpaceError

router = APIRouter(prefix="/chart", tags=["chart"])

logger = logging.getLogger(__name__)

Market = Literal["us", "commodities", "ashare", "taiwan"]
Interval = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"]

# Per-market supported intervals (v1). crypto reported for the frontend to
# render TF chips uniformly but never served by /chart/bars (Decision A).
_CAPABILITIES: dict[str, list[str]] = {
    "us": ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"],
    "commodities": ["1m", "5m", "15m", "30m", "1h", "1d"],
    "ashare": ["1d"],
    "taiwan": ["1d"],
    "crypto": ["1m", "5m", "15m", "30m", "1h", "4h", "1d", "1w"],
}

# Yahoo interval -> range. Yahoo enforces a per-interval range ceiling: a
# range past the cap is rejected with HTTP 422 (not clamped). Verified
# empirically 2026-10-13 (AAPL): 1m caps at 7d, 5m/15m/30m at 60d. "3mo"
# (~91d) for 15m/30m therefore 422'd — every US 15m/30m request came back 502
# and the chart rendered 无数据. Keep every value at or under its cap.
# 4h has no reliable native Yahoo interval: fetch 1h and aggregate (see
# _aggregate_4h), so its range must cover the 1h pull, not the 4h span.
_YAHOO_RANGES = {
    "1m": "7d",
    "5m": "1mo",
    "15m": "60d",
    "30m": "60d",
    "1h": "3mo",
    "4h": "3mo",
    "1d": "1y",
    "1w": "1y",
}

# US local-first: read windows (days) per interval, against the
# yahoo/us_stock/<SYMBOL>/<interval> partitions written by USStockSource
# (favorites sync). 4h has no own partition — it reads 1h and aggregates.
_US_LOCAL_WINDOW_DAYS = {
    "1m": 7,
    "5m": 30,
    "15m": 90,
    "30m": 90,
    "1h": 90,
    "4h": 30,
    "1d": 366,
    "1w": 366,
}

# Intraday interval -> ms (partition granularity; 4h gates on its 1h partition).
_US_INTRADAY_INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
    "30m": 1_800_000,
    "1h": 3_600_000,
}

# Gateway interval -> CN futures period ("1d" = daily source, rest minute).
_CN_PERIOD = {"1m": "1", "5m": "5", "15m": "15", "30m": "30", "1h": "60"}

_SH_TZ = ZoneInfo("Asia/Shanghai")

# ---------------------------------------------------------------------------
# Symbol search pools (for /chart/symbols) — mirrors the frontend pools in
# apps/desktop/lib/market/route-symbol.ts (_FUTURES_WL / _CRYPTO_NAMES).
# Backend can't import frontend TS, so the code sets are duplicated here.
# ---------------------------------------------------------------------------

_COMMODITY_NAMES: dict[str, str] = {
    "AU0": "沪金",
    "AG0": "沪银",
    "CU0": "沪铜",
    "SC0": "原油",
    "NI0": "沪镍",
    "ZN0": "沪锌",
    "AL0": "沪铝",
    "RB0": "螺纹钢",
    "TA0": "PTA",
    "PB0": "沪铅",
    "SN0": "沪锡",
    "SS0": "不锈钢",
    "FU0": "燃料油",
    "OI0": "菜油",
    "FG0": "玻璃",
    "SA0": "纯碱",
    "UR0": "尿素",
    "RU0": "橡胶",
    "NR0": "20号胶",
    "BU0": "沥青",
    "HC0": "热卷",
    "SP0": "纸浆",
    "JD0": "鸡蛋",
    "IF0": "沪深300",
    "IH0": "上证50",
    "IC0": "中证500",
    "IM0": "中证1000",
}

_CRYPTO_NAMES: dict[str, str] = {
    "BTC": "比特币",
    "ETH": "以太坊",
    "SOL": "索拉纳",
    "BNB": "币安币",
    "XRP": "瑞波币",
    "DOGE": "狗狗币",
    "ADA": "艾达币",
    "AVAX": "雪崩",
    "LINK": "Chainlink",
    "MATIC": "Polygon",
    "DOT": "波卡",
    "LTC": "莱特币",
    "UNI": "Uniswap",
    "FIL": "Filecoin",
    "APT": "Aptos",
    "ARB": "Arbitrum",
    "OP": "Optimism",
    "NEAR": "NEAR",
    "SUI": "Sui",
    "TRX": "波场",
    "TON": "Toncoin",
    "SHIB": "柴犬币",
    "PEPE": "Pepe",
}

_HOT_SYMBOLS: list[dict[str, str]] = [
    {"symbol": "BTC", "market": "crypto", "name": "比特币"},
    {"symbol": "ETH", "market": "crypto", "name": "以太坊"},
    {"symbol": "600519", "market": "ashare", "name": "贵州茅台"},
    {"symbol": "AAPL", "market": "us", "name": "Apple Inc"},
    {"symbol": "TSLA", "market": "us", "name": "Tesla Inc"},
    {"symbol": "SPY", "market": "us", "name": "SPDR S&P 500 ETF"},
    {"symbol": "2330", "market": "taiwan", "name": "台積電"},
    {"symbol": "AU0", "market": "commodities", "name": "沪金"},
]


def _match_code_name(q: str, items: Iterable[tuple[str, str]]) -> list[dict[str, str]]:
    """Rank matches: exact code > code prefix > name contains (case-insensitive)."""
    ql = q.strip().lower()
    exact, prefix, name = [], [], []
    for sym, nm in items:
        sl = sym.lower()
        if sl == ql:
            exact.append((sym, nm))
        elif sl.startswith(ql):
            prefix.append((sym, nm))
        elif ql in nm.lower():
            name.append((sym, nm))
    return [{"symbol": s, "name": n} for s, n in (exact + prefix + name)]


# (loaded_at_epoch, rows) memo so per-keystroke search doesn't re-read the cache
# file on every request; refresh after 1 hour.
_ASHARE_DF_CACHE: tuple[float, Any] | None = None


def _bounded_ashare_list(fetch_fn, timeout: float = 8.0):
    """Run the universe fetch with a bounded wait; overflow keeps running.

    A cold replica (no ``data_cache/universe`` file — gitignored, per-tree) makes
    ``get_a_share_list`` block on a live akshare fetch, which can exceed 90s and
    freezes ⌘K search. This runs the fetch on a daemon thread and waits at most
    ``timeout`` seconds: on a hit we use the DataFrame, on a miss we return
    ``None`` while the thread keeps warming the on-disk 7-day cache for the next
    call. A fresh thread per call (no shared pool) is deliberate — a hung akshare
    call must not queue-block later requests, and the warm file cache makes the
    common path return in milliseconds.
    """
    import threading

    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["df"] = fetch_fn()
        except Exception:  # noqa: BLE001 — degrade to None, caller logs
            box["df"] = None

    t = threading.Thread(target=_run, name="ashare-universe", daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        return None
    return box.get("df")


# (loaded_at_epoch, normalized rows) memo for FinMind TaiwanStockInfo full list —
# see _taiwan_search; refresh after 1 hour.
_TAIWAN_ROWS_CACHE: tuple[float, Any] | None = None


def _initials(name: str) -> str:
    """Pinyin first letters of the CJK chars in name (贵州茅台→gzmt, 台積電→tjd).

    Degrades to "" when pypinyin is absent — pinyin search silently off, the
    plain code/name matching paths are unaffected.
    """
    try:
        from pypinyin import Style, lazy_pinyin
    except ImportError:
        return ""
    return "".join(lazy_pinyin(name, style=Style.FIRST_LETTER, errors="ignore"))


def _use_pinyin(q: str) -> bool:
    """Pinyin initials only make sense for pure-ASCII-letter queries (gzmt/tjd)."""
    return q.isascii() and q.isalpha()


def _ashare_search(q: str) -> list[dict[str, str]]:
    """Full A-share list (akshare, 7-day file cache) filtered by q. Degrades to [].

    The upstream ``get_a_share_list`` call is bounded: a cold replica (no
    ``data_cache/universe`` file — that dir is gitignored, per-tree) otherwise
    blocks the request thread on a live akshare fetch, freezing ⌘K search for
    >90s. We serve whatever is memoized and refresh off-thread; the worker
    still warms the 7-day file cache so the next call is instant.
    """
    global _ASHARE_DF_CACHE
    import time

    from selene_space_ashare.data_fetcher import get_a_share_list

    now = time.time()
    if _ASHARE_DF_CACHE is None or now - _ASHARE_DF_CACHE[0] > 3600:
        try:
            df = _bounded_ashare_list(get_a_share_list)
        except Exception:
            logger.warning("ashare list fetch failed", exc_info=True)
            df = None
        if df is not None and not df.empty:
            rows: list[tuple[str, str, str]] = []
            for code, nm in zip(df["code"].astype(str), df["name"].astype(str)):
                rows.append((code, nm, _initials(nm)))
            _ASHARE_DF_CACHE = (now, rows)
    if _ASHARE_DF_CACHE is None:
        return []
    rows = _ASHARE_DF_CACHE[1]
    ql = q.strip().lower()
    py_ok = _use_pinyin(ql)
    exact, prefix, name, py = [], [], [], []
    for code, nm, ini in rows:
        sl = code.lower()
        if sl == ql:
            exact.append((code, nm))
        elif sl.startswith(ql):
            prefix.append((code, nm))
        elif ql in nm.lower():
            name.append((code, nm))
        elif py_ok and ini and ini.startswith(ql):
            py.append((code, nm))
    return [{"symbol": s, "name": n} for s, n in (exact + prefix + name + py)]


def _taiwan_search(q: str) -> list[dict[str, str]]:
    """FinMind TaiwanStockInfo search. Degrades to [] (token/network fragile)."""
    # TaiwanStockInfo 是全量静态列表（~2000 行）；不加 memo 时每次搜索都全量下载
    # FinMind（2-3s 网络往返），是 /chart/symbols 延迟的主凶——照抄 ashare 1h memo。
    # initials 在 memo 构建时一次算好，不逐请求跑 pypinyin。
    global _TAIWAN_ROWS_CACHE
    import time

    from selene_space_stock import taiwan_finmind

    now = time.time()
    if _TAIWAN_ROWS_CACHE is None or now - _TAIWAN_ROWS_CACHE[0] > 3600:
        raw = taiwan_finmind._get("TaiwanStockInfo")
        rows = [
            {
                "symbol": str(r.get("stock_id", "")),
                "name": r.get("stock_name", ""),
                "ini": _initials(r.get("stock_name", "")),
            }
            for r in (raw or [])
        ]
        _TAIWAN_ROWS_CACHE = (now, rows)
    all_rows = _TAIWAN_ROWS_CACHE[1]
    ql = q.strip().lower()
    py_ok = _use_pinyin(ql)
    seen: dict[str, dict[str, str]] = {}
    for r in all_rows:
        sid = r["symbol"]
        name = r["name"]
        if (
            ql in name.lower()
            or ql == sid.lower()
            or (py_ok and r["ini"] and r["ini"].startswith(ql))
        ) and sid not in seen:
            seen[sid] = {"symbol": sid, "name": name}
    return list(seen.values())[:10]


def _respond(request: Request, data: Any) -> APIResponse[dict[str, Any]]:
    return APIResponse(data=data, meta=build_response_meta(request), error=None)


def _raise_unsupported(market: str, interval: str) -> None:
    raise RequestValidationError(
        [
            {
                "loc": ["query", "interval"],
                "msg": f"interval '{interval}' not supported for market '{market}'",
                "type": "value_error",
            }
        ]
    )


# ---------------------------------------------------------------------------
# t helpers (R1 — Unix seconds UTC, per-source conversion)
# ---------------------------------------------------------------------------


def _to_unix_sec(raw: str) -> int | None:
    """Unit-agnostic Yahoo timestamp -> Unix seconds UTC (ms or s input)."""
    try:
        v = int(float(raw))
    except (ValueError, TypeError):
        return None
    return v // 1000 if v >= 10**12 else v


def _date_to_utc(date_str: str) -> int:
    """'YYYY-MM-DD' -> that day's UTC 00:00 epoch (date-anchored)."""
    return int(
        datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()
    )


def _cn_minute_epoch(ts_str: str) -> int:
    """CN minute 'YYYY-MM-DD HH:MM:SS' (Asia/Shanghai local) -> UTC seconds."""
    naive = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    return int(naive.replace(tzinfo=_SH_TZ).timestamp())


def _ashare_epoch(date_cell: Any) -> int | None:
    """Format-agnostic A-share date -> UTC 00:00 (YYYYMMDD or YYYY-MM-DD)."""
    s = str(date_cell).strip()
    if len(s) == 8 and s.isdigit():
        return int(
            datetime.strptime(s, "%Y%m%d").replace(tzinfo=timezone.utc).timestamp()
        )
    try:
        return _date_to_utc(s[:10])
    except ValueError:
        return None


def _fmt_iso(yyyymmdd: str) -> str:
    """YYYYMMDD -> YYYY-MM-DD for FinMind date semantics."""
    s = yyyymmdd.strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s


# ---------------------------------------------------------------------------
# Market branches
# ---------------------------------------------------------------------------


def _aggregate_4h(bars: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge every 4 consecutive 1h bars into one 4h bar; drop the partial tail."""
    out = []
    n = len(bars) - (len(bars) % 4)
    for i in range(0, n, 4):
        chunk = bars[i : i + 4]
        out.append(
            {
                "t": chunk[-1]["t"],
                "o": chunk[0]["o"],
                "h": max(x["h"] for x in chunk),
                "l": min(x["l"] for x in chunk),
                "c": chunk[-1]["c"],
                "v": sum(x["v"] for x in chunk),
            }
        )
    return out


async def _us_bars(client: Any, symbol: str, interval: str) -> list[dict[str, Any]]:
    # Gateway interval -> the Yahoo interval we actually request. 4h has no
    # native Yahoo interval we rely on: pull 1h and fold it down below. 1w
    # maps to Yahoo's "1wk" spelling. Passing "4h" straight through used to
    # hand back Yahoo's native 4h bars and then _aggregate_4h folded them
    # AGAIN — 4h bars merged into 16h bars, ~40 candles collapsed to ~10.
    fetch_interval = {"4h": "1h", "1w": "1wk"}.get(interval, interval)
    try:
        raw = await client.get_chart(
            symbol, interval=fetch_interval, range_=_YAHOO_RANGES[interval]
        )
    except Exception as e:
        raise SeleneSpaceError(
            code=ErrorCode.EXCHANGE_ERROR, message=f"Yahoo Finance API error: {e}"
        ) from e
    out = []
    for b in _bars_from_chart(raw, fmt_date=False):
        t = _to_unix_sec(b["timestamp"])
        if t is None:
            continue
        out.append(
            {
                "t": t,
                "o": b["open"],
                "h": b["high"],
                "l": b["low"],
                "c": b["close"],
                "v": b["volume"],
            }
        )
    if interval == "4h":
        out = _aggregate_4h(out)
    if not out:
        raise SeleneSpaceError(
            code=ErrorCode.NOT_FOUND, message=f"未找到 {symbol} 的行情数据"
        )
    return out


def _us_local_fresh(last_ts_ms: int, interval: str, now_ms: int) -> bool:
    """Freshness gate for the local us partition's last bar.

    1d/1w get equity-calendar allowances (US weekend/holiday closure: a Friday
    close bar is still fresh through Monday 09:30 ET ≈ 2.7 days, so daily
    tolerates 5 days and weekly 10). Intraday mirrors markets.py's crypto
    gate: max(30min, min(3*interval, 2d)). Gate-pass means "acceptably old",
    not "latest".
    """
    if interval in ("1d", "1w"):
        allowance_ms = 5 * 86_400_000 if interval == "1d" else 10 * 86_400_000
    else:
        interval_ms = _US_INTRADAY_INTERVAL_MS.get(interval, 3_600_000)
        allowance_ms = max(30 * 60_000, min(3 * interval_ms, 2 * 86_400_000))
    return now_ms - last_ts_ms <= allowance_ms


async def _us_bars_local(symbol: str, interval: str) -> list[dict[str, Any]] | None:
    """Try to serve US bars from the local klines.db partition (yahoo/us_stock).

    Returns bars on a fresh local hit, ``None`` to fall back to Yahoo.
    Read-only against klines.db (source="local" never writes) — USStockSource
    via favorites sync stays the single writer.
    """
    import time

    try:
        from app.core.dependencies import get_kline_manager

        km = get_kline_manager()
        if km is None:
            return None
        read_interval = "1h" if interval == "4h" else interval
        window_days = _US_LOCAL_WINDOW_DAYS[interval]
        start = datetime.now(timezone.utc) - timedelta(days=window_days)
        df = await km.get_klines(
            exchange="yahoo",
            market_type="us_stock",
            symbol=symbol.upper(),
            interval=read_interval,
            start_date=start,
            source="local",
        )
        if df is None or len(df) < 10:
            return None
        last_ts_ms = int(df["timestamp"].iloc[-1])
        if not _us_local_fresh(last_ts_ms, read_interval, int(time.time() * 1000)):
            return None
        bars = [
            {
                "t": int(row.timestamp) // 1000,
                "o": float(row.open),
                "h": float(row.high),
                "l": float(row.low),
                "c": float(row.close),
                "v": float(row.volume),
            }
            for row in df.itertuples(index=False)
        ]
        if interval == "4h":
            bars = _aggregate_4h(bars)
        return bars or None
    except Exception:
        logger.warning("us local-first read failed for %s", symbol, exc_info=True)
        return None


def _commodities_bars(
    symbol: str, interval: str, start: str, end: str
) -> list[dict[str, Any]]:
    from app.api.v1.commodities_fx import _wrap
    from selene_space_commodities_fx.data_fetcher import get_futures_kline

    period = "1d" if interval == "1d" else _CN_PERIOD[interval]
    records = _wrap(get_futures_kline, symbol, period, start, end)
    minute = interval != "1d"
    bars = []
    for r in records:
        ts = r["timestamp"]
        t = _cn_minute_epoch(ts) if minute else _date_to_utc(ts[:10])
        bars.append(
            {
                "t": t,
                "o": r["open"],
                "h": r["high"],
                "l": r["low"],
                "c": r["close"],
                "v": r.get("volume", 0),
            }
        )
    return bars


def _ashare_bars(symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    from app.api.v1.ashare_deep import _wrap
    from selene_space_ashare.data_fetcher import get_daily_klines

    records = _wrap(get_daily_klines, symbol, start, end, "hfq")
    bars = []
    for r in records:
        t = _ashare_epoch(r["date"])
        if t is None:
            continue
        bars.append(
            {
                "t": t,
                "o": r["open"],
                "h": r["high"],
                "l": r["low"],
                "c": r["close"],
                "v": r.get("volume", 0),
            }
        )
    return bars


def _taiwan_bars(symbol: str, start: str, end: str) -> list[dict[str, Any]]:
    try:
        from app.api.v1.taiwan import _wrap_finmind
        from selene_space_stock import taiwan_finmind
    except ImportError as e:
        raise SeleneSpaceError(
            code=ErrorCode.NOT_FOUND,
            message=f"selene-space-stock 未安装,无法获取台股数据: {e}",
        ) from e
    start_iso = _fmt_iso(start) if start else None
    end_iso = _fmt_iso(end) if end else None
    rows = _wrap_finmind(taiwan_finmind.kline, symbol, start_iso, end_iso)
    bars = []
    for r in rows:
        bars.append(
            {
                "t": _date_to_utc(r["date"]),
                "o": r["open"],
                "h": r["high"],
                "l": r["low"],
                "c": r["close"],
                "v": r.get("volume", 0),
            }
        )
    return bars


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/bars")
async def get_chart_bars(
    request: Request,
    market: Market = Query(...),
    symbol: str = Query(...),
    interval: Interval = Query(...),
    start: str = Query("", description="YYYYMMDD"),
    end: str = Query("", description="YYYYMMDD"),
) -> APIResponse[dict[str, Any]]:
    """Unified OHLCV bars ``{t,o,h,l,c,v}`` (t = Unix seconds UTC)."""
    if interval not in _CAPABILITIES[market]:
        _raise_unsupported(market, interval)
    if market == "us":
        # Local-first: klines.db yahoo/us_stock partitions (written by
        # USStockSource via favorites sync). Yahoo stays the fallback and the
        # only writer path is untouched.
        bars = await _us_bars_local(symbol, interval)
        source = "local"
        if bars is None:
            client = await _get_client()
            bars = await _us_bars(client, symbol, interval)
            source = "yahoo"
    elif market == "commodities":
        bars = await asyncio.to_thread(_commodities_bars, symbol, interval, start, end)
    elif market == "ashare":
        bars = await asyncio.to_thread(_ashare_bars, symbol, start, end)
    else:
        bars = await asyncio.to_thread(_taiwan_bars, symbol, start, end)
    payload: dict[str, Any] = {
        "market": market,
        "symbol": symbol,
        "interval": interval,
        "bars": bars,
    }
    if market == "us":
        payload["source"] = source  # optional field: only us has the local/yahoo split
    return _respond(request, payload)


@router.get("/capabilities")
def get_chart_capabilities(request: Request) -> APIResponse[dict[str, list[str]]]:
    """Per-market supported intervals (crypto reported for uniform rendering)."""
    return _respond(request, _CAPABILITIES)


@router.get("/symbols")
def get_chart_symbols(
    request: Request,
    q: str = Query(
        "", max_length=40, description="code/name fragment; empty returns hot symbols"
    ),
) -> APIResponse[dict[str, Any]]:
    """Unified symbol search across 5 markets → [{symbol, market, name}].

    Research-only symbol directory (redline 5). Each market is searched
    independently and degrades to an empty slice on upstream failure, so a
    slow/fragile source never 500s the whole endpoint.
    """
    q = q.strip()
    if not q:
        return _respond(request, {"query": "", "items": _HOT_SYMBOLS})

    items: list[dict[str, str]] = []

    # us — static curated pool in us_radar._UNIVERSE
    try:
        from app.api.v1.us_radar import _UNIVERSE

        for it in _match_code_name(
            q, ((s, info["name"]) for s, info in _UNIVERSE.items())
        )[:20]:
            items.append({"symbol": it["symbol"], "market": "us", "name": it["name"]})
    except Exception:
        logger.warning("us symbol search failed", exc_info=True)

    # ashare — akshare full A-list (memoized; slow first hit, degrade to [])
    try:
        for it in _ashare_search(q)[:20]:
            items.append(
                {"symbol": it["symbol"], "market": "ashare", "name": it["name"]}
            )
    except Exception:
        logger.warning("ashare symbol search failed", exc_info=True)

    # taiwan — FinMind TaiwanStockInfo (live; token/network fragile)
    try:
        for it in _taiwan_search(q)[:10]:
            items.append(
                {"symbol": it["symbol"], "market": "taiwan", "name": it["name"]}
            )
    except Exception:
        logger.warning("taiwan symbol search failed", exc_info=True)

    # commodities / crypto — static pools
    for it in _match_code_name(q, _COMMODITY_NAMES.items())[:10]:
        items.append(
            {"symbol": it["symbol"], "market": "commodities", "name": it["name"]}
        )
    for it in _match_code_name(q, _CRYPTO_NAMES.items())[:10]:
        items.append({"symbol": it["symbol"], "market": "crypto", "name": it["name"]})

    return _respond(request, {"query": q, "items": items})
