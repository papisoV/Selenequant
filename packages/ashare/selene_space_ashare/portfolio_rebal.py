"""
Periodic-rebalance portfolio layer for A-share factor strategies.

Window-return semantics: a rebalance at date t holds the TopN names (by
composite score) until the next rebalance date, and the window return
settles once at the end. This is fundamentally different from the daily
overlapping forward-return accounting in backtest.py, hence a separate
module (batch10 ADR: no dual semantics inside construct_portfolio).

Also provides benchmark alignment and excess-return metrics
(TE / IR / excess MDD / excess win rate) at window frequency.
"""

from collections.abc import Callable, Sequence
from typing import Any

import numpy as np
import pandas as pd

from selene_space_ashare.backtest import calc_performance_metrics
from selene_space_ashare.composite import _forward_horizon, build_composite_panel
from selene_space_ashare.data_fetcher import get_index_daily
from selene_space_ashare.factors import ALL_FACTORS


def backtest_periodic_portfolio(
    cross_section: pd.DataFrame,
    score_col: str = "composite_score",
    n_long: int = 20,
    rebalance_days: int = 5,
    forward_col: str = "fwd_5d",
    cost_bps: float = 0.0,
    weight_fn: "WeightFn | None" = None,
) -> pd.DataFrame:
    """N-day periodic rebalance backtest on a factor panel.

    At each rebalance date t (grid = sorted unique panel dates, every
    rebalance_days-th date), the TopN valid stocks by score_col are held
    equally weighted until the next rebalance date. The window return is
    the mean of forward_col over held stocks, settled once per window
    (non-overlapping windows). The last grid date has no next rebalance
    date to define a window end, so it never emits a row.

    Turnover convention (matches backtest.py:79-82): turnover =
    (added + removed) / n_long, both legs counted once at the portfolio
    level; the first window is charged no cost (prev is None). Net
    window return = gross mean forward return - cost_bps / 1e4 * turnover.
    A window with fewer than n_long valid rows (score and forward both
    present) is skipped entirely; the held set is carried over, so the
    next settled window's turnover reflects the full gap. This two-leg
    name-level convention is intentional (the legacy monitor charged cost
    on the one-leg removed-only ratio); do not "fix" it to one leg. Cost
    charged on name-level turnover is likewise inherited, not a bug.

    Note on annualization: callers normalize by len(returns). With zero
    skipped windows, n_windows * rebalance_days == total trading days T,
    so (1 + cum)^(252/T) equals calc_performance_metrics' (1 +
    cum)^(freq/n) exactly when freq = 252 / rebalance_days. When windows
    are skipped, len(returns) < T / N and the annualization is an
    approximation.

    Args:
        cross_section: panel with [date, symbol, score_col, forward_col]
        score_col: column to rank on (descending)
        n_long: number of names to hold
        rebalance_days: holding period in trading days
        forward_col: forward return column, horizon must equal rebalance_days
        cost_bps: one-way transaction cost in basis points, charged on
            turnover at the portfolio level (both legs counted once)
        weight_fn: optional per-window weighting hook. Contract: receives
            the held TopN frame (rows in score-descending order, full
            panel columns; its row order IS the output order) and returns
            a 1-D non-negative ndarray of len(held) weights aligned to
            those rows. Any history the fn uses must live in its own
            closure — it is not part of this signature. None (default)
            keeps equal weighting. A fn that raises, returns the wrong
            length, NaN, or all zeros falls back to equal weights for
            that window (see _resolve_weights).

    Returns:
        DataFrame [rebalance_date, end_date, period_return, turnover,
        n_held]; period_return is net of cost.

    Raises:
        ValueError: if rebalance_days != _forward_horizon(forward_col)
    """
    horizon = _forward_horizon(forward_col)
    if rebalance_days != horizon:
        raise ValueError(
            f"rebalance_days ({rebalance_days}) must equal the forward "
            f"horizon of {forward_col} ({horizon})"
        )

    panel = cross_section.copy()
    dates = sorted(panel["date"].unique())
    rebalance_dates = dates[::rebalance_days]

    results: list[dict[str, object]] = []
    prev_held: set[str] | None = None
    for i, t in enumerate(rebalance_dates[:-1]):
        end_date = rebalance_dates[i + 1]
        group = panel[panel["date"] == t]
        mask = group[score_col].notna() & group[forward_col].notna()
        valid = group[mask]
        if len(valid) < n_long:
            continue

        ranked = valid.sort_values(score_col, ascending=False)
        held = ranked.head(n_long)
        if weight_fn is None:
            gross = float(held[forward_col].mean())
        else:
            w = _resolve_weights(weight_fn, held)
            gross = float(np.dot(w, held[forward_col].to_numpy(dtype=float)))
        cur_held = set(str(s) for s in held["symbol"])

        turnover = 0.0
        if prev_held is not None:
            changed = len(cur_held - prev_held) + len(prev_held - cur_held)
            turnover = changed / max(1, n_long)
        cost = cost_bps / 10000.0 * turnover
        prev_held = cur_held

        results.append(
            {
                "rebalance_date": t,
                "end_date": end_date,
                "period_return": gross - cost,
                "turnover": turnover,
                "n_held": len(held),
            }
        )

    return pd.DataFrame(
        results,
        columns=["rebalance_date", "end_date", "period_return", "turnover", "n_held"],
    )


