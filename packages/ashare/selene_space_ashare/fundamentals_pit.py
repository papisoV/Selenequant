"""Point-in-Time financial statements — eastmoney datacenter API (primary),
baostock (fallback; currently unusable from this machine, see
baostock_smoke_result.txt).

Every record carries pubDate (announcement date) — factors take effect on the
announcement date, not the report period, to avoid look-ahead bias.

Both backends write the same cache format ({"done": ["sym|y|q"], "records"}),
so attach_fundamentals and the resume logic are backend-agnostic. Percentage
fields are normalized to decimals (baostock convention) at the fetch boundary.

baostock is an optional dependency (imported lazily inside functions). When
unavailable the fetch returns [] with a warning; attach_fundamentals fills
NaN columns so downstream pipelines keep working.
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data_cache"
)
CACHE_PATH = os.path.join(
    CACHE_DIR, "financials_v3.json"
)  # v3: pubDate semantics fixed (v2 done-keys would block refetch)

QUARTERS_FIRST = (2022, 4)


def _derive_quarters(today: date | None = None) -> list[tuple[int, int]]:
    """Quarters from QUARTERS_FIRST through the latest likely-announced one.

    Announcement-lag calendar (conservative): a quarter is only included once
    its report plausibly exists, so we never request an unannounced quarter
    (empty result would be permanently marked done by the cache).
    """
    d = today or date.today()
    if d.month <= 3:
        last = (d.year - 1, 3)
    elif d.month <= 8:
        last = (d.year, 1)
    elif d.month <= 10:
        last = (d.year, 2)
    else:
        last = (d.year, 3)
    out: list[tuple[int, int]] = []
    y, q = QUARTERS_FIRST
    while (y, q) <= last:
        out.append((y, q))
        q += 1
        if q == 5:
            y, q = y + 1, 1
    return out


QUARTERS = _derive_quarters()

DATASETS = {
    "query_profit_data": [
        "roeAvg",
        "npMargin",
        "gpMargin",
        "netProfit",
        "epsTTM",
        "totalShare",
        "liqaShare",
    ],
    "query_growth_data": ["YOYNI", "YOYEPSBasic", "YOYEquity", "YOYAsset"],
    "query_cash_flow_data": ["CFOToOR", "CFOToNP", "CFOToGr"],
    # ponytail/NOTE: 字段名据 baostock 官方文档推导（客户端源码无静态字段表，
    # 字段由服务端运行时下发；本机 18 次 login 均被服务端 FIN 挂断无法实拉，
    # 见 baostock_smoke_result.txt）。query_balance_data 是偿债能力比率表，
    # 无净资产绝对值字段 → BP 季频绝对值口径不可行，应改道 1/pbMRQ
    # （原项目先例: expression_factors.py:171，1/PB 数学等价于 BP）。
    # login 恢复后跑 pytest -m network smoke 验证字段清单。
    "query_balance_data": [
        "currentRatio",
        "quickRatio",
        "cashRatio",
        "YOYLiability",
        "liabilityToAsset",
        "assetEquity",
    ],
}

FUND_COLS = [
    "roeAvg",
    "gpMargin",
    "npMargin",
    "CFOToOR",
    "CFOToNP",
    "YOYNI",
    "YOYEPSBasic",
    "YOYEquity",
    "netProfit",
    "epsTTM",
    "totalShare",
    "liqaShare",
    # ponytail/NOTE: query_balance_data 为偿债比率表（无净资产绝对值），
    # assetEquity=权益比率可作杠杆因子；BP 走 1/pbMRQ 日频口径
    "assetEquity",
    # eastmoney backend: BPS (每股净资产) — derive pbMRQ = close/BPS at the
    # attach site to activate the BP factor (ashare.py pbMRQ branch)
    "BPS",
    # eastmoney balance-side leverage fields (batch4: LEVERAGE/CURRENT_RATIO
    # passthrough factors); absent from baostock DATASETS is fine — NaN fill
    "liabilityToAsset",
    "currentRatio",
]

# baostock backend pacing (fallback path)
FETCH_SLEEP_SECONDS = 0.08
SAVE_EVERY_N = 40

# ─── eastmoney backend ──────────────────────────────────────────────
# datacenter-web.eastmoney.com supports pageSize=500 per request; a full
# market cross-section per report quarter is ~12 pages for yjbb (income)
# and ~11 for zcfz (balance). 15 quarters ≈ 2-3 min direct connection.
EM_PAGE_SIZE = 500
EM_BASE = "https://datacenter-web.eastmoney.com/api/data/v1/get"
EM_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://data.eastmoney.com/",
}
# raw eastmoney column -> (record key, divisor to normalize to baostock
# convention). Verified live 2026-09-25 (600519/000637 samples, curl direct)
# — do not trust docs. BASIC_EPS is report-period EPS, stored as epsQ;
# epsTTM (trailing-4-quarter sum) is derived by _apply_eps_ttm.
EM_INCOME_FIELDS: dict[str, tuple[str, float]] = {
    "WEIGHTAVG_ROE": ("roeAvg", 100.0),
    "XSMLL": ("gpMargin", 100.0),
    "PARENT_NETPROFIT": ("netProfit", 1.0),
    "TOTAL_OPERATE_INCOME": ("revenue", 1.0),
    "BASIC_EPS": ("epsQ", 1.0),
    "SJLTZ": ("YOYNI", 100.0),
    "BPS": ("BPS", 1.0),
}
EM_BALANCE_FIELDS: dict[str, tuple[str, float]] = {
    "DEBT_ASSET_RATIO": ("liabilityToAsset", 100.0),
    "CURRENT_RATIO": ("currentRatio", 100.0),
    "TOTAL_EQUITY": ("assetEquity", 1.0),
}
# sort combos verified live per table (wrong names are rejected server-side)
EM_SORTS = {
    "RPT_LICO_FN_CPD": ("UPDATE_DATE,SECURITY_CODE", "-1,-1"),
    "RPT_DMSK_FN_BALANCE": ("SECURITY_CODE", "1"),
}

_fetch_session: Any = None


def _em_get(report_name: str, filter_str: str, page: int) -> list[dict]:
    """One 500-row page from the eastmoney datacenter API (None on failure)."""
    global _fetch_session
    import requests

    if _fetch_session is None:
        # domestic endpoint — must bypass any ambient HTTP(S)_PROXY (Clash
        # tunnel hangs on cn traffic; direct curl verified 200 in 0.25s)
        _fetch_session = requests.Session()
        _fetch_session.trust_env = False
    sort_cols, sort_types = EM_SORTS[report_name]
    params = {
        "reportName": report_name,
        "columns": "ALL",
        "pageSize": EM_PAGE_SIZE,
        "pageNumber": page,
        "sortColumns": sort_cols,
        "sortTypes": sort_types,
        "filter": filter_str,
    }
    resp = _fetch_session.get(EM_BASE, params=params, headers=EM_HEADERS, timeout=20)
    resp.raise_for_status()
    result = resp.json().get("result") or {}
    return result.get("data") or []


def _em_fetch_quarter(
    report_name: str,
    date_field: str,
    y: int,
    q: int,
    field_map: dict[str, tuple[str, float]],
) -> dict[str, dict[str, float | None]]:
    """Full-market pages for one report quarter -> {symbol: {key: value}}."""
    from itertools import count

    quarter_end = {1: "03-31", 2: "06-30", 3: "09-30", 4: "12-31"}[q]
    filter_str = f"({date_field}='{y}-{quarter_end}')"
    out: dict[str, dict[str, float | None]] = {}
    empty_pages = 0
    for page in count(1):
        rows = _em_get(report_name, filter_str, page)
        if not rows:
            empty_pages += 1
            if empty_pages >= 2 or page > 30:
                break
            continue
        empty_pages = 0
        for row in rows:
            code = row.get("SECURITY_CODE")
            if not code:
                continue
            rec: dict[str, float | None] = {}
            for raw, (key, div) in field_map.items():
                v = row.get(raw)
                rec[key] = float(v) / div if v is not None else None
            pub = row.get("NOTICE_DATE") or ""
            rec["pubDate"] = pub.split(" ")[0] if pub else None  # type: ignore[assignment]
            out[code] = rec
    return out


def _apply_eps_ttm(records: list[dict]) -> None:
    """Derive epsTTM = trailing-4-quarter sum of epsQ (in place).

    eastmoney BASIC_EPS is report-period EPS, not TTM. Needs 4 consecutive
    quarters per symbol; shorter history leaves epsTTM None for that record.
    """
    by_sym: dict[str, dict[tuple[int, int], float | None]] = {}
    for rec in records:
        by_sym.setdefault(rec["symbol"], {})[(rec["year"], rec["quarter"])] = rec.get(
            "epsQ"
        )
    for rec in records:
        eps_q = rec.get("epsQ")
        if eps_q is None:
            continue
        y, q = rec["year"], rec["quarter"]
        window = []
        for i in range(4):
            wy, wq = y, q - i
            while wq <= 0:
                wy, wq = wy - 1, wq + 4
            window.append(by_sym[rec["symbol"]].get((wy, wq)))
        if all(v is not None for v in window):
            rec["epsTTM"] = sum(window)  # type: ignore[assignment]


def _merge_em_records(
    income: dict[str, dict[str, float | None]],
    balance: dict[str, dict[str, float | None]],
    y: int,
    q: int,
    pub_dates: dict[str, str] | None,
) -> list[dict]:
    """Inner-join income+balance per symbol into cache-format records."""
    stat_date = f"{y}-{ {1: '03-31', 2: '06-30', 3: '09-30', 4: '12-31'}[q] }"
    records = []
    for code in income.keys() | balance.keys():
        rec: dict = {
            "symbol": code,
            "year": y,
            "quarter": q,
            "statDate": stat_date,
        }
        rec.update(income.get(code) or {})
        bal = dict(balance.get(code) or {})
        # balance NOTICE_DATE is the row's last-touched date, not the
        # announcement date (verified live: 000001 2024Q4 balance says
        # 2026-03-21 = its 2025 annual report date; truth is 2025-03-15).
        # Income NOTICE_DATE is the first announcement date and wins; a
        # balance-only record keeps pubDate=None rather than a bogus date.
        bal.pop("pubDate", None)
        rec.update(bal)
        rec.setdefault(
            "pubDate", None
        )  # keep the key so attach_fundamentals' DataFrame always has the column
        if pub_dates and not rec.get("pubDate"):
            rec["pubDate"] = pub_dates.get(code)
        records.append(rec)
    return records


def _cache_load(cache_path: str) -> tuple[set[str], list[dict]]:
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
            return set(cache.get("done", [])), cache.get("records", [])
        except Exception:
            pass
    return set(), []


def fetch_financials_pit_eastmoney(
    symbols: list[str] | None = None,
    quarters: list[tuple[int, int]] | None = None,
    cache_path: str = CACHE_PATH,
    verbose: bool = False,
) -> list[dict]:
    """Fetch quarterly financials via the eastmoney datacenter API.

    Per-quarter full-market requests (~2-3 min for 15 quarters), so the
    done-key is quarter-granular: once a quarter's tables are pulled, every
    symbol is marked done for it regardless of the `symbols` argument.
    Cache format is identical to the baostock backend; records for symbols
    outside `symbols` are still written (harmless, filterable downstream).
    """
    quarters = quarters or _derive_quarters()
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)
    done, records = _cache_load(cache_path)

    todo = [(y, q) for (y, q) in quarters if f"*|{y}|{q}" not in done]
    if verbose:
        logger.info(
            "eastmoney financials: %d quarters done, %d to fetch",
            len(quarters) - len(todo),
            len(todo),
        )
    if not todo:
        return records

    wanted = set(symbols) if symbols else None
    t0 = time.time()
    for y, q in todo:
        income = _em_fetch_quarter(
            "RPT_LICO_FN_CPD", "REPORTDATE", y, q, EM_INCOME_FIELDS
        )
        balance = _em_fetch_quarter(
            "RPT_DMSK_FN_BALANCE", "REPORT_DATE", y, q, EM_BALANCE_FIELDS
        )
        if not income and not balance:
            logger.warning("eastmoney: no rows for %dQ%d — not marking done", y, q)
            continue
        for rec in _merge_em_records(income, balance, y, q, None):
            if wanted and rec["symbol"] not in wanted:
                continue
            records.append(rec)
        done.add(f"*|{y}|{q}")
        _save_cache(cache_path, done, records)
        if verbose:
            logger.info(
                "eastmoney: %dQ%d -> %d income, %d balance",
                y,
                q,
                len(income),
                len(balance),
            )
    if verbose:
        logger.info(
            "eastmoney fetch complete: %d records (%.1f min)",
            len(records),
            (time.time() - t0) / 60,
        )
    _apply_eps_ttm(records)
    _save_cache(cache_path, done, records)
    return records


def _bs_code(symbol: str) -> str:
    """Convert plain symbol (600519) to baostock code (sh.600519)."""
    return f"sh.{symbol}" if symbol.startswith(("6", "9")) else f"sz.{symbol}"


def fetch_financials_pit(
    symbols: list[str],
    quarters: list[tuple[int, int]] | None = None,
    cache_path: str = CACHE_PATH,
    verbose: bool = False,
) -> list[dict]:
    """Fetch quarterly financials with pubDate, resumable via done-set cache.

    Primary backend: eastmoney datacenter API (no login, fast full-market
    quarterly pulls). Falls back to baostock when the eastmoney pull yields
    nothing AND the cache is empty. Cache format: {"done": [...], "records":
    [...]} — done keys differ per backend ("*|y|q" for eastmoney quarter
    granularity, "sym|y|q" for baostock) so both can share one file.
    Returns [] with a warning if both backends are unavailable (never raises).
    """
    try:
        records = fetch_financials_pit_eastmoney(symbols, quarters, cache_path, verbose)
    except Exception as e:
        logger.warning("eastmoney backend failed: %s", e)
        records = []
    if records:
        return records
    return _fetch_financials_pit_baostock(symbols, quarters, cache_path, verbose)


def _fetch_financials_pit_baostock(
    symbols: list[str],
    quarters: list[tuple[int, int]] | None = None,
    cache_path: str = CACHE_PATH,
    verbose: bool = False,
) -> list[dict]:
    """baostock backend (previous primary; kept as fallback).

    Cache format: {"done": ["sym|year|quarter", ...], "records": [...]}.
    Saves every SAVE_EVERY_N groups and at the end; re-running skips done pairs.
    Returns [] with a warning if baostock is unavailable (never raises).
    """
    try:
        import baostock as bs
    except ImportError:
        logger.warning(
            "baostock not installed — financials fetch skipped "
            "(pip install baostock or use the 'cn' extra)"
        )
        return []

    quarters = quarters or _derive_quarters()
    os.makedirs(os.path.dirname(cache_path), exist_ok=True)

    cache: dict = {}
    if os.path.exists(cache_path):
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                cache = json.load(f)
        except Exception:
            cache = {}
    done: set[str] = set(cache.get("done", []))
    records: list[dict] = cache.get("records", [])

    todo = [
        (sym, y, q)
        for sym in symbols
        for (y, q) in quarters
        if f"{sym}|{y}|{q}" not in done
    ]
    if verbose:
        logger.info("financials cache: %d done, %d to fetch", len(done), len(todo))
    if not todo:
        return records

    import socket

    socket.setdefaulttimeout(20)

    try:
        login = bs.login()
        if getattr(login, "error_code", "1") != "0":
            logger.warning(
                "baostock login failed: %s — returning cached records",
                getattr(login, "error_msg", "unknown"),
            )
            return records
    except Exception as e:
        logger.warning("baostock login raised %s — returning cached records", e)
        return records

    t0 = time.time()
    try:
        for i, (sym, y, q) in enumerate(todo):
            rec: dict = {"symbol": sym, "year": y, "quarter": q}
            got = False
            for fn, fields in DATASETS.items():
                try:
                    rs = getattr(bs, fn)(_bs_code(sym), y, q)
                    while rs.error_code == "0" and rs.next():
                        row = dict(zip(rs.fields, rs.get_row_data()))
                        rec["pubDate"] = row.get("pubDate")
                        rec["statDate"] = row.get("statDate")
                        for field in fields:
                            v = row.get(field)
                            if v is None or v == "":
                                rec[field] = None
                            else:
                                rec[field] = float(v)
                        got = True
                except Exception:
                    continue
            if got:
                records.append(rec)
            done.add(f"{sym}|{y}|{q}")
            if FETCH_SLEEP_SECONDS:
                time.sleep(FETCH_SLEEP_SECONDS)
            if (i + 1) % SAVE_EVERY_N == 0:
                _save_cache(cache_path, done, records)
    finally:
        try:
            bs.logout()
        except Exception:
            pass

    _save_cache(cache_path, done, records)
    if verbose:
        logger.info(
            "financials fetch complete: %d records -> %s (%.1f min)",
            len(records),
            cache_path,
            (time.time() - t0) / 60,
        )
    return records


def _save_cache(cache_path: str, done: set[str], records: list[dict]) -> None:
    with open(cache_path, "w", encoding="utf-8") as f:
        json.dump({"done": sorted(done), "records": records}, f, ensure_ascii=False)


def load_financials(cache_path: str = CACHE_PATH) -> list[dict]:
    """Load cached financial records ([] if cache missing)."""
    if not os.path.exists(cache_path):
        return []
    with open(cache_path, "r", encoding="utf-8") as f:
        records: list[dict] = json.load(f).get("records", [])
    return records


def attach_fundamentals(
    df: pd.DataFrame, financials: list[dict] | None
) -> pd.DataFrame:
    """Merge financials onto each trading day as of pubDate (Point-in-Time).

    Uses merge_asof(direction='backward', by='symbol'): a date only sees the
    latest report announced on or before it — future reports are invisible.
    Empty financials -> NaN columns (same shape, pipeline keeps running).
    """
    out = df.copy()
    if not financials:
        for col in FUND_COLS:
            out[col] = np.nan
        return out

    fin = pd.DataFrame(financials)
    fin["pubDate"] = pd.to_datetime(fin["pubDate"], errors="coerce")
    fin = fin.dropna(subset=["pubDate"])
    for col in FUND_COLS:
        if col not in fin.columns:
            fin[col] = np.nan
    fin = (
        fin[["symbol", "pubDate", "statDate"] + FUND_COLS]
        .sort_values(["pubDate", "statDate"])
        .drop_duplicates(subset=["symbol", "pubDate"], keep="last")
    )

    dates = pd.to_datetime(out["date"])
    left = pd.DataFrame({"symbol": out["symbol"], "date": dates}).drop_duplicates()
    left = left.sort_values("date")
    merged = pd.merge_asof(
        left, fin, left_on="date", right_on="pubDate", by="symbol", direction="backward"
    )
    merged = merged[["symbol", "date"] + FUND_COLS].rename(columns={"date": "_date_dt"})
    tmp = out.copy()
    tmp["_date_dt"] = dates
    out = tmp.merge(merged, on=["symbol", "_date_dt"], how="left").drop(
        columns=["_date_dt"]
    )
    return out
