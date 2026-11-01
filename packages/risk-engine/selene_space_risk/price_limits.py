# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 papisoV
# Source: https://github.com/papisoV/tick-stock-panel
"""A-share daily price-limit bands (涨跌停), date-aware and board-scoped.

The 5% band is **not** a property of "ST" — it is a legacy attribute of the
Shanghai/Shenzhen **main board's** 风险警示 designation:

===========================  ==========  ==========================
board                        ordinary    risk-warning (ST/*ST)
===========================  ==========  ==========================
main, before 2026-07-06      10%         5%
main, from 2026-07-06        10%         10%
创业板 / 科创板 (growth)      20%         20%
北交所 (BSE)                 30%         30%
===========================  ==========  ==========================

The growth and BSE boards never carried an ST discount: a 20%-band board's
risk-warning names keep the 20% band. Applying 5% to them (as the pre-2026-10
implementations did) both misses real limit-ups and invents ones that never
happened.

``MAIN_BOARD_ST_LIMIT_CHANGE_DATE`` records the 2026-07-06 reform under which
main-board risk-warning stocks joined the ordinary 10% band and the special
±15% abnormal-move regime was repealed. The date is sourced from TSP's
``price_limits.py`` / ``services/abnormal_moves.py``, which cite
上交所《交易规则(2026年修订)》; it is kept as one named constant so a later
correction is a single-line change.

Time semantics: ``trade_date=None`` means **the current rulebook**, which is
what a live quote or a fresh order needs. A historical caller that omits its
session date gets today's band, not a guess at an old one — so anything
replaying stored bars must pass the bar's own date.

**Not covered: the 注册制 no-limit window.** Under registration-based listing, a
newly listed stock trades without a daily band for its first few sessions (5
for 沪深主板/创业板/科创板, 1 for 北交所, scoped by listing date). TSP gates this
with ``is_no_limit_day(symbol, listing_date, trade_date)`` reading an
``instruments.listing_date`` dimension table. **Selene has no ``listing_date``
data anywhere**, so this module deliberately says nothing about it rather than
approximating: a caller that passes a date inside such a window gets the
board's ordinary band, and the first five sessions of a new listing may be
mis-read as limit-ups. Closing that needs a listing-date source first.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

TWO_PLACES: Final = Decimal("0.01")

MAIN_BOARD_LIMIT: Final = Decimal("0.10")
LEGACY_MAIN_BOARD_ST_LIMIT: Final = Decimal("0.05")
GROWTH_BOARD_LIMIT: Final = Decimal("0.20")
BEIJING_BOARD_LIMIT: Final = Decimal("0.30")

MAIN_BOARD_ST_LIMIT_CHANGE_DATE: Final = date(2026, 7, 6)

_BOARD_LIMITS: Final[dict[str, Decimal]] = {
    "main": MAIN_BOARD_LIMIT,
    "kcb": GROWTH_BOARD_LIMIT,
    "cyb": GROWTH_BOARD_LIMIT,
    "bse": BEIJING_BOARD_LIMIT,
}


def core_code(symbol: str) -> str:
    """Strip the exchange suffix, keep the numeric core.

    ``"600519.SH"`` → ``"600519"``; a bare code passes through unchanged.
    """
    return str(symbol).strip().split(".")[0]


def board_of_code(symbol: str) -> str:
    """Classify a code into ``"main"`` / ``"kcb"`` / ``"cyb"`` / ``"bse"``.

    Classification is by numeric prefix rather than exchange suffix so a bare
    code is classified as reliably as a suffixed one.
    """
    core = core_code(symbol)
    if core.startswith(("688", "689")):
        return "kcb"
    if core.startswith(("300", "301")):
        return "cyb"
    if core.startswith(("8", "4", "920")):
        return "bse"
    return "main"


def coerce_date(value: object) -> date | None:
    """Best-effort ``date`` from a date / datetime / ``YYYY-MM-DD`` / ``YYYYMMDD``.

    Returns ``None`` for anything else — including ``NaT`` and NaN — so an
    unparseable date reads as "unknown", never as a fabricated session.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        if value != value:  # NaT / NaN-like
            return None
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text or text.lower() in {"nan", "nat", "none", "null"}:
            return None
        compact = text.replace("-", "").replace("/", "")
        try:
            return datetime.strptime(compact, "%Y%m%d").date()
        except ValueError:
            return None
    return None


