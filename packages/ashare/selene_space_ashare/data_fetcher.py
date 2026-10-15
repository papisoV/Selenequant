"""
AKShare-based A-share data fetcher with local caching.

Fetches: daily klines, fundamental indicators, money flow, analyst ratings.
All data cached as Parquet files for incremental updates.

Requires [cn] optional dependencies: akshare, requests.
"""

# Timeout-isolation pattern absorbed from daily_stock_analysis (MIT License)
import logging
import multiprocessing
import os
import pickle
import queue
import random
import threading
import time
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)

# akshare 内部用 requests 抓网页,可能无限卡死(线程级 timeout 杀不掉);
# 子进程隔离 + 硬超时是唯一可靠手段。env 可调,默认 30s。
_FETCH_TIMEOUT_DEFAULT = 30.0
_FETCH_TIMEOUT_ENV = "SELENE_ASHARE_FETCH_TIMEOUT_SEC"

CACHE_DIR = Path(__file__).parent.parent / "data_cache"

# 指数缓存判 stale 的天数阈值(取 10 > 最长法定假期 8 天,防长假误触发全量重拉)
_INDEX_STALE_DAYS = 10

# Eastmoney 资金流 API(akshare 内部用 requests/urllib3,部分网络被 WAF 连接级重置;
# httpx 传输层通过率高得多 —— 2026-08-15 本机实测:同秒内 requests RemoteDisconnected, httpx 200)
_EASTMONEY_FFLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_EASTMONEY_FFLOW_UT = "b2884a393a59ad64002292a3e90d46a5"
_EASTMONEY_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_EASTMONEY_MARKET = {"sh": 1, "sz": 0, "bj": 0}

# 东财防封节流：串行最小间隔 + 抖动（风控阈值实测：>5次/s、并发≥10、
# 1min≥200、5min≥300 → 封 IP）。
# 全部东财直连入口共用一个节流点，批量拉取（300 只 fund flow）是头号封禁场景。
_EM_MIN_INTERVAL = float(os.environ.get("EM_MIN_INTERVAL", "1.0"))
_em_last_request_at = 0.0
_throttle_lock = threading.Lock()


def _em_throttle() -> None:
    """Block until at least _EM_MIN_INTERVAL (+ jitter) since the last EM call."""
    global _em_last_request_at
    with _throttle_lock:
        wait = _EM_MIN_INTERVAL * (1.0 + random.uniform(0.1, 0.5)) - (
            time.monotonic() - _em_last_request_at
        )
        if wait > 0:
            time.sleep(wait)
        _em_last_request_at = time.monotonic()


# 与 akshare stock_individual_fund_flow 的字段顺序一致(f51..f65),保证 API 契约不变
_FFLOW_COLS = [
    "日期",
    "主力净流入-净额",
    "小单净流入-净额",
    "中单净流入-净额",
    "大单净流入-净额",
    "超大单净流入-净额",
    "主力净流入-净占比",
    "小单净流入-净占比",
    "中单净流入-净占比",
    "大单净流入-净占比",
    "超大单净流入-净占比",
    "收盘价",
    "涨跌幅",
    "-",
    "-",
]
_FFLOW_KEEP = [
    "日期",
    "收盘价",
    "涨跌幅",
    "主力净流入-净额",
    "主力净流入-净占比",
    "超大单净流入-净额",
    "超大单净流入-净占比",
    "大单净流入-净额",
    "大单净流入-净占比",
    "中单净流入-净额",
    "中单净流入-净占比",
    "小单净流入-净额",
    "小单净流入-净占比",
]


class FetchTimeoutError(TimeoutError):
    """Raised when an isolated akshare call exceeds its hard timeout."""


def _fetch_timeout_seconds(timeout: float | None = None) -> float:
    if timeout is not None:
        return float(timeout)
    raw = os.environ.get(_FETCH_TIMEOUT_ENV, "")
    try:
        return float(raw) if raw else _FETCH_TIMEOUT_DEFAULT
    except ValueError:
        return _FETCH_TIMEOUT_DEFAULT


