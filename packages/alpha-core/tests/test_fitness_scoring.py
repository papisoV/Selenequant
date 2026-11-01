"""Fitness scoring (US-02): determinism, sentinels, penalties, panel contract.

Exercises :mod:`selene_space_factor_research.mining.fitness` with real
``PandasStackVM``-executable formulas on deterministic synthetic OHLCV panels
(AR(1) returns so current momentum predicts next-bar returns). Covers the plan's
Task-2 acceptance: deterministic score, structure-violation sentinel, degenerate
(``std < 1e-8``) sentinel, turnover penalty, panel column contract, and cache.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.dsl import canonical_hash
from selene_space_factor_research.mining.fitness import (
    FormulaFitness,
    build_factor_panel,
    score,
)


def _tok(name: str) -> int:
    return list(FORMULA_VOCAB.token_names).index(name)


RET = _tok("RET")
LIQ = _tok("LIQ_SCORE")
TS_MEAN = _tok("TS_MEAN")
TS_RANK = _tok("TS_RANK")
TS_MAX = _tok("TS_MAX")
SIGN = _tok("SIGN")
NEG = _tok("NEG")
ABS = _tok("ABS")

#: Momentum-following formula — positive Rank-IC on trending synthetic data.
GOOD = [RET, TS_RANK]
#: Momentum-reversal formula — negative Rank-IC on the same data.
BAD = [RET, NEG]
#: Constant output (LIQ_SCORE is 0 without liquidity/fdv columns) -> degenerate.
DEGENERATE = [LIQ, ABS]
#: 恒正感染链 (three consecutive always-positive operators) -> structure invalid.
INFECTION = [RET, TS_RANK, TS_MEAN, TS_MAX]


def _syn(
    n_dates: int = 70,
    n_symbols: int = 5,
    seed: int = 42,
    ar: float = 0.5,
    sigma: float = 0.02,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """Synthetic OHLCV panels: AR(1) per-symbol returns (momentum persistence).

    Returns ``(data_by_symbol, ret_by_symbol)`` matching ``fitness.score``'s
    contract: per-symbol DataFrames with a DatetimeIndex and open/high/low/close/
    volume columns, and per-symbol forward-return Series
    (``close[t+1] / close[t] - 1``, last bar NaN).
    """
    rng = np.random.default_rng(seed)
    dates = pd.bdate_range(start="2024-01-01", periods=n_dates)
    noise = rng.normal(0.0, sigma, (n_dates, n_symbols))
    rets = np.zeros_like(noise)
    rets[0] = noise[0]
    for t in range(1, n_dates):
        rets[t] = ar * rets[t - 1] + noise[t]
    close = 100.0 * np.cumprod(1.0 + rets, axis=0)
    open_ = close / (1.0 + rets)
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.996
    volume = 1000.0 * (1.0 + 0.5 * rng.random((n_dates, n_symbols)))

    data_by_symbol: dict[str, pd.DataFrame] = {}
    ret_by_symbol: dict[str, pd.Series] = {}
    for s in range(n_symbols):
        sym = f"S{s}"
        data_by_symbol[sym] = pd.DataFrame(
            {
                "open": open_[:, s],
                "high": high[:, s],
                "low": low[:, s],
                "close": close[:, s],
                "volume": volume[:, s],
            },
            index=dates,
        )
        closes = pd.Series(close[:, s], index=dates)
        ret_by_symbol[sym] = closes.shift(-1) / closes - 1.0
    return data_by_symbol, ret_by_symbol


def _default_panels() -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    return _syn(seed=42, ar=0.5)


# --------------------------------------------------------------------------
# Determinism.
# --------------------------------------------------------------------------
def test_deterministic_same_inputs_same_score() -> None:
    data, ret = _default_panels()
    f1 = score(GOOD, data, ret)
    f2 = score(GOOD, data, ret)
    assert f1 == f2
    assert f1.score == f2.score  # bit-identical, not just close


# --------------------------------------------------------------------------
# Rejection sentinels.
# --------------------------------------------------------------------------
def test_structure_violation_sentinel() -> None:
    data, ret = _default_panels()
    f = score(INFECTION, data, ret)
    assert isinstance(f, FormulaFitness)
    assert not f.valid
    assert f.score == float("-inf")


def test_degenerate_sentinel() -> None:
    data, ret = _default_panels()
    f = score(DEGENERATE, data, ret)
    assert not f.valid
    assert f.score == float("-inf")
    # A constant factor panel must be rejected before any backtest runs.
    factor_df, _ = build_factor_panel(DEGENERATE, data, ret)
    assert float(factor_df["factor"].std()) < 1e-8


# --------------------------------------------------------------------------
# Known good vs bad formulas.
# --------------------------------------------------------------------------
def test_good_formula_beats_bad_formula() -> None:
    data, ret = _default_panels()
    good = score(GOOD, data, ret)
    bad = score(BAD, data, ret)
    assert good.valid and bad.valid
    assert good.rank_ic > 0.1
    assert bad.rank_ic < -0.1
    assert good.score > bad.score


# --------------------------------------------------------------------------
# Penalties.
# --------------------------------------------------------------------------
def test_turnover_penalty_reduces_score() -> None:
    data, ret = _default_panels()
    no_penalty = score(GOOD, data, ret, turnover_penalty_weight=0.0)
    heavy = score(GOOD, data, ret, turnover_penalty_weight=50.0)
    # The synthetic panel's LS turnover exceeds the module threshold, so the
    # penalty term is active and must lower the composite score.
    assert no_penalty.turnover > 0.5
    assert heavy.score < no_penalty.score


def test_subperiod_consistency_penalty_fires_below_threshold() -> None:
    # ar=0.2/seed=3/n_subperiods=8 deterministically yields consistency 0.375
    # (< default min_subperiod_consistency=0.5) on the noise panel for
    # [RET, TS_MEAN] (a smoothed-momentum factor whose per-sub-period ICs flip).
    # (seed was re-calibrated 9→3 when _robust_norm went trailing-window.)
    data, ret = _syn(seed=3, ar=0.2)
    tokens = [RET, TS_MEAN]
    base = score(tokens, data, ret, n_subperiods=8, subperiod_consistency_weight=0.0)
    heavy = score(tokens, data, ret, n_subperiods=8, subperiod_consistency_weight=10.0)
    assert base.valid
    assert base.subperiod_ic_consistency < 0.5
    assert heavy.score < base.score


# --------------------------------------------------------------------------
# Panel contract + cache.
# --------------------------------------------------------------------------
def test_panel_columns_contract() -> None:
    data, ret = _default_panels()
    factor_df, ret_df = build_factor_panel(GOOD, data, ret)
    assert list(factor_df.columns) == ["trade_date", "symbol", "factor"]
    assert list(ret_df.columns) == ["trade_date", "symbol", "fut_ret_1"]
    assert not factor_df.empty
    assert factor_df["symbol"].nunique() == 5


def test_cache_hit_returns_cached_result() -> None:
    data, ret = _default_panels()
    cache: dict = {}
    f1 = score(GOOD, data, ret, cache=cache)
    f2 = score(GOOD, data, ret, cache=cache)
    assert f1 == f2
    assert len(cache) == 1
    key = canonical_hash(GOOD)
    assert key in cache
    assert cache[key] == f1


def test_cache_evicts_oldest_half_when_over_size() -> None:
    data, ret = _default_panels()
    cache: dict = {}
    # Force eviction by pre-filling the cache with dummy keys.
    for i in range(600):
        cache[f"dummy-{i}"] = i
    f = score(GOOD, data, ret, cache=cache)
    assert f.valid
    assert len(cache) <= 600  # bounded by _CACHE_SIZE after the newest insert


def test_score_does_not_mutate_inputs() -> None:
    data, ret = _default_panels()
    data_snapshot = {k: v.copy(deep=True) for k, v in data.items()}
    ret_snapshot = {k: v.copy(deep=True) for k, v in ret.items()}
    score(GOOD, data, ret)
    for sym, df in data.items():
        pd.testing.assert_frame_equal(df, data_snapshot[sym])
        pd.testing.assert_series_equal(ret[sym], ret_snapshot[sym])


@pytest.mark.parametrize("tokens", [GOOD, BAD])
def test_valid_formula_has_finite_score(tokens: list[int]) -> None:
    data, ret = _default_panels()
    f = score(tokens, data, ret)
    assert f.valid
    assert np.isfinite(f.score)
