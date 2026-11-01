# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""Index quotes for the market overview — the four core indices, live.

## Why this is a separate module with its own symbol table

``selene_space_core.sina_symbols.to_sina_symbol`` deliberately refuses index
codes: it requires a suffixed symbol *and* cross-checks the suffix against the
board the bare code belongs to, so ``000001.SH`` raises (000001 is a Shenzhen
code). That rejection is correct for stocks and must not be relaxed — it is the
guard that stops a bare code from being silently routed to the wrong exchange.

Indices need the opposite treatment: their code space overlaps the stock space
(``000001`` is both 上证指数 and 平安银行), so an index is identified **only** by
its exchange-qualified Sina string. Hence the explicit table below, probed live
on 2026-10-20:

    sh000001 上证指数 · sz399001 深证成指 · sz399006 创业板指
    sh000680 科创综指 (NOT 000688 科创50 — TSP's fourth core index)

``sh000680`` answers ``科创综指`` and ``sh000688`` answers ``科创50``; the two
are different indices and TSP's ``index_const`` names 000680. The table is the
single authority — consumers key on the suffixed ``CODE.SH`` form.

## Failure contract

:func:`fetch_index_quotes` never raises. An unreachable endpoint returns
``(quotes_so_far, error)`` with a non-empty ``error``; the caller reports it
rather than rendering an empty list as "the market has no indices".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Suffixed symbol → Sina hq string. Order is the display order (上证 → 深证 →
# 创业板 → 科创); the overview renders them in this order.
CORE_INDEX_SYMBOLS: tuple[str, ...] = (
    "000001.SH",
    "399001.SZ",
    "399006.SZ",
    "000680.SH",
)

CORE_INDEX_NAMES: dict[str, str] = {
    "000001.SH": "上证指数",
    "399001.SZ": "深证成指",
    "399006.SZ": "创业板指",
    "000680.SH": "科创综指",
}

# 编制层口径的官方简称（摘要里用）。与 TSP 前端 INDEX_SHORT 对齐。
INDEX_SHORT: dict[str, str] = {
    "000001.SH": "上",
    "399001.SZ": "深",
    "399006.SZ": "创",
    "000680.SH": "科",
}

_SINA_HQ_URL = "https://hq.sinajs.cn/list={codes}"
_HEADERS = {
    "Referer": "https://finance.sina.com.cn/",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
}
_TIMEOUT_SEC = 10.0

# Sina hq field layout for an index row — verified live 2026-10-20 against all
# eight probed indices. Stocks and indices share 0..5 and 30/31; an index's
# payload carries no real depth, so fields 10/20 are empty strings.
_FIELD_NAME = 0
_FIELD_PREV_CLOSE = 2
_FIELD_LAST = 3
_FIELD_HIGH = 4
_FIELD_LOW = 5
_FIELD_VOLUME = 8
_FIELD_AMOUNT = 9
_FIELD_DATE = 30
_FIELD_TIME = 31

# Minimum comma-separated fields we require before reading the tail slots.
_MIN_FIELDS = 32


@dataclass(frozen=True)
class IndexQuote:
    """One index's live state. ``change_pct`` is a **percentage number**."""

    symbol: str
    name: str
    last: float | None
    prev_close: float | None
    change: float | None
    change_pct: float | None
    high: float | None
    low: float | None
    amount: float | None
    date: str | None
    time: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "last_price": self.last,
            "prev_close": self.prev_close,
            "change_amount": self.change,
            "change_pct": self.change_pct,
            "high": self.high,
            "low": self.low,
            "amount": self.amount,
            "date": self.date,
            "time": self.time,
        }


def _sina_code(symbol: str) -> str:
    """``000001.SH`` → ``sh000001``. Raises for anything not in the core table.

    Callers pass table keys, so an unknown value is a programming error, not
    upstream data — but the message names the table rather than a bare KeyError
    because the natural mistake is passing a stock symbol here.
    """
    if symbol not in CORE_INDEX_NAMES:
        raise ValueError(
            f"{symbol!r} is not a core index; see index_quotes.CORE_INDEX_SYMBOLS"
        )
    code, _, suffix = symbol.partition(".")
    return f"{suffix.lower()}{code}"


def _float(fields: list[str], index: int) -> float | None:
    try:
        value = float(fields[index])
    except (IndexError, ValueError):
        return None
    return value if value == value else None  # NaN guard


def _parse_line(line: str) -> tuple[str, list[str]] | None:
    """``var hq_str_sh000001="...";`` → ``("sh000001", fields)``."""
    if "hq_str_" not in line or '="' not in line:
        return None
    head, _, rest = line.partition('="')
    var = head.rsplit("hq_str_", 1)[-1].strip()
    body = rest.split('"', 1)[0]
    if not var or not body:
        return None
    return var, body.split(",")


def parse_index_response(text: str) -> dict[str, IndexQuote]:
    """Sina hq body → ``{suffixed symbol: IndexQuote}``.

    Rows for indices not in the core table are ignored (the request only asks
    for four, but a mirror echoing extras must not leak them into the payload).
    A row whose price slot is 0 (pre-open, or a frozen post-close feed) is
    omitted: publishing a 0% change for it would read as "flat", which is a
    statement the feed did not make.
    """
    by_sina = {_sina_code(s): s for s in CORE_INDEX_SYMBOLS}
    out: dict[str, IndexQuote] = {}
    for line in text.splitlines():
        parsed = _parse_line(line)
        if parsed is None:
            continue
        var, fields = parsed
        symbol = by_sina.get(var)
        if symbol is None or len(fields) < _MIN_FIELDS:
            continue
        last = _float(fields, _FIELD_LAST)
        prev_close = _float(fields, _FIELD_PREV_CLOSE)
        if last is None or last <= 0:
            continue
        change = None
        change_pct = None
        if prev_close is not None and prev_close > 0:
            change = last - prev_close
            change_pct = change / prev_close * 100.0
        out[symbol] = IndexQuote(
            symbol=symbol,
            name=(fields[_FIELD_NAME] or "").strip() or CORE_INDEX_NAMES[symbol],
            last=last,
            prev_close=prev_close,
            change=change,
            change_pct=change_pct,
            high=_float(fields, _FIELD_HIGH),
            low=_float(fields, _FIELD_LOW),
            amount=_float(fields, _FIELD_AMOUNT),
            date=(fields[_FIELD_DATE] or "").strip() or None,
            time=(fields[_FIELD_TIME] or "").strip() or None,
        )
    return out


def fetch_index_quotes(
    *, timeout: float = _TIMEOUT_SEC
) -> tuple[list[dict[str, Any]], str | None]:
    """Live quotes for the four core indices, in table order.

    Returns ``(quotes, error)``. ``error`` is ``None`` on a clean fetch; it is a
    short reason string when the endpoint failed or a row was missing, and the
    returned list still carries whatever parsed. Never raises — the overview
    degrades its index block rather than failing the page.
    """
    codes = ",".join(_sina_code(s) for s in CORE_INDEX_SYMBOLS)
    try:
        response = httpx.get(
            _SINA_HQ_URL.format(codes=codes),
            headers=_HEADERS,
            timeout=timeout,
            trust_env=False,
        )
        response.raise_for_status()
        # Sina declares GB18030; pin it rather than trust detection, whose
        # mis-decode corrupts the name field (same convention as the snapshot
        # source's `_http_batch`).
        response.encoding = "gb18030"
        text = str(response.text)
    except Exception as exc:  # noqa: BLE001 — fail-open by contract
        logger.warning("index_quotes: fetch failed: %s", exc)
        return [], f"fetch_failed: {exc}"

    parsed = parse_index_response(text)
    quotes = [
        parsed[symbol].as_dict() for symbol in CORE_INDEX_SYMBOLS if symbol in parsed
    ]
    if len(quotes) < len(CORE_INDEX_SYMBOLS):
        missing = [s for s in CORE_INDEX_SYMBOLS if s not in parsed]
        logger.warning("index_quotes: missing indices %s", missing)
        return quotes, f"missing: {','.join(missing)}"
    return quotes, None


__all__ = [
    "CORE_INDEX_NAMES",
    "CORE_INDEX_SYMBOLS",
    "INDEX_SHORT",
    "IndexQuote",
    "fetch_index_quotes",
    "parse_index_response",
]
