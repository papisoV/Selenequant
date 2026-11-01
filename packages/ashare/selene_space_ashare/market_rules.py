# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); selene-ized: the limit-band rule
# itself moved to :mod:`selene_space_risk.price_limits` so the pre-trade risk
# gate and this package share one implementation.
"""A-share market rules: ST/delisting name detection, board classification,
price limit ratios, and limit-up/down price computation.

The band arithmetic lives in :mod:`selene_space_risk.price_limits`; this module
keeps the ashare-facing surface (a positional ``name`` argument, plus the
``is_st`` / ``board_of`` helpers other ashare modules already import) and
delegates. Callers that need a historical band must pass ``trade_date`` — a
replay of stored bars cannot inherit today's rulebook by accident.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from selene_space_risk.price_limits import (
    BEIJING_BOARD_LIMIT,
    GROWTH_BOARD_LIMIT,
    LEGACY_MAIN_BOARD_ST_LIMIT,
    MAIN_BOARD_LIMIT,
    MAIN_BOARD_ST_LIMIT_CHANGE_DATE,
    TWO_PLACES,
    board_of_code,
    coerce_date,
    core_code,
    is_legacy_main_board_st,
    limit_down_price as _limit_down_price,
    limit_up_price as _limit_up_price,
    price_limit_ratio,
    round_to_cent,
)

_TOLERANCE: Decimal = Decimal("1e-9")


def _normalize_code(code: str) -> str:
    """Strip exchange suffix, keep the 6-digit core (e.g. "600519.SH" -> "600519")."""
    return core_code(code)


def is_st(name: str) -> bool:
    """True if the stock name indicates ST/*ST or delisting arrangement.

    ST matching is case-sensitive: only uppercase "ST" counts;
    "退" anywhere in the name marks a delisting-period stock.

    This is a **name label** — the screener drops ST names and the concept
    ranker keeps them out of a theme's ladder. It is not the price-band
    predicate: a 退市整理期 name carries no 风险警示 discount, so the band rule
    asks :func:`is_risk_warning_name` instead (as TSP's ``rule_for`` does).
    """
    return "ST" in name or "退" in name


def is_risk_warning_name(name: str) -> bool:
    """True when the name carries the exchange's 风险警示 (ST / *ST) mark.

    Case-insensitive on purpose: the band follows the designation, and a
    lowercased name from a JSON round-trip still denotes the same stock.
    Delisting-period names ("某某退") do **not** count — their limit is the
    board's ordinary band, not the legacy 5%.
    """
    return "ST" in str(name or "").upper()


def board_of(code: str) -> str:
    """Classify an A-share code into "main" / "kcb" / "cyb" / "bse"."""
    return board_of_code(code)


def limit_ratio(
    code: str,
    name: str = "",
    *,
    trade_date: date | datetime | str | None = None,
) -> Decimal:
    """Daily price-limit ratio for ``code`` on ``trade_date``.

    The 5% band applies only to **main-board** risk-warning names before
    2026-07-06; growth and BSE boards keep their ordinary band whether or not
    the name is ST. ``trade_date=None`` means the current rulebook.
    """
    return price_limit_ratio(
        code,
        is_risk_warning=is_risk_warning_name(name),
        trade_date=trade_date,
    )


def limit_up_price(
    prev_close: Decimal | str | float,
    code: str,
    name: str = "",
    *,
    trade_date: date | datetime | str | None = None,
) -> Decimal:
    """Limit-up price: prev_close * (1 + ratio), rounded half-up to the cent."""
    return _limit_up_price(
        prev_close,
        code,
        is_risk_warning=is_risk_warning_name(name),
        trade_date=trade_date,
    )


def limit_down_price(
    prev_close: Decimal | str | float,
    code: str,
    name: str = "",
    *,
    trade_date: date | datetime | str | None = None,
) -> Decimal:
    """Limit-down price: prev_close * (1 - ratio), rounded half-up to the cent."""
    return _limit_down_price(
        prev_close,
        code,
        is_risk_warning=is_risk_warning_name(name),
        trade_date=trade_date,
    )


def is_suspended(price: Decimal | str | float | None) -> bool:
    """True if the price indicates a suspended/barred quote (None, '', '-', non-positive)."""
    if price is None:
        return True
    if isinstance(price, str):
        text = price.strip()
        if text in ("", "-"):
            return True
        try:
            value = Decimal(text)
        except Exception:
            return True
    else:
        value = Decimal(str(price))
    return value <= 0


def is_price_at_limit(
    price: Decimal | str | float,
    prev_close: Decimal | str | float,
    code: str,
    name: str = "",
    *,
    trade_date: date | datetime | str | None = None,
) -> bool:
    """True if price touches (within 1e-9 tolerance) the limit-up or limit-down price."""
    value = Decimal(str(price))
    close = Decimal(str(prev_close))
    up = limit_up_price(close, code, name, trade_date=trade_date)
    down = limit_down_price(close, code, name, trade_date=trade_date)
    return abs(value - up) <= _TOLERANCE or abs(value - down) <= _TOLERANCE


__all__ = [
    "BEIJING_BOARD_LIMIT",
    "GROWTH_BOARD_LIMIT",
    "LEGACY_MAIN_BOARD_ST_LIMIT",
    "MAIN_BOARD_LIMIT",
    "MAIN_BOARD_ST_LIMIT_CHANGE_DATE",
    "TWO_PLACES",
    "board_of",
    "coerce_date",
    "is_legacy_main_board_st",
    "is_price_at_limit",
    "is_risk_warning_name",
    "is_st",
    "is_suspended",
    "limit_down_price",
    "limit_ratio",
    "limit_up_price",
    "price_limit_ratio",
    "round_to_cent",
]