def align_benchmark_windows(
    index_daily: pd.DataFrame,
    windows: pd.DataFrame,
) -> pd.Series:
    """Per-window benchmark return from index closes at window endpoints.

    Args:
        index_daily: [date, close] as produced by get_index_daily (date
            may be str "%Y%m%d" or Timestamp; normalized to Timestamp
            internally so both sides compare)
        windows: backtest_periodic_portfolio output with rebalance_date
            and end_date columns

    Returns:
        Series of benchmark window returns indexed like windows, with
        .attrs["dropped"] = number of windows discarded because either
        endpoint was missing from the index calendar.
    """
    idx = index_daily.copy()
    idx["date"] = pd.to_datetime(idx["date"].astype(str))
    close = pd.Series(idx["close"].to_numpy(), index=idx["date"])
    close = close[~close.index.duplicated(keep="last")].sort_index()

    reb = pd.DatetimeIndex(pd.to_datetime(windows["rebalance_date"]))
    end = pd.DatetimeIndex(pd.to_datetime(windows["end_date"]))
    keep = reb.isin(close.index) & end.isin(close.index)

    dropped = int((~keep).sum())
    reb_close = close.reindex(reb[keep]).to_numpy()
    end_close = close.reindex(end[keep]).to_numpy()
    bench = end_close / reb_close - 1.0

    out = pd.Series(bench, index=windows.index[keep], dtype=float)
    out.name = "benchmark_return"
    out.attrs["dropped"] = dropped
    return out


def calc_benchmark_metrics(
    portfolio_returns: pd.Series,
    benchmark_returns: pd.Series,
    freq: int = 252,
) -> dict[str, float]:
    """Excess-return metrics at window frequency.

    Convention (pinned by tests): per-window excess = p - b (arithmetic,
    a reasonable approximation for equal-weight portfolios over short
    windows); excess curve = cumprod(1 + excess); TE = std(excess,
    ddof=1) * sqrt(freq); IR = mean(excess) / std(excess) * sqrt(freq),
    nan (never raises) when std is 0 or undefined; excess MDD taken on
    the excess curve via cummax drawdown; excess_win_rate =
    (excess > 0).mean().

    Args:
        portfolio_returns: window-frequency portfolio returns
        benchmark_returns: aligned window-frequency benchmark returns
            (same length/index as portfolio_returns)
        freq: annualization factor in windows per year; callers pass
            252 // rebalance_days (int, matching calc_performance_metrics)
    """
    p = pd.Series(portfolio_returns, dtype=float).reset_index(drop=True)
    b = pd.Series(benchmark_returns, dtype=float).reset_index(drop=True)
    excess = p - b

    std = float(excess.std(ddof=1))
    te = std * np.sqrt(freq)
    ir = excess.mean() / std * np.sqrt(freq) if std > 0 else float("nan")

    curve = (1 + excess).cumprod()
    running_max = curve.cummax()
    drawdown = curve / running_max - 1

    excess_cum = float(curve.iloc[-1] - 1) if len(curve) > 0 else float("nan")
    excess_annual = (
        float((1 + excess_cum) ** (freq / len(curve)) - 1)
        if len(curve) > 0 and excess_cum > -1
        else float("nan")
    )

    return {
        "te": float(te),
        "ir": float(ir),
        "excess_cumulative": excess_cum,
        "excess_annual": excess_annual,
        "excess_max_drawdown": float(drawdown.min())
        if len(curve) > 0
        else float("nan"),
        "excess_win_rate": float((excess > 0).mean()),
    }


