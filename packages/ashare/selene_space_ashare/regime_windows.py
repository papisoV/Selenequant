"""
Regime attribution for periodic-rebalance windows (post-processing layer).

CONSUMER STATUS: research-only — deliberately NOT wired to API / UI / CLI.
This module backs the batch13/14/15 verdicts (structural-premium and drawdown-
regime analysis); its output is a research *conclusion* (`scratch/research/*/verdict.md`
+ CSVs), not a product capability. Its only in-repo callers are the research
scripts under `scratch/` and tests. Wiring it into a page is a separate product
decision, not an oversight — audits should not flag the zero product consumer
as dead code. (Search: `grep -rn "regime_windows" --include=*.py` → tests only.)

Sits on top of backtest_periodic_portfolio's window table: joins the
pre-registered regime labels (trend / vol, as produced by regime.py from
index daily data) onto each window's rebalance date, then aggregates
window excess returns per regime state. Engine files stay untouched —
this module reads a window table, it never produces one (batch13 ADR:
attribution is post-processing, not engine semantics).

Two contracts deserve explicit wording:

PIT / t-close consistency. Every rolling window in regime.py looks
backward (no ``center``), so the label at date t is a function of index
data up to and including t's close. The engine buys at the rebalance
date's close (the t row of pct_change is realized at decision time —
the same convention as make_slsqp_weight_fn / make_inverse_vol_weight_fn
in portfolio_rebal.py). Decision information and trade execution share
the same timestamp: joining the t label onto the window that starts at
t introduces no lookahead, and no t-1 shift is needed.

Warm-up label trap (why callers must feed full index history). In
regime.py's np.select, any False comparison falls through to the
NORMAL_VOL default — including the comparisons that are False because
realized_vol_20d or its rolling p25/p75 quantiles are still NaN during
warm-up (min_periods=5 / 60). An index with insufficient history is
therefore silently labelled normal_vol instead of NaN. Callers must
compute labels from full history (e.g. 2005+) so that every label inside
the panel's date range is fully warmed up; feeding only the panel's own
date range would stamp fake normal_vol labels on its first ~60 trading
days.

alignment contract (batch13 R1/R2/D6): ``benchmark_return`` is merged
into the joined table so the drop decision applies to labels and
benchmark in one place (three columns live and die together); the
regime side is de-duplicated (keep="last", matching
align_benchmark_windows) before the left join so duplicate dates cannot
fan out window rows; windows whose rebalance date is missing from the
regime calendar are dropped and counted in ``attrs["dropped"]`` (never
forward-filled — one wrong label poisons a whole groupby slice).
"""

from collections.abc import Sequence

import numpy as np
import pandas as pd

from selene_space_types.stock import TrendRegime, VolRegime

_TREND_STATES: tuple[str, ...] = (
    TrendRegime.BULL.value,
    TrendRegime.NEUTRAL.value,
    TrendRegime.BEAR.value,
)
_VOL_STATES: tuple[str, ...] = (
    VolRegime.LOW_VOL.value,
    VolRegime.NORMAL_VOL.value,
    VolRegime.HIGH_VOL.value,
)

_JOINT_SEP = "×"

# Drawdown states are batch14-local. trend/vol reuse the shared enums in
# selene_space_types.stock, but this ladder is defined and frozen here: the
# 10%/20% boundaries are market convention (correction / bear market), not a
# quantity derived from this repo's own data (batch14 D1).
_DRAWDOWN_STATES: tuple[str, ...] = ("mild", "correction", "deep")

