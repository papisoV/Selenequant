# Absorbed from tick-stock-panel (MIT License)
# Copyright (c) 2026 papisoV
# Source: https://github.com/papisoV/tick-stock-panel
"""Limit-up ladder (连板梯队) structure layer — pandas port of TSP's ladder
aggregation.

The ladder describes the *shape* of a day's limit-up board: how tall it
stands (最高连板), how broad its base is (首板), how many rungs are filled
(N 板以上家数), and how well yesterday's promoted board carried over today
(晋级率). It is the structural input to the six-phase sentiment classifier in
:mod:`selene_space_ashare.market_phase`.

Input frame contract: columns ``symbol`` / ``date`` / ``consecutive_boards``
where ``consecutive_boards`` is an integer count of consecutive limit-ups
(0 = not at limit). Derive that column from unadjusted closes via
``market_rules.limit_up_price`` — see ``scripts/backfill_ladder_panel.py``.

The ``promo_rate`` (晋级率) is the continuation rate of yesterday's board:
of the stocks that were at limit yesterday, what fraction closed at limit
again today with exactly one more board. Pools smaller than
``PROMO_MIN_POOL`` record ``None`` rather than a noisy small-sample ratio.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from selene_space_ashare.market_rules import _normalize_code, limit_up_price

# 晋级率最小池（家数），低于此记 None（小样本噪声）
PROMO_MIN_POOL = 10

# 涨停判定容差（与 market_rules.is_price_at_limit 同一口径）
_LIMIT_TOL = 1e-9

# Integer count columns, restored after the seal-rate merge upcasts them.
_COUNT_COLUMNS = (
    "first_board",
    "ge2_count",
    "ge3_count",
    "ge5_count",
    "rungs_filled",
    "max_consecutive",
    "promo_pool",
    "promo_ok",
    "limit_up_count",
    "touched_count",
)


def run_length(flags: Iterable[bool]) -> list[int]:
    """Run length of a boolean sequence: each position holds the count of
    consecutive truthy values ending there (0 on any falsy value).

    Shared by :func:`compute_consecutive_limit_ups` and
    ``scripts/backfill_ladder_panel.py`` so the reset-on-break rule lives in
    exactly one place.
    """
    run = 0
    runs: list[int] = []
    for flag in flags:
        run = run + 1 if flag else 0
        runs.append(run)
    return runs


def compute_consecutive_limit_ups(
    df: pd.DataFrame,
    code: str,
    name: str = "",
    close_col: str = "close",
) -> pd.DataFrame:
    """Derive a consecutive-limit-up run length from **unadjusted** klines.

    Mirrors TSP ``indicators/pipeline.py:896-915``: a day is a limit-up when
    its raw close reaches ``limit_up_price(prev_close)``. The run resets to 0
    on any non-limit day, and — critically — **a missing/NaN previous close
    counts as non-limit, so the run resets to 0** (TSP's ``fill_null(False)``
    chain). This edge is the easiest to get wrong; it is pinned by test.

    Args:
        df: single-symbol frame sorted ascending by date, containing
            ``close_col`` (unadjusted close; float-castable).
        code: 6-digit code, for board-specific limit ratio.
        name: stock name, so ST (5%) limits are honoured when known.
        close_col: name of the close column.

    Returns:
        A copy of ``df`` with an integer ``consecutive_boards`` column.
    """
    out = df.copy()
    close = pd.to_numeric(out[close_col], errors="coerce")
    prev_close = close.shift(1)

    is_limit_up: list[bool] = []
    for prev, cur in zip(prev_close, close):
        if pd.isna(prev) or pd.isna(cur):
            is_limit_up.append(False)
            continue
        lu = float(limit_up_price(float(prev), code, name))
        is_limit_up.append(bool(cur >= lu - _LIMIT_TOL))

    out["consecutive_boards"] = run_length(is_limit_up)
    return out


def build_ladder_daily(cont_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate a per-stock board frame into per-date ladder breadth.

    Args:
        cont_df: frame with ``date`` and ``consecutive_boards`` columns
            (one row per stock-day).

    Returns:
        One row per date, sorted ascending, with columns:
        ``date`` / ``first_board`` (1 板家数) / ``ge2_count`` / ``ge3_count``
        / ``ge5_count`` / ``rungs_filled`` (non-empty height levels among
        2..max) / ``max_consecutive`` (highest board count).
    """
    if cont_df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "first_board",
                "ge2_count",
                "ge3_count",
                "ge5_count",
                "rungs_filled",
                "max_consecutive",
            ]
        )

    df = cont_df[["date", "consecutive_boards"]].copy()
    df["consecutive_boards"] = pd.to_numeric(df["consecutive_boards"], errors="coerce")

    grouped = df.groupby("date")["consecutive_boards"]
    out = pd.DataFrame(
        {
            "first_board": grouped.apply(lambda s: int((s == 1).sum())),
            "ge2_count": grouped.apply(lambda s: int((s >= 2).sum())),
            "ge3_count": grouped.apply(lambda s: int((s >= 3).sum())),
            "ge5_count": grouped.apply(lambda s: int((s >= 5).sum())),
            "rungs_filled": grouped.apply(lambda s: int(s[s >= 2].nunique())),
            "max_consecutive": grouped.apply(
                lambda s: int(s.max()) if s.notna().any() else 0
            ),
        }
    ).reset_index()

    return out.sort_values("date").reset_index(drop=True)


