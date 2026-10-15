# Absorbed from ai-berkshire (https://github.com/xbtlin/ai-berkshire), MIT License (c) 2026 xbtlin.
# Source: tools/twstock_data.py — 移植为 stock taiwan_finmind.py。见 docs/ABSORPTION_FROM_AI_BERKSHIRE.md。
"""Taiwan (台股) market data connector — FinMind open-data API.

Fetches Taiwan stock quotes, valuation, financials, monthly revenue and
dividends from FinMind (api.finmindtrade.com), covering twse/tpex listings.

Library API (the source tool's CLI subcommands are adapted to pure functions):

    quote(stock_id)       -> dict   latest snapshot + market-cap cross-check
    valuation(stock_id)   -> dict   PER/PBR/yield + 52-week high/low
    kline(stock_id)       -> list   daily OHLCV bars (unified gateway shape)
    financials(stock_id)  -> dict   ~5y annual core financials (quarter-summed)
    revenue(stock_id)     -> dict   ~13 months of monthly revenue + YoY
    dividend(stock_id)    -> dict   recent dividend policy
    search(keyword)       -> list   lookup by name (traditional Chinese) or code

Authentication:
    Token is read ONLY from the ``FINMIND_TOKEN`` environment variable. It is
    never written to disk, never logged, never committed. A missing token
    raises :class:`FinMindTokenError`.

Notes:
    - All amounts are in New Taiwan dollars (TWD).
    - FinMind income statements are single-quarter values; ``financials``
      sums them into annual figures.
    - Zero third-party dependencies: the HTTP stack is stdlib
      ``urllib.request``. This is a deliberate stack decision inherited from
      the source asset (see docs/ABSORPTION_FROM_AI_BERKSHIRE.md §2).
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, timedelta

_API = "https://api.finmindtrade.com/api/v4/data"
_TIMEOUT = 30

_BOARD = {"twse": "上市", "tpex": "上柜"}

_IS_KEYS = {
    "Revenue": "营收",
    "GrossProfit": "毛利",
    "OperatingIncome": "营业利益",
    "EquityAttributableToOwnersOfParent": "归母净利",
    "EPS": "EPS",
}


class FinMindError(RuntimeError):
    """Base error for Taiwan FinMind data access."""


class FinMindTokenError(FinMindError):
    """``FINMIND_TOKEN`` is not configured."""


class FinMindRateLimitError(FinMindError):
    """FinMind rejected the request with HTTP 429 (rate limit)."""


class FinMindEmptyError(FinMindError):
    """The requested dataset returned no rows."""


def _token() -> str:
    token = os.environ.get("FINMIND_TOKEN", "").strip()
    if not token:
        raise FinMindTokenError(
            "FINMIND_TOKEN 未设置。FinMind 台股数据需要 API token；"
            "请通过环境变量设置（绝不落盘、绝不进 git）。"
        )
    return token


def _get(
    dataset: str,
    data_id: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Request a FinMind dataset and return the ``data`` list.

    Raises :class:`FinMindTokenError` when the token is missing,
    :class:`FinMindRateLimitError` on HTTP 429, and :class:`FinMindError`
    for other transport/status failures.
    """
    params = {"dataset": dataset}
    if data_id:
        params["data_id"] = data_id
    if start_date:
        params["start_date"] = start_date
    if end_date:
        params["end_date"] = end_date
    params["token"] = _token()
    url = f"{_API}?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 429:
            raise FinMindRateLimitError(
                f"FinMind 请求过频（HTTP 429），请稍后重试或检查 token 额度 ({dataset})"
            ) from e
        if e.code in (400, 401, 402, 403):
            raise FinMindError(
                f"FinMind 拒绝请求（HTTP {e.code}），大概率是 token 无效或过期 ({dataset})。"
                "请检查环境变量 FINMIND_TOKEN 的内容。"
            ) from e
        raise FinMindError(f"FinMind 请求失败: HTTP {e.code} ({dataset})") from e
    except urllib.error.URLError as e:
        raise FinMindError(f"FinMind 网络请求失败: {e.reason} ({dataset})") from e
    if payload.get("status") != 200:
        raise FinMindError(f"FinMind 请求失败: {payload.get('msg')} ({dataset})")
    return payload.get("data", [])


def _days_ago(n: int) -> str:
    return (date.today() - timedelta(days=n)).isoformat()


def _stock_name(stock_id: str) -> tuple[str, str]:
    """Best-effort name/board lookup; never hides a missing-token error."""
    try:
        rows = _get("TaiwanStockInfo", data_id=stock_id)
    except FinMindTokenError:
        raise
    except FinMindError:
        return stock_id, ""
    if not rows:
        return stock_id, ""
    board = _BOARD.get(rows[0].get("type", ""), "")
    return rows[0].get("stock_name", stock_id), board


