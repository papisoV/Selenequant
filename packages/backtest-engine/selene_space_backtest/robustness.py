"""Robustness statistics for backtest results.

Answers "was this path lucky?" with three independent resampling tools:

  - :func:`permutation_test` — shuffle the trade order; is the observed
    Sharpe / max-drawdown better than a random reordering of the *same*
    trades?
  - :func:`bootstrap_sharpe_ci` — resample returns with replacement; how
    wide is the Sharpe confidence interval?
  - :func:`equity_curve_percentiles` — percentile bands for the equity
    curve under resampled return paths.

This module is deliberately **primitives-only**: it takes plain sequences of
floats, not any engine's ``TradeRecord``. Both the standalone
``selene_space_backtest`` engine and the ``subsystems/vibe`` engine tree can
call it without either depending on the other's data classes — the reason the
same statistic previously existed (and drifted) in two places.

Scope warning for consumers: the permutation test is about *sequence* luck,
not edge. It is complementary to DSR/PBO (selection bias) and should be read
alongside them, not instead of them.
"""

from __future__ import annotations

import math
from numbers import Integral, Real
from typing import Any, Sequence

import numpy as np

# Equity-band sampling is chunked over time so the (n_samples, n_bars)
# float64 resample matrix is never materialised in full. Peak transient memory
# is bounded by the int32 index matrix (n_samples * n_bars * 4 bytes) plus the
# working chunk (n_samples * chunk floats), rather than by a float64
# (n_samples, n_bars) matrix turned into Python floats — at 10_000 x 1_000 the
# latter is ~80 MB of float64 before anything is even returned.
#
# The index matrix is drawn in one call rather than per chunk on purpose: it is
# what makes the chunk size a pure performance knob. Drawing per chunk would
# consume the RNG stream in a chunk-dependent order, and the bands would change
# with ``chunk``.
_DEFAULT_CHUNK = 256

# Guards against a caller passing a pathologically large request.
_MAX_SAMPLES = 200_000

# Upper bound on the number of points per returned band. The bands are a
# display artifact and are persisted per backtest row, so their length must not
# scale with the bar count: a 40_000-bar run emitted 5 x 40_001 floats and a
# 1.9 MB ``robustness_json`` for a panel that reads none of it. Points are
# sampled uniformly from the full-resolution path, endpoints included.
_MAX_BAND_POINTS = 512

# Ceiling for compounded resampled equity. Chosen an order of magnitude below
# the float64 maximum so the per-step multiply in the band loop cannot overflow
# to ``inf``; a saturated band is visible as a huge number, where ``inf`` would
# be written as ``null`` and read as "missing".
_MAX_EQUITY = 1e300


def _json_num(value: float, digits: int = 6) -> float | None:
    """Round a float, mapping NaN/Inf to ``None`` for strict JSON writers."""
    v = float(value)
    if not math.isfinite(v):
        return None
    return round(v, digits)


def _sharpe(returns: np.ndarray, bars_per_year: int = 252) -> float:
    """Annualised Sharpe of a return series (0.0 when variance is zero)."""
    if returns.size < 2:
        return 0.0
    std = float(returns.std())
    if std <= 0.0:
        return 0.0
    return float(returns.mean() / std * math.sqrt(bars_per_year))


def _path_metrics(pnls: np.ndarray, initial_capital: float) -> dict[str, float]:
    """Sharpe and max drawdown of an equity path built from a PnL sequence."""
    equity = initial_capital + np.cumsum(pnls)
    if equity.size > 1:
        prev = equity[:-1]
        # A non-positive running equity is a ruined path; guard the division.
        safe = np.where(prev > 0, prev, 1.0)
        returns = np.diff(equity) / safe
    else:
        returns = np.array([0.0])
    peak = np.maximum.accumulate(equity)
    dd = (equity - peak) / np.where(peak > 0, peak, 1.0)
    return {"sharpe": _sharpe(returns), "max_dd": float(dd.min())}


# ─── Permutation test ───