def attach_ladder_promo(df: pd.DataFrame) -> pd.DataFrame:
    """Attach promotion candidates (晋级分子/分母) by comparing to yesterday.

    Args:
        df: per-stock frame with ``symbol`` / ``date`` /
            ``consecutive_boards``.

    Returns:
        A copy of ``df`` sorted by (symbol, date) with three added columns:

        - ``_prev_consec``: that symbol's previous-day board count
          (NaN on each symbol's first row).
        - ``promo_pool``: 1 if yesterday's count existed and was >= 1
          (a stock in yesterday's board), else 0.
        - ``promo_ok``: 1 if yesterday >= 1 **and** today == yesterday + 1
          (the board advanced by exactly one), else 0.
    """
    out = df.sort_values(["symbol", "date"]).copy()
    out["_prev_consec"] = out.groupby("symbol")["consecutive_boards"].shift(1)

    prev = out["_prev_consec"]
    consec = out["consecutive_boards"]
    in_pool = prev.notna() & (prev >= 1)
    out["promo_pool"] = in_pool.astype(int)
    out["promo_ok"] = (in_pool & (consec == prev + 1)).astype(int)
    return out


def ladder_daily_panel(cont_df: pd.DataFrame) -> pd.DataFrame:
    """Compose the full per-date ladder panel from a per-stock board frame.

    Joins the breadth aggregates (:func:`build_ladder_daily`) with the daily
    promotion aggregates and appends ``ladder_completeness``. This is the
    canonical daily panel consumed by the phase classifier and persisted by
    the backfill script.

    Args:
        cont_df: per-stock frame with ``symbol`` / ``date`` /
            ``consecutive_boards``.

    Returns:
        One row per date (ascending), columns: ``date`` / ``first_board`` /
        ``ge2_count`` / ``ge3_count`` / ``ge5_count`` / ``rungs_filled`` /
        ``max_consecutive`` / ``promo_pool`` / ``promo_ok`` / ``promo_rate``
        / ``ladder_completeness``.
    """
    if cont_df.empty:
        return pd.DataFrame(
            columns=[
                "date",
                "first_board",
                "ge2_count",
                "ge3_count",
                "ge5_count",
                "rungs_filled",
                "max_consecutive",
                "promo_pool",
                "promo_ok",
                "promo_rate",
                "ladder_completeness",
            ]
        )

    breadth = build_ladder_daily(cont_df)
    with_promo = attach_ladder_promo(cont_df)
    promo = (
        with_promo.groupby("date")
        .agg(promo_pool=("promo_pool", "sum"), promo_ok=("promo_ok", "sum"))
        .reset_index()
    )
    promo["promo_rate"] = promo.apply(
        lambda r: (
            (r["promo_ok"] / r["promo_pool"])
            if r["promo_pool"] >= PROMO_MIN_POOL
            else None
        ),
        axis=1,
    )

    panel = breadth.merge(promo, on="date", how="left")
    panel["ladder_completeness"] = [
        ladder_completeness(int(h), int(r))
        for h, r in zip(panel["max_consecutive"], panel["rungs_filled"])
    ]
    return panel.sort_values("date").reset_index(drop=True)