def _call_with_no_proxy(
    fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    """Run one fetch with requests.Session.trust_env disabled (proxy-free).

    Runs inside the isolated child (or inline for unpicklable fns); the
    parent's Session patch cannot cross the spawn boundary.
    """
    import requests

    original_init = requests.Session.__init__

    def _no_proxy_init(self, *args2, **kw2):
        original_init(self, *args2, **kw2)
        self.trust_env = False

    requests.Session.__init__ = _no_proxy_init
    try:
        return fn(*args, **kwargs)
    finally:
        requests.Session.__init__ = original_init


def _child_entry(
    fn: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    result_q: Any,
) -> None:
    """Child-process shim: put (status, payload) on the queue."""
    try:
        result_q.put(("ok", _call_with_no_proxy(fn, args, kwargs)))
    except Exception as exc:  # noqa: BLE001 - propagated to parent via pickle
        result_q.put(("err", exc))


def run_with_timeout(
    fn: Callable[..., Any],
    args: tuple[Any, ...] = (),
    kwargs: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> Any:
    """Run fn(*args, **kwargs) in a spawned subprocess with a hard timeout.

    akshare internally uses requests against web endpoints and can hang
    forever; a thread-level timeout cannot kill that, so the call is
    isolated in a child process. On timeout the child is terminated and
    FetchTimeoutError raised — the parent never hangs. Functions that are
    not picklable (e.g. monkeypatched stubs in tests) run inline in-process.
    """
    kw = dict(kwargs or {})
    limit = _fetch_timeout_seconds(timeout)
    try:
        pickle.dumps(fn)
        pickle.dumps(args)
        pickle.dumps(kw)
    except Exception:
        return fn(*args, **kw)

    ctx = multiprocessing.get_context("spawn")
    result_q: multiprocessing.Queue[Any] = ctx.Queue()
    proc = ctx.Process(target=_child_entry, args=(fn, args, kw, result_q))
    proc.daemon = True
    proc.start()
    try:
        status, payload = result_q.get(timeout=limit)
    except queue.Empty:
        proc.terminate()
        proc.join(timeout=5)
        raise FetchTimeoutError(
            f"{getattr(fn, '__name__', repr(fn))} timed out after {limit:.0f}s"
        ) from None
    finally:
        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=5)
    if status == "err":
        raise payload
    return payload


def _ensure_cache(subdir: str = "") -> Path:
    d = CACHE_DIR / subdir if subdir else CACHE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(name: str, subdir: str = "") -> Path:
    return _ensure_cache(subdir) / f"{name}.parquet"


def _cache_age_days(cache_name: str, subdir: str) -> int:
    return (
        datetime.now()
        - datetime.fromtimestamp(_cache_path(cache_name, subdir).stat().st_mtime)
    ).days


def _read_cache(name: str, subdir: str = "") -> pd.DataFrame | None:
    p = _cache_path(name, subdir)
    if p.exists():
        return pd.read_parquet(p)
    return None


def _write_cache(df: pd.DataFrame, name: str, subdir: str = "") -> None:
    p = _cache_path(name, subdir)
    df.to_parquet(p, index=False)


def _fetch_with_retry(func, retries: int = 3, delay: float = 1.0, **kwargs):
    """Fetch with retry + subprocess hard-timeout isolation.

    Returns None on failure instead of raising. Each attempt runs in a
    spawned child (see run_with_timeout); akshare can hang forever inside
    requests, and only killing the child process reliably breaks that.
    """
    for i in range(retries):
        try:
            return run_with_timeout(func, kwargs=kwargs)
        except FetchTimeoutError:
            logger.warning(
                "fetch %s timed out (attempt %d/%d)",
                getattr(func, "__name__", repr(func)),
                i + 1,
                retries,
            )
        except Exception:
            if i == retries - 1:
                return None
        if i < retries - 1:
            time.sleep(delay * (i + 1))
    return None


# ─── Stock Universe ────────────────────────────────────────────────


def get_a_share_list(force: bool = False) -> pd.DataFrame:
    """Get all A-share stock list with basic info."""
    import akshare as ak

    cache_name = "ashare_list"
    if not force:
        cached = _read_cache(cache_name, "universe")
        if cached is not None and not cached.empty:
            cache_age = (
                datetime.now()
                - datetime.fromtimestamp(
                    _cache_path(cache_name, "universe").stat().st_mtime
                )
            ).days
            if cache_age < 7:
                return cached

    df = _fetch_with_retry(ak.stock_info_a_code_name)
    df.columns = ["code", "name"]
    df = df[~df["code"].str.startswith(("N", "^"))]
    df = df[df["code"].str.match(r"^\d{6}$")]
    _write_cache(df, cache_name, "universe")
    return df


def get_hs300_constituents(force: bool = False) -> pd.DataFrame:
    """Get CSI 300 constituents as DataFrame[code, name]."""
    import akshare as ak

    cache_name = "hs300"
    if not force:
        cached = _read_cache(cache_name, "universe")
        if cached is not None and not cached.empty:
            cache_age = (
                datetime.now()
                - datetime.fromtimestamp(
                    _cache_path(cache_name, "universe").stat().st_mtime
                )
            ).days
            if cache_age < 7:
                return cached

    raw = _fetch_with_retry(ak.index_stock_cons_csindex, symbol="000300")
    if raw is None or raw.empty:
        logger.warning("hs300 constituents fetch failed, returning empty DataFrame")
        return pd.DataFrame(columns=["code", "name"])

    df = raw.rename(columns={"成分券代码": "code", "成分券名称": "name"})
    df = df[df["code"].astype(str).str.match(r"^\d{6}$")][["code", "name"]]
    _write_cache(df, cache_name, "universe")
    return df.reset_index(drop=True)


def get_csi500_constituents(force: bool = False) -> pd.DataFrame:
    """Get CSI 500 constituents as DataFrame[code, name]."""
    import akshare as ak

    cache_name = "csi500"
    if not force:
        cached = _read_cache(cache_name, "universe")
        if cached is not None and not cached.empty:
            cache_age = (
                datetime.now()
                - datetime.fromtimestamp(
                    _cache_path(cache_name, "universe").stat().st_mtime
                )
            ).days
            if cache_age < 7:
                return cached

    raw = _fetch_with_retry(ak.index_stock_cons_csindex, symbol="000905")
    if raw is None or raw.empty:
        logger.warning("csi500 constituents fetch failed, returning empty DataFrame")
        return pd.DataFrame(columns=["code", "name"])

    df = raw.rename(columns={"成分券代码": "code", "成分券名称": "name"})
    df = df[df["code"].astype(str).str.match(r"^\d{6}$")][["code", "name"]]
    _write_cache(df, cache_name, "universe")
    return df.reset_index(drop=True)


def get_hs300_history() -> pd.DataFrame | None:
    """PIT historical constituents from universe/hs300_history.parquet.

    Schema: symbol, in_date, out_date (out_date NaT = still member).
    Returns None if the file does not exist (caller falls back to current
    constituents path — the D1a degraded behavior).
    """
    p = _cache_path("hs300_history", "universe")
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    required = {"symbol", "in_date", "out_date"}
    if not required.issubset(df.columns):
        logger.warning(
            "hs300_history.parquet missing required columns %s, ignoring",
            sorted(required),
        )
        return None
    return df


def filter_universe_by_history(history_df: pd.DataFrame, date_str: str) -> set[str]:
    """Symbols in the index as of date_str, from a PIT history DataFrame.

    Rule: in_date <= date < out_date (out_date NaT = still member).
    """
    d = pd.Timestamp(date_str)
    hist = history_df.copy()
    hist["in_date"] = pd.to_datetime(hist["in_date"], errors="coerce")
    hist["out_date"] = pd.to_datetime(hist["out_date"], errors="coerce")
    mask = (hist["in_date"] <= d) & (hist["out_date"].isna() | (hist["out_date"] > d))
    return set(hist.loc[mask, "symbol"].astype(str))


# ─── Daily Klines ───────���──────────────────────────────────────────


def get_daily_klines(
    symbol: str,
    start_date: str = "",
    end_date: str = "",
    adjust: str = "hfq",
    force: bool = False,
) -> pd.DataFrame:
    """Fetch daily klines for a single stock.

    Priority: 同花顺 direct API (8400+ days) → AKShare → sina fallback

    Args:
        symbol: 6-digit stock code e.g. "000001"
        start_date: YYYYMMDD format
        end_date: YYYYMMDD format
        adjust: "hfq" (后复权), "qfq" (前复权), "" (不复权)
    """
    try:
        from selene_space_ashare.ths_data import (
            fetch_klines_ths,
            fetch_klines_ths_all,
        )

        ths_df = fetch_klines_ths(
            symbol, period="daily", range_type="last", force=force
        )
        # "last" only returns ~140 bars; when the requested window extends
        # beyond cached coverage, fall back to year-by-year full history.
        if not ths_df.empty and start_date and end_date:
            ths_str = ths_df["date"].astype(str)
            covered = ((ths_str >= start_date) & (ths_str <= end_date)).sum()
            if covered < 200:
                full_df = fetch_klines_ths_all(symbol, force=force)
                if not full_df.empty:
                    full_df = full_df[
                        (full_df["date"].astype(str) >= start_date)
                        & (full_df["date"].astype(str) <= end_date)
                    ]
                    if len(full_df) > len(ths_df):
                        ths_df = full_df
        if not ths_df.empty:
            ths_df = ths_df.rename(columns={"turnover": "turnover_rate"})
            if "turnover_rate" in ths_df.columns and "turnover" not in ths_df.columns:
                ths_df["turnover"] = ths_df["turnover_rate"]
            return ths_df
    except Exception:
        pass

    import akshare as ak

    subdir = f"klines/{adjust}"
    cache_name = f"{symbol}"

    if not force:
        cached = _read_cache(cache_name, subdir)
        if cached is not None and not cached.empty:
            if not start_date or cached["date"].min() <= start_date:
                if not end_date or cached["date"].max() >= end_date:
                    return cached
            last_date = cached["date"].max()
            new_start = (pd.Timestamp(last_date) + timedelta(days=1)).strftime("%Y%m%d")
            if new_start > (end_date or datetime.now().strftime("%Y%m%d")):
                return cached

    if not start_date:
        start_date = "20200101"
    if not end_date:
        end_date = datetime.now().strftime("%Y%m%d")

    df = _fetch_with_retry(
        ak.stock_zh_a_hist,
        symbol=symbol,
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust=adjust,
    )

    if df is None or df.empty:
        prefix = "sz" if symbol.startswith(("0", "3")) else "sh"
        df = _fetch_with_retry(
            ak.stock_zh_a_daily,
            symbol=f"{prefix}{symbol}",
            start_date=start_date,
            end_date=end_date,
            adjust=adjust,
        )
        if df is not None and not df.empty:
            df["code"] = symbol
            df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
            _write_cache(df, cache_name, subdir)
            return df
        return pd.DataFrame()

    df.columns = [c.strip() for c in df.columns]
    col_map = {
        "日期": "date",
        "开盘": "open",
        "收盘": "close",
        "最高": "high",
        "最低": "low",
        "成交量": "volume",
        "成交额": "amount",
        "换手率": "turnover",
        "涨跌幅": "pct_change",
    }
    df = df.rename(columns=col_map)
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
    df["code"] = symbol

    _write_cache(df, cache_name, subdir)
    return df


def batch_daily_klines(
    symbols: list[str],
    start_date: str = "",
    end_date: str = "",
    adjust: str = "hfq",
    delay: float = 0.3,
) -> dict[str, pd.DataFrame]:
    """Fetch daily klines for multiple stocks with rate limiting."""
    results = {}
    total = len(symbols)
    for i, sym in enumerate(symbols):
        try:
            df = get_daily_klines(sym, start_date, end_date, adjust)
            if not df.empty:
                results[sym] = df
            if (i + 1) % 20 == 0:
                print(f"  [{i + 1}/{total}] Fetched klines for {len(results)} stocks")
        except Exception as e:
            print(f"  Failed {sym}: {e}")
        time.sleep(delay)
    return results


# ─── Fundamental Data ───────��──────────────────────────────────────


def get_stock_pe_pb(symbol: str, force: bool = False) -> pd.DataFrame:
    """Get daily PE/PB for a single stock."""
    import akshare as ak

    subdir = "fundamental/pe_pb"
    cache_name = f"{symbol}"

    if not force:
        cached = _read_cache(cache_name, subdir)
        if cached is not None and not cached.empty:
            return cached

    df = _fetch_with_retry(
        ak.stock_a_indicator_lg,
        symbol=symbol,
    )
    if df is None or df.empty:
        return pd.DataFrame()

    df = df.rename(columns={"trade_date": "date", "pe_ttm": "pe_ttm", "pb": "pb"})
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
    df["code"] = symbol
    cols = ["code", "date", "pe_ttm", "pb"]
    available = [c for c in cols if c in df.columns]
    df = df[available]

    _write_cache(df, cache_name, subdir)
    return df


def get_valuation_snapshot(force: bool = False) -> pd.DataFrame:
    """Get A-share valuation snapshot (PE/PB/ROE etc.) for all stocks."""
    import akshare as ak

    cache_name = "valuation_snapshot"

    if not force:
        cached = _read_cache(cache_name, "fundamental")
        if cached is not None and not cached.empty:
            cache_age = (
                datetime.now()
                - datetime.fromtimestamp(
                    _cache_path(cache_name, "fundamental").stat().st_mtime
                )
            ).days
            if cache_age < 1:
                return cached

    df = _fetch_with_retry(ak.stock_a_indicator_all)
    if df is None or df.empty:
        return pd.DataFrame()

    _write_cache(df, cache_name, "fundamental")
    return df


# ─── Money Flow ────────────────────────────────────────────────────


def get_northbound_flow(force: bool = False) -> pd.DataFrame:
    """Get northbound (北向资金) daily flow data."""
    import akshare as ak

    cache_name = "northbound_daily"

    if not force:
        cached = _read_cache(cache_name, "money_flow")
        if cached is not None and not cached.empty:
            return cached

    # akshare 1.18.55 移除了 stock_hsgt_north_net_flow_in_em,改为 stock_hsgt_hist_em
    df = _fetch_with_retry(ak.stock_hsgt_hist_em)
    if df is None or df.empty:
        return pd.DataFrame()
    if "日期" in df.columns:
        df = df.rename(columns={"日期": "date"})
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")

    _write_cache(df, cache_name, "money_flow")
    return df


def _latest_trade_date(ak) -> str:
    """Latest trading day <= today (YYYYMMDD) via Sina trade calendar."""
    try:
        cal = run_with_timeout(ak.tool_trade_date_hist_sina)
        d = pd.to_datetime(cal["trade_date"]).dt.strftime("%Y%m%d")
        past = d[d <= datetime.now().strftime("%Y%m%d")]
        if len(past):
            latest = str(past.iloc[-1])
            return latest
    except Exception:
        pass
    return datetime.now().strftime("%Y%m%d")


def get_margin_data(symbol: str, force: bool = False) -> pd.DataFrame:
    """Get margin trading (融资融券) data for a stock.

    akshare 1.18.55 移除了按 symbol 的 detail 函数;改用按日期的全市场
    detail(SSE/SZSE) 取最新交易日,再按标的证券代码过滤。
    """
    import akshare as ak

    subdir = "money_flow/margin"
    cache_name = f"{symbol}"

    if not force:
        cached = _read_cache(cache_name, subdir)
        if cached is not None and not cached.empty:
            return cached

    market_fn = (
        ak.stock_margin_detail_sse
        if symbol.startswith(("6", "9"))
        else ak.stock_margin_detail_szse
    )
    df = _fetch_with_retry(market_fn, date=_latest_trade_date(ak))
    if df is None or df.empty:
        return pd.DataFrame()
    df = df[df["标的证券代码"].astype(str) == symbol]

    _write_cache(df, cache_name, subdir)
    return df


# ─── Analyst Ratings ───────────────────────────────────────────────


def get_analyst_ratings(force: bool = False) -> pd.DataFrame:
    """Get latest analyst ratings/consensus for A-shares."""
    import akshare as ak

    cache_name = "analyst_ratings"

    if not force:
        cached = _read_cache(cache_name, "analyst")
        if cached is not None and not cached.empty:
            cache_age = (
                datetime.now()
                - datetime.fromtimestamp(
                    _cache_path(cache_name, "analyst").stat().st_mtime
                )
            ).days
            if cache_age < 3:
                return cached

    df = _fetch_with_retry(ak.stock_rank_forecast_cninfo)
    if df is None or df.empty:
        return pd.DataFrame()

    _write_cache(df, cache_name, "analyst")
    return df


# ─── Index Data ────────────────────────────────────────────────────


def get_index_daily(symbol: str = "000300", force: bool = False) -> pd.DataFrame:
    """Get index daily data (default: CSI 300)."""
    import akshare as ak

    subdir = "index"
    cache_name = f"{symbol}"

    if not force:
        cached = _read_cache(cache_name, subdir)
        if cached is not None and not cached.empty:
            cache_max_date = str(cached["date"].max())
            stale_after = (datetime.now() - timedelta(days=_INDEX_STALE_DAYS)).strftime(
                "%Y%m%d"
            )
            if cache_max_date >= stale_after:
                return cached
            logger.warning(
                f"index {symbol} cache stale (max={cache_max_date}), refetching"
            )

    df = _fetch_with_retry(
        ak.index_zh_a_hist,
        symbol=symbol,
        period="daily",
    )
    if df is not None and not df.empty:
        col_map = {
            "日期": "date",
            "开盘": "open",
            "收盘": "close",
            "最高": "high",
            "最低": "low",
            "成交量": "volume",
        }
        df = df.rename(columns=col_map)
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
        _write_cache(df, cache_name, subdir)
        return df

    prefix = "sh" if symbol.startswith(("000", "9")) else "sz"
    df = _fetch_with_retry(
        ak.stock_zh_index_daily,
        symbol=f"{prefix}{symbol}",
    )
    if df is not None and not df.empty:
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
        _write_cache(df, cache_name, subdir)
        return df

    if not force and cached is not None and not cached.empty:
        logger.warning(
            f"index {symbol} cache stale (max={cache_max_date}), "
            "refresh failed, serving stale data"
        )
        return cached
    return pd.DataFrame()


# ─── Money Flow Detail & LHB ────────────────────────────────────────


def _fetch_fund_flow_http(stock: str, market: str) -> pd.DataFrame:
    """Fetch Eastmoney individual money-flow via httpx (trust_env=False, proxy-free).

    Replicates akshare ``stock_individual_fund_flow`` request+parse; the akshare
    requests/urllib3 transport is TCP-reset by Eastmoney WAF on some networks.
    """
    import httpx

    params: dict[str, str | int] = {
        "lmt": "0",
        "klt": "101",
        "secid": f"{_EASTMONEY_MARKET[market]}.{stock}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "ut": _EASTMONEY_FFLOW_UT,
        "_": int(time.time() * 1000),
    }
    with httpx.Client(
        trust_env=False,
        timeout=httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0),
        headers={"User-Agent": _EASTMONEY_UA},
    ) as client:
        _em_throttle()
        resp = client.get(_EASTMONEY_FFLOW_URL, params=params)
    resp.raise_for_status()
    data = resp.json().get("data") or {}
    rows = [k.split(",") for k in data.get("klines") or []]
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df.columns = _FFLOW_COLS[: df.shape[1]]
    return df[[c for c in _FFLOW_KEEP if c in df.columns]]


