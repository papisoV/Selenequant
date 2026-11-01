# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 tick-stock-panel contributors
# Source: https://github.com/shy3130/tick-stock-panel
"""A-share market overview — the assembler behind the dashboard and the AI recap.

## What this is (and what it is not)

TSP computes the overview from ``kline_daily_enriched`` — a table of
pre-computed ``ma5/ma20/ma60``, ``vol_ratio_5d``, ``consecutive_limit_ups`` and
``signal_*`` booleans. **selene has no such table.** Its whole-market daily base
(``("astock","daily_all","1d")``) stores raw OHLCV plus the prior close, and
nothing else. So this module is a rewrite of the same view on top of the raw
bars, not a port.

Two consequences drive every design decision here:

* **Indicators are recomputed, not looked up.** A 120-session baseline read is
  the expensive part (~4-7 s); it is cached per trading day by the caller and
  the per-request work is pure assembly over an already-computed frame.
* **``amount`` does not exist in the daily base** (``market_daily`` explains
  why: Sina's kline endpoint publishes no turnover, and every substitute would
  carry precision the column does not have). Turnover therefore comes **only**
  from the live snapshot cross-section. Absent snapshot ⇒ ``amount_available``
  is ``False`` and no zero is written — a fabricated 0 reads as "nobody traded".

## The partial-newest-session trap (read before changing the date logic)

The newest session in the daily base is written incrementally and is regularly
**missing ~1600 of 5574 names** while it is still filling (measured 2026-10-19:
3973 rows). Any breadth/MA/量比 statistic that folds that session into the
baseline silently under-counts a third of the market.

The rule used here: **the baseline is every session strictly before ``today``;
today's prices come from the quote cross-section** (the snapshot when available,
otherwise today's own daily rows, with coverage reported). That is also why the
overview is correct intraday and post-close with the same code path.

## Calibration independence

Nothing here imports ``market_phase`` or ``ladder``'s panel-gated exports — the
overview must work on a fresh clone with no backfilled panel. Streak lengths are
computed locally from raw closes using the same limit rule as
:func:`selene_space_ashare.ladder.compute_consecutive_limit_ups`.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date as _date, timedelta
from typing import Any, Mapping, Sequence

import pandas as pd

from selene_space_ashare.market_daily import market_daily_window
from selene_space_ashare.market_rules import (
    board_of,
    coerce_date,
    is_st,
    limit_down_price,
    limit_ratio,
    limit_up_price,
)

logger = logging.getLogger(__name__)

# Sessions read for the baseline. 60 is the longest MA; 120 leaves room for a
# streak that started before the 60-day window (the longest observed streak on
# this base is 11, so 120 is generous) without paying for a full history read.
BASELINE_SESSIONS = 120

# Fewer than this many sessions and every MA is NaN; refuse rather than serve a
# page whose "above MA" block is silently empty.
MIN_BASELINE_SESSIONS = 60

# Same tolerance as the ladder's price test, so "at the limit" can never
# disagree between the two consumers about the same print.
_LIMIT_TOL = 1e-9

# Half a cent plus float slack: the exact limit price is the theoretical
# ``prev * (1 + ratio)`` rounded half-up to a cent, so it can sit at most
# 0.005 below the theoretical value. Anything below this bound cannot be at the
# limit, and every row this admits is re-checked with the Decimal rule. This is
# a *prefilter*, never the answer.
_CENT_PREFILTER = 0.0051

# Below this share of the universe a cross-section is treated as degraded
# rather than reported as a thin market. 40% is well under any normal session
# (97-100% observed) and well over a broken partial write (~71%).
MIN_COVERAGE = 0.4

# 8 涨跌幅 bands, matching TSP's dashboard.
_PCT_BANDS: tuple[tuple[str, float | None, float | None], ...] = (
    ("<-5%", None, -5.0),
    ("-5~-3%", -5.0, -3.0),
    ("-3~-1%", -3.0, -1.0),
    ("-1~0%", -1.0, 0.0),
    ("0~1%", 0.0, 1.0),
    ("1~3%", 1.0, 3.0),
    ("3~5%", 3.0, 5.0),
    (">5%", 5.0, None),
)


def _finite(value: Any) -> float | None:
    """Anything → finite float or ``None``. NaN/inf are "no data", never a value."""
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _score(value: float | None, low: float, high: float) -> int:
    """Map a value onto 0-100. Non-finite input is neutral (50), never an error."""
    if value is None or not math.isfinite(value) or high <= low:
        return 50
    return max(0, min(100, round((value - low) / (high - low) * 100)))


def _code_of(symbol: str) -> str:
    return str(symbol).split(".", 1)[0]


# ---------------------------------------------------------------------------
# Limit tests
# ---------------------------------------------------------------------------


def _ratio_cache() -> dict[tuple[str, bool, str], float]:
    return {}


def _ratio_of(
    code: str,
    name: str,
    cache: dict[tuple[str, bool, str], float],
    *,
    trade_date: object = None,
) -> float:
    """Limit ratio as a float, memoised on ``(code, is_st, date)``.

    ``limit_ratio`` returns a Decimal because the *price* must be exact; the
    ratio itself is only used for the float prefilter, where 1e-16 of slack is
    irrelevant next to the half-cent window. The date is part of the key: the
    main-board ST band moved 5% → 10% on 2026-07-06, so one window can hold two
    different ratios for the same symbol.
    """
    day = coerce_date(trade_date)
    key = (code, is_st(name), "" if day is None else day.isoformat())
    cached = cache.get(key)
    if cached is None:
        cached = float(limit_ratio(code, name, trade_date=day))
        cache[key] = cached
    return cached


def _exact_limit_up(
    prev: float, close: float, code: str, name: str, *, trade_date: object = None
) -> bool:
    if prev <= 0 or close <= 0:
        return False
    day = coerce_date(trade_date)
    return close >= float(limit_up_price(prev, code, name, trade_date=day)) - _LIMIT_TOL


def _exact_limit_down(
    prev: float, close: float, code: str, name: str, *, trade_date: object = None
) -> bool:
    if prev <= 0 or close <= 0:
        return False
    day = coerce_date(trade_date)
    return (
        close <= float(limit_down_price(prev, code, name, trade_date=day)) + _LIMIT_TOL
    )


def _flag_limits(frame: pd.DataFrame) -> pd.DataFrame:
    """Add ``sig_limit_up`` / ``sig_limit_down`` / ``sig_broken`` to a bar frame.

    ``frame`` needs ``symbol`` / ``name`` / ``close`` / ``prev_close`` /
    ``high`` and, when it spans the 2026-07-06 ST reform, ``date``. The float
    prefilter admits a superset and the Decimal rule decides — the same
    exactness as the ladder, at ~1% of the cost.

    The per-row ``date`` (when present) is what the band is resolved against.
    A frame without one — the live cross-section, which is one session by
    construction — falls back to the current rulebook; its caller passes the
    session explicitly when it wants historical exactness.

    ``sig_broken`` (炸板) is "touched the limit intraday but did not close
    there". A row that *closed* at the limit is not broken even if its high is
    the limit, hence the ``~up`` term.
    """
    cache = _ratio_cache()
    # Positional throughout: the caller may hand us any index (a filtered
    # baseline frame keeps its own), so nothing here may assume labels run
    # 0..n-1.
    up = pd.Series(False, index=frame.index, dtype=bool)
    down = pd.Series(False, index=frame.index, dtype=bool)
    broken = pd.Series(False, index=frame.index, dtype=bool)
    if frame.empty:
        frame = frame.copy()
        frame["sig_limit_up"], frame["sig_limit_down"], frame["sig_broken"] = (
            up,
            down,
            broken,
        )
        return frame

    code = frame["symbol"].astype(str).str.split(".").str[0].to_numpy()
    name = frame["name"].fillna("").astype(str).to_numpy()
    prev = pd.to_numeric(frame["prev_close"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    high = pd.to_numeric(frame["high"], errors="coerce")
    days = (
        [coerce_date(v) for v in frame["date"]]
        if "date" in frame.columns
        else [None] * len(frame)
    )
    ratio = pd.Series(
        [
            _ratio_of(c, n, cache, trade_date=d)
            for c, n, d in zip(code, name, days, strict=False)
        ],
        index=frame.index,
        dtype=float,
    )
    valid = prev.notna() & (prev > 0) & close.notna() & (close > 0)

    theo_up = prev * (1.0 + ratio)
    theo_down = prev * (1.0 - ratio)

    up_cand = (valid & (close >= theo_up - _CENT_PREFILTER)).to_numpy()
    down_cand = (valid & (close <= theo_down + _CENT_PREFILTER)).to_numpy()
    touch_cand = (valid & high.notna() & (high >= theo_up - _CENT_PREFILTER)).to_numpy()

    up_arr = up.to_numpy(copy=True)
    down_arr = down.to_numpy(copy=True)
    broken_arr = broken.to_numpy(copy=True)
    prev_arr = prev.to_numpy(dtype=float)
    close_arr = close.to_numpy(dtype=float)
    high_arr = high.to_numpy(dtype=float)

    for pos in range(len(frame)):
        if up_cand[pos] and _exact_limit_up(
            prev_arr[pos],
            close_arr[pos],
            code[pos],
            name[pos],
            trade_date=days[pos],
        ):
            up_arr[pos] = True

    for pos in range(len(frame)):
        if down_cand[pos] and _exact_limit_down(
            prev_arr[pos],
            close_arr[pos],
            code[pos],
            name[pos],
            trade_date=days[pos],
        ):
            down_arr[pos] = True

    for pos in range(len(frame)):
        if (
            touch_cand[pos]
            and not up_arr[pos]
            and _exact_limit_up(
                prev_arr[pos],
                high_arr[pos],
                code[pos],
                name[pos],
                trade_date=days[pos],
            )
        ):
            broken_arr[pos] = True

    out = frame.copy()
    out["sig_limit_up"] = up_arr
    out["sig_limit_down"] = down_arr
    out["sig_broken"] = broken_arr
    return out


def _run_length(flags: pd.Series, symbols: pd.Series) -> pd.Series:
    """Consecutive-truthy count within each symbol; 0 where the flag is false.

    The vectorised form of :func:`selene_space_ashare.ladder.run_length`:
    ``cumsum`` minus the cumulative count *at the last false row* gives the run
    length without a per-symbol Python loop.
    """
    as_int = flags.fillna(False).astype(int)
    cumulative = as_int.groupby(symbols, sort=False).cumsum()
    last_false = (
        cumulative.where(~flags.fillna(False))
        .groupby(symbols, sort=False)
        .ffill()
        .fillna(0)
    )
    return (cumulative - last_false).astype(int)


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    """Per-symbol history as of the last session *before* ``today``.

    ``frame`` is indexed by ``symbol`` and carries ``name`` / ``last_date`` /
    ``close`` / ``ma5`` / ``ma20`` / ``ma60`` / ``high60`` / ``low60`` /
    ``avg_vol5`` / ``boards``. A symbol absent from the frame has no baseline
    (new listing, or no bar in the window) — the caller must treat its trend
    fields as unknown rather than as zero.
    """

    as_of: str | None
    sessions: int
    frame: pd.DataFrame
    symbols: int = 0
    last_dates: tuple[str, ...] = field(default_factory=tuple)

    @property
    def empty(self) -> bool:
        return bool(self.frame.empty)


def _empty_baseline(sessions: int = BASELINE_SESSIONS) -> Baseline:
    return Baseline(
        as_of=None,
        sessions=sessions,
        frame=pd.DataFrame(
            columns=[
                "name",
                "last_date",
                "close",
                "ma5",
                "ma20",
                "ma60",
                "high60",
                "low60",
                "avg_vol5",
                "boards",
            ]
        ),
    )


def compute_baseline(
    db: Any,
    *,
    today: str,
    sessions: int = BASELINE_SESSIONS,
) -> Baseline:
    """Indicators for every symbol through the session before ``today``.

    Args:
        db: anything exposing ``get_partition_range`` (see ``market_daily``).
        today: the **session being scored**, ``YYYY-MM-DD``. Rows on or after it
            are excluded — the newest daily session is regularly partial, and
            folding it in would move a third of the market out of the baseline.
        sessions: window length in sessions; the last ``sessions`` distinct
            trade dates are used.

    Returns:
        A :class:`Baseline`. Empty (never an exception) when the base has no
        prior session; the caller reports that as ``degraded``.
    """
    if sessions < MIN_BASELINE_SESSIONS:
        raise ValueError(
            f"sessions must be >= {MIN_BASELINE_SESSIONS} (got {sessions}); "
            "a shorter window cannot support the 60-day averages"
        )
    try:
        end = _date.fromisoformat(today)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"today must be YYYY-MM-DD, got {today!r}") from exc

    # Calendar span for `sessions` trade dates: ~1.55 calendar days per session
    # (weekends + ~10 holidays/year), padded so a long holiday run still yields
    # the requested count.
    start = end - timedelta(days=int(sessions * 1.55) + 40)
    frame = market_daily_window(db, start_date=start.isoformat(), end_date=today)
    if frame.empty:
        return _empty_baseline(sessions)

    frame = frame[frame["date"] < today]
    if frame.empty:
        return _empty_baseline(sessions)

    dates = sorted(frame["date"].unique())[-sessions:]
    frame = frame[frame["date"].isin(dates)].copy()
    frame = frame.sort_values(["symbol", "date"], kind="stable").reset_index(drop=True)

    # `market_daily_window` resolves `prev_close` as each symbol's close before
    # the *window start* — one constant per symbol, correct only for its first
    # bar (see `_prev_close_lookup`). Using that column per-row would compare
    # every bar against a price 120 sessions old and manufacture enormous
    # limit-up streaks (measured: 37 and 120 consecutive "boards" on names that
    # never had them). Rebuild it as a true per-bar shift, falling back to the
    # window-level value for the first bar so a streak that began before the
    # window still scores its first session.
    window_prev = pd.to_numeric(frame["prev_close"], errors="coerce")
    shifted_prev = (
        pd.to_numeric(frame["close"], errors="coerce")
        .groupby(frame["symbol"], sort=False)
        .shift(1)
    )
    frame["prev_close"] = shifted_prev.where(shifted_prev.notna(), window_prev)
    frame = _flag_limits(frame)

    symbols = frame["symbol"]

    def _rolling(col: str, window: int, how: str) -> pd.Series:
        series = pd.to_numeric(frame[col], errors="coerce")
        grouped = series.groupby(symbols, sort=False)

        def _apply(block: pd.Series) -> pd.Series:
            rolled = block.rolling(window, min_periods=window)
            if how == "mean":
                return rolled.mean()
            if how == "max":
                return rolled.max()
            return rolled.min()

        # Per symbol, not over the concatenated frame: an ungrouped rolling
        # window would carry the previous symbol's last 59 bars into this one's
        # first window. ``min_periods=window`` already nulls any partial window,
        # so no extra position mask is needed here.
        return grouped.transform(_apply)

    frame["ma5"] = _rolling("close", 5, "mean")
    frame["ma20"] = _rolling("close", 20, "mean")
    frame["ma60"] = _rolling("close", 60, "mean")
    frame["high60"] = _rolling("high", 60, "max")
    frame["low60"] = _rolling("low", 60, "min")
    frame["avg_vol5"] = _rolling("volume", 5, "mean")
    frame["boards"] = _run_length(frame["sig_limit_up"], symbols)

    tail = frame.groupby("symbol", sort=False).tail(1).copy()
    tail = tail.set_index("symbol")
    keep = [
        "name",
        "date",
        "close",
        "ma5",
        "ma20",
        "ma60",
        "high60",
        "low60",
        "avg_vol5",
        "boards",
    ]
    tail = tail[keep].rename(columns={"date": "last_date"})
    return Baseline(
        as_of=max(dates) if dates else None,
        sessions=len(dates),
        frame=tail,
        symbols=len(tail),
        last_dates=tuple(dates),
    )


# ---------------------------------------------------------------------------
# Quotes
# ---------------------------------------------------------------------------


def quotes_from_snapshot(rows: Mapping[str, object]) -> list[dict[str, Any]]:
    """Snapshot cross-section → the assembler's quote shape.

    The snapshot is the preferred source: it is whole-universe, carries the
    exchange's own ``prev_close``, and is the only source with ``amount``.
    Rows with a non-positive price are already omitted by the parser; this
    keeps the guard locally so a JSON round-trip cannot reintroduce them.
    """

    def _get(row: object, name: str) -> Any:
        value = getattr(row, name, None)
        if value is not None:
            return value
        if isinstance(row, Mapping):
            return row.get(name)
        return None

    out: list[dict[str, Any]] = []
    for symbol, row in rows.items():
        last = _finite(_get(row, "last"))
        prev = _finite(_get(row, "prev_close"))
        if last is None or last <= 0:
            continue
        out.append(
            {
                "symbol": str(_get(row, "symbol") or symbol),
                "name": str(_get(row, "name") or ""),
                "last": last,
                "prev_close": prev,
                "high": _finite(_get(row, "high")),
                "low": _finite(_get(row, "low")),
                "volume": _finite(_get(row, "volume")),
                "amount": _finite(_get(row, "amount")),
            }
        )
    return out


def quotes_from_daily(frame: pd.DataFrame, *, today: str) -> list[dict[str, Any]]:
    """Today's own daily rows → the assembler's quote shape (no ``amount``).

    The fallback when the snapshot source is absent. ``date == today`` is the
    only filter that matters: ``market_daily_universe`` keeps suspended names
    on their true older date, and a stale row must not be counted as today's
    price (that is exactly how a suspension turns into fake breadth). Verified
    live 2026-10-20: without this filter, suspended names scored a 5-30%
    "change" against a 120-session-old close and inflated the limit-up count.
    """
    if frame is None or frame.empty or "date" not in frame.columns:
        return []
    today_rows = frame[frame["date"] == today]
    out: list[dict[str, Any]] = []
    for row in today_rows.to_dict(orient="records"):
        last = _finite(row.get("close"))
        if last is None or last <= 0:
            continue
        out.append(
            {
                "symbol": str(row.get("symbol")),
                "name": str(row.get("name") or ""),
                "last": last,
                "prev_close": _finite(row.get("prev_close")),
                "high": _finite(row.get("high")),
                "low": _finite(row.get("low")),
                "volume": _finite(row.get("volume")),
                "amount": None,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Assembly helpers
# ---------------------------------------------------------------------------


def _pct_bands(values: Sequence[float]) -> list[dict[str, Any]]:
    total = len(values) or 1
    out: list[dict[str, Any]] = []
    for label, low, high in _PCT_BANDS:
        # Bind the bounds to concrete floats first: mypy cannot narrow optionals
        # unpacked from a tuple inside a generator expression.
        lo = -math.inf if low is None else low
        hi = math.inf if high is None else high
        count = sum(1 for v in values if lo <= v < hi)
        out.append({"label": label, "count": count, "pct": count / total * 100})
    return out


def _top(
    rows: Sequence[dict[str, Any]], key: str, limit: int, *, descending: bool = True
) -> list[dict[str, Any]]:
    usable = [r for r in rows if _finite(r.get(key)) is not None]
    usable.sort(key=lambda r: _finite(r.get(key)) or 0.0, reverse=descending)
    return [
        {
            "symbol": r["symbol"],
            "name": r.get("name") or "",
            "close": _finite(r.get("last")),
            "change_pct": _finite(r.get("change_pct")),
            "amount": _finite(r.get("amount")),
            "volume_ratio": _finite(r.get("volume_ratio")),
            "board": board_of(_code_of(r["symbol"])),
            "boards": int(r.get("boards") or 0),
        }
        for r in usable[:limit]
    ]


def _rank_block(
    frame: pd.DataFrame | None, name_col: str, limit: int = 5
) -> dict[str, Any]:
    """Eastmoney board-rank DataFrame → ``{leading, lagging, basis}``.

    These are **board-index** returns (the板块's own index), not the
    equal-weight member average ``concept_rps`` computes. The two are different
    questions and must not be conflated; ``basis`` says which one this is.
    """
    if frame is None or len(frame) == 0 or name_col not in frame.columns:
        return {"leading": [], "lagging": [], "basis": "board_index"}
    items: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        change = _finite(row.get("change_pct"))
        if change is None:
            continue
        up = _finite(row.get("up_count")) or 0
        down = _finite(row.get("down_count")) or 0
        items.append(
            {
                "name": str(row.get(name_col) or ""),
                "avg_pct": change,
                "count": int(up + down),
                "up_count": int(up),
                "down_count": int(down),
                "leader": {"symbol": None, "name": str(row.get("leader") or "")},
            }
        )
    return {
        "leading": sorted(items, key=lambda x: x["avg_pct"], reverse=True)[:limit],
        "lagging": sorted(items, key=lambda x: x["avg_pct"])[:limit],
        "basis": "board_index",
    }


# ---------------------------------------------------------------------------
# Main assembler
# ---------------------------------------------------------------------------


def build_quote_rows(
    *,
    quotes: Sequence[Mapping[str, Any]],
    baseline: Baseline,
    trade_date: str | None = None,
) -> list[dict[str, Any]]:
    """Quote cross-section + baseline → one enriched row per symbol.

    The single definition of the per-symbol row shape: the overview consumes it
    to aggregate, the screener consumes it to filter. Both must see the same
    ``change_pct``, ``volume_ratio`` and ``boards`` or the two panels would
    disagree about the same stock on the same day.

    Pure: every input is already fetched. Rows whose price is missing or
    non-positive are dropped (a quote with no price cannot be scored); every
    other row is kept, with unknown trend fields left as ``None`` rather than
    zero-filled.

    Args:
        quotes: ``quotes_from_snapshot`` / ``quotes_from_daily`` output.
        baseline: :func:`compute_baseline` output.
        trade_date: the session the quotes belong to (``YYYY-MM-DD``), used to
            resolve the price-limit band that ruled on that day. Optional so a
            caller with no session at hand still works — it then gets the
            current rulebook, which is right for a live tape and wrong for a
            replay; a replay must pass it.

    Returns:
        Rows carrying ``symbol`` / ``name`` / ``last`` / ``prev_close`` /
        ``high`` / ``low`` / ``volume`` / ``amount`` / ``change_pct`` /
        ``baseline_close`` / ``ma5`` / ``ma20`` / ``ma60`` / ``high60`` /
        ``low60`` / ``avg_vol5`` / ``prev_boards`` / ``volume_ratio`` /
        ``limit_up`` / ``limit_down`` / ``broken`` / ``boards``.

        ``volume_ratio`` = today's volume over the prior 5-session average. For
        a still-forming session it compares a partial day against a full-day
        average and reads low by construction — consumers must label it, not
        rescale it.
    """
    base = baseline.frame
    rows: list[dict[str, Any]] = []
    for quote in quotes:
        symbol = str(quote.get("symbol") or "")
        if not symbol:
            continue
        last = _finite(quote.get("last"))
        prev = _finite(quote.get("prev_close"))
        if last is None or last <= 0:
            continue
        change_pct = (
            (last - prev) / prev * 100.0 if prev is not None and prev > 0 else None
        )
        history = base.loc[symbol] if symbol in base.index else None

        def _hist(column: str) -> float | None:
            if history is None:
                return None
            value = history[column]
            return _finite(value)

        row: dict[str, Any] = {
            "symbol": symbol,
            "name": str(quote.get("name") or "") or (_hist_name(history)),
            "last": last,
            "prev_close": prev,
            "high": _finite(quote.get("high")),
            "low": _finite(quote.get("low")),
            "volume": _finite(quote.get("volume")),
            "amount": _finite(quote.get("amount")),
            "change_pct": change_pct,
            "baseline_close": _hist("close"),
            "ma5": _hist("ma5"),
            "ma20": _hist("ma20"),
            "ma60": _hist("ma60"),
            "high60": _hist("high60"),
            "low60": _hist("low60"),
            "avg_vol5": _hist("avg_vol5"),
            "prev_boards": int(_hist("boards") or 0),
        }
        volume = row["volume"]
        avg_vol = row["avg_vol5"]
        row["volume_ratio"] = (
            volume / avg_vol if volume and avg_vol and avg_vol > 0 else None
        )
        rows.append(row)

    # Limit flags on today's print (exact Decimal rule).
    if rows:
        frame = _flag_limits(
            pd.DataFrame(
                {
                    "symbol": [r["symbol"] for r in rows],
                    "name": [r["name"] for r in rows],
                    "close": [r["last"] for r in rows],
                    "prev_close": [r["prev_close"] for r in rows],
                    "high": [r["high"] for r in rows],
                    # One session for the whole cross-section, so a constant
                    # column rather than a per-row lookup.
                    "date": [trade_date] * len(rows),
                }
            )
        )
        for i, row in enumerate(rows):
            up = bool(frame.at[i, "sig_limit_up"])
            row["limit_up"] = up
            row["limit_down"] = bool(frame.at[i, "sig_limit_down"])
            row["broken"] = bool(frame.at[i, "sig_broken"])
            # A streak continues only through a close at the limit; a broken
            # board is not a new rung, it is a failed one.
            row["boards"] = row["prev_boards"] + 1 if up else 0

    return rows


def assemble_overview(
    *,
    today: str,
    quotes: Sequence[Mapping[str, Any]],
    baseline: Baseline,
    indices: Sequence[Mapping[str, Any]] | None = None,
    indices_reason: str | None = None,
    concept_rank: pd.DataFrame | None = None,
    industry_rank: pd.DataFrame | None = None,
    sealed: Mapping[str, Any] | None = None,
    sealed_reason: str | None = None,
    quote_source: str = "snapshot",
    universe_size: int | None = None,
) -> dict[str, Any]:
    """Assemble the overview from a quote cross-section plus a baseline.

    Pure: every input is already fetched. This is the whole reason the function
    is cheap enough to run per request while the baseline is cached daily.

    Args:
        today: the session being scored (``YYYY-MM-DD``).
        quotes: ``quotes_from_snapshot`` / ``quotes_from_daily`` output.
        baseline: :func:`compute_baseline` output.
        indices / indices_reason: live core-index quotes and the reason any are
            missing.
        concept_rank / industry_rank: Eastmoney board-rank frames.
        sealed: ``sealed_counts`` output, or ``None`` when the live depth path
            is unavailable (which is the normal state off continuous trading).
        sealed_reason: why ``sealed`` is ``None`` / not ready.
        quote_source: ``"snapshot"`` / ``"daily"`` — carried into the payload so
            a consumer can tell a live cross-section from the daily fallback.
        universe_size: the current universe count, for the coverage ratio.

    Returns:
        The overview dict. Percentages are **percent numbers** (``1.23`` means
        +1.23%), everywhere, with no exceptions — TSP mixed fractions and
        percent across fields, and that inconsistency is not carried over.
    """
    rows = build_quote_rows(quotes=quotes, baseline=baseline, trade_date=today)

    total = len(rows)
    changes = [r["change_pct"] for r in rows if r["change_pct"] is not None]
    up_count = sum(1 for v in changes if v > 0)
    down_count = sum(1 for v in changes if v < 0)
    flat = total - up_count - down_count
    up_pct = up_count / total * 100 if total else 0.0
    down_pct = down_count / total * 100 if total else 0.0
    avg_pct = sum(changes) / len(changes) if changes else 0.0
    median_pct = sorted(changes)[len(changes) // 2] if changes else 0.0
    strong_up = sum(1 for v in changes if v >= 3.0)
    strong_down = sum(1 for v in changes if v <= -3.0)

    amounts = [r["amount"] for r in rows if r["amount"] is not None]
    amount_available = total > 0 and len(amounts) >= max(1, total // 2)
    total_amount = sum(amounts) if amount_available else None

    limit_up = sum(1 for r in rows if r.get("limit_up"))
    limit_down = sum(1 for r in rows if r.get("limit_down"))
    broken = sum(1 for r in rows if r.get("broken"))
    fake_up = 0
    fake_down = 0
    sealed_ready = False
    if sealed is not None and bool(sealed.get("ready")):
        sealed_ready = True
        fake_up = int(sealed.get("fake_up") or 0)
        fake_down = int(sealed.get("fake_down") or 0)
        limit_up = max(0, limit_up - fake_up)
        limit_down = max(0, limit_down - fake_down)
    seal_rate = limit_up / (limit_up + broken) * 100 if (limit_up + broken) > 0 else 0.0

    def _above(column: str) -> int:
        return sum(
            1
            for r in rows
            if r["last"] is not None
            and r[column] is not None
            and r["last"] >= r[column]
        )

    above_ma5 = _above("ma5")
    above_ma20 = _above("ma20")
    above_ma60 = _above("ma60")
    new_high = sum(
        1 for r in rows if r["high60"] is not None and r["last"] >= r["high60"]
    )
    new_low = sum(1 for r in rows if r["low60"] is not None and r["last"] <= r["low60"])

    ratios = [r["volume_ratio"] for r in rows if r["volume_ratio"] is not None]
    avg_vol_ratio = sum(ratios) / len(ratios) if ratios else 1.0
    high_vol_count = sum(1 for v in ratios if v >= 1.5)
    high_vol_pct = high_vol_count / total * 100 if total else 0.0

    boards_map: dict[str, dict[str, Any]] = {}
    for r in rows:
        name = board_of(_code_of(r["symbol"]))
        item = boards_map.setdefault(
            name, {"board": name, "count": 0, "up": 0, "down": 0, "amount": 0.0}
        )
        item["count"] += 1
        change = r["change_pct"] or 0.0
        if change > 0:
            item["up"] += 1
        elif change < 0:
            item["down"] += 1
        if r["amount"] is not None:
            item["amount"] += r["amount"]
    boards = sorted(
        boards_map.values(),
        key=lambda x: x["amount"] if amount_available else x["count"],
        reverse=True,
    )
    for item in boards:
        item["up_pct"] = item["up"] / item["count"] * 100 if item["count"] else 0.0

    tiers_map: dict[int, list[dict[str, Any]]] = {}
    for r in rows:
        n = int(r.get("boards") or 0)
        if n > 0:
            tiers_map.setdefault(n, []).append(
                {
                    "symbol": r["symbol"],
                    "name": r["name"],
                    "amount": r["amount"] or 0.0,
                }
            )
    tiers: list[dict[str, Any]] = [
        {
            "boards": k,
            "count": len(v),
            "stocks": sorted(v, key=lambda x: x["amount"], reverse=True)[:5],
        }
        for k, v in sorted(tiers_map.items(), key=lambda kv: -kv[0])
    ]
    max_boards = max(tiers_map) if tiers_map else 0
    tier2_count = sum(t["count"] for t in tiers if t["boards"] >= 2)

    concept_block = _rank_block(concept_rank, "concept")
    industry_block = _rank_block(industry_rank, "industry")

    index_changes: list[float] = [
        v
        for v in (_finite(i.get("change_pct")) for i in (indices or []))
        if v is not None
    ]
    avg_index_pct = sum(index_changes) / len(index_changes) if index_changes else None

    strong_diff_pct = (strong_up - strong_down) / total * 100 if total else 0.0
    strong_down_pct = strong_down / total * 100 if total else 0.0
    mainline_items = concept_block["leading"][:3] + industry_block["leading"][:3]
    mainline_avg = max(
        [_finite(i.get("avg_pct")) or 0.0 for i in mainline_items], default=0.0
    )
    mainline_cover = max(
        [(i.get("count") or 0) / total * 100 for i in mainline_items],
        default=0.0,
    )
    mainline_score = (
        round(
            _score(mainline_avg, -0.5, 3.0) * 0.65
            + _score(mainline_cover, 1, 12) * 0.35
        )
        if mainline_items
        else 50
    )

    # An empty cross-section is not a neutral market. Every band below maps
    # "no data" to a number, and averaging those numbers produces a confident
    # temperature (42/"震荡") from nothing at all. Emit no reading instead.
    radar: list[dict[str, Any]] = (
        []
        if total == 0
        else [
            {
                "key": "index",
                "label": "指数",
                "value": _score(avg_index_pct, -2.5, 2.5),
            },
            {
                "key": "profit",
                "label": "赚钱",
                "value": round(
                    _score(up_pct, 20, 80) * 0.45
                    + _score(avg_pct, -2, 2) * 0.25
                    + _score(median_pct, -2, 2) * 0.20
                    + _score(strong_diff_pct, -8, 8) * 0.10
                ),
            },
            {
                "key": "money",
                "label": "量能",
                "value": round(
                    _score(avg_vol_ratio, 0.6, 1.8) * 0.70
                    + _score(high_vol_pct, 2, 12) * 0.30
                ),
            },
            {
                "key": "speculation",
                "label": "投机",
                "value": round(
                    _score(limit_up, 5, 90) * 0.25
                    + _score(seal_rate, 30, 85) * 0.35
                    + _score(max_boards, 1, 8) * 0.25
                    + _score(tier2_count, 0, 30) * 0.15
                ),
            },
            {
                "key": "resilience",
                "label": "抗跌",
                "value": 100
                - round(
                    _score(down_pct, 20, 80) * 0.55
                    + _score(strong_down_pct, 1, 12) * 0.45
                ),
            },
            {"key": "mainline", "label": "主线", "value": mainline_score},
        ]
    )
    if radar:
        radar_values: list[float] = [float(r["value"]) for r in radar]
        emotion_score = round(sum(radar_values) / len(radar_values))
    else:
        emotion_score = None
    if emotion_score is None:
        emotion_label = "无数据"
    elif emotion_score >= 70:
        emotion_label = "强势"
    elif emotion_score >= 55:
        emotion_label = "偏暖"
    elif emotion_score >= 45:
        emotion_label = "震荡"
    elif emotion_score >= 30:
        emotion_label = "偏冷"
    else:
        emotion_label = "冰点"

    coverage = None
    if universe_size:
        coverage = total / universe_size
    if total == 0:
        status, reason = "empty", "no_quotes"
    elif coverage is not None and coverage < MIN_COVERAGE:
        status, reason = "degraded", "partial_coverage"
    else:
        status, reason = "ok", None

    return {
        "status": status,
        "reason": reason,
        "as_of": today,
        "adjust": "raw",
        "quote_source": quote_source,
        "coverage": {
            "quoted": total,
            "universe": universe_size,
            "ratio": coverage,
            "baseline_symbols": baseline.symbols,
            "baseline_as_of": baseline.as_of,
            "baseline_sessions": baseline.sessions,
            "amount_available": amount_available,
        },
        "indices": list(indices or []),
        "indices_reason": indices_reason,
        "breadth": {
            "total": total,
            "up": up_count,
            "down": down_count,
            "flat": flat,
            "up_pct": up_pct,
            "down_pct": down_pct,
            "avg_pct": avg_pct,
            "median_pct": median_pct,
            "strong_up": strong_up,
            "strong_down": strong_down,
        },
        "amount": {
            "total": total_amount,
            "available": amount_available,
        },
        "boards": boards,
        "limit": {
            "limit_up": limit_up,
            "broken": broken,
            "limit_down": limit_down,
            "max_boards": max_boards,
            "seal_rate": seal_rate,
            "tiers": tiers,
            "sealed_ready": sealed_ready,
            "sealed_reason": sealed_reason,
            "fake_up": fake_up,
            "fake_down": fake_down,
        },
        "distribution": _pct_bands(changes),
        "trend": {
            "above_ma5": above_ma5,
            "above_ma20": above_ma20,
            "above_ma60": above_ma60,
            "above_ma5_pct": above_ma5 / total * 100 if total else 0.0,
            "above_ma20_pct": above_ma20 / total * 100 if total else 0.0,
            "above_ma60_pct": above_ma60 / total * 100 if total else 0.0,
            "new_high": new_high,
            "new_low": new_low,
        },
        "activity": {
            "avg_vol_ratio": avg_vol_ratio,
            "high_vol_count": high_vol_count,
            "high_vol_pct": high_vol_pct,
            "vol_ratio_coverage": len(ratios) / total if total else 0.0,
        },
        "radar": radar,
        "emotion": {"score": emotion_score, "label": emotion_label},
        "top_gainers": _top(rows, "change_pct", 8),
        "top_losers": _top(rows, "change_pct", 8, descending=False),
        "amount_leaders": _top(rows, "amount", 8) if amount_available else [],
        "vol_ratio_leaders": _top(rows, "volume_ratio", 8),
        "concept_rank": concept_block,
        "industry_rank": industry_block,
    }


def _hist_name(history: Any) -> str:
    if history is None:
        return ""
    try:
        value = history["name"]
    except (KeyError, IndexError, TypeError):
        return ""
    return "" if value is None else str(value)


__all__ = [
    "BASELINE_SESSIONS",
    "Baseline",
    "MIN_BASELINE_SESSIONS",
    "MIN_COVERAGE",
    "assemble_overview",
    "build_quote_rows",
    "compute_baseline",
    "quotes_from_daily",
    "quotes_from_snapshot",
]