def detect_panel_factors(panel: pd.DataFrame) -> list[str]:
    """Whitelist factors present in the panel: ALL_FACTORS keys ∩ panel columns.

    Intersection only (never an exclusion sweep): real panels carry 50+
    columns (OHLCV, industry, duplicated fundamental cols), and an
    exclude-list silently re-admits non-factor columns.
    """
    cols = set(panel.columns)
    return [name for name in ALL_FACTORS if name in cols]


def run_portfolio_backtest(
    panel: pd.DataFrame,
    factor_names: list[str] | None = None,
    benchmark_index: str = "000300",
    index_fetcher: Callable[[str], pd.DataFrame] = get_index_daily,
    n_long: int = 20,
    rebalance_days: int = 5,
    forward_col: str = "fwd_5d",
    cost_bps: float = 15.0,
    weight_method: str = "ir_walkforward",
    stock_weight: str = "equal",
) -> dict[str, Any]:
    """End-to-end periodic-rebalance portfolio run with benchmark excess.

    Pipeline: resolve factors (explicit list or ALL_FACTORS whitelist
    detection) -> build_composite_panel with walk-forward IC weights ->
    backtest_periodic_portfolio -> index_fetcher(benchmark_index) ->
    align_benchmark_windows -> portfolio / benchmark / excess metrics.
    Windows whose endpoints are missing from the index calendar are
    dropped on both sides before metrics, so the portfolio and benchmark
    return series always share the same index.

    Args:
        panel: cross-section with [date, symbol, factor cols, forward_col]
        factor_names: explicit factors, or None for whitelist detection
        benchmark_index: index code passed to index_fetcher
        index_fetcher: injectable [date, close] source (get_index_daily
            signature subset); tests pass a synthetic one
        n_long: names held per rebalance
        rebalance_days: holding period in trading days; also sets the
            annualization freq (252 // rebalance_days) on all metrics
        forward_col: forward return column, horizon must equal rebalance_days
        cost_bps: one-way transaction cost in basis points
        weight_method: factor composite weighting method, one of
            {"ir", "ic", "equal", "ir_walkforward"} — anything else would
            silently produce zero weights downstream (compute_ic_weights
            has no else branch), so it is rejected here
        stock_weight: stock weighting scheme, one of {"equal", "slsqp",
            "inv_vol"} — rejected here up front. "equal" (default) keeps
            the engine's equal weighting; "slsqp" / "inv_vol" dispatch to
            the same closures the param-lab grid uses and require a
            "close" column for the risk history.

    Returns:
        dict with portfolio_metrics, benchmark_metrics, excess_metrics,
        windows (the aligned window table with period_return), config
        (n_long, rebalance_days, cost_bps, forward_col, benchmark_index,
        factor_names, n_factors_detected, weight_method, stock_weight).

    Raises:
        ValueError: if whitelist detection finds zero factor columns,
            the benchmark fetch returns an empty frame, weight_method
            or stock_weight is not in its valid set, or stock_weight
            "slsqp"/"inv_vol" is requested on a panel without close.
    """
    valid_methods = {"ir", "ic", "equal", "ir_walkforward"}
    if weight_method not in valid_methods:
        raise ValueError(
            f"unknown weight_method {weight_method!r}; valid: "
            f"{sorted(valid_methods)} (compute_ic_weights would silently "
            "produce zero weights for anything else)"
        )
    valid_sw = {"equal", "slsqp", "inv_vol"}
    if stock_weight not in valid_sw:
        raise ValueError(
            f"unknown stock_weight {stock_weight!r}; valid: {sorted(valid_sw)} "
            "(a typo like 'invol' would be silently treated as 'equal')"
        )
    if stock_weight in {"slsqp", "inv_vol"} and "close" not in panel.columns:
        raise ValueError(
            f'stock_weight "{stock_weight}" requires a "close" column in the '
            'panel (needed for the risk history); only "equal" is wired '
            "into this run so far (US-105 adds the dispatch)"
        )

    if factor_names is None:
        factor_names = detect_panel_factors(panel)
        if not factor_names:
            raise ValueError(
                "no ALL_FACTORS columns found in panel "
                f"({len(panel.columns)} columns); pass factor_names explicitly"
            )

    scored, _config = build_composite_panel(
        panel,
        factor_names,
        forward_col,
        weight_method=weight_method,
    )
    # stock-weight dispatch (F3 pattern, portfolio_rebal.py:723/732):
    # "equal" -> None keeps the engine's equal weighting; the others close
    # over the scored panel's close pivot for their risk history
    weight_fn: WeightFn | None = None
    if stock_weight == "slsqp":
        weight_fn = make_slsqp_weight_fn(scored)
    elif stock_weight == "inv_vol":
        weight_fn = make_inverse_vol_weight_fn(scored)

    windows = backtest_periodic_portfolio(
        scored,
        n_long=n_long,
        rebalance_days=rebalance_days,
        forward_col=forward_col,
        cost_bps=cost_bps,
        weight_fn=weight_fn,
    )

    freq = 252 // rebalance_days
    portfolio_returns = pd.Series(windows["period_return"].to_numpy(), dtype=float)

    index_daily = index_fetcher(benchmark_index)
    if index_daily is None or index_daily.empty:
        raise ValueError(f"index_fetcher returned no data for {benchmark_index}")
    bench = align_benchmark_windows(index_daily, windows)
    if int(bench.attrs.get("dropped", 0)) > 0:
        windows = windows.loc[bench.index]
        portfolio_returns = portfolio_returns.loc[bench.index]

    portfolio_metrics = calc_performance_metrics(portfolio_returns, freq=freq)
    benchmark_metrics = calc_performance_metrics(bench, freq=freq)
    excess_metrics = calc_benchmark_metrics(portfolio_returns, bench, freq=freq)

    return {
        "portfolio_metrics": portfolio_metrics,
        "benchmark_metrics": benchmark_metrics,
        "excess_metrics": excess_metrics,
        "windows": windows,
        "config": {
            "n_long": n_long,
            "rebalance_days": rebalance_days,
            "cost_bps": cost_bps,
            "forward_col": forward_col,
            "benchmark_index": benchmark_index,
            "factor_names": factor_names,
            "n_factors_detected": len(factor_names),
            "weight_method": weight_method,
            "stock_weight": stock_weight,
        },
    }


