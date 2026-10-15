"""
同花顺(10jqka.com.cn) direct data access.

K-line API: d.10jqka.com.cn/v6/line/ — direct, no anti-scraping, 8400+ days
Limit-up API: data.10jqka.com.cn/dataapi/ — browser UA required, no rate limit

Requires [cn] optional dependency: requests.
"""

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

CACHE_DIR = Path(__file__).parent.parent / "data_cache"

# ─── K-line API ──────────────────────────────────────────────────────

KLINE_BASE = "https://d.10jqka.com.cn/v6/line"
KLINE_HEADERS = {"Referer": "https://stockpage.10jqka.com.cn/"}

PERIOD_MAP = {
    "daily": "01",
    "weekly": "11",
    "monthly": "21",
    "15min": "41",
    "30min": "51",
    "60min": "61",
}

RANGE_MAP = {
    "last": "last.js",
    "all": "all.js",
    "2020": "2020.js",
    "2021": "2021.js",
    "2022": "2022.js",
    "2023": "2023.js",
    "2024": "2024.js",
    "2025": "2025.js",
    "2026": "2026.js",
}


def _ths_prefix(code: str) -> str:
    return f"hs_{code}"


def _ensure_cache(subdir: str = "") -> Path:
    d = CACHE_DIR / subdir if subdir else CACHE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _cache_path(name: str, subdir: str = "") -> Path:
    return _ensure_cache(subdir) / f"{name}.parquet"


def _read_cache(name: str, subdir: str = "") -> pd.DataFrame | None:
    p = _cache_path(name, subdir)
    if p.exists():
        return pd.read_parquet(p)
    return None


def _write_cache(df: pd.DataFrame, name: str, subdir: str = "") -> None:
    p = _cache_path(name, subdir)
    df.to_parquet(p, index=False)


# Cache freshness gate. Without it, fetch_klines_ths returned the parquet
# unconditionally once written, so any symbol whose cache was last touched on
# an old date stayed frozen forever (2026-10: 731/804 daily files stuck in
# September while 同花顺 already served October). TTL is per-period: daily and
# coarser bars only change once per session so 6h is ample, while intraday
# bars need a short window. Env override for tests / unusual schedules.
_CACHE_TTL_SEC_DEFAULT = {"daily": 6 * 3600, "weekly": 6 * 3600, "monthly": 24 * 3600}
_INTRADAY_TTL_SEC = 300.0


def _cache_ttl_seconds(period: str) -> float:
    raw = os.environ.get("SELENE_ASHARE_CACHE_TTL_SEC", "")
    if raw:
        try:
            return float(raw)
        except ValueError:
            pass
    return float(_CACHE_TTL_SEC_DEFAULT.get(period, _INTRADAY_TTL_SEC))


def _cache_age_seconds(name: str, subdir: str) -> float | None:
    """Seconds since the cache file was last written, or None if absent."""
    p = _cache_path(name, subdir)
    if not p.exists():
        return None
    return time.time() - p.stat().st_mtime


def _cache_fresh(name: str, subdir: str, period: str) -> bool:
    age = _cache_age_seconds(name, subdir)
    return age is not None and age <= _cache_ttl_seconds(period)


def fetch_klines_ths(
    code: str,
    period: str = "daily",
    range_type: str = "last",
    force: bool = False,
) -> pd.DataFrame:
    """Fetch klines from 同花顺 direct API.

    Args:
        code: 6-digit stock code e.g. "000001"
        period: "daily", "weekly", "monthly", "15min", "30min", "60min"
        range_type: "last" (~140 bars), "all" (full history compressed),
                    or "2024"/"2025" (year-specific)
        force: bypass cache

    Returns:
        DataFrame with [date, open, high, low, close, volume, amount, turnover]
    """
    import requests

    subdir = f"ths_klines/{period}"
    cache_name = code

    stale_cache: pd.DataFrame | None = None
    if not force:
        cached = _read_cache(cache_name, subdir)
        if cached is not None and not cached.empty:
            if _cache_fresh(cache_name, subdir, period):
                return cached
            # Stale but present: keep it as a fallback for when the live
            # fetch fails, so a transient upstream/network error degrades to
            # slightly-old data instead of an empty chart.
            stale_cache = cached

    prefix = _ths_prefix(code)
    period_code = PERIOD_MAP.get(period, "01")
    range_file = RANGE_MAP.get(range_type, "last.js")

    url = f"{KLINE_BASE}/{prefix}/{period_code}/{range_file}"

    try:
        resp = requests.get(url, headers=KLINE_HEADERS, timeout=15)
        resp.raise_for_status()
    except requests.RequestException:
        return stale_cache if stale_cache is not None else pd.DataFrame()

    text = resp.text
    match = re.search(r"\((\{.*\})\)", text, re.DOTALL)
    if not match:
        return stale_cache if stale_cache is not None else pd.DataFrame()

    data = json.loads(match.group(1))
    raw = data.get("data", "")
    if not raw:
        return stale_cache if stale_cache is not None else pd.DataFrame()

    rows = []
    for line in raw.split(";"):
        parts = line.split(",")
        if len(parts) < 8:
            continue
        try:
            rows.append(
                {
                    "date": parts[0],
                    "open": float(parts[1]),
                    "high": float(parts[2]),
                    "low": float(parts[3]),
                    "close": float(parts[4]),
                    "volume": int(float(parts[5])),
                    "amount": float(parts[6]),
                    "turnover": float(parts[7]) if parts[7] else 0.0,
                }
            )
        except (ValueError, IndexError):
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    df["code"] = code
    _write_cache(df, cache_name, subdir)
    return df