# The definition set is frozen per batch: a new view must be pre-registered
# before that batch's data is touched. batch13 D2 sealed trend /
# vol / trend×vol; batch14 pre-registers the drawdown views below, and its
# US-1 commit lands before any R3 excess table exists. Views added after
# seeing the data invalidate the analysis — rejected here rather than
# silently computed.
_ALLOWED_VIEWS: dict[tuple[str, ...], tuple[str, ...]] = {
    ("trend_regime",): _TREND_STATES,
    ("vol_regime",): _VOL_STATES,
    ("trend_regime", "vol_regime"): tuple(
        f"{t}{_JOINT_SEP}{v}" for t in _TREND_STATES for v in _VOL_STATES
    ),
    ("drawdown_regime",): _DRAWDOWN_STATES,
    ("trend_regime", "drawdown_regime"): tuple(
        f"{t}{_JOINT_SEP}{d}" for t in _TREND_STATES for d in _DRAWDOWN_STATES
    ),
    ("vol_regime", "drawdown_regime"): tuple(
        f"{v}{_JOINT_SEP}{d}" for v in _VOL_STATES for d in _DRAWDOWN_STATES
    ),
}

_SUMMARY_COLUMNS = [
    "label",
    "def",
    "state",
    "n_windows",
    "n_segments",
    "mean_window_excess",
    "cum_excess",
    "win_rate",
]


def join_window_regimes(
    windows: pd.DataFrame,
    regime_df: pd.DataFrame,
    benchmark_return: pd.Series,
    extra_label_cols: Sequence[str] = (),
) -> pd.DataFrame:
    """Left-join regime labels + benchmark return onto the window table.

    The output is the self-consistent per-window analysis table consumed
    by :func:`regime_excess_summary` (which deliberately takes no
    separate benchmark Series — see the module docstring on the single
    alignment point).

    Args:
        windows: backtest_periodic_portfolio output with rebalance_date
            and end_date columns
        regime_df: calc_index_regime output ([date, trend_regime,
            vol_regime, ...]); date may be ``%Y%m%d`` str or Timestamp,
            normalized internally. Duplicate dates are dropped
            (keep="last") before the join so rows cannot fan out.
        benchmark_return: per-window benchmark returns indexed exactly
            like ``windows`` (run_portfolio_backtest guarantees this).
            A misaligned index raises instead of reindexing silently.
        extra_label_cols: additional label columns to carry through from
            ``regime_df`` (batch14 D2). They join the same all-columns
            notna keep, so default ``()`` reproduces the batch13 two-column
            behaviour exactly.

    Returns:
        windows rows plus columns [benchmark_return, trend_regime,
        vol_regime, *extra_label_cols], restricted to windows whose
        rebalance date exists in the regime calendar, with
        ``.attrs["dropped"]`` = number of discarded windows. Read attrs
        immediately; pandas does not propagate them through later
        operations.
    """
    win_idx = windows.index
    bench_idx = pd.Index(benchmark_return.index)
    if not win_idx.equals(bench_idx):
        raise ValueError(
            "benchmark_return index does not match windows index "
            f"({len(bench_idx)} vs {len(win_idx)} rows); "
            "run_portfolio_backtest guarantees the alignment — reindexing "
            "here would silently misalign excess returns"
        )

    label_cols = ["trend_regime", "vol_regime", *extra_label_cols]

    reg = regime_df.copy()
    reg["date"] = pd.to_datetime(reg["date"].astype(str))
    reg = reg.drop_duplicates(subset="date", keep="last")
    labels = reg.set_index("date")[label_cols]

    reb = pd.DatetimeIndex(pd.to_datetime(windows["rebalance_date"]))
    aligned = labels.reindex(reb)
    # Every label column lives and dies together: a window survives only
    # when all of them are present (with the default extra_label_cols=()
    # this is the batch13 two-column predicate, bit for bit).
    keep = aligned[label_cols].notna().all(axis=1).to_numpy()

    out = windows.copy()
    out["benchmark_return"] = np.asarray(benchmark_return, dtype=float)
    for col in label_cols:
        out[col] = aligned[col].to_numpy()
    out = out.loc[keep]
    out.attrs["dropped"] = int((~keep).sum())
    return out