def get_fund_flow(symbol: str, start: str = "", end: str = "") -> pd.DataFrame:
    """Get individual stock money-flow detail (主力/超大单/大单/中单/小单).

    Primary transport: httpx direct to Eastmoney (WAF passes httpx, resets
    requests/urllib3 on this network). Falls back to akshare (requests) where
    the WAF allows it. Rows outside [start, end] (YYYYMMDD) are dropped.
    """
    import akshare as ak

    if not start:
        start = "20200101"
    if not end:
        end = datetime.now().strftime("%Y%m%d")

    market = "sh" if symbol.startswith(("6", "9")) else "sz"
    df = _fetch_with_retry(_fetch_fund_flow_http, stock=symbol, market=market)
    if df is None or df.empty:
        df = _fetch_with_retry(
            ak.stock_individual_fund_flow, stock=symbol, market=market
        )
    if df is None or df.empty:
        return pd.DataFrame()

    if "日期" in df.columns:
        df = df.rename(columns={"日期": "date"})
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y%m%d")
        df = df[(df["date"] >= start) & (df["date"] <= end)]
    df["code"] = symbol
    return df


def get_lhb(date: str = "") -> pd.DataFrame:
    """Get 龙虎榜 (Dragon-Tiger list) for a single trading day.

    Wraps ``ak.stock_lhb_detail_em`` (Eastmoney 龙虎榜详情). The detail API
    takes a date range; passing the same date for both bounds yields one day.
    """
    import akshare as ak

    if not date:
        date = datetime.now().strftime("%Y%m%d")

    df = _fetch_with_retry(ak.stock_lhb_detail_em, start_date=date, end_date=date)
    if df is None or df.empty:
        return pd.DataFrame()
    return df