def permutation_test(
    pnls: Sequence[float],
    initial_capital: float,
    n_simulations: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """Shuffle trade PnL order to test path significance.

    Null hypothesis: the observed Sharpe / max-drawdown is no better than a
    random reordering of the same trades. A low p-value means the *order* the
    trades arrived in was favourable — it says nothing about whether the
    strategy has an edge.

    The p-value floor is ``1 / (n_simulations + 1)``, and with few trades it
    cannot reach significance at all; callers should surface that rather than
    let a small sample read as a weak result.

    Args:
        pnls: Realised PnL per completed trade, in cash terms.
        initial_capital: Starting capital the path compounds from.
        n_simulations: Number of random permutations to draw.
        seed: RNG seed for reproducibility.

    Returns:
        Dict with ``actual_sharpe`` / ``p_value_sharpe`` / ``actual_max_dd`` /
        ``p_value_max_dd`` plus the simulated Sharpe distribution. On invalid
        input returns ``{"error": ..., "p_value_sharpe": 1.0}`` — a refusal,
        never a silent pass.
    """
    if (
        isinstance(n_simulations, bool)
        or not isinstance(n_simulations, Integral)
        or n_simulations < 1
    ):
        return {
            "error": f"n_simulations must be >= 1, got {n_simulations}",
            "p_value_sharpe": 1.0,
        }
    if isinstance(seed, bool) or not isinstance(seed, Integral) or seed < 0:
        return {"error": f"seed must be >= 0, got {seed}", "p_value_sharpe": 1.0}

    arr = np.asarray(list(pnls), dtype=np.float64)
    if arr.size < 3:
        return {"error": "need at least 3 trades", "p_value_sharpe": 1.0}
    if not np.all(np.isfinite(arr)):
        return {"error": "pnls must be finite", "p_value_sharpe": 1.0}

    actual = _path_metrics(arr, initial_capital)

    rng = np.random.default_rng(seed)
    sharpe_count = 0
    dd_count = 0
    sim_sharpes = np.empty(int(n_simulations), dtype=np.float64)

    for i in range(int(n_simulations)):
        shuffled = rng.permutation(arr)
        sim = _path_metrics(shuffled, initial_capital)
        sim_sharpes[i] = sim["sharpe"]
        if sim["sharpe"] >= actual["sharpe"]:
            sharpe_count += 1
        if sim["max_dd"] >= actual["max_dd"]:  # less negative == better
            dd_count += 1

    return {
        "actual_sharpe": _json_num(actual["sharpe"], 4),
        "actual_max_dd": _json_num(actual["max_dd"], 4),
        "p_value_sharpe": round(sharpe_count / int(n_simulations), 4),
        "p_value_max_dd": round(dd_count / int(n_simulations), 4),
        "simulated_sharpe_mean": _json_num(sim_sharpes.mean(), 4),
        "simulated_sharpe_std": _json_num(sim_sharpes.std(), 4),
        "simulated_sharpe_p5": _json_num(np.percentile(sim_sharpes, 5), 4),
        "simulated_sharpe_p95": _json_num(np.percentile(sim_sharpes, 95), 4),
        "n_simulations": int(n_simulations),
        "n_trades": int(arr.size),
        "p_value_floor": round(1.0 / (int(n_simulations) + 1), 6),
    }


# ─── Bootstrap Sharpe CI ───


def bootstrap_sharpe_ci(
    returns: Sequence[float],
    n_bootstrap: int = 1000,
    confidence: float = 0.95,
    bars_per_year: int = 252,
    seed: int = 42,
) -> dict[str, Any]:
    """Resample period returns to estimate a Sharpe confidence interval.

    Args:
        returns: Period returns (fractional, e.g. 0.01 == 1%).
        n_bootstrap: Number of bootstrap samples.
        confidence: Confidence level in (0, 1).
        bars_per_year: Annualisation factor.
        seed: RNG seed.

    Returns:
        Dict with ``observed_sharpe``, ``ci_lower``, ``ci_upper``,
        ``median_sharpe``, ``prob_positive``; or ``{"error": ...}``.
    """
    if (
        isinstance(n_bootstrap, bool)
        or not isinstance(n_bootstrap, Integral)
        or n_bootstrap < 1
    ):
        return {"error": f"n_bootstrap must be >= 1, got {n_bootstrap}"}
    if (
        isinstance(confidence, bool)
        or not isinstance(confidence, Real)
        or not math.isfinite(float(confidence))
        or not 0.0 < float(confidence) < 1.0
    ):
        return {"error": f"confidence must be in (0, 1), got {confidence}"}
    if isinstance(seed, bool) or not isinstance(seed, Integral) or seed < 0:
        return {"error": f"seed must be >= 0, got {seed}"}

    arr = np.asarray(list(returns), dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size < 5:
        return {"error": "need at least 5 return observations"}

    observed = _sharpe(arr, bars_per_year)

    rng = np.random.default_rng(seed)
    n = arr.size
    boot = np.empty(int(n_bootstrap), dtype=np.float64)
    for i in range(int(n_bootstrap)):
        boot[i] = _sharpe(rng.choice(arr, size=n, replace=True), bars_per_year)

    alpha = (1.0 - float(confidence)) / 2.0
    return {
        "observed_sharpe": _json_num(observed, 4),
        "ci_lower": _json_num(np.percentile(boot, alpha * 100.0), 4),
        "ci_upper": _json_num(np.percentile(boot, (1.0 - alpha) * 100.0), 4),
        "median_sharpe": _json_num(np.median(boot), 4),
        "prob_positive": _json_num(float(np.mean(boot > 0)), 4),
        "confidence": float(confidence),
        "n_bootstrap": int(n_bootstrap),
    }


# ─── Equity curve percentile bands ───


def equity_curve_percentiles(
    returns: Sequence[float],
    initial_equity: float = 10_000.0,
    n_samples: int = 1000,
    seed: int | None = None,
    chunk: int = _DEFAULT_CHUNK,
) -> dict[str, Any]:
    """Percentile bands for the equity curve under resampled return paths.

    Resamples the return sequence with replacement and compounds each path.
    Work is chunked over time so peak memory stays bounded (see module
    docstring) instead of materialising the whole ``(n_samples, n_bars)``
    matrix — the previous implementation built Python lists of every element
    and could reach hundreds of MB at the defaults.

    Args:
        returns: Period returns (fractional, e.g. 0.01 == 1%).
        initial_equity: Starting equity.
        n_samples: Number of resampled paths.
        seed: RNG seed.
        chunk: Time steps processed per pass.

    Returns:
        Dict with ``pctile_5`` … ``pctile_95`` bands and a ``summary`` of
        terminal equity; or ``{"error": ...}``. Bands are sampled to at most
        ``_MAX_BAND_POINTS`` points (the first always the starting equity, the
        last always the terminal one), so their length does not scale with the
        bar count. The ``summary`` block is computed from the full-resolution
        paths regardless of how many points the bands keep.
    """
    if (
        isinstance(n_samples, bool)
        or not isinstance(n_samples, Integral)
        or n_samples < 1
    ):
        return {"error": f"n_samples must be >= 1, got {n_samples}"}
    if int(n_samples) > _MAX_SAMPLES:
        return {"error": f"n_samples must be <= {_MAX_SAMPLES}, got {n_samples}"}
    if isinstance(chunk, bool) or not isinstance(chunk, Integral) or chunk < 1:
        return {"error": f"chunk must be >= 1, got {chunk}"}
    if seed is not None and (
        isinstance(seed, bool) or not isinstance(seed, Integral) or seed < 0
    ):
        return {"error": f"seed must be >= 0, got {seed}"}

    arr = np.asarray(list(returns), dtype=np.float64)
    arr = arr[np.isfinite(arr)]
    if arr.size < 2:
        return {"error": "need at least 2 return observations"}
    if not math.isfinite(float(initial_equity)) or float(initial_equity) <= 0:
        return {"error": f"initial_equity must be positive, got {initial_equity}"}

    n = arr.size
    total = int(n_samples)
    rng = np.random.default_rng(seed)
    indices = rng.integers(0, n, size=(total, n))

    percentiles = (5, 25, 50, 75, 95)

    # Band point ``i`` is the equity after ``i`` returns have compounded
    # (point 0 is the starting equity), so candidate positions are 1..n and a
    # band of at most ``_MAX_BAND_POINTS`` points keeps that many minus the
    # seed. Keep the endpoints so the first band value is the starting equity
    # and the last is the exact terminal equity the summary reports.
    keep = min(_MAX_BAND_POINTS - 1, n)
    positions = np.unique(np.linspace(1, n, num=keep).round().astype(np.int64))
    bands: dict[str, list[float | None]] = {
        f"pctile_{p}": [float(initial_equity)] for p in percentiles
    }

    # ``current`` threads the compounded equity across chunk boundaries; it is
    # seeded once, outside the loop. Re-seeding it per chunk would restart
    # compounding and silently understate every band past the first chunk.
    #
    # Compounding thousands of resampled returns can walk the equity past the
    # float64 ceiling (a single overflow turned into ``inf``, which the JSON
    # writer would then emit as ``null`` — a band that reads as "no data"
    # rather than as the absurd value it is). Clamping the per-step factor at
    # zero keeps a return below -100% from flipping the equity's sign, and
    # clamping the level keeps the percentile arithmetic finite. Both clamps
    # only engage far outside the range where the statistic means anything.
    current = np.full(total, float(initial_equity), dtype=np.float64)
    step = int(chunk)
    for start in range(0, n, step):
        stop = min(start + step, n)
        # (total, stop-start) — the only large array alive at any moment.
        block = arr[indices[:, start:stop]]
        eq = np.empty((total, stop - start), dtype=np.float64)
        for col in range(block.shape[1]):
            current = current * np.maximum(1.0 + block[:, col], 0.0)
            np.clip(current, 0.0, _MAX_EQUITY, out=current)
            eq[:, col] = current
        # Chunk columns are band positions ``start + 1`` … ``stop``.
        lo = int(np.searchsorted(positions, start + 1, side="left"))
        hi = int(np.searchsorted(positions, stop, side="right"))
        if hi > lo:
            cols = positions[lo:hi] - (start + 1)
            for p in percentiles:
                bands[f"pctile_{p}"].extend(
                    _json_num(v, 2) for v in np.percentile(eq[:, cols], p, axis=0)
                )

    # Fraction of *individual* resampled paths that end above water — not a
    # read of the median band, which would only ever be 0 or 1.
    prob_profit = float(np.mean(current > float(initial_equity)))
    terminal = np.asarray([bands[f"pctile_{p}"][-1] for p in percentiles], dtype=float)
    result: dict[str, Any] = dict(bands)
    result["summary"] = {
        "initial_equity": _json_num(initial_equity, 2),
        "terminal_p5": _json_num(terminal[0], 2),
        "terminal_p50": _json_num(terminal[2], 2),
        "terminal_p95": _json_num(terminal[4], 2),
        "prob_profit": _json_num(prob_profit, 4),
        "n_samples": total,
        "n_bars": int(n),
    }
    return result


# ─── Aggregate ───


def compute_robustness(
    pnls: Sequence[float],
    equity_curve: Sequence[float],
    initial_capital: float,
    *,
    bars_per_year: int = 252,
    n_simulations: int = 1000,
    n_bootstrap: int = 1000,
    seed: int = 42,
) -> dict[str, Any]:
    """Run every robustness check and return one JSON-safe payload.

    Args:
        pnls: Realised PnL per completed trade.
        equity_curve: Equity series (used to derive period returns).
        initial_capital: Starting capital.
        bars_per_year: Annualisation factor for the return series.
        n_simulations: Permutations for the trade-order test.
        n_bootstrap: Bootstrap resamples for the Sharpe CI.
        seed: RNG seed shared by both.

    Returns:
        ``{"permutation": ..., "bootstrap": ..., "equity_bands": ...}`` where
        any member that could not be computed carries an ``error`` key. Never
        raises on bad input — a failed check must be visible, not crash a run.
    """
    equity = np.asarray(list(equity_curve), dtype=np.float64)
    if equity.size > 1:
        prev = equity[:-1]
        safe = np.where(prev > 0, prev, 1.0)
        returns = np.diff(equity) / safe
    else:
        returns = np.array([], dtype=np.float64)

    return {
        "permutation": permutation_test(
            pnls, initial_capital, n_simulations=n_simulations, seed=seed
        ),
        "bootstrap": bootstrap_sharpe_ci(
            returns, n_bootstrap=n_bootstrap, bars_per_year=bars_per_year, seed=seed
        ),
        "equity_bands": equity_curve_percentiles(
            returns,
            initial_equity=initial_capital,
            n_samples=min(n_bootstrap, 1000),
            seed=seed,
        ),
        "seed": int(seed),
    }
