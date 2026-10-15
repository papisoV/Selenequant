# Cleanroom implementation: design intent derived from prior art; independent expression.
"""Search-config dataclass for the deterministic genetic formula search (T3).

T3 of the absorption plan: the configuration object consumed by
:mod:`selene_space_factor_research.mining.genetic_search`. It is deliberately
torch-free (``test_no_torch_in_search_import.py`` asserts the whole search
chain imports no torch) and frozen so a run is fully described by one
immutable value — same seed + same data + same config ⇒ bit-identical
top-N formulas and fitness (the hard determinism acceptance).

Default ``eval_budget`` follows the US-02 perf calibration (docs
``docs/absorption-notes.md``) updated for the WS-A multi-component reward: a
single-candidate ``score()`` now costs ~0.48-0.57 s per symbol (the base
~0.44 s plus the ``_symbol_agreement`` per-symbol IC overhead, measured
+10-30% — not near-zero), so 5000 evaluations ≈ 40-47 min single-threaded.
``smoke_config()`` drops the budget to 1500 for quick runs.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["SearchConfig", "smoke_config"]


@dataclass(frozen=True)
class SearchConfig:
    """Deterministic configuration for :func:`genetic_search.run_search`.

    Frozen: a run is fully described by this value (plus the data dicts), so
    every field below participates in reproducibility. Nothing here is mutated
    at runtime. The multi-objective reward weights are Selene's OWN calibration
    — deliberately not derived from any upstream config values.
    """

    #: RNG seed — every random decision (init, tournament, crossover, mutation)
    #: draws from :class:`random.Random` seeded with this.
    seed: int = 42
    #: Maximum number of *non-cached* ``fitness.score`` calls (per US-02 perf
    #: calibration incl. the WS-A multi-component overhead: 5000 ≈ 40-47 min
    #: single-threaded). Cache hits are free.
    eval_budget: int = 5000
    #: Elite pool size ``mu`` (steady-state mu+lambda: the population never
    #: exceeds this after a generation's merge).
    population_size: int = 40
    #: Children produced per generation ``lambda``.
    lambda_offspring: int = 20
    #: Tournament-selection size (pick the best of ``k`` random samples).
    tournament_size: int = 3
    #: Probability that an offspring slot is produced by subtree crossover
    #: (otherwise it clones a single tournament winner).
    crossover_rate: float = 0.8
    #: Per-offspring probability of a validity-preserving mutation.
    mutation_rate: float = 0.3
    #: Minimum token length of any generated / kept formula.
    min_formula_len: int = 2
    #: Maximum token length of any generated / kept formula.
    max_formula_len: int = 8
    #: Early-stop generations without a new global-best score (stagnation).
    stagnation_generations: int = 10
    #: |spearman corr of factor panels| at/above which a pair is a pruning
    #: candidate (conservative diversity filter).
    corr_prune_threshold: float = 0.9
    #: Required score gap (``better.score - worse.score``) above which the
    #: worse correlated member is dropped; within the margin both are kept.
    corr_prune_margin: float = 1e-4
    #: Worker count. 0/1 = serial in-process path (exactly the same code path
    #: as parallel, deterministic); >1 = joblib loky with a module-level worker.
    n_jobs: int = 1
    #: Number of top formulas returned in :class:`SearchResult.top_formulas`.
    top_n: int = 5
    #: Master switch for the factor-panel correlation pruning step.
    corr_prune: bool = True
    #: Run the correlation prune every N generations (bounds its panel-build cost).
    corr_prune_interval: int = 5
    #: Max new factor-panel builds per prune call (extra builds beyond this are
    #: skipped — the filter stays conservative, "when in doubt keep both").
    corr_prune_max_builds: int = 8

    # --- Multi-objective reward weights (WS-A) ---
    # These mirror the module constants in :mod:`...mining.fitness`
    # (W_IC / W_SHARPE / W_SORTINO / W_CALMAR / W_STABILITY /
    # W_SYMBOL_AGREEMENT / W_COST_STRESS / IC_STD_REF). Selene's own
    # calibration — not the upstream reference's 0.35 Sortino / 0.20 Calmar.
    # ``test_search_config_reward`` (C2) asserts the two sources stay in sync.
    w_ic: float = 1.0
    w_sharpe: float = 1.0
    w_sortino: float = 0.5
    w_calmar: float = 0.5
    w_stability: float = 0.25
    w_symbol_agreement: float = 0.25
    ic_stability_ref: float = 0.5
    turnover_penalty_weight: float = 1.0
    subperiod_consistency_weight: float = 1.0
    min_subperiod_consistency: float = 0.5
    n_subperiods: int = 4
    cost_stress_multipliers: tuple[float, ...] = ()
    cost_stress_weight: float = 0.5


def smoke_config() -> SearchConfig:
    """A SearchConfig sized for quick smoke runs (US-02 plan §10: 1000–2000).

    The structural knobs (mu/lambda/stagnation/… ) match :class:`SearchConfig`
    defaults; only the eval budget is lowered to 1500 so a single-symbol run
    finishes in minutes per the perf calibration (~0.48-0.57 s / candidate).
    """
    return SearchConfig(eval_budget=1500)