# ---------------------------------------------------------------------------
# SLSQP weight optimization: shrunk covariance, constrained score
# maximization, and the panel-closure weight function.
# ---------------------------------------------------------------------------

WeightFn = Callable[[pd.DataFrame], np.ndarray]


def shrunk_cov(daily_rets: pd.DataFrame, delta: float = 0.5) -> np.ndarray:
    """Annualized shrinkage covariance: (1-delta)*S + delta*diag(diag(S)).

    Faithful port of legacy portfolio.py:26-34. S is the ddof=1 sample
    covariance annualized x250 (the legacy math keeps 250; metric-level
    annualization elsewhere in this module uses 252 — intentional, do not
    unify). A column that is all-NaN inside the window yields a zero
    row/column after nan_to_num — callers must subset to valid columns
    before optimizing (see make_slsqp_weight_fn).
    """
    if daily_rets is None or daily_rets.shape[1] == 0:
        return np.zeros((0, 0))
    s = daily_rets.cov().to_numpy(dtype=float) * 250
    s = np.nan_to_num(s, nan=0.0)
    return np.asarray((1 - delta) * s + delta * np.diag(np.diag(s)), dtype=float)


def optimize_weights(
    score: pd.Series,
    cov: np.ndarray,
    max_w: float = 0.06,
    lam: float = 2.0,
) -> np.ndarray:
    """Maximize score·w - lam·w'C·w s.t. sum(w)=1, 0 <= w_i <= max_w.

    Faithful port of legacy portfolio.py:37-72. score and cov are each
    normalized by their max absolute value before the objective, so lam's
    effective strength drifts with the subset handed in (legacy behavior,
    kept). Feasibility clamp max_w = max(max_w, 1/n); the legacy re-clamp
    at :56-57 is unreachable after it and is not ported. Solver failure
    or exception falls back to equal weights.
    """
    # Deferred import: scipy is an optional [alpha] extra, so the module
    # stays importable without it.
    from scipy.optimize import minimize

    n = len(score)
    if n == 0:
        return np.array([])
    s = np.asarray(score, dtype=float)
    s = s / (np.abs(s).max() + 1e-12)
    c = np.asarray(cov, dtype=float)
    if c.shape != (n, n):
        c = np.zeros((n, n))
    c = c / (np.abs(c).max() + 1e-12)
    max_w = max(max_w, 1.0 / n)

    def obj(w: np.ndarray) -> float:
        return float(-(s @ w - lam * (w @ c @ w)))

    w0 = np.full(n, 1.0 / n)
    try:
        res = minimize(
            obj,
            w0,
            method="SLSQP",
            bounds=[(0.0, max_w)] * n,
            constraints=[{"type": "eq", "fun": lambda w: w.sum() - 1.0}],
            options={"maxiter": 300, "ftol": 1e-10},
        )
        w = np.asarray(res.x, dtype=float) if res.success else w0
    except Exception:
        w = w0
    w = np.clip(w, 0.0, None)
    total = float(w.sum())
    return w / total if total > 0 else w0