def fetch_klines_ths_all(
    code: str,
    period: str = "daily",
    force: bool = False,
) -> pd.DataFrame:
    """Fetch full history klines using year-by-year approach."""
    subdir = f"ths_klines/{period}"

    stale_cache: pd.DataFrame | None = None
    if not force:
        cached = _read_cache(f"{code}_full", subdir)
        if cached is not None and not cached.empty:
            if _cache_fresh(f"{code}_full", subdir, period):
                return cached
            stale_cache = cached

    all_rows = []
    current_year = datetime.now().year

    for year in range(2020, current_year + 1):
        df = fetch_klines_ths(code, period, str(year), force=True)
        if not df.empty:
            all_rows.append(df)
        time.sleep(0.1)

    if not all_rows:
        if stale_cache is not None:
            return stale_cache
        return fetch_klines_ths(code, period, "last", force)

    result = pd.concat(all_rows, ignore_index=True)
    result = result.drop_duplicates(subset=["date"]).sort_values("date")

    _write_cache(result, f"{code}_full", subdir)
    return result


# ─── Limit-Up API ────────────────────────────────────────────────────

LIMITUP_BASE = "https://data.10jqka.com.cn/dataapi/limit_up/limit_up_pool"
LIMITUP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Referer": "https://data.10jqka.com.cn/",
}

LIMITUP_FIELDS = "199112,10,9001,330323,330324,330325,9002,330329,133971,133970,1968584,3475914,9003,9004"


