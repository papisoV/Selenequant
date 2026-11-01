# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""Whole-market 龙虎榜 for one session — the recap's 资金动向 block.

## Why not ``data_fetcher.get_lhb_detail``

That function is **per-symbol** (``SECURITY_CODE="..."`` in its filter) and
returns the board *seat* rows. The recap needs the opposite: every name on the
board for one trading day, one row per (name, reason). Adding a ``date`` to the
existing signature would change a function the deep-analysis route already
consumes, so this is a separate reader over the same datacenter report.

## The join key is ``SECUCODE``, not ``SECURITY_CODE``

The report publishes both: ``SECURITY_CODE`` is the bare six digits
(``603259``), ``SECUCODE`` is the exchange-qualified form (``603259.SH``).
selene's entire symbol space is suffixed upper-case, so joining on the bare
code would miss every row. Verified live 2026-10-20 on 2026-10-16: 66 rows,
one page, both columns present on every row.

## Paging

The endpoint defaults to 100 rows/page. A busy session can exceed that, so the
reader follows ``result.pages``. Every page is fetched through the shared
throttle in ``data_fetcher`` so a whole-market pull cannot burst the host (the
same limit those callers learned the hard way — see ``_em_throttle``).

## Failure contract

Returns ``(rows, error)``; never raises. ``rows`` is whatever parsed, so a
partially answered day still renders. ``error`` is a short reason string.
"""

from __future__ import annotations

import logging
from typing import Any


logger = logging.getLogger(__name__)

_REPORT = "RPT_DAILYBILLBOARD_DETAILSNEW"
_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_PAGE_SIZE = 500
_MAX_PAGES = 20  # 10k rows — far above any real session (~60-100)

_COLUMNS = (
    "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,TRADE_DATE,EXPLANATION,"
    "BILLBOARD_BUY_AMT,BILLBOARD_SELL_AMT,BILLBOARD_NET_AMT,"
    "BILLBOARD_DEAL_AMT,ACCUM_AMOUNT,DEAL_NET_RATIO,DEAL_AMOUNT_RATIO,"
    "TURNOVERRATE,CLOSE_PRICE,CHANGE_RATE"
)


def _norm_date(value: str) -> str:
    """``YYYYMMDD`` or ``YYYY-MM-DD`` (or ``/`` separated) → ``YYYY-MM-DD``."""
    text = str(value).strip().replace("/", "-")
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    return text[:10]


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None


def _row(raw: dict[str, Any]) -> dict[str, Any] | None:
    """One datacenter row → the recap shape, or ``None`` when unjoinable."""
    symbol = str(raw.get("SECUCODE") or "").strip().upper()
    if not symbol:
        # Without the suffixed code the row cannot be joined to anything in
        # selene's symbol space; dropping it is honest, keeping it under a bare
        # code would silently create a second symbol vocabulary.
        return None
    change_pct = _num(raw.get("CHANGE_RATE"))
    net = _num(raw.get("BILLBOARD_NET_AMT"))
    buy = _num(raw.get("BILLBOARD_BUY_AMT"))
    sell = _num(raw.get("BILLBOARD_SELL_AMT"))
    return {
        "symbol": symbol,
        "code": str(raw.get("SECURITY_CODE") or "").strip(),
        "name": str(raw.get("SECURITY_NAME_ABBR") or "").strip(),
        "date": str(raw.get("TRADE_DATE") or "")[:10],
        "reason": str(raw.get("EXPLANATION") or "").strip(),
        "change_pct": change_pct,
        "close": _num(raw.get("CLOSE_PRICE")),
        "turnover_rate": _num(raw.get("TURNOVERRATE")),
        "buy_amount": buy,
        "sell_amount": sell,
        "net_amount": net,
        "deal_amount": _num(raw.get("BILLBOARD_DEAL_AMT")),
        "accum_amount": _num(raw.get("ACCUM_AMOUNT")),
        "net_ratio": _num(raw.get("DEAL_NET_RATIO")),
        "amount_ratio": _num(raw.get("DEAL_AMOUNT_RATIO")),
    }


def fetch_lhb_day(trade_date: str) -> tuple[list[dict[str, Any]], str | None]:
    """Every 龙虎榜 row for ``trade_date``, across all pages.

    Args:
        trade_date: ``YYYYMMDD`` or ``YYYY-MM-DD``.

    Returns:
        ``(rows, error)``. ``rows`` is de-duplicated on
        ``(symbol, reason)`` — the report repeats a name once per listing
        reason, and the recap wants each appearance once. ``error`` is
        ``None`` on a clean pull, ``"empty"`` when the host answered but the
        day has no board rows (a normal, non-error answer), and a reason string
        when the fetch failed.
    """
    import httpx

    from selene_space_ashare.data_fetcher import _EASTMONEY_UA, _em_throttle

    day = _norm_date(trade_date)
    if not day:
        return [], "bad_date"

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    pages: int | None = None
    page = 1
    while page <= _MAX_PAGES:
        try:
            _em_throttle()
            response = httpx.get(
                _URL,
                params={
                    "reportName": _REPORT,
                    "columns": _COLUMNS,
                    "pageSize": str(_PAGE_SIZE),
                    "pageNumber": str(page),
                    "filter": f"(TRADE_DATE='{day}')",
                    "sortColumns": "BILLBOARD_NET_AMT",
                    "sortTypes": "-1",
                },
                headers={"User-Agent": _EASTMONEY_UA},
                timeout=20,
                # Same reason as `_em_datacenter_get`: a proxy in the ambient
                # environment turns this into an intermittent 502.
                trust_env=False,
            )
            response.raise_for_status()
            result = (response.json() or {}).get("result") or {}
        except Exception as exc:  # noqa: BLE001 — fail-open by contract
            logger.warning("lhb_daily: fetch failed on page %d: %s", page, exc)
            return rows, f"fetch_failed: {exc}"

        if pages is None:
            pages = int(result.get("pages") or 0)
        batch = result.get("data") or []
        if not batch:
            break
        for raw in batch:
            parsed = _row(raw)
            if parsed is None:
                continue
            key = (parsed["symbol"], parsed["reason"])
            if key in seen:
                continue
            seen.add(key)
            rows.append(parsed)

        if pages <= page:
            break
        page += 1

    if not rows:
        return [], "empty"
    return rows, None


def summarize_lhb(rows: list[dict[str, Any]], limit: int = 10) -> dict[str, Any]:
    """Aggregate a day's board rows for the prompt and the payload.

    ``net_buy`` / ``net_sell`` are ranked on the **per-name net** (a name that
    appears under two reasons must not count twice), which is why the totals
    are summed over ``symbol`` rather than over rows.
    """
    by_symbol: dict[str, dict[str, Any]] = {}
    for row in rows:
        symbol = row["symbol"]
        current = by_symbol.get(symbol)
        if current is None:
            by_symbol[symbol] = {
                "symbol": symbol,
                "name": row["name"],
                "change_pct": row["change_pct"],
                "net_amount": row["net_amount"] or 0.0,
                "reasons": [row["reason"]] if row["reason"] else [],
            }
            continue
        current["net_amount"] += row["net_amount"] or 0.0
        if row["reason"] and row["reason"] not in current["reasons"]:
            current["reasons"].append(row["reason"])

    items = sorted(by_symbol.values(), key=lambda r: r["net_amount"], reverse=True)
    total_net = sum(r["net_amount"] for r in items)
    institutions = sum(1 for r in items if r["net_amount"] > 0)
    return {
        "count": len(items),
        "net_buy": items[:limit],
        "net_sell": sorted(items, key=lambda r: r["net_amount"])[:limit],
        "total_net_amount": total_net,
        "net_buy_count": institutions,
        "reasons": sorted({row["reason"] for row in rows if row["reason"]}),
    }


def build_recap_context(rows: list[dict[str, Any]], limit: int = 8) -> str:
    """Board block for the recap prompt. Empty string when there is no data.

    An empty block is deliberate: the prompt section is omitted entirely, so
    the model never sees "龙虎榜: (暂无)" and never invents one.
    """
    if not rows:
        return ""
    summary = summarize_lhb(rows, limit=limit)
    lines = [
        f"- 上榜家数: {summary['count']} (净买入 {summary['net_buy_count']} 家)",
        f"- 合计净额: {summary['total_net_amount'] / 1e8:+.2f} 亿元",
    ]
    if summary["net_buy"]:
        lines.append(
            "- 净买入前列: "
            + "、".join(
                f"{r['name']}({r['net_amount'] / 1e8:+.2f}亿)"
                for r in summary["net_buy"][:limit]
            )
        )
    if summary["net_sell"]:
        lines.append(
            "- 净卖出前列: "
            + "、".join(
                f"{r['name']}({r['net_amount'] / 1e8:+.2f}亿)"
                for r in summary["net_sell"][:limit]
            )
        )
    if summary["reasons"]:
        lines.append("- 上榜原因: " + "、".join(summary["reasons"][:6]))
    return "\n".join(lines)


__all__ = [
    "build_recap_context",
    "fetch_lhb_day",
    "summarize_lhb",
]