def make_slsqp_weight_fn(
    panel: pd.DataFrame,
    score_col: str = "composite_score",
    max_w: float = 0.06,
    lam: float = 2.0,
    delta: float = 0.5,
    lookback: int = 250,
    min_names: int = 5,
) -> WeightFn:
    """Build a WeightFn closing over the panel's close pivot.

    The returned fn receives the engine's held slice (TopN rows in score-
    descending order, full panel columns) and returns one weight per held
    row. History = daily close pct_change up to and including t (t-close
    buy; the t row of pct_change is realized at decision time — no
    lookahead, matching legacy :228).

    Subset semantics (legacy :228-232): all-NaN columns in
    the lookback window are dropped before cov/optimization. Scattering
    without subsetting would hand all-NaN names a zero covariance block —
    a free asset the optimizer funds at a positive weight. Dropped names
    get weight 0; fewer than min_names valid columns falls back to equal
    weight across all held rows. Returns use pct_change(fill_method=None):
    suspension gaps stay NaN (pairwise covariance) and all-NaN columns
    are subset away entirely.
    """
    if "close" not in panel.columns:
        raise ValueError(
            "make_slsqp_weight_fn requires a 'close' column in the panel "
            f"(got {len(panel.columns)} columns)"
        )
    closes = panel[["date", "symbol", "close"]].copy()
    closes["date"] = pd.to_datetime(closes["date"])
    closes["symbol"] = closes["symbol"].astype(str)
    close_wide = closes.pivot_table(
        index="date", columns="symbol", values="close", aggfunc="last"
    ).sort_index()
    rets = close_wide.pct_change(fill_method=None)

    def weight_fn(held: pd.DataFrame) -> np.ndarray:
        t = pd.to_datetime(held["date"].iloc[0])
        sel = [str(x) for x in held["symbol"]]
        hist = rets.loc[rets.index <= t].tail(lookback).reindex(columns=sel)
        hist = hist.dropna(axis=1, how="all")
        if hist.shape[1] < min_names:
            return np.full(len(held), 1.0 / len(held))
        scores = pd.Series(
            held[score_col].to_numpy(dtype=float), index=pd.Index(sel)
        ).reindex(hist.columns)
        sub = optimize_weights(
            scores,
            shrunk_cov(hist, delta),
            max_w=max_w,
            lam=lam,
        )
        out = np.zeros(len(held))
        pos = {name: i for i, name in enumerate(sel)}
        for k, col in enumerate(hist.columns):
            out[pos[str(col)]] = sub[k]
        return out

    return weight_fn