# ─── Eastmoney Industry Map ────────────────────────────────────────
# Same transport constraint as fund flow above: requests/urllib3 dies behind
# the local proxy, httpx with trust_env=False passes. Primary path is the
# datacenter valuation report (RPT_VALUEANALYSIS_DET BOARD_NAME = EM industry
# label): one date-filtered page covers 500 stocks, ~12 requests for the full
# market — push2 clist/ulist endpoints rate-limit aggressively on bursts
# (2026-09-27 本机实测: 40+ 快速请求 → RemoteDisconnected, 10min 不解封).
# Fallback: akshare board enumeration (requests transport).

_EM_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_EM_INDUSTRY_REPORT = "RPT_VALUEANALYSIS_DET"


def _em_datacenter_get(params: dict[str, str]) -> list[dict] | None:
    """One throttled datacenter GET; returns result.data rows or None."""
    import httpx

    try:
        _em_throttle()
        r = httpx.get(
            _EM_DATACENTER_URL,
            params=params,
            headers={"User-Agent": _EASTMONEY_UA},
            timeout=15,
            trust_env=False,
        )
        r.raise_for_status()
        return ((r.json() or {}).get("result") or {}).get("data") or []
    except Exception:
        return None


def _em_industry_page(page: int, trade_date: str) -> list[dict] | None:
    """One 500-row page of (symbol, industry) from the datacenter report."""
    rows = _em_datacenter_get(
        {
            "reportName": _EM_INDUSTRY_REPORT,
            "columns": "SECURITY_CODE,BOARD_NAME",
            "pageSize": "500",
            "pageNumber": str(page),
            "filter": f"(TRADE_DATE='{trade_date}')",
            "sortColumns": "SECURITY_CODE",
            "sortTypes": "1",
        }
    )
    if rows is None:
        return None
    out = []
    for row in rows:
        code = str(row.get("SECURITY_CODE", "")).strip()
        industry = str(row.get("BOARD_NAME", "") or "").strip()
        if code and industry:
            out.append({"symbol": code, "industry": industry})
    return out