def attach_limit_flags(
    df: pd.DataFrame,
    code: str,
    name: str = "",
    close_col: str = "close",
    high_col: str = "high",
) -> pd.DataFrame:
    """Classify each day as sealed-at-limit and/or touched-limit.

    Two distinct notions, both resolved against ``limit_up_price(prev_close)``
    on **unadjusted** prices:

    - ``is_limit_up`` (封板): the day's *close* reached the limit.
    - ``touched_limit`` (曾涨停): the day's *high* reached the limit, whether
      or not it held (a 炸板 day is ``touched_limit`` and not ``is_limit_up``).

    A missing previous close makes both flags ``False`` — the same
    run-resetting boundary as :func:`compute_consecutive_limit_ups`. When no
    ``high_col`` is present, ``touched_limit`` mirrors ``is_limit_up``.

    Args:
        df: single-symbol frame sorted ascending by date.
        code: 6-digit code, for board-specific limit ratio.
        name: stock name, so ST (5%) limits are honoured when known.
        close_col: name of the close column.
        high_col: name of the high column (optional; falls back to close).

    Returns:
        A copy of ``df`` with boolean ``is_limit_up`` and ``touched_limit``.
    """
    out = df.copy()
    close = pd.to_numeric(out[close_col], errors="coerce")
    if high_col in out.columns:
        high = pd.to_numeric(out[high_col], errors="coerce")
    else:
        high = close
    prev_close = close.shift(1)

    sealed: list[bool] = []
    touched: list[bool] = []
    for prev, cur, hi in zip(prev_close, close, high):
        if pd.isna(prev):
            sealed.append(False)
            touched.append(False)
            continue
        lu = float(limit_up_price(float(prev), code, name))
        touched.append(bool(not pd.isna(hi) and hi >= lu - _LIMIT_TOL))
        sealed.append(bool(not pd.isna(cur) and cur >= lu - _LIMIT_TOL))

    out["is_limit_up"] = sealed
    out["touched_limit"] = touched
    return out