def make_inverse_vol_weight_fn(
    panel: pd.DataFrame,
    lookback: int = 60,
    min_names: int = 5,
) -> WeightFn:
    """Build a WeightFn closing over the panel's close pivot.

    The returned fn receives the engine's held slice (TopN rows in score-
    descending order, full panel columns) and returns one weight per held
    row. History = daily close pct_change up to and including t (t-close
    buy; the t row of pct_change is realized at decision time — no
    lookahead, identical convention to make_slsqp_weight_fn).

    Per-name weight is proportional to 1/std(daily return) over the
    lookback window, normalized across the surviving (vol-valid) names.
    Columns whose vol is NaN (no return history) or zero (constant close,
    suspended flat pricing) are dropped — they get weight 0. Fewer than
    min_names surviving columns falls back to equal weight across all held
    rows.

    Subset/keep semantics differ in motive from make_slsqp_weight_fn.
    SLSQP MUST subset because an all-NaN column becomes a zero-covariance
    "free asset" the optimizer would fund at a positive weight (batch11
    Critic R-1). There is no optimizer here, so that mechanism does not
    exist; dropping a column is purely because inverse-vol needs a valid
    vol estimate at all. Annualization is a constant factor across names
    and cancels in the normalization, so it is not applied.
    """
    if "close" not in panel.columns:
        raise ValueError(
            "make_inverse_vol_weight_fn requires a 'close' column in the "
            f"panel (got {len(panel.columns)} columns)"
        )
    closes = panel[["date", "symbol", "close"]].copy()
    closes["date"] = pd.to_datetime(closes["date"])
    closes["symbol"] = closes["symbol"].astype(str)
    close_wide = closes.pivot_table(
        index="date", columns="symbol", values="close", aggfunc="last"
    ).sort_index()
    rets = close_wide.pct_change(fill_method=None)

    def weight_fn(held: pd.DataFrame) -> np.ndarray:
        t = pd.to_datetime(held["date"].iloc[0])
        sel = [str(x) for x in held["symbol"]]
        hist = rets.loc[rets.index <= t].tail(lookback).reindex(columns=sel)
        vol = hist.std(ddof=1, axis=0)
        valid = vol[(vol.notna()) & (vol != 0.0)]
        if len(valid) < min_names:
            return np.full(len(held), 1.0 / len(held))
        w = 1.0 / valid
        w = w / w.sum()
        out = np.zeros(len(held))
        pos = {name: i for i, name in enumerate(sel)}
        for name, weight in w.items():
            out[pos[str(name)]] = weight
        return out

    return weight_fn


def _resolve_weights(fn: WeightFn, held: pd.DataFrame) -> np.ndarray:
    """Validate a weight_fn's output for one window; dirty output -> equal.

    A hook the user supplies must never crash or poison the backtest, so
    every failure mode (exception, wrong length, NaN, all-zero) degrades
    to equal weights for that window only. A valid output is clipped at 0
    and normalized to sum to 1.
    """
    n = len(held)
    equal = np.full(n, 1.0 / n)
    try:
        w = np.asarray(fn(held), dtype=float)
    except Exception:
        return equal
    if w.ndim != 1 or w.shape[0] != n or not np.all(np.isfinite(w)):
        return equal
    w = np.clip(w, 0.0, None)
    total = float(w.sum())
    return w / total if total > 0 else equal