def _em_latest_trade_date() -> str | None:
    """Latest TRADE_DATE present in the valuation report (YYYY-MM-DD)."""
    rows = _em_datacenter_get(
        {
            "reportName": _EM_INDUSTRY_REPORT,
            "columns": "TRADE_DATE",
            "pageSize": "1",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
        }
    )
    if rows and rows[0].get("TRADE_DATE"):
        return str(rows[0]["TRADE_DATE"])[:10]
    return None


def fetch_industry_map(force: bool = False) -> pd.DataFrame:
    """Eastmoney industry membership -> DataFrame[symbol, industry].

    Primary: datacenter RPT_VALUEANALYSIS_DET BOARD_NAME pages (~12 requests,
    throttled, latest completed trade date). Fallback: akshare board
    enumeration (stock_board_industry_name_em + cons_em). Cached as parquet,
    reused within 7 days.
    """
    cache_name = "industry_map_em"
    if not force:
        cached = _read_cache(cache_name, "universe")
        if (
            cached is not None
            and not cached.empty
            and _cache_age_days(cache_name, "universe") < 7
        ):
            return cached

    frames: list[pd.DataFrame] = []
    fallback_failures = 0

    trade_date = _em_latest_trade_date()

    if trade_date:
        for page in range(1, 20):
            rows = _em_industry_page(page, trade_date)
            if not rows:
                break
            frames.append(pd.DataFrame(rows))
            if len(rows) < 500:
                break
            time.sleep(0.3)

    if frames:
        logger.info(
            "industry map via datacenter: %d rows (date %s)",
            sum(len(f) for f in frames),
            trade_date,
        )
    else:
        import akshare as ak

        logger.warning("datacenter industry path failed, trying akshare board fallback")
        boards = _fetch_with_retry(ak.stock_board_industry_name_em)
        if boards is None or boards.empty or "板块名称" not in boards.columns:
            logger.warning("industry board list fetch failed, returning empty map")
            return pd.DataFrame(columns=["symbol", "industry"])
        for name in boards["板块名称"]:
            cons = _fetch_with_retry(ak.stock_board_industry_cons_em, symbol=str(name))
            if cons is None or cons.empty or "代码" not in cons.columns:
                fallback_failures += 1
                continue
            codes = [str(x).strip() for x in cons["代码"] if str(x).strip()]
            if codes:
                frames.append(pd.DataFrame({"symbol": codes, "industry": str(name)}))
            time.sleep(0.3)
        if frames and fallback_failures:
            logger.warning("industry map fallback: %d boards failed", fallback_failures)

    if not frames:
        logger.warning("no industry data fetched, returning empty map")
        return pd.DataFrame(columns=["symbol", "industry"])

    df = pd.concat(frames, ignore_index=True).drop_duplicates(subset="symbol")
    logger.info("industry map: %d pages/samples, %d symbols", len(frames), len(df))
    _write_cache(df, cache_name, "universe")
    return df


# ─── Eastmoney Market Cap (SIZE direct path) ───────────────────────
# Same datacenter report as the industry map, pulling the valuation
# columns instead of BOARD_NAME. RPT_VALUEANALYSIS_DET carries daily
# history per symbol (~8.5y, 2026-09-27 audit): IN-filter + date-range
# pages for symbol lists, single-date full-market pages for snapshots.

_MC_EMPTY = pd.DataFrame(
    columns=["symbol", "date", "float_mv", "total_mv", "float_shares"]
)


def _norm_em_date(d: str) -> str:
    """YYYYMMDD or YYYY-MM-DD -> YYYY-MM-DD (EM datacenter filter format)."""
    s = str(d).strip().replace("/", "-")
    if len(s) == 8 and s.isdigit():
        s = f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return s[:10]


