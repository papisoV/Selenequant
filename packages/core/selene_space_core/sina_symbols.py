"""Canonical A-share code/symbol ⇄ Sina prefix mapping.

Single source of truth for the board → Sina URL prefix rule. Before this
module the rule lived in two places with *different coverage*:

* ``selene_space_market.sources.astock_market_snapshot_source._sina_prefix``
  — correct for all boards (``6``→sh, ``0``/``3``→sz, ``8``/``4``/``920``→bj).
* ``selene_space_market.sources.astock_source._to_sina_symbol``
  — only ``{"SZ": "sz", "SH": "sh"}``, so ``920002.BJ`` raised ``ValueError``.
  The whole-market daily backfill hits this: 349 of the 5574 A-share codes are
  Beijing (``92xxxx``), and every one of them would be dropped.

The two rules drifting apart is the bug class ``kline_paths`` was written to
end for on-disk paths, applied to symbol formatting. Both call sites delegate
here.

Lives in ``selene_space_core`` — the leaf package both ``ashare`` (the
consumer side, B1/B3) and ``market-data`` (the source side) already depend on.
Putting it in either sibling would force a cross-sibling import the layout
forbids.
"""

from __future__ import annotations

from typing import Literal

SinaPrefix = Literal["sh", "sz", "bj"]

# Prefix → canonical exchange suffix (the ``.SH`` in ``600519.SH``).
_PREFIX_TO_SUFFIX = {"sh": "SH", "sz": "SZ", "bj": "BJ"}
# Suffix → prefix, the inverse map ``to_sina_symbol`` walks.
_SUFFIX_TO_PREFIX = {suffix: prefix for prefix, suffix in _PREFIX_TO_SUFFIX.items()}


def sina_prefix(code: str) -> SinaPrefix:
    """6-digit code → Sina exchange prefix (``sh`` / ``sz`` / ``bj``).

    Board classification follows the A-share universe convention: Shanghai
    codes start ``6`` (main) or ``68`` (STAR); Shenzhen ``0`` / ``3``;
    everything else (``8`` / ``4`` / ``920`` — the BSE ranges) is Beijing.

    Raises ``ValueError`` for anything that is not a 6-digit code, so a bad
    universe entry fails loudly rather than silently polling the wrong venue
    (Sina answers an empty payload for a mismatched prefix, which reads as
    "no data" instead of "wrong prefix").
    """
    if not code.isdigit() or len(code) != 6:
        raise ValueError(f"Invalid A-share code: {code!r}")
    if code[0] == "6":
        return "sh"
    if code[0] in ("0", "3"):
        return "sz"
    return "bj"


def code_to_symbol(code: str) -> str:
    """6-digit code → canonical ``<code>.<SH|SZ|BJ>`` symbol."""
    return f"{code}.{_PREFIX_TO_SUFFIX[sina_prefix(code)]}"


def to_sina_symbol(symbol: str) -> str:
    """``'000001.SZ'`` → ``'sz000001'``; ``'920002.BJ'`` → ``'bj920002'``.

    Accepts a suffixed symbol only — the suffix is what disambiguates a
    Beijing ``920002`` from any other board, so a bare code is rejected
    rather than guessed.
    """
    code, _, suffix = symbol.partition(".")
    prefix = _SUFFIX_TO_PREFIX.get(suffix.upper())
    if prefix is None:
        raise ValueError(f"Invalid A-share symbol: {symbol!r}")

    # Cross-check the suffix against the board the code actually belongs to:
    # ``920002.SZ`` is a malformed symbol, not a Shenzhen listing.
    expected = sina_prefix(code)
    if prefix != expected:
        raise ValueError(
            f"A-share symbol {symbol!r} has suffix {suffix!r} but code "
            f"{code!r} belongs to {expected!r}"
        )
    return f"{prefix}{code}"