def _latest_shares(stock_id: str) -> float | None:
    """Latest issued share count; None when unavailable (token errors propagate)."""
    try:
        rows = _get("TaiwanStockShareholding", data_id=stock_id, start_date=_days_ago(14))
    except FinMindTokenError:
        raise
    except FinMindError:
        return None
    if not rows:
        return None
    return rows[-1].get("NumberOfSharesIssued")


def format_twd(value: object) -> str:
    """Format a TWD amount into 亿/万 display units (source ``_fmt_yi``)."""
    if value is None or value == "":
        return "-"
    try:
        v = float(value)
    except (ValueError, TypeError):
        return str(value)
    if abs(v) >= 1e8:
        return f"{v / 1e8:,.1f}亿"
    if abs(v) >= 1e4:
        return f"{v / 1e4:,.1f}万"
    return f"{v:,.2f}"


# ---------------------------------------------------------------------------
# Data functions
# ---------------------------------------------------------------------------


def quote(stock_id: str) -> dict:
    """Latest quote snapshot + market-cap cross-check (close × shares)."""
    name, board = _stock_name(stock_id)
    prices = _get("TaiwanStockPrice", data_id=stock_id, start_date=_days_ago(14))
    if not prices:
        raise FinMindEmptyError(f"未找到股票 {stock_id} 的行情数据")
    p = prices[-1]
    prev_close = prices[-2]["close"] if len(prices) >= 2 else None

    pers = _get("TaiwanStockPER", data_id=stock_id, start_date=_days_ago(14))
    per = pers[-1] if pers else {}

    change = None
    change_pct = None
    if prev_close is not None:
        change = p["close"] - prev_close
        change_pct = change / prev_close * 100

    shares = _latest_shares(stock_id)
    market_cap = p["close"] * shares if shares else None

    return {
        "stock_id": stock_id,
        "name": name,
        "board": board,
        "currency": "TWD",
        "date": p["date"],
        "open": p["open"],
        "max": p["max"],
        "min": p["min"],
        "close": p["close"],
        "change": change,
        "change_pct": change_pct,
        "trading_volume": p.get("Trading_Volume"),
        "trading_money": p.get("Trading_money"),
        "per": per.get("PER"),
        "pbr": per.get("PBR"),
        "dividend_yield": per.get("dividend_yield"),
        "shares_issued": shares,
        "market_cap": market_cap,
    }


def valuation(stock_id: str) -> dict:
    """Valuation metrics: PER/PBR/yield + 52-week high/low."""
    name, board = _stock_name(stock_id)
    prices = _get("TaiwanStockPrice", data_id=stock_id, start_date=_days_ago(370))
    if not prices:
        raise FinMindEmptyError(f"未找到股票 {stock_id} 的行情数据")
    p = prices[-1]
    high_52w = max(r["max"] for r in prices)
    low_52w = min(r["min"] for r in prices)

    pers = _get("TaiwanStockPER", data_id=stock_id, start_date=_days_ago(370))
    per = pers[-1] if pers else {}
    per_vals = [r["PER"] for r in pers if r.get("PER")]

    shares = _latest_shares(stock_id)
    market_cap = p["close"] * shares if shares else None

    return {
        "stock_id": stock_id,
        "name": name,
        "board": board,
        "currency": "TWD",
        "date": p["date"],
        "close": p["close"],
        "per": per.get("PER"),
        "per_year_range": [min(per_vals), max(per_vals)] if per_vals else None,
        "pbr": per.get("PBR"),
        "dividend_yield": per.get("dividend_yield"),
        "high_52w": high_52w,
        "low_52w": low_52w,
        "shares_issued": shares,
        "market_cap": market_cap,
    }


def kline(
    stock_id: str,
    start_date: str | None = None,
    end_date: str | None = None,
) -> list[dict]:
    """Daily OHLCV bars for a Taiwan stock (v1: daily only).

    Maps FinMind TaiwanStockPrice rows (open/max/min/close/Trading_Volume)
    to the unified OHLCV shape consumed by the bars gateway. Defaults to the
    trailing ~370 calendar days when no date range is given.

    Raises :class:`FinMindEmptyError` when the dataset returns no rows.
    """
    if not start_date:
        start_date = _days_ago(370)
    rows = _get("TaiwanStockPrice", data_id=stock_id, start_date=start_date, end_date=end_date)
    if not rows:
        raise FinMindEmptyError(f"未找到股票 {stock_id} 的 K 线数据")
    return [
        {
            "date": r["date"],
            "open": r["open"],
            "high": r["max"],
            "low": r["min"],
            "close": r["close"],
            "volume": r.get("Trading_Volume"),
        }
        for r in rows
    ]


