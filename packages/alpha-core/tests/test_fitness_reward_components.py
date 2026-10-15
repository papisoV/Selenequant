"""Multi-component reward (WS-A Step 1): Sortino/Calmar/stability/agreement/cost-stress.

Locks the new composite components added to :mod:`fitness` for the RL reward
alignment: the four new :class:`FormulaFitness` fields, the weight-driven
composite deltas (stability bonus, cross-symbol agreement, cost-stress
penalty), and the ``v1.2``-reproduction identity when the new weights are
zeroed. The composite applies ``_bounded_risk`` (a monotone odd map onto
(-1, 1)) to the annualized risk-adjusted ratios before weighting so no single
lucky Sortino/Calmar tail can swamp the Rank-IC term. The 12 existing
behavior-lock cases in ``test_fitness_scoring.py`` are left untouched.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.fitness import _bounded_risk, score

RET = list(FORMULA_VOCAB.token_names).index("RET")
LIQ = list(FORMULA_VOCAB.token_names).index("LIQ_SCORE")
TS_RANK = list(FORMULA_VOCAB.token_names).index("TS_RANK")
NEG = list(FORMULA_VOCAB.token_names).index("NEG")
ABS = list(FORMULA_VOCAB.token_names).index("ABS")

GOOD = [RET, TS_RANK]
BAD = [RET, NEG]
DEGENERATE = [LIQ, ABS]


def _syn(
    n_dates: int = 70,
    n_symbols: int = 5,
    seed: int = 42,
    ar: float = 0.5,
    sigma: float = 0.02,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """Homogeneous AR(1) panel — every symbol is momentum-persistent."""
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


def _syn_mixed() -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """3 symbols: two momentum (ar=+0.5) + one mean-reverting (ar=-0.5).

    ``GOOD`` (momentum) then has partial cross-symbol agreement: its
    time-series IC agrees with the overall positive sign on only 2/3 of the
    symbols (empirically 0.667 at seed=7).
    """
    rng = np.random.default_rng(7)
    dates = pd.bdate_range(start="2024-01-01", periods=70)
    ars = [0.5, 0.5, -0.5]
    data_by_symbol: dict[str, pd.DataFrame] = {}
    ret_by_symbol: dict[str, pd.Series] = {}
    for s, ar in enumerate(ars):
        noise = rng.normal(0.0, 0.02, 70)
        rets = np.zeros_like(noise)
        rets[0] = noise[0]
        for t in range(1, 70):
            rets[t] = ar * rets[t - 1] + noise[t]
        close = 100.0 * np.cumprod(1.0 + rets)
        open_ = close / (1.0 + rets)
        high = np.maximum(open_, close) * 1.004
        low = np.minimum(open_, close) * 0.996
        volume = 1000.0 * (1.0 + 0.5 * rng.random(70))
        sym = f"S{s}"
        data_by_symbol[sym] = pd.DataFrame(
            {
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
            },
            index=dates,
        )
        closes = pd.Series(close, index=dates)
        ret_by_symbol[sym] = closes.shift(-1) / closes - 1.0
    return data_by_symbol, ret_by_symbol


def _default_panels() -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    return _syn(seed=42, ar=0.5)


# --------------------------------------------------------------------------
# Composite identity: zeroing the new weights reproduces the v1.2 two-component
# composite bit-for-bit.
# --------------------------------------------------------------------------
def test_zeroed_new_weights_reproduce_two_component() -> None:
    data, ret = _default_panels()
    full = score(GOOD, data, ret)
    two = score(
        GOOD,
        data,
        ret,
        w_ic=1.0,
        w_sharpe=1.0,
        sortino_weight=0.0,
        calmar_weight=0.0,
        stability_weight=0.0,
        symbol_agreement_weight=0.0,
        cost_stress_multipliers=(),
        cost_stress_weight=0.0,
    )
    assert full.score == pytest.approx(
        two.score
        + (
            _bounded_risk(full.ls_sortino) * 0.5
            + _bounded_risk(full.ls_calmar) * 0.5
            + full.stability_bonus * 0.25
            + full.symbol_agreement * 0.25
        )
    )


def test_new_components_deterministic() -> None:
    data, ret = _default_panels()
    f1 = score(GOOD, data, ret)
    f2 = score(GOOD, data, ret)
    assert f1 == f2
    assert f1.ls_sortino == f2.ls_sortino
    assert f1.ls_calmar == f2.ls_calmar
    assert f1.stability_bonus == f2.stability_bonus
    assert f1.symbol_agreement == f2.symbol_agreement


# --------------------------------------------------------------------------
# New fields: finite for GOOD, zeroed on the sentinel.
# --------------------------------------------------------------------------
def test_new_components_finite_for_good() -> None:
    data, ret = _default_panels()
    f = score(GOOD, data, ret)
    assert f.valid
    assert math.isfinite(f.ls_sortino)
    assert math.isfinite(f.ls_calmar)
    assert 0.0 <= f.stability_bonus <= 1.0
    assert 0.0 <= f.symbol_agreement <= 1.0


def test_degenerate_sentinel_new_fields_zero() -> None:
    data, ret = _default_panels()
    f = score(DEGENERATE, data, ret)
    assert not f.valid
    assert f.score == float("-inf")
    assert f.ls_sortino == 0.0
    assert f.ls_calmar == 0.0
    assert f.symbol_agreement == 0.0
    assert f.stability_bonus == 0.0


# --------------------------------------------------------------------------
# Stability bonus: monotone in weight, exact delta = weight * bonus.
# --------------------------------------------------------------------------
def test_stability_weight_monotonic() -> None:
    data, ret = _default_panels()
    w0 = score(GOOD, data, ret, stability_weight=0.0)
    w1 = score(GOOD, data, ret, stability_weight=1.0)
    # GOOD on the clean AR(1) panel has ic_stability < IC_STD_REF -> bonus > 0.
    assert w0.stability_bonus > 0.0
    assert w1.stability_bonus == w0.stability_bonus
    assert w1.score == pytest.approx(w0.score + w0.stability_bonus)


# --------------------------------------------------------------------------
# Cross-symbol agreement: partial agreement on the mixed panel, weight-scaled.
# --------------------------------------------------------------------------
def test_symbol_agreement_weight_prefers_agreement() -> None:
    data, ret = _syn_mixed()
    f0 = score(GOOD, data, ret, symbol_agreement_weight=0.0)
    assert f0.valid
    # 2/3 of the symbols agree with the overall sign -> partial agreement.
    assert 0.0 < f0.symbol_agreement < 1.0
    f10 = score(GOOD, data, ret, symbol_agreement_weight=10.0)
    assert f10.score == pytest.approx(f0.score + 10.0 * f0.symbol_agreement)
    assert f10.score > f0.score


# --------------------------------------------------------------------------
# Cost stress: default disabled (zero delta), on = penalty reduces the score.
# --------------------------------------------------------------------------
def test_cost_stress_default_zero_delta() -> None:
    data, ret = _default_panels()
    base = score(GOOD, data, ret)
    weighted = score(GOOD, data, ret, cost_stress_weight=5.0)
    assert weighted == base
    assert weighted.score == base.score


def test_cost_stress_penalty_reduces_score() -> None:
    data, ret = _default_panels()
    base = score(GOOD, data, ret)
    stressed = score(GOOD, data, ret, cost_stress_multipliers=(3.0,))
    assert stressed.valid
    # Higher fee/slippage cannot raise net Sharpe -> penalty >= 0 -> no gain.
    assert stressed.score < base.score
    assert base.score - stressed.score > 0.0


# --------------------------------------------------------------------------
# Selection: GOOD still beats BAD under the default multi-component weights.
# --------------------------------------------------------------------------
def test_good_beats_bad_under_default_weights() -> None:
    data, ret = _default_panels()
    good = score(GOOD, data, ret)
    bad = score(BAD, data, ret)
    assert good.valid and bad.valid
    assert good.rank_ic > 0.1
    assert bad.rank_ic < -0.1
    assert good.score > bad.score