def build_seal_rate_daily(flag_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate per-stock limit flags into a per-date 封板率.

    ``seal_rate`` is the fraction of limit-touching names that held into the
    close: ``sealed / touched``. Dates where nothing touched the limit record
    ``None`` rather than a 0/0 division.

    Args:
        flag_df: frame with ``date`` / ``is_limit_up`` / ``touched_limit``
            (as produced by :func:`attach_limit_flags`).

    Returns:
        One row per date (ascending) with ``date`` / ``limit_up_count`` /
        ``touched_count`` / ``seal_rate``.
    """
    if flag_df.empty:
        return pd.DataFrame(
            columns=["date", "limit_up_count", "touched_count", "seal_rate"]
        )

    grouped = flag_df.groupby("date")
    out = pd.DataFrame(
        {
            "limit_up_count": grouped["is_limit_up"].sum().astype(int),
            "touched_count": grouped["touched_limit"].sum().astype(int),
        }
    ).reset_index()
    out["seal_rate"] = [
        (int(s) / int(t)) if int(t) > 0 else None
        for s, t in zip(out["limit_up_count"], out["touched_count"])
    ]
    return out.sort_values("date").reset_index(drop=True)


def ladder_completeness(height: int, rungs: int) -> float:
    """Fraction of the 2..height height-levels that are populated.

    A ladder of ``height`` has ``height - 1`` rungs above the first board
    (2 板, 3 板, ... height 板). Returns 0.0 when ``height < 3`` — a one- or
    two-board ladder has no interior rung to be complete or incomplete.

    Args:
        height: highest board count of the day.
        rungs: number of distinct occupied levels among 2..height.

    Returns:
        ``rungs / (height - 1)`` for ``height >= 3``, else ``0.0``.
    """
    if height < 3:
        return 0.0
    return rungs / (height - 1)


# ─────────────────── 实时横截面(live cross-section) ───────────────────
#
# The live twin of the panel above: fold a *whole-market snapshot* cross-section
# into today's ladder row so the same ``market_phase`` rules that label the EOD
# panel can label a session still in progress. Everything here is provisional by
# construction — the bell has not rung, so a seal can still break — and callers
# must tag the output ``label_scope="intraday_provisional"``, never filing it
# beside an EOD label.

_CST = timezone(timedelta(hours=8))


def next_run_length(prev_run: int | float | None, flag: bool) -> int:
    """Extend a consecutive-board run by one day.

    The single-step form of :func:`run_length`'s fold — ``run + 1 if flag else
    0`` — for the live path, where only the previous *run length* is on hand
    (the daily history stores run lengths, not the underlying per-day flags). A
    test pins it to agree with :func:`run_length`, so the reset-on-break rule
    keeps a single meaning.

    Args:
        prev_run: the symbol's board count as of the previous session (``None``
            or NaN counts as 0).
        flag: whether today sealed at the limit.

    Returns:
        ``prev_run + 1`` when ``flag``, else ``0``.
    """
    base = 0 if prev_run is None or prev_run != prev_run else int(prev_run)
    return base + 1 if flag else 0


def _field(row: object, name: str, default: Any = None) -> Any:
    """Read a field from a duck-typed row — attribute or mapping key.

    The real ``SnapshotRow`` is a frozen dataclass (attribute access); a caller
    that round-tripped the cross-section through JSON holds plain dicts (item
    access). Accept both so the duck-typed contract is honest. Returns ``Any``
    because the field's type is the caller's to know — the consumers below each
    coerce with ``float``/``int``/``str`` and handle bad values.
    """
    value = getattr(row, name, None)
    if value is not None:
        return value
    if isinstance(row, Mapping):
        return row.get(name, default)
    return default


def _reaches_limit(
    price: float | None,
    prev_close: float | None,
    code: str,
    name: str = "",
) -> bool:
    """True when ``price`` sits at/above the day's limit-up price.

    The cross-section counterpart of :func:`attach_limit_flags`: that function
    recovers ``prev_close`` by shifting a per-symbol close series, whereas a
    snapshot row carries the exchange's official previous close directly. Same
    rule (``limit_up_price`` plus ``_LIMIT_TOL``) and the same
    missing-previous-close → ``False`` edge.

    A non-positive ``prev_close`` — a first-day listing, where the upstream
    publishes 0 — returns ``False`` rather than a limit price of 0; otherwise
    every such name would read as a limit-up.
    """
    if price is None or prev_close is None:
        return False
    try:
        value = float(price)
        base = float(prev_close)
    except (TypeError, ValueError):
        return False
    if value != value or base != base or value <= 0 or base <= 0:
        return False
    limit = float(limit_up_price(base, code, name))
    return bool(value >= limit - _LIMIT_TOL)


def _snapshot_date(rows: Mapping[str, object]) -> str:
    """The session date (``YYYYMMDD``, CST) of a snapshot cross-section.

    Taken as the latest row timestamp: the source publishes true-epoch
    milliseconds and a live cross-section is one session by construction.
    """
    stamps = [
        int(_field(row, "timestamp"))
        for row in rows.values()
        if _field(row, "timestamp")
    ]
    if not stamps:
        raise ValueError(
            "live_ladder_row: 快照行没有 timestamp,无法确定交易日;请显式传 today="
        )
    return datetime.fromtimestamp(max(stamps) / 1000.0, tz=_CST).strftime("%Y%m%d")


def _latest_history_rows(daily_history: pd.DataFrame, today: str) -> pd.DataFrame:
    """Each symbol's most recent pre-``today`` ladder row.

    Args:
        daily_history: frame with ``symbol`` / ``date`` / ``consecutive_boards``.
        today: ``YYYYMMDD``; rows on or after it are ignored, so re-running
            after a partial write cannot leak today into its own baseline.

    Returns:
        ``symbol`` / ``date`` / ``consecutive_boards`` with one row per symbol,
        or an empty frame with those columns when there is no earlier history.
    """
    cols = ["symbol", "date", "consecutive_boards"]
    if daily_history is None or len(daily_history) == 0:
        return pd.DataFrame(columns=cols)
    missing = [c for c in cols if c not in daily_history.columns]
    if missing:
        raise ValueError(f"daily_history 缺少列: {sorted(missing)}")
    hist = daily_history.loc[daily_history["date"].astype(str) < str(today), cols]
    if hist.empty:
        return pd.DataFrame(columns=cols)
    hist = hist.reset_index(drop=True)
    return hist.loc[hist.groupby("symbol")["date"].idxmax()].reset_index(drop=True)


def _scalar(value: object) -> object:
    """numpy scalar → Python scalar; NaN/NaT → ``None``.

    Keeps the returned row honest: a missing ``promo_rate`` reads as ``None``,
    not as a NaN that later comparisons silently propagate.
    """
    if value is None:
        return None
    if isinstance(value, float) and value != value:
        return None
    item = getattr(value, "item", None)
    if callable(item):
        out = item()
        return None if isinstance(out, float) and out != out else out
    return value


def live_ladder_row(
    rows: Mapping[str, object],
    daily_history: pd.DataFrame,
    *,
    today: str | None = None,
) -> dict:
    """Today's ladder row from a whole-market snapshot cross-section.

    Folds the current cross-section into the *same* columns the EOD
    :func:`ladder_daily_panel` produces, so
    :func:`~selene_space_ashare.market_phase.rule_labels` can label the session
    in progress. Per symbol it derives today's limit state from the snapshot's
    own official ``prev_close`` and extends the previous session's run; the
    breadth, promotion and seal-rate aggregates are then delegated unchanged to
    :func:`ladder_daily_panel` and :func:`build_seal_rate_daily`.

    The result is **provisional**: it describes a session that has not closed,
    so a seal can still break and the whole tape can still move.

    Args:
        rows: ``{symbol: snapshot row}`` — the read face of
            ``AstockMarketSnapshotSource.get_market_rows()``. Rows are
            duck-typed (this package does not depend on the market-data
            package): each must expose ``last`` / ``prev_close`` / ``high`` /
            ``name``, plus ``timestamp`` unless ``today`` is given.
        daily_history: per-stock ladder history with ``symbol`` / ``date`` /
            ``consecutive_boards`` (the backfilled panel). A symbol absent from
            it starts from a zero run.
        today: ``YYYYMMDD`` override; derived from the snapshot timestamps when
            omitted.

    Returns:
        One row's columns, aligned to
        :data:`~selene_space_ashare.market_phase._REQUIRED_COLUMNS` (``date`` /
        ``max_consecutive`` / ``first_board`` / ``ge2_count`` / ``promo_rate`` /
        ``seal_rate``) plus the remaining ladder and seal columns.

    Raises:
        ValueError: when ``rows`` is empty, or a required column or timestamp is
            absent. An empty cross-section is ambiguous — a closed market and a
            failed fetch look identical — and must never be scored as a dead
            tape, so it fails loudly rather than reporting a silent 冰点.
    """
    if not rows:
        raise ValueError(
            "live_ladder_row: 快照为空 —— 休市与取数失败不可区分,不得据此判定市场状态"
        )
    day = str(today) if today is not None else _snapshot_date(rows)

    prev = _latest_history_rows(daily_history, day)
    if prev.empty:
        prev_run: dict[str, int] = {}
    else:
        counts = pd.to_numeric(prev["consecutive_boards"], errors="coerce")
        prev_run = {
            str(s): int(c) for s, c in zip(prev["symbol"], counts) if pd.notna(c)
        }

    today_rows: list[dict] = []
    flag_rows: list[dict] = []
    for symbol, row in rows.items():
        code = _normalize_code(str(symbol))
        name = _field(row, "name", "") or ""
        prev_close = _field(row, "prev_close")
        sealed = _reaches_limit(_field(row, "last"), prev_close, code, name)
        touched = _reaches_limit(_field(row, "high"), prev_close, code, name)
        today_rows.append(
            {
                "symbol": code,
                "date": day,
                "consecutive_boards": next_run_length(prev_run.get(code, 0), sealed),
            }
        )
        flag_rows.append({"date": day, "is_limit_up": sealed, "touched_limit": touched})

    cont = pd.concat(
        [prev[["symbol", "date", "consecutive_boards"]], pd.DataFrame(today_rows)],
        ignore_index=True,
    )
    panel = ladder_daily_panel(cont).merge(
        build_seal_rate_daily(pd.DataFrame(flag_rows)), on="date", how="left"
    )
    match = panel.loc[panel["date"].astype(str) == day]
    if match.empty:  # pragma: no cover - ladder_daily_panel always emits `day`
        raise ValueError(f"live_ladder_row: 聚合结果缺少 {day} 行")
    row = match.iloc[0].to_dict()
    # The merge leaves the history dates without seal flags, which upcasts the
    # count columns to float64. Restore integer counts so a live row is
    # type-identical to the EOD panel's row.
    for col in _COUNT_COLUMNS:
        if col in row:
            row[col] = int(row[col])
    return {k: _scalar(v) for k, v in row.items()}


def decode_high_days_value(value: int | float | str | None) -> int:
    """Decode 10jqka's packed ``high_days_value`` into the ``M`` of ``N天M板``.

    The pool field is bit-packed as ``(M << 16) | N`` where ``N`` is the number
    of days in the lookback window and ``M`` the number of boards inside it
    (``65537 == 0x1_0001`` = 首板; ``196611 == 0x3_0003`` = 3天3板;
    ``720915 == 0xB_0013`` = 19天11板). Verified against the live endpoint on
    2026-10-15 for every sampled row.

    **This ``M`` is NOT the consecutive run.** "19天11板" reports ``M=11`` while
    the true consecutive count is different — the pool carries no consecutive
    count at all. Use :func:`ladder_history_from_pool` (which reads
    ``continue_num`` from the sibling endpoint) for the run; this decoder exists
    only so a legible board count can be surfaced alongside it, never as the
    promotion baseline.

    Args:
        value: the raw ``high_days_value`` (missing/None tolerated). A ``str``
            is accepted because the field is JSON and the endpoint has been
            seen to quote numeric fields; a non-numeric string decodes to ``0``.

    Returns:
        The decoded ``M``, or ``0`` when the input is absent or not numeric.
    """
    if value is None:
        return 0
    try:
        as_int = int(value)
    except (TypeError, ValueError):
        return 0
    return as_int >> 16


def _first_board_codes(
    pool_codes: Iterable[str], continuous_codes: Iterable[str]
) -> set[str]:
    """Pool codes absent from the continuous list — i.e. today's 首板 (1 board).

    The pool endpoint lists every limit-up; the continuous endpoint lists only
    those on their **second board or higher**. So the difference is exactly the
    first-board set, which the continuous endpoint alone omits. Verified forward
    on 2026-10-15: across four consecutive session pairs every such code that
    limited up again the next session reported ``continue_num == 2`` (29/29),
    confirming its implied count was 1.
    """
    return {str(c) for c in pool_codes} - {str(c) for c in continuous_codes}


def ladder_history_from_pool(
    pool: pd.DataFrame,
    continuous: pd.DataFrame,
) -> pd.DataFrame:
    """Per-stock ``consecutive_boards`` for one session, from the 10jqka pair.

    Combines the two sibling endpoints into the ``daily_history`` frame
    :func:`live_ladder_row` extends:

    * every ``continuous`` row contributes its true ``continue_num``;
    * every ``pool`` code absent from ``continuous`` contributes ``1`` (首板).

    The result is *not* the pool's ``high_days_value`` — see
    :func:`decode_high_days_value` for why that packed field is the wrong
    quantity outside a plain 1-board case.

    Args:
        pool: frame with ``code`` (the pool endpoint, all limit-ups).
        continuous: frame with ``code`` / ``consecutive_boards`` (the
            continuous endpoint, ≥2 boards only). May be empty.

    Returns:
        ``symbol`` / ``date`` / ``consecutive_boards`` — one row per limit-up
        code, ``symbol`` carrying the bare 6-digit code (the ladder frame's
        own convention). Empty in, empty frame out.
    """
    cols = ["symbol", "date", "consecutive_boards"]
    pool_codes = (
        [str(c) for c in pool["code"]]
        if pool is not None and len(pool) and "code" in pool.columns
        else []
    )
    date = ""
    for frame in (continuous, pool):
        if frame is not None and len(frame) and "date" in frame.columns:
            date = str(frame["date"].iloc[0])
            break

    cont_rows: list[dict[str, Any]] = []
    cont_codes: list[str] = []
    if continuous is not None and len(continuous) and "code" in continuous.columns:
        cont_codes = [str(c) for c in continuous["code"]]
        for code, boards in zip(cont_codes, continuous.get("consecutive_boards", [])):
            cont_rows.append(
                {"symbol": code, "date": date, "consecutive_boards": boards}
            )
    if not pool_codes and not cont_rows:
        return pd.DataFrame(columns=cols)

    rows = list(cont_rows)
    for code in sorted(_first_board_codes(pool_codes, cont_codes)):
        rows.append({"symbol": code, "date": date, "consecutive_boards": 1})
    out = pd.DataFrame(rows, columns=cols)
    out["consecutive_boards"] = pd.to_numeric(
        out["consecutive_boards"], errors="coerce"
    )
    return out.dropna(subset=["consecutive_boards"]).reset_index(drop=True)
