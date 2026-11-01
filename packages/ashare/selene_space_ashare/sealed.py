# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 papisoV
# Source: https://github.com/papisoV/tick-stock-panel
"""Sealed-at-limit classification from the free level-1 depth in the quote feed.

## Why this exists

The ladder's ``sealed`` flag today is a *price* test: ``last`` reaches the limit
price (see :func:`~selene_space_ashare.ladder._reaches_limit`). That says a name
is **at** the limit at the moment of the snapshot — but not whether it is
**held** there. A 涨停 with offers still resting on the sell side is a 假封板
about to open; it prints the same ``last`` as one with the book swept clean.

The whole-market snapshot payload already carries five levels of depth on both
Sina and 腾讯 (verified live 2026-10-17 — no extra request, no paid feed), so
the distinction is available for free. This module turns it into an answer.

## The rule (ported verbatim from TSP's ``depth_service._seal_locked``)

* 涨停真封 ⇔ **no resting sell order at the limit**: ``ask1_vol == 0``.
* 跌停真封 ⇔ **no resting buy order at the limit**: ``bid1_vol == 0``.

Measured on the 60 real limit-ups of the 2026-10-16 session: 58 held
``sell1_vol == 0``; two did not (``000678.SZ`` at 523789 shares,
``002531.SZ`` at 191100) — precisely the 假封板 this module makes visible.

## The auction-window trap (do not remove the guard)

TSP carries a scar here (``depth_service.py``): between 11:30 and 13:00 the
book is not in continuous trading, and 12:55–13:00 is the afternoon call-auction
preparation window where level-1 semantics differ. Computing "sealed" there and
caching it **overwrote a correct 11:30 seal with a false 假涨停**. So a
classification is only issued inside continuous trading (09:30–11:30 /
13:00–15:00 CST, weekdays), judged by the **quote's own** timestamp rather than
wall-clock "now" — the quote time is the honest basis, and it keeps the result
deterministic under test.

Outside that window the answer is ``None`` ("not decidable"), never a guess.

Note the afternoon window ends at 15:00 inclusive, so it also covers the
14:57–15:00 closing call auction. That is deliberate and matches TSP's guard
verbatim: the scar was the *lunch* auction overwriting a good seal, whereas the
close is the final state — a row stamped 15:00:00 is the settled closing book
and is worth deciding. Do not narrow this to 14:57 without re-measuring; a
divergence here would put selene and TSP on different answers for the close.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Any, Mapping

from selene_space_ashare.market_rules import limit_down_price, limit_up_price

_CST = timezone(timedelta(hours=8))

# Same tolerance the ladder's price test uses, so "at the limit" here and there
# can never disagree about the same print.
_LIMIT_TOL = 1e-9

# Continuous-trading windows (CST). The quote's own time decides, so a snapshot
# taken at 12:58 is refused rather than misread.
_MORNING_OPEN = dt_time(9, 30)
_MORNING_CLOSE = dt_time(11, 30)
_AFTERNOON_OPEN = dt_time(13, 0)
_AFTERNOON_CLOSE = dt_time(15, 0)

# `direction` values.
UP = "up"
DOWN = "down"
NONE = "none"

# `reason` values — every branch that yields a non-answer says which it was.
REASON_SEALED = "sealed"
REASON_OPEN = "open"
REASON_NOT_AT_LIMIT = "not_at_limit"
REASON_NO_PREV_CLOSE = "no_prev_close"
REASON_NO_DEPTH = "no_depth"
REASON_NO_QUOTE_TIME = "no_quote_time"
REASON_NOT_CONTINUOUS = "not_continuous"


@dataclass(frozen=True)
class SealedState:
    """One symbol's sealed state at a price limit.

    ``sealed`` is the tri-state the consumers key off:

    * ``True``  — held: the opposing side of the book is empty at the limit;
    * ``False`` — **at the limit but not held** (a 假封板 / 假跌停) — the whole
      point of this module, and indistinguishable from ``True`` under the
      price-only test the ladder used before;
    * ``None``  — not decidable here, with ``reason`` saying why (not at a
      limit, depth or quote time absent, or outside continuous trading).

    ``vol`` is the 封单量 in **shares** — the resting size on the side that
    *holds* the limit (buy-one size for a 涨停, sell-one for a 跌停). ``None``
    whenever ``sealed`` is not ``True``: there is no seal to size.
    """

    direction: str
    sealed: bool | None
    vol: float | None
    reason: str


def _field(row: object, name: str, default: Any = None) -> Any:
    """Read a field from a duck-typed row — attribute or mapping key.

    The market-data package's ``SnapshotRow`` is a frozen dataclass (attribute
    access); a caller that round-tripped the cross-section through JSON holds
    plain dicts (item access). Mirrors ``ladder._field`` — this package does not
    depend on the market-data package, so the contract is duck-typed.
    """
    value = getattr(row, name, None)
    if value is not None:
        return value
    if isinstance(row, Mapping):
        return row.get(name, default)
    return default


def in_continuous_trading(moment: datetime) -> bool:
    """True inside A-share continuous trading (CST), weekdays only.

    Holiday-unaware on purpose, matching the sources' own session windows: a
    closed session serves a frozen cross-section, and this guard exists to
    refuse the *auction* windows, not to keep an exchange calendar.

    Args:
        moment: timezone-aware instant. A naive datetime is read as CST — the
            feed is CST-stamped and a naive `now()` on a UTC host would be 8h
            off, silently shifting which window is open.
    """
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_CST)
    local = moment.astimezone(_CST)
    if local.weekday() >= 5:
        return False
    t = local.time()
    return (_MORNING_OPEN <= t <= _MORNING_CLOSE) or (
        _AFTERNOON_OPEN <= t <= _AFTERNOON_CLOSE
    )


def _at_or_above(price: float, target: Any) -> bool:
    return bool(price >= float(target) - _LIMIT_TOL)


def _at_or_below(price: float, target: Any) -> bool:
    return bool(price <= float(target) + _LIMIT_TOL)


def classify_sealed(row: object) -> SealedState:
    """Classify a snapshot row's sealed state at a price limit.

    Args:
        row: a duck-typed snapshot row exposing ``symbol`` / ``name`` /
            ``last`` / ``prev_close`` / ``bid1_vol`` / ``ask1_vol`` /
            ``timestamp``. Missing depth or timestamp fields read as ``None``
            and degrade to a ``None`` verdict — never to a fabricated seal.

    Returns:
        A :class:`SealedState`. ``direction`` is ``"up"`` / ``"down"`` /
        ``"none"``; ``sealed`` is tri-state (see the dataclass).

    A row that is at neither limit yields ``direction="none"``,
    ``sealed=None`` — the caller decides whether that is "not a limit-up today"
    (normal) or "the feed is broken" (it cannot tell from one row alone).
    """
    symbol = str(_field(row, "symbol") or "")
    code = symbol.split(".")[0]
    name = str(_field(row, "name") or "")
    last = _field(row, "last")
    prev_close = _field(row, "prev_close")

    if last is None or prev_close is None:
        return SealedState(NONE, None, None, REASON_NO_PREV_CLOSE)
    try:
        price = float(last)
        base = float(prev_close)
    except (TypeError, ValueError):
        return SealedState(NONE, None, None, REASON_NO_PREV_CLOSE)
    if price != price or base != base or price <= 0 or base <= 0:
        # A first-day listing publishes prev_close == 0; a limit price of 0
        # would make every such name read as a limit-up.
        return SealedState(NONE, None, None, REASON_NO_PREV_CLOSE)

    # The band that ruled on *this print's* session, not on today's rulebook:
    # a stale snapshot (or a replay) can carry a tape from before the
    # 2026-07-06 ST reform, when a 10% print was not a limit-up on a
    # main-board ST name. A missing stamp leaves the date unknown, which the
    # rule resolves as the current rulebook — and the row is then refused
    # below for want of a quote time anyway.
    stamp = _field(row, "timestamp")
    moment = _quote_moment(stamp)
    trade_date = None if moment is None else moment.date()

    at_up = _at_or_above(price, limit_up_price(base, code, name, trade_date=trade_date))
    at_down = _at_or_below(
        price, limit_down_price(base, code, name, trade_date=trade_date)
    )
    if not at_up and not at_down:
        return SealedState(NONE, None, None, REASON_NOT_AT_LIMIT)

    # A 涨停 holds when nothing is offered against it; a 跌停 holds when no one
    # bids against it. See the module docstring for the TSP provenance.
    direction = UP if at_up else DOWN
    holding_vol = _field(row, "ask1_vol") if at_up else _field(row, "bid1_vol")
    if holding_vol is None:
        return SealedState(direction, None, None, REASON_NO_DEPTH)

    if moment is None:
        return SealedState(direction, None, None, REASON_NO_QUOTE_TIME)
    if not in_continuous_trading(moment):
        return SealedState(direction, None, None, REASON_NOT_CONTINUOUS)

    try:
        resting = float(holding_vol)
    except (TypeError, ValueError):
        return SealedState(direction, None, None, REASON_NO_DEPTH)
    if resting < 0:
        return SealedState(direction, None, None, REASON_NO_DEPTH)

    if resting == 0:
        # Sealed: size the seal by the *supporting* side (涨停买一 / 跌停卖一).
        support = _field(row, "bid1_vol") if at_up else _field(row, "ask1_vol")
        try:
            vol = None if support is None else float(support)
        except (TypeError, ValueError):
            vol = None
        return SealedState(direction, True, vol, REASON_SEALED)
    return SealedState(direction, False, None, REASON_OPEN)


def _quote_moment(stamp: Any) -> datetime | None:
    """Epoch-ms (or None) → CST-aware datetime for the session guard.

    Takes ``Any`` because the stamp arrives duck-typed from ``_field``: a real
    row carries an int, a JSON round-trip carries a float or str. The runtime
    coercion below is the validator.
    """
    try:
        ms = int(stamp)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=_CST)


def sealed_counts(rows: Mapping[str, object]) -> dict[str, int | float | bool]:
    """Aggregate a whole cross-section into sealed/unsealed counts.

    This is the breadth view TSP's dashboard shows — "涨停 N, 其中真封 M" — and
    the reason the per-symbol verdict is worth computing at all.

    Args:
        rows: ``{symbol: snapshot row}``, the read face of the whole-market
            snapshot source.

    Returns:
        ``real_up`` / ``fake_up`` (真封 / 假封 涨停), the same pair for 跌停
        (``real_down`` / ``fake_down``), and ``undecided`` — at a limit but
        without a verdict (no depth, no quote time, or off continuous
        trading). ``ready`` is ``False`` when nothing could be decided, so a
        caller can tell "a quiet tape with no limit-ups" from "depth is
        missing across the board" and refuse to render a 0 as if it meant
        something.
    """
    real_up = fake_up = real_down = fake_down = undecided = 0
    for row in rows.values():
        state = classify_sealed(row)
        if state.direction == NONE:
            continue
        if state.sealed is None:
            undecided += 1
        elif state.direction == UP:
            if state.sealed:
                real_up += 1
            else:
                fake_up += 1
        else:
            if state.sealed:
                real_down += 1
            else:
                fake_down += 1

    decided = real_up + fake_up + real_down + fake_down
    return {
        "real_up": real_up,
        "fake_up": fake_up,
        "real_down": real_down,
        "fake_down": fake_down,
        "undecided": undecided,
        "ready": decided > 0,
    }


__all__ = [
    "DOWN",
    "NONE",
    "UP",
    "SealedState",
    "classify_sealed",
    "in_continuous_trading",
    "sealed_counts",
]