def fetch_limit_up_pool(
    date: str = "",
    page: int = 1,
    limit: int = 50,
    force: bool = False,
    strict: bool = False,
) -> pd.DataFrame:
    """Fetch one page of the 涨停 pool from 同花顺.

    .. warning::
       The upstream sort is **unstable**: asking for the same page twice
       returns a different subset of rows (measured 2026-10-15 — repeated
       ``page=1&limit=50`` calls on one date shared only 40..50 of 50 codes).
       Paging a large result that way therefore *silently drops* names. Use
       :func:`fetch_limit_up_pool_full`, which asks for one big page instead.
       This function is a low-level page reader and is not safe to paginate
       without a sort key the server actually honours (none was found).

    Args:
        date: YYYYMMDD format, empty = today
        page: page number (see the warning above)
        limit: items per page — the server rejects ``limit > 200`` with
            ``status_code = -1`` and an empty body.
        force: bypass cache
        strict: on a transport or parse failure, raise
            :class:`LimitUpDataUnavailable` instead of returning an empty
            frame, so a caller that depends on the pool can tell a dead
            endpoint from an empty one. A genuinely empty page (a
            past-the-end page, or a non-trading day) still returns empty —
            only an unanswered request raises.

    Returns:
        DataFrame with limit-up stock details

    Raises:
        LimitUpDataUnavailable: only when ``strict=True`` and the endpoint did
            not answer.
    """
    import requests

    if not date:
        date = datetime.now().strftime("%Y%m%d")

    subdir = "ths_limitup"
    # `limit` is part of the key: a page cached under one size must never be
    # served to a caller asking for another, or a 50-row page would silently
    # stand in for a 200-row one and truncate the pool.
    cache_name = f"limitup_{date}_p{page}_l{limit}"

    if not force:
        cached = _read_cache(cache_name, subdir)
        if cached is not None and not cached.empty:
            return cached

    params = {
        "page": page,
        "limit": limit,
        "field": LIMITUP_FIELDS,
    }
    if date:
        params["date"] = date

    try:
        resp = requests.get(
            LIMITUP_BASE, headers=LIMITUP_HEADERS, params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as exc:
        if strict:
            raise LimitUpDataUnavailable(
                f"limit_up_pool unavailable for {date} page {page}"
            ) from exc
        return pd.DataFrame()

    info = data.get("data", {}).get("info", [])
    if not info:
        return pd.DataFrame()

    rows = []
    for item in info:
        row = {
            "code": item.get("code", ""),
            "name": item.get("name", ""),
            "market_id": item.get("market_id", 0),
            "date": date,
            "change_rate": item.get("change_rate"),
            "latest_price": item.get("latest"),
            "reason_type": item.get("reason_type", ""),
            "first_limit_time": item.get("first_limit_up_time"),
            "last_limit_time": item.get("last_limit_up_time"),
            "limit_up_type": item.get("limit_up_type", ""),
            "open_count": item.get("open_num"),
            "high_days": item.get("high_days", ""),
            "seal_volume": item.get("order_volume"),
            "seal_amount": item.get("order_amount"),
            "turnover_rate": item.get("turnover_rate"),
            "float_market_cap": item.get("currency_value"),
            "success_rate": item.get("limit_up_suc_rate"),
            "high_days_value": item.get("high_days_value", 0),
            "change_tag": item.get("change_tag", ""),
        }
        rows.append(row)

    df = pd.DataFrame(rows)
    if not df.empty:
        _write_cache(df, cache_name, subdir)
    return df


# The upstream pool endpoint rejects `limit > 200` (status_code -1, empty
# body), so this is the largest single request it will answer — and because the
# sort is unstable, a single large page is also the *only* way to read a pool
# of up to this size without losing rows.
POOL_MAX_PAGE = 200


def fetch_limit_up_pool_full(
    date: str = "",
    force: bool = False,
    strict: bool = False,
) -> pd.DataFrame:
    """Fetch the whole 涨停 pool for a date in as few requests as the API allows.

    A single ``limit=200`` page is the default: the backend returns a *stable*
    result for that size, whereas paging with ``limit=50`` reads an unstable
    sort and silently drops names (see :func:`fetch_limit_up_pool`). Pools that
    genuinely exceed 200 fall back to paging, which is best-effort — the union
    is returned deduplicated by ``code``, and a page the server refuses simply
    ends the walk.

    Args:
        date: YYYYMMDD; empty means today.
        force: bypass cache.
        strict: forward to :func:`fetch_limit_up_pool` — a transport failure
            raises :class:`LimitUpDataUnavailable` rather than ending the walk
            with a partial (or empty) pool. A genuinely empty page still ends
            the walk, so a pool that simply has no names does not raise.

    Returns:
        One row per limit-up stock. Empty when the endpoint answers nothing.

    Raises:
        LimitUpDataUnavailable: only when ``strict=True`` and a page did not
            answer.
    """
    if not date:
        date = datetime.now().strftime("%Y%m%d")

    all_rows: list[pd.DataFrame] = []
    page = 1
    while True:
        df = fetch_limit_up_pool(
            date, page=page, limit=POOL_MAX_PAGE, force=force, strict=strict
        )
        if df.empty:
            break
        all_rows.append(df)
        if len(df) < POOL_MAX_PAGE:
            break
        page += 1
        time.sleep(0.2)

    if not all_rows:
        return pd.DataFrame()

    merged = pd.concat(all_rows, ignore_index=True)
    # The unstable sort makes overlaps possible; keep one row per code so a
    # duplicate never inflates a breadth count.
    return merged.drop_duplicates(subset=["code"], keep="first").reset_index(drop=True)


def fetch_limit_up_stats(
    date: str = "",
    force: bool = False,
) -> dict:
    """Fetch limit-up/down statistics for a date.

    Returns dict with:
    - limit_up_today: 今日涨停数
    - limit_up_yesterday: 昨日涨停数
    - seal_rate: 封板率
    - open_count: 炸板数
    - limit_down_today: 今日跌停数
    """
    import requests

    if not date:
        date = datetime.now().strftime("%Y%m%d")

    subdir = "ths_limitup"
    cache_name = f"stats_{date}"

    if not force:
        p = _cache_path(cache_name, subdir)
        if p.exists():
            with open(p) as f:
                loaded: dict = json.load(f)
                return loaded

    params = {
        "page": 1,
        "limit": 1,
        "field": LIMITUP_FIELDS,
        "date": date,
    }

    try:
        resp = requests.get(
            LIMITUP_BASE, headers=LIMITUP_HEADERS, params=params, timeout=15
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError):
        return {}

    result = {}
    lu = data.get("data", {}).get("limit_up_count", {})
    if lu:
        today = lu.get("today", {})
        yesterday = lu.get("yesterday", {})
        result = {
            "limit_up_today": today.get("num", 0),
            "limit_up_yesterday": yesterday.get("num", 0),
            "seal_rate": today.get("rate", 0),
            "open_count": today.get("open_num", 0),
        }

    ld = data.get("data", {}).get("limit_down_count", {})
    if ld:
        result["limit_down_today"] = ld.get("today", {}).get("num", 0)
        result["limit_down_yesterday"] = ld.get("yesterday", {}).get("num", 0)

    if result:
        p = _cache_path(cache_name, subdir)
        with open(p, "w") as f:
            json.dump(result, f)

    return result


class LimitUpDataUnavailable(RuntimeError):
    """The limit-up endpoint did not answer, and the caller asked for strict mode.

    Raised only when ``strict=True``. The default (lenient) callers receive an
    empty frame instead, so a transient blip degrades one factor rather than
    aborting a month-long batch.
    """


def fetch_continuous_limit_up(
    date: str = "",
    force: bool = False,
    strict: bool = False,
) -> pd.DataFrame:
    """Fetch continuous limit-up (连板股) data.

    Only names on their **second board or higher** are listed; a stock on its
    first board is absent. ``consecutive_boards`` is the true consecutive run
    (endpoint field ``continue_num``), which is *not* the ``M`` in the pool's
    ``N天M板`` string — see :func:`~selene_space_ashare.ladder.ladder_history_from_pool`.

    Args:
        date: ``YYYYMMDD``, empty = today. The endpoint reaches back roughly
            six weeks; an older or non-trading date comes back empty rather
            than erroring. When empty the request omits the parameter (the
            endpoint's own notion of "today"), which is what callers got before
            this parameter existed.
        force: bypass cache.
        strict: on a transport or parse failure, raise
            :class:`LimitUpDataUnavailable` instead of returning an empty frame.
            An empty frame is ambiguous — it reads both as "no 连板 today" and
            as "the endpoint is down" — and the ladder combiner resolves the
            ambiguity as the former, labelling every pool name a 首板. Callers
            that depend on that distinction (the live walk, ``/ladder``) pass
            ``strict=True``; the batch callers keep the empty frame.

    Raises:
        LimitUpDataUnavailable: only when ``strict=True`` and the endpoint did
            not answer.
    """
    import requests

    url = "https://data.10jqka.com.cn/dataapi/limit_up/continuous_limit_up"

    requested = date
    if not date:
        date = datetime.now().strftime("%Y%m%d")

    subdir = "ths_limitup"
    # Date-keyed cache: the same endpoint serves any session, so a single
    # unnamed file would hand one date's boards back for every other date.
    cache_name = "continuous" if not requested else f"continuous_{date}"

    if not force:
        cached = _read_cache(cache_name, subdir)
        if cached is not None and not cached.empty:
            cache_age = (
                datetime.now()
                - datetime.fromtimestamp(
                    _cache_path(cache_name, subdir).stat().st_mtime
                )
            ).days
            if cache_age < 1:
                return cached

    params = {"date": requested} if requested else {}
    try:
        resp = requests.get(url, headers=LIMITUP_HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, json.JSONDecodeError) as exc:
        if strict:
            raise LimitUpDataUnavailable(
                f"continuous_limit_up unavailable for {date}"
            ) from exc
        return pd.DataFrame()

    groups = data.get("data", [])
    if not groups or not isinstance(groups, list):
        return pd.DataFrame()

    rows = []
    for group in groups:
        height = group.get("height", 0)
        for stock in group.get("code_list", []):
            # `continue_num` is the true run. A key that is *present but null*
            # has to take the same fallback a missing key does: ``dict.get``
            # only defaults when the key is absent, so an explicit null would
            # reach `ladder_history_from_pool` as NaN and drop the code from
            # every row — it is already excluded from the 首板 difference for
            # being listed here, so the stock would vanish entirely. The group's
            # own height is this batch's board grouping, the right stand-in.
            boards = stock.get("continue_num")
            if boards is None:
                boards = height
            rows.append(
                {
                    "code": stock.get("code", ""),
                    "name": stock.get("name", ""),
                    "consecutive_boards": boards,
                    "height_group": height,
                    "date": date,
                }
            )

    df = pd.DataFrame(rows)
    if not df.empty:
        _write_cache(df, cache_name, subdir)
    return df
