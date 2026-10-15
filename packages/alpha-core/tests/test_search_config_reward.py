"""US-03 reward plumbing: config weights reach ``fitness.score`` through the search.

Locks the WS-A wiring added in ``genetic_search.run_search`` (US-03 Step 3):

- **Transmission** — a ``SearchConfig`` reward weighting (not just the module
  defaults) actually reaches ``score()`` during a run. Re-scoring the reported
  top formula with :func:`reward_score_kwargs` must reproduce its fitness
  bit-for-bit; if the search silently used the defaults, the re-score would
  differ (the config weight is deliberately non-default).
- **C2 source-of-truth sync** — ``SearchConfig`` reward fields mirror the
  ``fitness`` module constants (both are Selene's own calibration; a drift here
  silently changes the reward a search optimizes) and the pure reward params
  match ``score()``'s own defaults.
- **Field↔param correspondence** — the explicit map pairs each ``score()``
  keyword with its ``SearchConfig`` source field (not a blind pass-through of
  every config field).
"""
from __future__ import annotations

import dataclasses
import inspect

import numpy as np
import pandas as pd

from selene_space_alpha.vocab import FORMULA_VOCAB
import selene_space_factor_research.mining.fitness as fitness
from selene_space_factor_research.mining.fitness import score
from selene_space_factor_research.mining.genetic_search import (
    reward_score_kwargs,
    run_search,
)
from selene_space_factor_research.mining.search_config import SearchConfig

RET = list(FORMULA_VOCAB.token_names).index("RET")
TS_RANK = list(FORMULA_VOCAB.token_names).index("TS_RANK")

GOOD = [RET, TS_RANK]

#: Explicit score()-param → SearchConfig-field pairing (US-03 Step 3 spec).
SCORE_PARAM_TO_CONFIG_FIELD: dict[str, str] = {
    "w_ic": "w_ic",
    "w_sharpe": "w_sharpe",
    "sortino_weight": "w_sortino",
    "calmar_weight": "w_calmar",
    "stability_weight": "w_stability",
    "symbol_agreement_weight": "w_symbol_agreement",
    "ic_std_ref": "ic_stability_ref",
    "turnover_penalty_weight": "turnover_penalty_weight",
    "subperiod_consistency_weight": "subperiod_consistency_weight",
    "min_subperiod_consistency": "min_subperiod_consistency",
    "n_subperiods": "n_subperiods",
    "cost_stress_multipliers": "cost_stress_multipliers",
    "cost_stress_weight": "cost_stress_weight",
}