def run_param_lab(
    panel: pd.DataFrame,
    factor_names: list[str] | None = None,
    benchmark_index: str = "000300",
    index_fetcher: Callable[[str], pd.DataFrame] = get_index_daily,
    rebalances: Sequence[int] = (5, 21),
    n_longs: Sequence[int] = (20, 30, 50),
    stock_weights: Sequence[str] = ("equal", "slsqp"),
    weight_methods: Sequence[str] = ("ir_walkforward",),
    cost_bps: float = 15.0,
    slsqp_kwargs: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Parameter sensitivity grid over the periodic-rebalance backtest.

    This is a parameter sensitivity check, not an optimization — read the
    full grid, do not cherry-pick a cell (the legacy monitor's warning,
    kept verbatim in spirit). One row per (weight_method, rebalance,
    n_long, stock_weight) combination with portfolio, benchmark, and
    excess-return metrics per cell.

    Note on lambda comparability: optimize_weights normalizes the
    covariance by its max absolute value, so the effective strength of
    `lam` drifts with the subset size handed in — cells with different
    n_long see slightly different effective risk penalties. The legacy
    monitor behaved the same way; this is a faithful port.

    Note on empty cells: long-lookback factors warm up over history, so
    high-horizon cells (e.g. rebalance=21) may settle zero windows in the
    panel's tail. Empty cells are reported with n_windows=0 and NaN
    metrics rather than dropped — the truth stays visible.

    Args:
        panel: cross-section with [date, symbol, factor cols, forward cols,
            close (required when "slsqp" or "inv_vol" is in stock_weights)]
        factor_names: explicit factors, or None for whitelist detection
            (ALL_FACTORS keys present in panel columns)
        benchmark_index: index code passed to index_fetcher
        index_fetcher: injectable [date, close] source; called once
        rebalances: holding periods in trading days; each must have a
            matching fwd_{rebalance}d column (the engine requires
            rebalance_days == forward horizon)
        n_longs: names held per rebalance
        stock_weights: subset of {"equal", "slsqp", "inv_vol"} — "equal"
            (uniform), "slsqp" (score-risk optimization), "inv_vol"
            (inverse-volatility risk weights). Unknown values are rejected
            up front rather than silently treated as "equal"
        weight_methods: factor composite weighting methods, subset of
            {"ir", "ic", "equal", "ir_walkforward"} — anything else would
            silently produce zero weights downstream (compute_ic_weights
            has no else branch), so it is rejected here
        cost_bps: one-way transaction cost in basis points
        slsqp_kwargs: optional overrides for make_slsqp_weight_fn
            (max_w, lam, delta, lookback, min_names)

    Returns:
        DataFrame with columns [rebalance, n_long, stock_weight,
        weight_method, n_windows, cumulative_return, annual_return,
        benchmark_annual, excess_annual, te, ir, excess_max_drawdown,
        excess_win_rate, avg_turnover].

    Raises:
        ValueError: on a missing forward column, an unknown weight
            method, an unknown stock_weight, a missing close column for
            "slsqp"/"inv_vol", or an empty factor whitelist.
    """
    valid_methods = {"ir", "ic", "equal", "ir_walkforward"}
    bad_methods = [m for m in weight_methods if m not in valid_methods]
    if bad_methods:
        raise ValueError(
            f"unknown weight_methods {bad_methods}; valid: "
            f"{sorted(valid_methods)} (compute_ic_weights would silently "
            "produce zero weights for anything else)"
        )
    missing_fwd = [reb for reb in rebalances if f"fwd_{reb}d" not in panel.columns]
    if missing_fwd:
        available = sorted(c for c in panel.columns if c.startswith("fwd_"))
        raise ValueError(
            f"panel lacks forward columns {['fwd_%dd' % r for r in missing_fwd]}; "
            f"available fwd columns: {available} (the engine requires "
            "rebalance_days == forward horizon)"
        )
    valid_sw = {"equal", "slsqp", "inv_vol"}
    bad_sw = [s for s in stock_weights if s not in valid_sw]
    if bad_sw:
        raise ValueError(
            f"unknown stock_weights {bad_sw}; valid: {sorted(valid_sw)} "
            "(a typo like 'invol' would be silently treated as 'equal')"
        )
    if ({"slsqp", "inv_vol"} & set(stock_weights)) and "close" not in panel.columns:
        raise ValueError(
            'stock_weights includes "slsqp" or "inv_vol" but the panel has '
            'no "close" column (needed for the risk history)'
        )
    if factor_names is None:
        factor_names = detect_panel_factors(panel)
        if not factor_names:
            raise ValueError(
                "no ALL_FACTORS columns found in panel "
                f"({len(panel.columns)} columns); pass factor_names explicitly"
            )

    index_daily = index_fetcher(benchmark_index)
    if index_daily is None or index_daily.empty:
        raise ValueError(f"index_fetcher returned no data for {benchmark_index}")

    # One composite build + one weight_fn closure per (forward_col, method)
    # pair — the two cost drivers are shared across every n_long/stock
    # weight cell that reuses the pair. The inv_vol closure depends only on
    # the panel's close pivot (not on fwd_col/method), so it is built once
    # here rather than per cache key.
    inv_vol_fn: WeightFn | None = (
        make_inverse_vol_weight_fn(panel) if "inv_vol" in stock_weights else None
    )
    cache: dict[tuple[str, str], tuple[pd.DataFrame, WeightFn | None]] = {}
    for reb in rebalances:
        fwd_col = f"fwd_{reb}d"
        for wm in weight_methods:
            scored, _cfg = build_composite_panel(panel, factor_names, fwd_col, wm)
            wf: WeightFn | None = None
            if "slsqp" in stock_weights:
                kwargs: dict[str, Any] = dict(slsqp_kwargs or {})
                wf = make_slsqp_weight_fn(scored, **kwargs)
            cache[(fwd_col, wm)] = (scored, wf)

    rows: list[dict[str, object]] = []
    for reb in rebalances:
        fwd_col = f"fwd_{reb}d"
        freq = 252 // reb
        for wm in weight_methods:
            scored, wf = cache[(fwd_col, wm)]
            fn_map: dict[str, WeightFn | None] = {"slsqp": wf, "inv_vol": inv_vol_fn}
            for n_long in n_longs:
                for sw in stock_weights:
                    windows = backtest_periodic_portfolio(
                        scored,
                        n_long=n_long,
                        rebalance_days=reb,
                        forward_col=fwd_col,
                        cost_bps=cost_bps,
                        weight_fn=fn_map.get(sw),  # "equal" -> None
                    )
                    n_windows = len(windows)
                    if n_windows == 0:
                        nan = float("nan")
                        rows.append(
                            {
                                "rebalance": reb,
                                "n_long": n_long,
                                "stock_weight": sw,
                                "weight_method": wm,
                                "n_windows": 0,
                                "cumulative_return": nan,
                                "annual_return": nan,
                                "benchmark_annual": nan,
                                "excess_annual": nan,
                                "te": nan,
                                "ir": nan,
                                "excess_max_drawdown": nan,
                                "excess_win_rate": nan,
                                "avg_turnover": nan,
                            }
                        )
                        continue

                    pr = pd.Series(windows["period_return"].to_numpy(), dtype=float)
                    bench = align_benchmark_windows(index_daily, windows)
                    if int(bench.attrs.get("dropped", 0)) > 0:
                        windows = windows.loc[bench.index]
                        pr = pd.Series(windows["period_return"].to_numpy(), dtype=float)
                    pm = calc_performance_metrics(pr, freq=freq)
                    bm = calc_performance_metrics(bench, freq=freq)
                    em = calc_benchmark_metrics(pr, bench, freq=freq)
                    rows.append(
                        {
                            "rebalance": reb,
                            "n_long": n_long,
                            "stock_weight": sw,
                            "weight_method": wm,
                            "n_windows": n_windows,
                            "cumulative_return": float((1 + pr).prod() - 1),
                            "annual_return": float(
                                pm.get("annual_return", float("nan"))
                            ),
                            "benchmark_annual": float(
                                bm.get("annual_return", float("nan"))
                            ),
                            "excess_annual": float(em["excess_annual"]),
                            "te": float(em["te"]),
                            "ir": float(em["ir"]),
                            "excess_max_drawdown": float(em["excess_max_drawdown"]),
                            "excess_win_rate": float(em["excess_win_rate"]),
                            "avg_turnover": float(windows["turnover"].mean()),
                        }
                    )

    return pd.DataFrame(
        rows,
        columns=[
            "rebalance",
            "n_long",
            "stock_weight",
            "weight_method",
            "n_windows",
            "cumulative_return",
            "annual_return",
            "benchmark_annual",
            "excess_annual",
            "te",
            "ir",
            "excess_max_drawdown",
            "excess_win_rate",
            "avg_turnover",
        ],
    )