def _em_mktcap_page(filter_expr: str, page: int) -> list[dict] | None:
    """One 500-row page of valuation rows for the given filter expression."""
    rows = _em_datacenter_get(
        {
            "reportName": _EM_INDUSTRY_REPORT,
            "columns": (
                "SECURITY_CODE,TRADE_DATE,NOTLIMITED_MARKETCAP_A,"
                "TOTAL_MARKET_CAP,FREE_SHARES_A"
            ),
            "pageSize": "500",
            "pageNumber": str(page),
            "filter": filter_expr,
            "sortColumns": "SECURITY_CODE,TRADE_DATE",
            "sortTypes": "1,1",
        }
    )
    if rows is None:
        return None
    out: list[dict] = []
    for row in rows:
        code = str(row.get("SECURITY_CODE", "") or "").strip()
        date = str(row.get("TRADE_DATE", "") or "")[:10]
        if not code or not date:
            continue
        out.append(
            {
                "symbol": code,
                "date": date,
                "float_mv": row.get("NOTLIMITED_MARKETCAP_A"),
                "total_mv": row.get("TOTAL_MARKET_CAP"),
                "float_shares": row.get("FREE_SHARES_A"),
            }
        )
    return out


def _em_mktcap_rows(filter_expr: str, max_pages: int) -> tuple[list[dict], int, bool]:
    """Page through all rows for a filter. Returns (rows, n_requests, complete).

    complete=False means a page failed or max_pages was hit — partial rows
    are still returned so callers can warn-and-continue.
    """
    rows_out: list[dict] = []
    n_req = 0
    for page in range(1, max_pages + 1):
        rows = _em_mktcap_page(filter_expr, page)
        n_req += 1
        if rows is None:
            time.sleep(1.0)
            rows = _em_mktcap_page(filter_expr, page)
            n_req += 1
        if rows is None:
            return rows_out, n_req, False
        rows_out.extend(rows)
        if len(rows) < 500:
            return rows_out, n_req, True
        time.sleep(0.2)
    return rows_out, n_req, False