def regime_excess_summary(
    joined: pd.DataFrame,
    by: Sequence[str] = ("trend_regime",),
    label: str = "",
    freq: int | None = None,
) -> pd.DataFrame:
    """Aggregate window excess returns per regime state (D7 conventions).

    Window excess = period_return - benchmark_return (arithmetic
    difference, matching calc_benchmark_metrics — not geometric). The
    input must be :func:`join_window_regimes` output; states absent from
    the data still get a row with n=0/NaN (empty cells are reported, not
    dropped), and an empty input yields the full frozen state set at
    n=0 rather than an exception.

    ``n_segments`` counts maximal contiguous runs of the state over the
    time-ordered table. Regime labels are highly autocorrelated, so
    n_windows overstates statistical power; power language must quote
    n_segments, never n_windows (batch13 rule 5).

    Args:
        joined: join_window_regimes output (self-consistent: labels and
            benchmark_return share the row set by construction)
        by: pre-registered view — one of the ``_ALLOWED_VIEWS`` keys
            (batch13: trend / vol / trend×vol; batch14:
            drawdown_regime and its trend / vol joints). Anything else is
            rejected: the definition set is frozen per batch, and a new
            view must be pre-registered before that batch's data is
            touched.
        label: free-form tag copied into the output (e.g. "reb21_n20")
        freq: annualization factor for the FULL anchor row's
            excess_annual (callers pass 252 // rebalance_days). None
            omits the column entirely. State rows never get an annual
            figure — discrete windows compounded per state have no
            implementable meaning (D7).

    Returns:
        DataFrame [label, def, state, n_windows, n_segments,
        mean_window_excess, cum_excess, win_rate] plus excess_annual
        when freq is given; first row is the FULL anchor
        (state="FULL"), then one row per frozen state in enum order.
    """
    by_t = tuple(by)
    if by_t not in _ALLOWED_VIEWS:
        raise ValueError(
            f"by={by_t!r} is not a pre-registered view; allowed: "
            f"{sorted(_ALLOWED_VIEWS)} (the definition set is frozen per "
            "batch — a new view must be pre-registered in a new batch before "
            "that batch's data is touched)"
        )
    expected_states = list(_ALLOWED_VIEWS[by_t])

    keys = _state_keys(joined, by_t)
    excess = joined["period_return"].astype(float) - joined["benchmark_return"].astype(
        float
    )

    segments: dict[str, int] = {}
    prev: str | None = None
    for s in (str(x) for x in keys):
        if s != prev:
            segments[s] = segments.get(s, 0) + 1
        prev = s

    if len(joined):
        full_cum = float((1.0 + excess).cumprod().iloc[-1] - 1.0)
        full_mean = float(excess.mean())
        full_win_rate = float((excess > 0).mean())
        full_annual = (
            (1.0 + full_cum) ** (freq / len(joined)) - 1.0
            if freq is not None and full_cum > -1.0
            else float("nan")
        )
    else:
        full_cum = full_mean = full_win_rate = full_annual = float("nan")

    rows: list[dict[str, object]] = [
        {
            "label": label,
            "def": "FULL",
            "state": "FULL",
            "n_windows": int(len(joined)),
            "n_segments": int(sum(segments.values())),
            "mean_window_excess": full_mean,
            "cum_excess": full_cum,
            "win_rate": full_win_rate,
            "excess_annual": full_annual,
        }
    ]
    for state in expected_states:
        mask = (keys.astype(str) == state).to_numpy()
        n = int(mask.sum())
        if n:
            sub = excess[mask]
            state_cum = float((1.0 + sub).cumprod().iloc[-1] - 1.0)
            state_mean = float(sub.mean())
            state_win_rate = float((sub > 0).mean())
        else:
            state_cum = state_mean = state_win_rate = float("nan")
        rows.append(
            {
                "label": label,
                "def": _JOINT_SEP.join(by_t),
                "state": state,
                "n_windows": n,
                "n_segments": int(segments.get(state, 0)),
                "mean_window_excess": state_mean,
                "cum_excess": state_cum,
                "win_rate": state_win_rate,
                # No state-level annualization (D7): always NaN.
                "excess_annual": float("nan"),
            }
        )

    columns = list(_SUMMARY_COLUMNS) + (["excess_annual"] if freq is not None else [])
    return pd.DataFrame(rows, columns=columns)


