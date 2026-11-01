# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 papisoV
# Source: https://github.com/papisoV/tick-stock-panel
"""Trade-execution gates for the A-share backtest: limit-board interception.

TSP's backtest engine (``backend/app/backtest/engine.py``) refuses trades a
real account could not have filled:

- **buy at limit-up close** (``close_t`` fill): a stock sealed at its limit-up
  at the signal close has a queue the order cannot reach — filling it at the
  limit price inflates backtest returns. TSP's own comment: 「旧实现只拦一字
  板, 尾盘封板股按涨停价'成交'导致回测收益虚高」.
- **sell at limit-down close**: the mirror — a sealed limit-down close cannot
  exit; TSP defers to the first sellable day (``pending_exit``).

Selene's backtest is a *forward-return* framework, not a fill-by-fill
simulator, so the same semantics land as two panel-level transforms:

- :func:`mask_limit_up_entries` — drop limit-up-sealed rows from the
  cross-section *before* top-N selection (the next-ranked stock takes the
  slot, exactly what a live top-N book would have done).
- :func:`defer_limit_down_exits` — rewrite each position's exit price from
  the fixed horizon close to the close of the **first non-sealed-limit-down
  day at or after the horizon**. Holding an un-sellable stock is not a choice
  the strategy made; charging it to the strategy's holding-period return as
  if it exited on time underestimates the loss.

Both use the shared, date-aware band rules in
:mod:`selene_space_risk.price_limits` (the item-1 unified implementation) —
never the pre-item-1 divergent copies.
"""

from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from selene_space_risk.price_limits import (
    limit_down_price,
    limit_up_price,
)

logger = logging.getLogger(__name__)

# A frame shorter than this has no meaningful prev-close history; bands from
# a single bar's self-referential prev_close would misfire on every row.
MIN_ROWS_FOR_BANDS = 2

# TSP force-closes still-blocked positions at end of period; the analogue
# here is a bounded deferral window, after which the forward return is NaN.
MAX_DEFER_DAYS = 10


def attach_limit_flags(
    panel: pd.DataFrame,
    *,
    close_col: str = "close",
    symbol_col: str = "symbol",
    date_col: str = "date",
    name_col: str = "name",
) -> pd.DataFrame:
    """Add ``limit_up_sealed`` / ``limit_down_sealed`` bool columns to a panel.

    Must be called on the **whole time series** per symbol — prev_close is
    ``close.shift(1)`` within each symbol, sorted by ``date``. A cross-section
    (single date) frame cannot judge its own bands; call this once on the
    panel *before* any per-date grouping, then let
    :func:`mask_limit_up_entries` drop the sealed rows.

    Rows whose band cannot be judged (first bar of a symbol, prev_close NaN,
    close <= 0) read ``False`` — fail-open, same direction as
    :mod:`selene_space_risk.price_limits` (unknown date = current rulebook,
    never a fabricated historical band).
    """
    required = {close_col, symbol_col}
    if panel.empty or not required.issubset(panel.columns):
        out = panel.copy()
        out["limit_up_sealed"] = False
        out["limit_down_sealed"] = False
        return out

    parts = []
    for _, group in panel.groupby(symbol_col, sort=False):
        g = (
            group.sort_values(date_col, kind="stable")
            if date_col in group.columns
            else group
        )
        closes = pd.to_numeric(g[close_col], errors="coerce")
        prev = closes.shift(1)
        names = (
            g[name_col].fillna("").astype(str)
            if name_col in g.columns
            else pd.Series("", index=g.index)
        )
        dates = (
            g[date_col]
            if date_col in g.columns
            else pd.Series([None] * len(g), index=g.index)
        )
        symbols = g[symbol_col].astype(str)

        up = np.zeros(len(g), dtype=bool)
        down = np.zeros(len(g), dtype=bool)
        values, prevs = closes.to_numpy(), prev.to_numpy()
        for i in range(len(g)):
            close, prev_close = values[i], prevs[i]
            if pd.isna(prev_close) or pd.isna(close) or close <= 0 or prev_close <= 0:
                continue
            try:
                up_price = float(
                    limit_up_price(
                        prev_close,
                        symbols.iloc[i],
                        is_risk_warning=bool(names.iloc[i]),
                        trade_date=dates.iloc[i],
                    )
                )
                down_price = float(
                    limit_down_price(
                        prev_close,
                        symbols.iloc[i],
                        is_risk_warning=bool(names.iloc[i]),
                        trade_date=dates.iloc[i],
                    )
                )
            except Exception:
                continue
            up[i] = close >= up_price
            down[i] = close <= down_price
        parts.append(
            pd.DataFrame(
                {"limit_up_sealed": up, "limit_down_sealed": down}, index=g.index
            )
        )

    flags = (
        pd.concat(parts)
        if parts
        else pd.DataFrame({"limit_up_sealed": [], "limit_down_sealed": []})
    )
    out = panel.copy()
    out = out.join(flags).fillna({"limit_up_sealed": False, "limit_down_sealed": False})
    out["limit_up_sealed"] = out["limit_up_sealed"].astype(bool)
    out["limit_down_sealed"] = out["limit_down_sealed"].astype(bool)
    return out