def _mktcap_frame(rows: list[dict]) -> pd.DataFrame:
    """Coerce raw EM rows to the typed market-cap frame (dedup symbol+date)."""
    if not rows:
        return _MC_EMPTY.copy()
    df = pd.DataFrame(rows)
    for col in ("float_mv", "total_mv", "float_shares"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return (
        df.drop_duplicates(subset=["symbol", "date"])
        .sort_values(["symbol", "date"])
        .reset_index(drop=True)
    )


_MC_PAGE_ROWS = 500
_MC_MAX_PAGES = 40
_MC_MAX_SPLIT_DEPTH = 3


def _date_window_midpoint(start_n: str, end_n: str) -> str:
    """Midpoint of a closed YYYY-MM-DD window (floor to whole day)."""
    d0 = datetime.strptime(start_n, "%Y-%m-%d")
    d1 = datetime.strptime(end_n, "%Y-%m-%d")
    return (d0 + (d1 - d0) / 2).strftime("%Y-%m-%d")


def _shift_em_date(d_n: str, days: int) -> str:
    return (datetime.strptime(d_n, "%Y-%m-%d") + timedelta(days=days)).strftime(
        "%Y-%m-%d"
    )


def _split_date_window(
    start_n: str, end_n: str
) -> tuple[tuple[str, str], tuple[str, str]] | None:
    """Split a date window into two halves for recursive sub-fetches.

    Closed bounds split at the midpoint (rows on the mid date land in both
    halves; _mktcap_frame dedups). An open bound keeps its open side and
    splits at the known bound pushed 365 days inward, so each half is
    strictly narrower than the original (recursion terminates) while the
    far side stays covered by deeper splits. Returns None only for a fully
    open window, which cannot shrink — unreachable from fetch_market_cap
    (end_n defaults to today there).
    """
    if start_n and end_n:
        mid = _date_window_midpoint(start_n, end_n)
        return (start_n, mid), (mid, end_n)
    if end_n:
        split_at = _shift_em_date(end_n, -365)
        return ("", split_at), (split_at, end_n)
    if start_n:
        split_at = _shift_em_date(start_n, 365)
        return (start_n, split_at), (split_at, "")
    return None


def _fetch_mktcap_batch(
    batch_codes: list[str], start_n: str, end_n: str, depth: int = 0
) -> tuple[list[dict], int, bool]:
    """Fetch one symbol batch over [start_n, end_n], halving the window on
    max_pages truncation.

    complete=False + rows >= max_pages*page_size means the pager hit
    max_pages (a mid-page failure always returns fewer rows) — split the
    window and recurse. depth caps the recursion; past it we warn and
    return partial rows (warn-and-continue, caller excludes from cache).
    """
    date_expr = (f"(TRADE_DATE>='{start_n}')" if start_n else "") + (
        f"(TRADE_DATE<='{end_n}')" if end_n else ""
    )
    in_expr = (
        "(SECURITY_CODE in ("
        + ",".join(f'"{c}"' for c in batch_codes)
        + "))"
        + date_expr
    )
    rows, n_req, complete = _em_mktcap_rows(in_expr, max_pages=_MC_MAX_PAGES)
    if complete or len(rows) < _MC_PAGE_ROWS * _MC_MAX_PAGES:
        return rows, n_req, complete
    if depth >= _MC_MAX_SPLIT_DEPTH:
        logger.warning(
            "market cap batch truncated at split depth %d (%d symbols, "
            "window %s..%s, %d rows) — partial rows",
            depth,
            len(batch_codes),
            start_n or "-inf",
            end_n or "+inf",
            len(rows),
        )
        return rows, n_req, complete
    halves = _split_date_window(start_n, end_n)
    if halves is None:
        return rows, n_req, complete
    out: list[dict] = []
    n_req_total = n_req
    all_complete = True
    for h_start, h_end in halves:
        h_rows, h_req, h_complete = _fetch_mktcap_batch(
            batch_codes, h_start, h_end, depth + 1
        )
        out.extend(h_rows)
        n_req_total += h_req
        all_complete = all_complete and h_complete
    return out, n_req_total, all_complete


def fetch_market_cap(
    symbols: list[str] | None = None,
    start_date: str = "",
    end_date: str = "",
    force: bool = False,
) -> pd.DataFrame:
    """Daily float/total market cap from EM datacenter valuation report.

    symbols=None → full market (latest trade date snapshot only, ~12 pages).
    symbols=list → daily history for those codes over [start_date, end_date]
    (IN-filter pages, ~1 request per 500 rows; hs300×151d ≈ 91 pages).

    Returns DataFrame[symbol, date, float_mv, total_mv, float_shares].
    Cached as parquet; reused within 7 days (symbols+date-range keyed).
    """
    import hashlib

    if symbols is None:
        cache_name = "market_cap_snapshot"
        if not force:
            cached = _read_cache(cache_name, "universe")
            if (
                cached is not None
                and not cached.empty
                and _cache_age_days(cache_name, "universe") < 7
            ):
                return cached
        trade_date = _em_latest_trade_date()
        if not trade_date:
            logger.warning("fetch_market_cap: no latest trade date, returning empty")
            return _MC_EMPTY.copy()
        rows, n_req, complete = _em_mktcap_rows(
            f"(TRADE_DATE='{trade_date}')", max_pages=20
        )
        if not complete:
            logger.warning(
                "fetch_market_cap snapshot incomplete (%d requests) — partial rows, not cached",
                n_req,
            )
        df = _mktcap_frame(rows)
        logger.info(
            "market_cap snapshot: %d requests, %d rows (date %s)",
            n_req,
            len(df),
            trade_date,
        )
        if not df.empty and complete:
            _write_cache(df, cache_name, "universe")
        return df

    codes = sorted({str(s).strip() for s in symbols if str(s).strip()})
    if not codes:
        return _MC_EMPTY.copy()
    start_n = _norm_em_date(start_date) if start_date else ""
    end_n = (
        _norm_em_date(end_date)
        if end_date
        else _norm_em_date(datetime.now().strftime("%Y%m%d"))
    )
    digest = hashlib.sha1(",".join(codes).encode("utf-8")).hexdigest()[:8]
    cache_name = (
        f"market_cap_hist_{digest}_{start_n.replace('-', '') or 'min'}"
        f"_{end_n.replace('-', '') or 'max'}"
    )
    if not force:
        cached = _read_cache(cache_name, "universe")
        if (
            cached is not None
            and not cached.empty
            and _cache_age_days(cache_name, "universe") < 7
        ):
            return cached

    all_rows: list[dict] = []
    n_req_total = 0
    n_batches = (len(codes) + 99) // 100
    failed_batches: list[int] = []
    for bi in range(0, len(codes), 100):
        batch = codes[bi : bi + 100]
        rows, n_req, complete = _fetch_mktcap_batch(batch, start_n, end_n)
        n_req_total += n_req
        if not complete:
            failed_batches.append(bi // 100)
        all_rows.extend(rows)

    if failed_batches:
        logger.warning(
            "market cap history: %d/%d symbol batches incomplete %s — partial rows",
            len(failed_batches),
            n_batches,
            failed_batches,
        )
    df = _mktcap_frame(all_rows)
    logger.info(
        "market_cap history: %d requests, %d rows, %d symbols",
        n_req_total,
        len(df),
        df["symbol"].nunique() if not df.empty else 0,
    )
    if not df.empty and not failed_batches:
        _write_cache(df, cache_name, "universe")
    return df


# ─── Eastmoney D1 三件: 解禁 / 行业对比 / 龙虎榜席位明细 ────────────
# 免费 datacenter/push2 直连端点（RPT_LIFT_STAGE / RPT_DAILYBILLBOARD_* /
# RPT_BILLBOARD_DAILYDETAILSBUY，字段名已实测踩平）。全部走
# _em_datacenter_get / _em_throttle（防封），
# 上游失败返回空 DataFrame 不 raise（调用方按"无数据"处理）。


_LHB_EMPTY = pd.DataFrame(
    columns=["symbol", "date", "reason", "net_buy", "turnover_rate"]
)


def get_lockup_release(
    symbol: str,
    start: str = "",
    end: str = "",
) -> pd.DataFrame:
    """限售解禁批次（历史 + 区间内）for one symbol.

    EM datacenter ``RPT_LIFT_STAGE``。返回列:
    symbol, free_date, shares_type, free_shares, free_ratio。
    ``start``/``end`` (YYYYMMDD or YYYY-MM-DD) 过滤 FREE_DATE，缺省全历史。
    """
    code = str(symbol).strip()
    if not code:
        return pd.DataFrame(
            columns=["symbol", "free_date", "shares_type", "free_shares", "free_ratio"]
        )

    rows = _em_datacenter_get(
        {
            "reportName": "RPT_LIFT_STAGE",
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
            "pageSize": "100",
            "pageNumber": "1",
            "sortColumns": "FREE_DATE",
            "sortTypes": "1",
        }
    )
    if not rows:
        return pd.DataFrame(
            columns=["symbol", "free_date", "shares_type", "free_shares", "free_ratio"]
        )

    out = []
    for row in rows:
        free_date = str(row.get("FREE_DATE", ""))[:10]
        if not free_date:
            continue
        if start and free_date < _norm_em_date(start):
            continue
        if end and free_date > _norm_em_date(end):
            continue
        out.append(
            {
                "symbol": code,
                "free_date": free_date,
                "shares_type": str(row.get("LIMITED_STOCK_TYPE", "") or ""),
                "free_shares": pd.to_numeric(
                    row.get("FREE_SHARES_NUM"), errors="coerce"
                ),
                "free_ratio": pd.to_numeric(
                    str(row.get("FREE_RATIO", "")).replace("%", ""), errors="coerce"
                ),
            }
        )
    return pd.DataFrame(out)


_INDUSTRY_CMP_EMPTY = pd.DataFrame(
    columns=["rank", "industry", "change_pct", "up_count", "down_count", "leader"]
)
_CONCEPT_CMP_EMPTY = pd.DataFrame(
    columns=["rank", "concept", "change_pct", "up_count", "down_count", "leader"]
)

# 东财 push2 板块类型:行业=m:90+t:2,概念=m:90+t:3(与 A股行情/涨停侧互补)。
_BOARD_TYPE = {"industry": "m:90+t:2", "concept": "m:90+t:3"}


def _board_comparison(kind: str, top_n: int, name_key: str) -> pd.DataFrame:
    """东财板块当日表现排名(行业/概念共用实现,push2 clist)。

    返回列: rank, <name_key>, change_pct, up_count, down_count, leader。
    单请求 pz 覆盖全部板块,burst 风险低;仍走 _em_throttle。
    push2 限速教训(:668 注释):批量场景必须降速,勿并发。
    """
    import httpx

    fs = _BOARD_TYPE.get(kind)
    if fs is None:
        return _INDUSTRY_CMP_EMPTY.copy()

    try:
        _em_throttle()
        r = httpx.get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params={
                "pn": "1",
                "pz": str(max(100, int(top_n))),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": fs,
                "fields": "f3,f12,f14,f104,f105,f140",
            },
            headers={"User-Agent": _EASTMONEY_UA},
            timeout=15,
            trust_env=False,
        )
        r.raise_for_status()
        diff = ((r.json() or {}).get("data") or {}).get("diff") or {}
        items = list(diff.values()) if isinstance(diff, dict) else list(diff)
    except Exception:
        return _INDUSTRY_CMP_EMPTY.copy()

    out = []
    for i, item in enumerate(items[:top_n]):
        out.append(
            {
                "rank": i + 1,
                name_key: str(item.get("f14", "") or ""),
                "change_pct": pd.to_numeric(item.get("f3"), errors="coerce"),
                "up_count": pd.to_numeric(item.get("f104"), errors="coerce"),
                "down_count": pd.to_numeric(item.get("f105"), errors="coerce"),
                "leader": str(item.get("f140", "") or ""),
            }
        )
    return pd.DataFrame(out)


def get_concept_boards(top_n: int = 60) -> pd.DataFrame:
    """东财概念板块当日表现排名(push2 clist, m:90+t:3).

    返回列: rank, concept, change_pct, up_count, down_count, leader。
    行业用 :func:`get_industry_comparison`;两者共用 :func:`_board_comparison`。
    """
    return _board_comparison("concept", top_n, "concept")


def get_industry_comparison(top_n: int = 90) -> pd.DataFrame:
    """东财行业板块当日表现排名（push2 clist, m:90+t:2）.

    返回列: rank, industry, change_pct, up_count, down_count, leader。
    单请求 pz=100 覆盖全部行业（~90 个），burst 风险低；仍走 _em_throttle。
    push2 限速教训（:668 注释）：批量场景必须降速，勿并发。
    """
    import httpx

    try:
        _em_throttle()
        r = httpx.get(
            "https://push2.eastmoney.com/api/qt/clist/get",
            params={
                "pn": "1",
                "pz": str(max(100, int(top_n))),
                "po": "1",
                "np": "1",
                "fltt": "2",
                "invt": "2",
                "fs": "m:90+t:2",
                "fields": "f3,f12,f14,f104,f105,f140",
            },
            headers={"User-Agent": _EASTMONEY_UA},
            timeout=15,
            trust_env=False,
        )
        r.raise_for_status()
        diff = ((r.json() or {}).get("data") or {}).get("diff") or {}
        items = list(diff.values()) if isinstance(diff, dict) else list(diff)
    except Exception:
        return _INDUSTRY_CMP_EMPTY.copy()

    out = []
    for i, item in enumerate(items[:top_n]):
        out.append(
            {
                "rank": i + 1,
                "industry": str(item.get("f14", "") or ""),
                "change_pct": pd.to_numeric(item.get("f3"), errors="coerce"),
                "up_count": pd.to_numeric(item.get("f104"), errors="coerce"),
                "down_count": pd.to_numeric(item.get("f105"), errors="coerce"),
                "leader": str(item.get("f140", "") or ""),
            }
        )
    return pd.DataFrame(out)


def get_lhb_detail(
    symbol: str,
    look_back_days: int = 30,
    end_date: str = "",
) -> pd.DataFrame:
    """龙虎榜上榜记录 + 席位明细（东财 datacenter 直连）.

    返回列: date, reason, seat, seat_type(buy/sell), amount, net_amount,
    is_institution(OPERATEDEPT_CODE=="0" → 机构席位)。
    与既有 get_lhb（akshare 单日榜单）并列，不改其签名。
    """
    code = str(symbol).strip()
    empty = pd.DataFrame(
        columns=[
            "date",
            "reason",
            "seat",
            "seat_type",
            "amount",
            "net_amount",
            "is_institution",
        ]
    )
    if not code:
        return empty

    end_str = (
        _norm_em_date(end_date) if end_date else datetime.now().strftime("%Y-%m-%d")
    )
    start_str = (
        datetime.strptime(end_str, "%Y-%m-%d") - timedelta(days=look_back_days)
    ).strftime("%Y-%m-%d")

    board_rows = _em_datacenter_get(
        {
            "reportName": "RPT_DAILYBILLBOARD_DETAILSNEW",
            "columns": "ALL",
            "filter": (
                f"(TRADE_DATE>='{start_str}')(TRADE_DATE<='{end_str}')"
                f'(SECURITY_CODE="{code}")'
            ),
            "pageSize": "50",
            "pageNumber": "1",
            "sortColumns": "TRADE_DATE",
            "sortTypes": "-1",
        }
    )
    if not board_rows:
        return empty

    latest_date = str(board_rows[0].get("TRADE_DATE", ""))[:10]
    out = []
    for row in board_rows:
        out.append(
            {
                "date": str(row.get("TRADE_DATE", ""))[:10],
                "reason": str(row.get("EXPLANATION", "") or ""),
                "seat": "",
                "seat_type": "board",
                "amount": pd.to_numeric(row.get("BILLBOARD_BUY_AMT"), errors="coerce"),
                "net_amount": pd.to_numeric(
                    row.get("BILLBOARD_NET_AMT"), errors="coerce"
                ),
                "is_institution": False,
            }
        )

    for seat_type, report, sort_col in (
        ("buy", "RPT_BILLBOARD_DAILYDETAILSBUY", "BUY"),
        ("sell", "RPT_BILLBOARD_DAILYDETAILSSELL", "SELL"),
    ):
        seat_rows = _em_datacenter_get(
            {
                "reportName": report,
                "columns": "ALL",
                "filter": f"(TRADE_DATE='{latest_date}')(SECURITY_CODE=\"{code}\")",
                "pageSize": "10",
                "pageNumber": "1",
                "sortColumns": sort_col,
                "sortTypes": "-1",
            }
        )
        for row in seat_rows or []:
            dept_code = str(row.get("OPERATEDEPT_CODE", "") or "")
            out.append(
                {
                    "date": latest_date,
                    "reason": str(row.get("EXPLANATION", "") or ""),
                    "seat": str(row.get("OPERATEDEPT_NAME", "") or ""),
                    "seat_type": seat_type,
                    "amount": pd.to_numeric(row.get(sort_col), errors="coerce"),
                    "net_amount": pd.to_numeric(row.get("NET"), errors="coerce"),
                    "is_institution": dept_code == "0",
                }
            )
    return pd.DataFrame(out)
