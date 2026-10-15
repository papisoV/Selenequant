# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
"""A-share market rules: ST/deListing name detection, board classification,
price limit ratios, and limit-up/down price computation."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Final

TWO_PLACES: Final = Decimal("0.01")

_LIMIT_MAIN: Final = Decimal("0.10")
_LIMIT_ST: Final = Decimal("0.05")
_LIMIT_KCB_CYB: Final = Decimal("0.20")
_LIMIT_BSE: Final = Decimal("0.30")

_TOLERANCE: Final = Decimal("1e-9")


def _normalize_code(code: str) -> str:
    """Strip exchange suffix, keep the 6-digit core (e.g. "600519.SH" -> "600519")."""
    return code.strip().split(".")[0]


def is_st(name: str) -> bool:
    """True if the stock name indicates ST/*ST or delisting arrangement.

    ST matching is case-sensitive: only uppercase "ST" counts;
    "退" anywhere in the name marks a delisting-period stock.
    """
    return "ST" in name or "退" in name


def board_of(code: str) -> str:
    """Classify an A-share code into "main" / "kcb" / "cyb" / "bse"."""
    core = _normalize_code(code)
    if core.startswith(("688", "689")):
        return "kcb"
    if core.startswith(("300", "301")):
        return "cyb"
    if core.startswith(("8", "4", "920")):
        return "bse"
    return "main"


def limit_ratio(code: str, name: str = "") -> Decimal:
    """Daily price limit ratio. ST name takes precedence over board classification."""
    if is_st(name):
        return _LIMIT_ST
    board = board_of(code)
    if board in ("kcb", "cyb"):
        return _LIMIT_KCB_CYB
    if board == "bse":
        return _LIMIT_BSE
    return _LIMIT_MAIN


def _round_to_cent(value: Decimal) -> Decimal:
    return value.quantize(TWO_PLACES, rounding=ROUND_HALF_UP)


def limit_up_price(
    prev_close: Decimal | str | float, code: str, name: str = ""
) -> Decimal:
    """Limit-up price: prev_close * (1 + ratio), rounded half-up to the cent."""
    close = Decimal(str(prev_close))
    return _round_to_cent(close * (Decimal(1) + limit_ratio(code, name)))


def limit_down_price(
    prev_close: Decimal | str | float, code: str, name: str = ""
) -> Decimal:
    """Limit-down price: prev_close * (1 - ratio), rounded half-up to the cent."""
    close = Decimal(str(prev_close))
    return _round_to_cent(close * (Decimal(1) - limit_ratio(code, name)))


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
) -> bool:
    """True if price touches (within 1e-9 tolerance) the limit-up or limit-down price."""
    value = Decimal(str(price))
    close = Decimal(str(prev_close))
    up = limit_up_price(close, code, name)
    down = limit_down_price(close, code, name)
    return abs(value - up) <= _TOLERANCE or abs(value - down) <= _TOLERANCE