def financials(stock_id: str) -> dict:
    """~5y annual core financials — single-quarter statements summed per year."""
    name, board = _stock_name(stock_id)
    start = f"{date.today().year - 5}-01-01"
    rows = _get("TaiwanStockFinancialStatements", data_id=stock_id, start_date=start)
    if not rows:
        raise FinMindEmptyError(f"未找到股票 {stock_id} 的财务数据")

    years: dict[str, dict] = {}
    for r in rows:
        if r.get("type") not in _IS_KEYS:
            continue
        y = r["date"][:4]
        d = years.setdefault(y, {"_quarters": set()})
        d["_quarters"].add(r["date"])
        d[r["type"]] = d.get(r["type"], 0) + (r["value"] or 0)

    equity_by_year: dict[str, float] = {}
    try:
        bs = _get("TaiwanStockBalanceSheet", data_id=stock_id, start_date=start)
        for r in bs:
            if r.get("type") == "EquityAttributableToOwnersOfParent" and r.get("date", "")[5:7] == "12":
                equity_by_year[r["date"][:4]] = r["value"]
    except FinMindTokenError:
        raise
    except FinMindError:
        pass

    result = []
    for y in sorted(years, reverse=True):
        d = years[y]
        nq = len(d["_quarters"])
        rev = d.get("Revenue")
        gp = d.get("GrossProfit")
        op = d.get("OperatingIncome")
        ni = d.get("EquityAttributableToOwnersOfParent")
        eps = d.get("EPS")
        eq = equity_by_year.get(y)
        result.append(
            {
                "year": y,
                "full_year": nq == 4,
                "quarters_count": nq,
                "revenue": rev,
                "gross_profit": gp,
                "gross_margin_pct": (gp / rev * 100) if (gp and rev) else None,
                "operating_income": op,
                "operating_margin_pct": (op / rev * 100) if (op and rev) else None,
                "net_income": ni,
                "net_margin_pct": (ni / rev * 100) if (ni and rev) else None,
                "eps": eps,
                "equity": eq,
                "roe_pct": (ni / eq * 100) if (eq and ni and nq == 4) else None,
            }
        )
    return {"stock_id": stock_id, "name": name, "board": board, "currency": "TWD", "years": result}


def revenue(stock_id: str) -> dict:
    """~13 months of monthly revenue with YoY — Taiwan's monthly disclosure."""
    name, board = _stock_name(stock_id)
    rows = _get("TaiwanStockMonthRevenue", data_id=stock_id, start_date=_days_ago(800))
    if not rows:
        raise FinMindEmptyError(f"未找到股票 {stock_id} 的月营收数据")

    by_month = {(r["revenue_year"], r["revenue_month"]): r["revenue"] for r in rows}
    months = []
    for y, m in sorted(by_month)[-13:]:
        rev = by_month[(y, m)]
        prev = by_month.get((y - 1, m))
        months.append(
            {
                "year": y,
                "month": m,
                "revenue": rev,
                "yoy_pct": ((rev / prev - 1) * 100) if prev else None,
            }
        )
    return {"stock_id": stock_id, "name": name, "board": board, "currency": "TWD", "months": months}


def dividend(stock_id: str) -> dict:
    """Recent dividend policy (TWD per share; quarterly payments, sum yourself)."""
    name, board = _stock_name(stock_id)
    start = f"{date.today().year - 5}-01-01"
    rows = _get("TaiwanStockDividend", data_id=stock_id, start_date=start)
    if not rows:
        raise FinMindEmptyError(f"未找到股票 {stock_id} 的股利数据")

    dividends = []
    for r in rows:
        cash = (r.get("CashEarningsDistribution") or 0) + (r.get("CashStatutorySurplus") or 0)
        stock = (r.get("StockEarningsDistribution") or 0) + (r.get("StockStatutorySurplus") or 0)
        if not cash and not stock:
            continue
        dividends.append(
            {
                "year": r.get("year", ""),
                "cash": cash,
                "stock": stock,
                "cash_ex_date": r.get("CashExDividendTradingDate") or None,
                "cash_payment_date": r.get("CashDividendPaymentDate") or None,
            }
        )
    return {"stock_id": stock_id, "name": name, "board": board, "currency": "TWD", "dividends": dividends}


def search(keyword: str) -> list[dict]:
    """Search Taiwan stocks by name (traditional Chinese) or stock code.

    Returns an empty list when nothing matches (search semantics; the
    per-stock data functions raise :class:`FinMindEmptyError` instead).
    """
    rows = _get("TaiwanStockInfo")
    seen: dict[str, dict] = {}
    for r in rows:
        if keyword in r.get("stock_name", "") or keyword == r.get("stock_id", ""):
            sid = r["stock_id"]
            if sid not in seen:
                seen[sid] = {
                    "stock_id": sid,
                    "name": r["stock_name"],
                    "board": _BOARD.get(r.get("type", ""), r.get("type", "")),
                    "industries": [],
                }
            cat = r.get("industry_category", "")
            if cat and cat not in seen[sid]["industries"]:
                seen[sid]["industries"].append(cat)
    return [seen[sid] for sid in sorted(seen)]


__all__ = [
    "FinMindEmptyError",
    "FinMindError",
    "FinMindRateLimitError",
    "FinMindTokenError",
    "dividend",
    "financials",
    "format_twd",
    "kline",
    "quote",
    "revenue",
    "search",
    "valuation",
]