def mask_limit_up_entries(
    cross_section: pd.DataFrame,
) -> pd.DataFrame:
    """Drop rows sealed at limit-up so top-N selection cannot buy them.

    Expects the ``limit_up_sealed`` column from :func:`attach_limit_flags`
    (band judgement needs each symbol's own prior bars, so it is computed at
    panel level — a per-date cross-section cannot shift prev_close correctly).
    A frame without that column passes through unchanged: the gate only
    activates when the producer attached the flags, and silently filtering on
    an absent signal would be the 「产出但没人读」 failure mode in reverse.
    """
    if cross_section.empty or "limit_up_sealed" not in cross_section.columns:
        return cross_section
    sealed = cross_section["limit_up_sealed"].astype(bool)
    if not sealed.any():
        return cross_section
    out = cross_section[~sealed.to_numpy()]
    logger.info(
        "backtest gate: dropped %d limit-up-sealed entry rows", int(sealed.sum())
    )
    return out


def defer_limit_down_exits(
    panel: pd.DataFrame,
    forward_col: str,
    horizon: int,
    *,
    close_col: str = "close",
    symbol_col: str = "symbol",
    date_col: str = "date",
    max_defer_days: int = MAX_DEFER_DAYS,
) -> pd.DataFrame:
    """Rewrite ``forward_col`` so a limit-down-sealed horizon close defers.

    Expects ``limit_down_sealed`` from :func:`attach_limit_flags` — without
    that column the panel passes through unchanged (the gate only activates
    when its input signal was produced, mirroring mask_limit_up_entries).

    For each (symbol, date) row whose horizon close (``close.shift(-horizon)``
    position, found by date order within the symbol) is sealed at limit-down,
    the forward return is recomputed against the close of the **first
    non-sealed day at or after the horizon**. Deferral runs up to
    ``max_defer_days``: past that the row's forward return becomes NaN — the
    position never got a fill, and pretending it did would be fabrication
    (matches TSP: end-of-period still-blocked positions are force-closed at
    the last price *with* the blocked marker kept).

    Non-deferred rows are value-identical: the rewrite touches only rows
    whose horizon close is sealed.
    """
    if (
        panel.empty
        or forward_col not in panel.columns
        or close_col not in panel.columns
        or "limit_down_sealed" not in panel.columns
        or "date" not in panel.columns
        or horizon <= 0
    ):
        return panel

    ws = panel.copy()
    touched = 0
    given_up = 0

    for symbol, group in ws.groupby(symbol_col, sort=False):
        group = group.sort_values(date_col, kind="stable")
        closes = pd.to_numeric(group[close_col], errors="coerce")
        sealed = group["limit_down_sealed"].astype(bool).to_numpy()
        n = len(group)
        if n < MIN_ROWS_FOR_BANDS:
            continue
        fwd = ws.loc[group.index, forward_col].copy()
        fwd_values = fwd.to_numpy()
        close_values = closes.to_numpy()
        for i in range(n):
            j = i + horizon
            if j >= n or not sealed[j] or pd.isna(fwd_values[i]):
                continue
            # horizon close is sealed limit-down: find first non-sealed day
            replacement = None
            for k in range(j, min(j + max_defer_days, n)):
                if not sealed[k]:
                    replacement = k
                    break
            if replacement is None:
                fwd_values[i] = np.nan
                given_up += 1
            else:
                base = close_values[i]
                exit_close = close_values[replacement]
                if pd.isna(base) or pd.isna(exit_close) or base <= 0:
                    continue
                fwd_values[i] = exit_close / base - 1.0
                touched += 1
        ws.loc[group.index, forward_col] = fwd_values

    if touched or given_up:
        logger.info(
            "backtest gate: deferred %d limit-down-sealed exits, gave up on %d "
            "(no sellable day within %d)",
            touched,
            given_up,
            max_defer_days,
        )
    return ws
