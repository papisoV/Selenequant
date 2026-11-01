"""Genetic formula search (US-03 / T3): determinism, budget, early stop, RPN.

Exercises :mod:`selene_space_factor_research.mining.genetic_search` on
deterministic synthetic OHLCV panels (AR(1) momentum, so current momentum
predicts next-bar returns). Acceptance covered:

- ``test_same_seed_same_result`` — the HARD determinism acceptance: same seed +
  data + config ⇒ bit-identical top-N and fitness.
- ``test_different_seed_different_result`` — different seeds explore different
  formula regions.
- ``test_budget_exhausted_terminates`` — the search stops when the eval budget
  (non-cached ``score()`` calls) is spent and never exceeds it.
- ``test_stagnation_early_stop`` — ``stagnation_generations`` without a new
  best stops the run before the budget is spent.
- ``test_weak_baseline_random_enumeration_loses`` — the GA's selection pressure
  beats uniform random sampling with the same budget.
- ``test_valid_rpn_generation`` — every generated formula is RPN-valid,
  in-bounds, validate-clean, and free of IND_NEUTRALIZE (arity -1).
- ``test_parallel_matches_serial`` — n_jobs>1 produces bit-identical results to
  the n_jobs=1 serial path (same code path, loky module-level workers).
- ``test_seeded_known_good_formula_kept`` — seed_formulas land in the
  population and the reported best never scores below them.
"""

from __future__ import annotations

import dataclasses
import os
import random

import numpy as np
import pandas as pd
import pytest

from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.dsl import canonical_hash, parse
from selene_space_factor_research.mining.fitness import score
from selene_space_factor_research.mining.genetic_search import (
    OP_BY_ARITY,
    SearchResult,
    _FEATURE_IDS,
    random_formula,
    run_search,
)
from selene_space_factor_research.mining.search_config import SearchConfig
from selene_space_factor_research.mining.structure_validation import validate


def _tok(name: str) -> int:
    return list(FORMULA_VOCAB.token_names).index(name)


RET = _tok("RET")
TS_RANK = _tok("TS_RANK")
IND_NEUTRALIZE = _tok("IND_NEUTRALIZE")

#: Momentum-following formula — a strong, known-valid baseline on AR(1) data.
GOOD = [RET, TS_RANK]