def _syn(
    n_dates: int = 40,
    n_symbols: int = 2,
    seed: int = 42,
    ar: float = 0.5,
    sigma: float = 0.02,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """Homogeneous AR(1) panel (momentum persistence) — the score() contract."""
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


def _small_config(seed: int = 21, **overrides: object) -> SearchConfig:
    return dataclasses.replace(
        SearchConfig(
            seed=seed,
            eval_budget=24,
            population_size=8,
            lambda_offspring=2,
            min_formula_len=2,
            max_formula_len=4,
            stagnation_generations=3,
            corr_prune=False,
            n_jobs=1,
        ),
        **overrides,  # type: ignore[arg-type]
    )


# --------------------------------------------------------------------------
# Transmission: the config reward weighting reaches score() during a run.
# --------------------------------------------------------------------------
def test_score_kwargs_reach_score_during_run() -> None:
    data, ret = _syn()
    # Deliberately non-default weights: w_ic=2.0 (rank_ic term doubled) and all
    # risk-ratio components zeroed. If run_search silently used score()'s module
    # defaults, re-scoring the reported top formula with these kwargs would NOT
    # reproduce its fitness.
    config = _small_config(
        seed=21,
        w_ic=2.0,
        w_sortino=0.0,
        w_calmar=0.0,
        w_stability=0.0,
        w_symbol_agreement=0.0,
    )
    result = run_search(data, ret, config, seed_formulas=[GOOD])
    top = result.top_formulas[0]
    assert top.fitness.valid
    assert top.fitness.rank_ic != 0.0  # else the weight change is unobservable
    recomputed = score(top.tokens, data, ret, **reward_score_kwargs(config))
    assert top.fitness.score == recomputed.score  # bit-identical transmission
    # Sanity: the config weighting actually differs from score()'s defaults, so
    # this assertion is not vacuous.
    defaults = score(top.tokens, data, ret)
    assert recomputed.score != defaults.score


def test_sortino_weight_zero_changes_search_score() -> None:
    # Plan US-03 criterion 3: with SearchConfig(w_sortino=0) the reported top-1
    # carries a zero Sortino contribution — re-scoring that same formula with
    # sortino_weight=1 must produce a different composite (proving the config
    # weight, not the module default, drove the search).
    data, ret = _syn()
    result = run_search(data, ret, _small_config(seed=21, w_sortino=0.0), seed_formulas=[GOOD])
    top = result.top_formulas[0]
    assert top.fitness.valid
    assert top.fitness.ls_sortino != 0.0  # else the weight toggle is unobservable
    s0 = score(top.tokens, data, ret, sortino_weight=0.0)
    s1 = score(top.tokens, data, ret, sortino_weight=1.0)
    assert top.fitness.score == s0.score  # search scored with w_sortino=0
    assert s1.score != s0.score


# --------------------------------------------------------------------------
# C2 source-of-truth sync: SearchConfig mirrors fitness module constants.
# --------------------------------------------------------------------------
def test_config_weight_fields_match_fitness_constants() -> None:
    cfg = SearchConfig()
    assert cfg.w_ic == fitness.W_IC
    assert cfg.w_sharpe == fitness.W_SHARPE
    assert cfg.w_sortino == fitness.W_SORTINO
    assert cfg.w_calmar == fitness.W_CALMAR
    assert cfg.w_stability == fitness.W_STABILITY
    assert cfg.w_symbol_agreement == fitness.W_SYMBOL_AGREEMENT
    assert cfg.ic_stability_ref == fitness.IC_STD_REF
    assert cfg.cost_stress_weight == fitness.W_COST_STRESS


def test_config_pure_params_match_score_defaults() -> None:
    cfg = SearchConfig()
    params = inspect.signature(score).parameters
    for field in (
        "turnover_penalty_weight",
        "subperiod_consistency_weight",
        "min_subperiod_consistency",
        "n_subperiods",
    ):
        assert getattr(cfg, field) == params[field].default


# --------------------------------------------------------------------------
# Field↔param correspondence: the explicit map (not a blind pass-through).
# --------------------------------------------------------------------------
def test_map_pairs_score_params_with_config_fields() -> None:
    cfg = SearchConfig()
    mapped = reward_score_kwargs(cfg)
    assert mapped == {
        score_param: getattr(cfg, config_field)
        for score_param, config_field in SCORE_PARAM_TO_CONFIG_FIELD.items()
    }


def test_map_keys_are_score_kwargs_and_fields_exist() -> None:
    score_params = set(inspect.signature(score).parameters)
    config_fields = {f.name for f in dataclasses.fields(SearchConfig)}
    for score_param, config_field in SCORE_PARAM_TO_CONFIG_FIELD.items():
        assert score_param in score_params
        assert config_field in config_fields


def test_map_covers_all_reward_fields() -> None:
    # The explicit map must cover every reward field on SearchConfig — a field
    # added to config without reaching score() would silently change nothing.
    reward_fields = {
        "w_ic",
        "w_sharpe",
        "w_sortino",
        "w_calmar",
        "w_stability",
        "w_symbol_agreement",
        "ic_stability_ref",
        "turnover_penalty_weight",
        "subperiod_consistency_weight",
        "min_subperiod_consistency",
        "n_subperiods",
        "cost_stress_multipliers",
        "cost_stress_weight",
    }
    assert set(SCORE_PARAM_TO_CONFIG_FIELD.values()) == reward_fields
    assert set(SCORE_PARAM_TO_CONFIG_FIELD) == set(reward_score_kwargs(SearchConfig()))