def is_risk_warning_name(name: str) -> bool:
    """True for a 风险警示 name (``ST`` / ``*ST`` / ``S*ST`` …).

    The exchange's designation is the ASCII ``ST`` badge, so the test is
    case-insensitive containment rather than a prefix: covered names come
    through as ``*ST某某``, ``S*ST某某`` and, on a few feeds, with padding.

    ``退`` names (退市整理期) are deliberately **not** risk-warning names here:
    they trade in the ordinary band for their board, not the legacy 5% one.
    :func:`selene_space_ashare.market_rules.is_st` answers the different
    question — "is this a name the screener should label" — and includes 退.
    """
    return "ST" in str(name or "").upper()


def is_legacy_main_board_st(
    symbol: str,
    is_risk_warning: bool,
    trade_date: date | datetime | str | None = None,
) -> bool:
    """True when the legacy 5% main-board risk-warning band applies.

    That is: main board, risk-warning name, **and** a session before
    :data:`MAIN_BOARD_ST_LIMIT_CHANGE_DATE`. An unknown date resolves to
    ``False`` — the current rulebook — because a live caller that could not
    supply a date is looking at today's tape, not at 2024's.
    """
    if not is_risk_warning or board_of_code(symbol) != "main":
        return False
    day = coerce_date(trade_date)
    if day is None:
        return False
    return day < MAIN_BOARD_ST_LIMIT_CHANGE_DATE


def price_limit_ratio(
    symbol: str,
    *,
    is_risk_warning: bool = False,
    trade_date: date | datetime | str | None = None,
) -> Decimal:
    """Daily price-limit ratio for ``symbol`` on ``trade_date``.

    Args:
        symbol: bare or suffixed A-share code.
        is_risk_warning: the exchange's 风险警示 designation (ST / *ST).
        trade_date: the session the band applies to. ``None`` means the
            current rulebook; see the module docstring.
    """
    if is_legacy_main_board_st(symbol, is_risk_warning, trade_date):
        return LEGACY_MAIN_BOARD_ST_LIMIT
    return _BOARD_LIMITS[board_of_code(symbol)]


def round_to_cent(value: Decimal) -> Decimal:
    """Round half-up to the cent — the exchange's own limit-price rounding."""
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def limit_up_from_ratio(prev_close: Decimal | str | float, ratio: Decimal) -> Decimal:
    """``prev_close * (1 + ratio)``, rounded half-up to the cent."""
    close = Decimal(str(prev_close))
    return round_to_cent(close * (Decimal(1) + ratio))


def limit_down_from_ratio(prev_close: Decimal | str | float, ratio: Decimal) -> Decimal:
    """``prev_close * (1 - ratio)``, rounded half-up to the cent."""
    close = Decimal(str(prev_close))
    return round_to_cent(close * (Decimal(1) - ratio))


def limit_up_price(
    prev_close: Decimal | str | float,
    symbol: str,
    *,
    is_risk_warning: bool = False,
    trade_date: date | datetime | str | None = None,
) -> Decimal:
    """Limit-up price for ``symbol`` on ``trade_date``."""
    return limit_up_from_ratio(
        prev_close,
        price_limit_ratio(
            symbol, is_risk_warning=is_risk_warning, trade_date=trade_date
        ),
    )


def limit_down_price(
    prev_close: Decimal | str | float,
    symbol: str,
    *,
    is_risk_warning: bool = False,
    trade_date: date | datetime | str | None = None,
) -> Decimal:
    """Limit-down price for ``symbol`` on ``trade_date``."""
    return limit_down_from_ratio(
        prev_close,
        price_limit_ratio(
            symbol, is_risk_warning=is_risk_warning, trade_date=trade_date
        ),
    )


__all__ = [
    "BEIJING_BOARD_LIMIT",
    "GROWTH_BOARD_LIMIT",
    "LEGACY_MAIN_BOARD_ST_LIMIT",
    "MAIN_BOARD_LIMIT",
    "MAIN_BOARD_ST_LIMIT_CHANGE_DATE",
    "TWO_PLACES",
    "board_of_code",
    "coerce_date",
    "core_code",
    "is_legacy_main_board_st",
    "limit_down_from_ratio",
    "limit_down_price",
    "limit_up_from_ratio",
    "limit_up_price",
    "price_limit_ratio",
    "round_to_cent",
]