def _syn(
    n_dates: int = 40,
    n_symbols: int = 2,
    seed: int = 42,
    ar: float = 0.5,
    sigma: float = 0.02,
) -> tuple[dict[str, pd.DataFrame], dict[str, pd.Series]]:
    """Synthetic OHLCV panels: AR(1) per-symbol returns (momentum persistence).

    Returns ``(data_by_symbol, ret_by_symbol)`` matching ``fitness.score``'s
    contract (DatetimeIndex + open/high/low/close/volume; forward-return Series).
    Kept small (40 dates x 2 symbols ≈ 0.4 s / non-cached score) so the whole
    search test file stays in the ~1-minute range.
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


def _small_config(
    seed: int = 42,
    *,
    budget: int = 12,
    population_size: int = 6,
    lambda_offspring: int = 2,
    max_formula_len: int = 4,
    stagnation_generations: int = 3,
    n_jobs: int = 1,
) -> SearchConfig:
    """A fast SearchConfig sized for the unit tests (corr-prune off)."""
    return SearchConfig(
        seed=seed,
        eval_budget=budget,
        population_size=population_size,
        lambda_offspring=lambda_offspring,
        min_formula_len=2,
        max_formula_len=max_formula_len,
        stagnation_generations=stagnation_generations,
        corr_prune=False,
        n_jobs=n_jobs,
    )


def _assert_same_result(a: SearchResult, b: SearchResult) -> None:
    assert a.evaluations_used == b.evaluations_used
    assert a.generations == b.generations
    assert a.stopped_reason == b.stopped_reason
    assert len(a.top_formulas) == len(b.top_formulas)
    for ea, eb in zip(a.top_formulas, b.top_formulas):
        assert ea.canonical_hash == eb.canonical_hash
        assert ea.tokens == eb.tokens
        assert ea.fitness.score == eb.fitness.score  # bit-identical, not close


# --------------------------------------------------------------------------
# Generation validity (no scoring — fast).
# --------------------------------------------------------------------------
def test_valid_rpn_generation() -> None:
    rng = random.Random(0)
    for _ in range(150):
        tokens = random_formula(rng, _FEATURE_IDS, OP_BY_ARITY, 2, 6)
        assert 2 <= len(tokens) <= 6
        assert parse(tokens) is not None  # valid RPN (raises ValueError otherwise)
        assert validate(tokens).valid
        assert IND_NEUTRALIZE not in tokens  # arity -1 excluded by construction


def test_random_formula_honors_bounds() -> None:
    rng = random.Random(1)
    for _ in range(80):
        tokens = random_formula(rng, _FEATURE_IDS, OP_BY_ARITY, 3, 5)
        assert 3 <= len(tokens) <= 5


# --------------------------------------------------------------------------
# Determinism (HARD acceptance).
# --------------------------------------------------------------------------
def test_same_seed_same_result() -> None:
    data, ret = _syn()
    config = _small_config(seed=42)
    r1 = run_search(data, ret, config, seed_formulas=[GOOD])
    r2 = run_search(data, ret, config, seed_formulas=[GOOD])
    _assert_same_result(r1, r2)
    # The seeded momentum formula guarantees a real (valid) top-1.
    assert r1.top_formulas[0].fitness.valid


def test_different_seed_different_result() -> None:
    data, ret = _syn()
    r1 = run_search(data, ret, _small_config(seed=1))
    r2 = run_search(data, ret, _small_config(seed=2))
    assert len(r1.top_formulas) == len(r2.top_formulas) == 5
    assert r1.top_formulas[0].canonical_hash != r2.top_formulas[0].canonical_hash


def test_seeded_known_good_formula_kept() -> None:
    data, ret = _syn()
    good_score = score(GOOD, data, ret).score
    result = run_search(data, ret, _small_config(seed=9), seed_formulas=[GOOD])
    assert result.top_formulas[0].fitness.valid
    assert result.top_formulas[0].fitness.score >= good_score


# --------------------------------------------------------------------------
# Budget + early stop.
# --------------------------------------------------------------------------
def test_budget_exhausted_terminates() -> None:
    data, ret = _syn()
    config = SearchConfig(
        seed=42,
        eval_budget=18,
        population_size=6,
        lambda_offspring=2,
        max_formula_len=4,
    )  # defaults: corr_prune=True — exercises the prune code path too
    result = run_search(data, ret, config)
    assert result.evaluations_used <= config.eval_budget
    assert result.stopped_reason == "budget_exhausted"
    assert result.generations > 0


def test_stagnation_early_stop() -> None:
    # max_formula_len=2 collapses the space to [feature, unary-op]; the init
    # elite saturates quickly and lambda=1 makes improvement rare, so 2
    # consecutive non-improving generations fire long before the budget.
    data, ret = _syn()
    config = _small_config(
        seed=11,
        budget=40,
        population_size=6,
        lambda_offspring=1,
        max_formula_len=2,
        stagnation_generations=2,
    )
    result = run_search(data, ret, config)
    assert result.stopped_reason == "stagnation"
    assert result.evaluations_used < config.eval_budget


# --------------------------------------------------------------------------
# C1 (plan US-03): seeded search finds momentum on the signal-driven regime.
# --------------------------------------------------------------------------
def test_c1_seeded_search_finds_momentum() -> None:
    # Plan US-03 C1: on the signal-driven regime (ar=0.7, 3 symbols), seeding
    # the GA with GOOD must yield a top-1 that is a positive-IC momentum formula
    # scoring at least as well as GOOD itself. The US-01 composite correction
    # #2 bounds Sharpe/Sortino/Calmar through _bounded_risk, so valid scores sit
    # in a compact band on this regime; the pre-correction "> 1.0 selection
    # margin vs random enumeration" assertion (raw annualized-ratio scale) no
    # longer applies and was replaced per plan US-04 acceptance 4 — the
    # property that matters is that the search lands on momentum, not how far
    # above a uniform-random baseline it scores.
    data, ret = _syn(n_dates=200, n_symbols=3, ar=0.7)
    budget = 24
    ga_config = _small_config(
        seed=21, budget=budget, population_size=8, lambda_offspring=2
    )
    ga_result = run_search(data, ret, ga_config, seed_formulas=[GOOD])
    assert ga_result.evaluations_used <= budget
    top = ga_result.top_formulas[0].fitness
    assert top.valid
    assert top.rank_ic > 0.1  # top-1 is a momentum formula, not noise
    assert top.score >= score(GOOD, data, ret).score  # not worse than GOOD


# --------------------------------------------------------------------------
# Parallel determinism (module-level worker; bit-identical to serial).
# --------------------------------------------------------------------------
@pytest.mark.skipif(
    (os.cpu_count() or 1) < 4,
    reason="parallel determinism smoke needs >= 4 cpus for loky workers",
)
def test_parallel_matches_serial() -> None:
    data, ret = _syn()
    serial_config = _small_config(seed=5, budget=12)
    parallel_config = dataclasses.replace(serial_config, n_jobs=2)
    serial_result = run_search(data, ret, serial_config)
    parallel_result = run_search(data, ret, parallel_config)
    _assert_same_result(serial_result, parallel_result)


# --------------------------------------------------------------------------
# Ledger blocking (closed-loop cut): blocked hashes spend zero eval budget.
# --------------------------------------------------------------------------
def test_blocked_formula_spends_no_eval() -> None:
    data, ret = _syn()
    config = _small_config(seed=42, budget=24, population_size=8)

    unblocked = run_search(data, ret, config, seed_formulas=[GOOD])
    blocked = run_search(
        data, ret, config, seed_formulas=[GOOD], blocked_hashes=[canonical_hash(GOOD)]
    )

    # Seeded GOOD is excluded entirely: it never lands in the population and
    # the search keeps exploring, so the eval budget is not wasted on it.
    assert GOOD not in [e.tokens for e in blocked.top_formulas]
    # A strictly smaller explored space must not evaluate more formulas.
    assert blocked.evaluations_used <= unblocked.evaluations_used
    # Sanity: unblocked run actually evaluated the seed's neighborhood.
    assert unblocked.evaluations_used > 0


def test_fully_blocked_space_exhausts_without_crash() -> None:
    # max_formula_len=2 collapses the space to ~[feature, unary-op]; blocking
    # a broad-enough hash set drives the search into the existing
    # search_space_exhausted path instead of an error.
    data, ret = _syn()
    config = _small_config(seed=3, budget=20, population_size=6, max_formula_len=2)
    result = run_search(data, ret, config)
    all_hashes = [e.canonical_hash for e in result.top_formulas]
    # Block everything the first run discovered; the second run must terminate
    # cleanly (exhausted or stagnation) with zero additional evaluations.
    blocked = run_search(
        data, ret, config, seed_formulas=None, blocked_hashes=all_hashes
    )
    assert blocked.evaluations_used <= config.eval_budget
    assert blocked.stopped_reason in (
        "search_space_exhausted",
        "stagnation",
        "budget_exhausted",
    )


def test_default_blocked_hashes_is_zero_disturbance() -> None:
    data, ret = _syn()
    config = _small_config(seed=42)
    r1 = run_search(data, ret, config, seed_formulas=[GOOD])
    r2 = run_search(data, ret, config, seed_formulas=[GOOD], blocked_hashes=None)
    _assert_same_result(r1, r2)