def _state_keys(frame: pd.DataFrame, by_t: tuple[str, ...]) -> pd.Series:
    """Per-row state key: the label column itself, or the joint combo."""
    if len(by_t) == 1:
        return frame[by_t[0]].astype(str)
    combo = frame[by_t[0]].astype(str)
    for col in by_t[1:]:
        combo = combo + _JOINT_SEP + frame[col].astype(str)
    return combo


def calc_drawdown_regime(
    index_daily: pd.DataFrame,
    peak_window: int = 252,
    min_periods: int = 252,
    levels: tuple[float, float] = (0.10, 0.20),
) -> pd.DataFrame:
    """Label each index day by its drawdown from the rolling peak (batch14 D1).

    The reference peak is the trailing ``peak_window``-day maximum of the
    close, so the label at t is a function of index data up to and
    including t's close — the same PIT argument as the module docstring:
    the engine buys at the rebalance date's close, so a label computed
    from t's close introduces no lookahead. A full-history peak is
    deliberately *not* used: it would make the label depend on where the
    data starts.

    ``min_periods`` is pinned to the full window. Days that have not seen
    ``peak_window`` closes yet keep ``drawdown = NaN`` and
    ``drawdown_regime = NaN`` — they are never forward-filled or stamped
    with a real label, because a partially warmed peak is not a peak.
    Callers feed full history (2005+) so that every day inside the panel
    is warmed; the residual NaN days are then dropped by
    :func:`join_window_regimes` alongside any missing regime date, by the
    same notna keep.

    Args:
        index_daily: index daily bars with [date, close]
        peak_window: trailing window for the reference peak (trading days)
        min_periods: minimum observations for a non-NaN drawdown; pinned to
            ``peak_window`` by default so the peak is a true full-window max
        levels: ``(correction, deep)`` drawdown boundaries as positive
            fractions — ``mild`` is drawdown shallower than ``-correction``,
            ``correction`` is ``(-deep, -correction]`` and ``deep`` is
            ``<= -deep`` (boundary values fall to the deeper state).
            **The default ``(0.10, 0.20)`` is the frozen batch14 main
            definition** (market convention: -10% correction, -20% bear
            market). Other values are for the pre-registered sensitivity
            grid only; they must not be used as the main definition without
            a new batch pre-registration.

    Returns:
        DataFrame [date, drawdown, drawdown_regime] with the input's row
        order; ``drawdown`` is ``close / peak - 1`` (negative in drawdown).
    """
    corr_lvl, deep_lvl = (float(x) for x in levels)
    if not 0.0 < corr_lvl < deep_lvl < 1.0:
        raise ValueError(
            f"levels={levels!r} must satisfy 0 < correction < deep < 1 "
            "(positive drawdown fractions)"
        )
    if min_periods < 1 or peak_window < min_periods:
        raise ValueError(
            f"need 1 <= min_periods <= peak_window, got min_periods="
            f"{min_periods}, peak_window={peak_window}"
        )

    # ``close`` is only an input to the peak/drawdown arithmetic — it is not
    # part of the returned contract, so it stays local instead of riding along
    # in ``out`` (see the Returns section above).
    out = index_daily[["date"]].copy()
    close = index_daily["close"].astype(float)
    peak = close.rolling(peak_window, min_periods=min_periods).max()
    dd = close / peak - 1.0

    d = dd.to_numpy(dtype=float)
    # NaN comparisons are False, so warm-up days would silently fall to the
    # "deep" default — the same trap regime.py's np.select has. Overwrite
    # them with NaN explicitly instead of letting a fake label through.
    regime = np.select(
        [d > -corr_lvl, d > -deep_lvl],
        ["mild", "correction"],
        default="deep",
    ).astype(object)
    regime[np.isnan(d)] = np.nan

    out["drawdown"] = dd
    out["drawdown_regime"] = regime
    return out
