# Cleanroom implementation: design intent derived from prior art; independent expression.
"""Deterministic steady-state genetic search over the formula DSL (T3, no torch).

Task 3 of the absorption plan: the search engine that turns the
formula space into a ranked top-N of :class:`FormulaFitness`-scored token
sequences for the T4 validation protocol. It consumes ``fitness.score``
(US-02) and the DSL utilities (US-01) unchanged; it never re-implements
scoring or validation.

Design:

- **Formula generation** — a valid RPN is a rooted ordered tree whose internal
  nodes are operators (arity ≥ 1) and leaves are features (arity 0). For any
  tree of ``n`` nodes the operator arities sum to ``n - 1``; the generator
  draws a random arity multiset summing to that total, builds the tree in
  pre-order, and emits its post-order token list — valid by construction.
  ``IND_NEUTRALIZE`` (arity -1, pops the whole stack) is **excluded** from the
  generated space: it is not representable as a single node of the tree model,
  and the pandas VM's ``execute`` treats a negative arity as zero popped args
  (which raises and returns ``None``), so no generated formula can use it.
  Every emitted formula is additionally filtered through
  ``structure_validation.validate`` so the 恒正感染链 advisory never leaks into
  the population.

- **Steady-state mu+lambda** — the population is capped at ``mu``; each
  generation ``lambda`` offspring are produced by tournament selection,
  subtree crossover (swapping one complete subtree between two parents — always
  RPN-valid), and a validity-preserving mutation (point token replace, wrap a
  node in a unary op, unwrap a unary op, or replace a subtree with a fresh
  random one). Duplicates are dropped by ``canonical_hash``. An optional
  conservative correlation prune drops a candidate only when it is ≥
  ``corr_prune_threshold``-correlated (Spearman over the factor panels) with an
  already-kept member whose score beats it by more than ``corr_prune_margin``.

- **Determinism is a hard acceptance** — every random decision draws from
  ``random.Random(config.seed)`` (no module-global randomness, no ``numpy``
  RNG), the population/result ordering is pinned by score + canonical-hash
  tie-breaks, and the shared ``fitness.score`` cache is updated in a fixed
  order. Same seed + same data + same config ⇒ bit-identical top-N and fitness.

- **Parallel eval** — ``n_jobs > 1`` evaluates each offspring batch with
  joblib-loky using a module-level worker (win32 spawn requirement). The
  parent dumps ``(data_by_symbol, ret_by_symbol)`` once to a temp file; workers
  mmap-read it once per process and call ``fitness.score`` (which never mutates
  its inputs). Results are collected in submission order, so the outcome is
  identical to the ``n_jobs = 1`` serial path (which is the *same code path*).

- **Budget** — ``eval_budget`` counts non-cached ``score()`` calls; a
  canonical-hash cache hit is free. The search stops on budget exhaustion or
  on ``stagnation_generations`` generations without a new global best.
"""

from __future__ import annotations

import os
import random
import tempfile
from dataclasses import dataclass
from typing import Any, Callable
from collections.abc import Collection

import joblib
import numpy as np
import pandas as pd
from joblib import Parallel, delayed
from scipy import stats

from selene_space_alpha.ops.registry import OPERATOR_DEFS
from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.dsl import Node, canonical_hash, parse, render
from selene_space_factor_research.mining.fitness import (
    FormulaFitness,
    build_factor_panel,
    score,
)
from selene_space_factor_research.mining.search_config import SearchConfig
from selene_space_factor_research.mining.structure_validation import validate

__all__ = [
    "FormulaEntry",
    "SearchResult",
    "random_formula",
    "reward_score_kwargs",
    "run_search",
]

# ---------------------------------------------------------------------------
# Frozen vocab views (import time; never mutate).
# ---------------------------------------------------------------------------
_TOKEN_NAMES: tuple[str, ...] = FORMULA_VOCAB.token_names
_FEATURE_IDS: list[int] = list(range(FORMULA_VOCAB.feature_count))
_OPERATOR_OFFSET: int = FORMULA_VOCAB.operator_offset
_ARITY_MAP: dict[str, int] = {name: arity for name, arity in OPERATOR_DEFS}
_TOKEN_ID: dict[str, int] = {name: i for i, name in enumerate(_TOKEN_NAMES)}
#: Max factor-panel entries in the correlation-prune panel cache.
_PANEL_CACHE_SIZE: int = 256


def _build_op_maps() -> tuple[dict[int, list[int]], list[int]]:
    """Operator token ids grouped by arity (IND_NEUTRALIZE excluded)."""
    op_by_arity: dict[int, list[int]] = {}
    unary: list[int] = []
    for i, name in enumerate(FORMULA_VOCAB.operator_names):
        token_id = _OPERATOR_OFFSET + i
        arity = _ARITY_MAP[name]
        if arity < 0:
            continue  # IND_NEUTRALIZE: see module docstring
        op_by_arity.setdefault(arity, []).append(token_id)
        if arity == 1:
            unary.append(token_id)
    return op_by_arity, unary


OP_BY_ARITY, UNARY_OP_IDS = _build_op_maps()


# ---------------------------------------------------------------------------
# Result containers.
# ---------------------------------------------------------------------------
@dataclass
class FormulaEntry:
    """One scored formula in the search output."""

    tokens: list[int]
    readable: str
    canonical_hash: str
    fitness: FormulaFitness


@dataclass
class SearchResult:
    """Output of :func:`run_search` — the T4 validation protocol's input."""

    top_formulas: list[FormulaEntry]
    evaluations_used: int
    generations: int
    stopped_reason: str
    seed: int


@dataclass
class _EvalResult:
    """Worker return value — structured, order-stable (never a positional tuple)."""

    tokens: tuple[int, ...]
    fitness: FormulaFitness


# ---------------------------------------------------------------------------
# Random valid-RPN generation.
# ---------------------------------------------------------------------------
def _random_arities(rng: random.Random, total: int, max_arity: int) -> list[int]:
    """A random multiset of arities in ``[1, max_arity]`` summing to ``total``."""
    arities: list[int] = []
    remaining = total
    while remaining > 0:
        a = rng.randint(1, min(max_arity, remaining))
        arities.append(a)
        remaining -= a
    return arities


def _make_tree(
    rng: random.Random,
    arities: list[int],
    i: int,
    feature_ids: list[int],
    op_by_arity: dict[int, list[int]],
) -> tuple[list[int], int]:
    """Build a random tree from ``arities``; return (post-order tokens, next index)."""
    if i >= len(arities):
        return [rng.choice(feature_ids)], i
    a = arities[i]
    i += 1
    op_token = rng.choice(op_by_arity[a])
    tokens: list[int] = []
    for _ in range(a):
        child_tokens, i = _make_tree(rng, arities, i, feature_ids, op_by_arity)
        tokens.extend(child_tokens)
    tokens.append(op_token)
    return tokens, i


def _random_rpn_of_exact_size(
    rng: random.Random,
    n: int,
    feature_ids: list[int],
    op_by_arity: dict[int, list[int]],
) -> list[int]:
    """A valid RPN token list of exactly ``n`` tokens (by construction)."""
    arities = _random_arities(rng, n - 1, max_arity=max(op_by_arity))
    tokens, _ = _make_tree(rng, arities, 0, feature_ids, op_by_arity)
    return tokens


def random_formula(
    rng: random.Random,
    feature_ids: list[int],
    op_by_arity: dict[int, list[int]],
    min_len: int,
    max_len: int,
    max_tries: int = 50,
) -> list[int]:
    """Return a random valid RPN token list of length in ``[min_len, max_len]``.

    RPN validity holds by construction; the 恒正感染链 advisory filter
    (:func:`structure_validation.validate`) is applied by rejection sampling.
    The deterministic fallback ``[first feature] + [NEG]*(min_len-1)`` never
    forms a positive-infection chain (``NEG`` breaks it) and guarantees
    termination.
    """
    min_len = max(1, int(min_len))
    max_len = max(min_len, int(max_len))
    for _ in range(max_tries):
        n = rng.randint(min_len, max_len)
        tokens = _random_rpn_of_exact_size(rng, n, feature_ids, op_by_arity)
        if validate(tokens).valid:
            return tokens
    neg_id = op_by_arity[1][0]  # NEG — a positive-chain breaker
    return [feature_ids[0]] + [neg_id] * (min_len - 1)


# ---------------------------------------------------------------------------
# Tree helpers (immutable :class:`Node` rebuilds — no in-place mutation).
# ---------------------------------------------------------------------------
def _node_count(node: Node) -> int:
    return 1 + sum(_node_count(c) for c in node.children)


def _collect_nodes(node: Node) -> list[Node]:
    nodes = [node]
    for c in node.children:
        nodes.extend(_collect_nodes(c))
    return nodes


def _node_to_tokens(node: Node) -> list[int]:
    tokens: list[int] = []
    for c in node.children:
        tokens.extend(_node_to_tokens(c))
    tokens.append(_TOKEN_ID[node.name])
    return tokens


def _replace_node(node: Node, old: Node, new: Node) -> Node:
    """Return a copy of the tree with the subtree at ``old`` (identity) replaced."""
    if node is old:
        return new
    return Node(node.name, tuple(_replace_node(c, old, new) for c in node.children))


# ---------------------------------------------------------------------------
# GA operators.
# ---------------------------------------------------------------------------
def _better(a: FormulaEntry, b: FormulaEntry) -> bool:
    """True when ``a`` ranks above ``b`` (valid first, then higher score)."""
    a_key = (0 if a.fitness.valid else 1, -a.fitness.score)
    b_key = (0 if b.fitness.valid else 1, -b.fitness.score)
    return a_key < b_key


def _sort_population(population: list[FormulaEntry]) -> list[FormulaEntry]:
    """Score-descending with a canonical-hash tie-break (deterministic order)."""
    return sorted(
        population,
        key=lambda e: (0 if e.fitness.valid else 1, -e.fitness.score, e.canonical_hash),
    )


def _tournament(
    rng: random.Random, population: list[FormulaEntry], k: int
) -> FormulaEntry:
    """Best of ``k`` uniform samples (with replacement)."""
    best: FormulaEntry | None = None
    for _ in range(k):
        cand = rng.choice(population)
        if best is None or _better(cand, best):
            best = cand
    assert best is not None  # k >= 1 always yields a sample
    return best


def _subtree_crossover(
    rng: random.Random,
    tokens_a: list[int],
    tokens_b: list[int],
    min_len: int,
    max_len: int,
) -> list[list[int]]:
    """Swap one random subtree between two parents; returns 0–2 valid children."""
    node_a = parse(tokens_a)
    node_b = parse(tokens_b)
    sub_a = rng.choice(_collect_nodes(node_a))
    sub_b = rng.choice(_collect_nodes(node_b))
    size_a = _node_count(sub_a)
    size_b = _node_count(sub_b)
    children: list[list[int]] = []
    for child_node, child_size in (
        (_replace_node(node_a, sub_a, sub_b), _node_count(node_a) - size_a + size_b),
        (_replace_node(node_b, sub_b, sub_a), _node_count(node_b) - size_b + size_a),
    ):
        if min_len <= child_size <= max_len:
            tokens = _node_to_tokens(child_node)
            if validate(tokens).valid:
                children.append(tokens)
    return children


def _point_mutate(rng: random.Random, tokens: list[int]) -> list[int]:
    """Replace one token with a same-class token (feature→feature, op→same arity)."""
    node = parse(tokens)
    target = rng.choice(_collect_nodes(node))
    if not target.children:  # feature leaf
        return _node_to_tokens(
            _replace_node(node, target, Node(_TOKEN_NAMES[rng.choice(_FEATURE_IDS)]))
        )
    arity = _ARITY_MAP[target.name]
    same_arity_ids = OP_BY_ARITY.get(arity, [_TOKEN_ID[target.name]])
    return _node_to_tokens(
        _replace_node(
            node,
            target,
            Node(_TOKEN_NAMES[rng.choice(same_arity_ids)], target.children),
        )
    )


def _wrap_unary(
    rng: random.Random, tokens: list[int], max_len: int
) -> list[int] | None:
    """Insert: wrap a random node in a random unary operator (length + 1)."""
    if len(tokens) >= max_len:
        return None
    node = parse(tokens)
    target = rng.choice(_collect_nodes(node))
    op_name = _TOKEN_NAMES[rng.choice(UNARY_OP_IDS)]
    return _node_to_tokens(_replace_node(node, target, Node(op_name, (target,))))


def _unwrap_unary(
    rng: random.Random, tokens: list[int], min_len: int
) -> list[int] | None:
    """Delete: replace a random unary-op node with its child (length − 1)."""
    if len(tokens) <= min_len:
        return None
    node = parse(tokens)
    unary_nodes = [n for n in _collect_nodes(node) if len(n.children) == 1]
    if not unary_nodes:
        return None
    target = rng.choice(unary_nodes)
    return _node_to_tokens(_replace_node(node, target, target.children[0]))


def _subtree_replace(
    rng: random.Random,
    tokens: list[int],
    min_len: int,
    max_len: int,
) -> list[int] | None:
    """Replace a random subtree with a fresh random one (length may change)."""
    node = parse(tokens)
    nodes = _collect_nodes(node)
    target = rng.choice(nodes)
    old_size = _node_count(target)
    for _ in range(10):
        new_size = rng.randint(1, max_len)
        if not (min_len <= _node_count(node) - old_size + new_size <= max_len):
            continue
        sub_node = parse(
            _random_rpn_of_exact_size(rng, new_size, _FEATURE_IDS, OP_BY_ARITY)
        )
        return _node_to_tokens(_replace_node(node, target, sub_node))
    return None


def _mutate(
    rng: random.Random, tokens: list[int], min_len: int, max_len: int
) -> list[int]:
    """One validity-preserving mutation; original tokens as a last resort."""
    for _ in range(20):
        kind = rng.random()
        candidate: list[int] | None
        if kind < 0.30:
            candidate = _point_mutate(rng, tokens)
        elif kind < 0.55:
            candidate = _wrap_unary(rng, tokens, max_len)
        elif kind < 0.75:
            candidate = _unwrap_unary(rng, tokens, min_len)
        else:
            candidate = _subtree_replace(rng, tokens, min_len, max_len)
        if (
            candidate is not None
            and min_len <= len(candidate) <= max_len
            and validate(candidate).valid
        ):
            return candidate
    return list(tokens)


def _generate_offspring(
    rng: random.Random,
    population: list[FormulaEntry],
    lambda_offspring: int,
    tournament_size: int,
    crossover_rate: float,
    mutation_rate: float,
    min_len: int,
    max_len: int,
    seen_hashes: set[str],
) -> list[list[int]]:
    """Produce up to ``lambda_offspring`` new (never-evaluated) formulas."""
    offspring: list[list[int]] = []
    max_attempts = lambda_offspring * 20 + 10
    attempts = 0
    while len(offspring) < lambda_offspring and attempts < max_attempts:
        attempts += 1
        p1 = _tournament(rng, population, tournament_size)
        p2 = _tournament(rng, population, tournament_size)
        if rng.random() < crossover_rate:
            children = _subtree_crossover(rng, p1.tokens, p2.tokens, min_len, max_len)
        else:
            children = [list(p1.tokens)]
        for child in children:
            if rng.random() < mutation_rate:
                child = _mutate(rng, child, min_len, max_len)
            h = _safe_hash(child)
            if h is not None and h not in seen_hashes and child not in offspring:
                offspring.append(child)
                if len(offspring) >= lambda_offspring:
                    break
    return offspring


# ---------------------------------------------------------------------------
# Evaluation with shared cache + budget accounting.
# ---------------------------------------------------------------------------
def _safe_hash(tokens: list[int]) -> str | None:
    try:
        return canonical_hash(tokens)
    except ValueError:
        return None


def _make_entry(tokens: list[int], fitness: FormulaFitness) -> FormulaEntry:
    # Callers only pass formulas whose hash survived _evaluate_batch's
    # ``key is not None`` filter (unparseable tokens never reach results).
    entry_hash = _safe_hash(tokens)
    assert entry_hash is not None, "unparseable tokens reached _make_entry"
    return FormulaEntry(
        tokens=list(tokens),
        readable=render(tokens),
        canonical_hash=entry_hash,
        fitness=fitness,
    )


def _evict_cache(cache: dict) -> None:
    """Bound the shared fitness cache (mirrors ``fitness._CACHE_SIZE`` policy)."""
    from selene_space_factor_research.mining.fitness import _CACHE_SIZE

    if len(cache) > _CACHE_SIZE:
        keys = list(cache.keys())
        for k in keys[: len(keys) // 2]:
            del cache[k]


def _evaluate_batch(
    tokens_list: list[list[int]],
    data_by_symbol: dict[str, pd.DataFrame],
    ret_by_symbol: dict[str, pd.DataFrame | pd.Series],
    cache: dict,
    n_jobs: int,
    data_path: str | None,
    score_kwargs: dict[str, Any],
    budget_remaining: int,
) -> tuple[list[tuple[int, list[int], FormulaFitness]], int]:
    """Score a batch, honoring the shared cache and eval budget.

    Returns ``(results, evals_used)`` where ``results`` is a list of
    ``(input_index, tokens, fitness)`` in input order for every formula scored
    within the budget (including cache hits, which cost no budget), and
    ``evals_used`` is the number of non-cached ``score()`` calls consumed.
    """
    cached: dict[int, tuple[int, list[int], FormulaFitness]] = {}
    pending: list[tuple[int, list[int], str | None]] = []
    pending_keys: set[str] = set()
    for i, tokens in enumerate(tokens_list):
        key = _safe_hash(tokens)
        if key is not None and key in cache:
            cached[i] = (i, tokens, cache[key])
            continue
        if key is not None and key in pending_keys:
            continue  # duplicate inside the batch — score once
        if key is not None:
            pending_keys.add(key)
        pending.append((i, tokens, key))

    to_eval = pending[:budget_remaining]
    evals_used = len(to_eval)
    if to_eval:
        if n_jobs > 1 and len(to_eval) > 1 and data_path is not None:
            worker_results = Parallel(n_jobs=n_jobs, backend="loky", mmap_mode="r")(
                delayed(_eval_worker)(list(tokens), data_path, score_kwargs)
                for _, tokens, _ in to_eval
            )
            for (_, _, key), wr in zip(to_eval, worker_results):
                if key is not None:
                    cache[key] = wr.fitness
        else:
            for _, tokens, key in to_eval:
                fitness = score(
                    tokens, data_by_symbol, ret_by_symbol, cache=cache, **score_kwargs
                )
                if key is not None:
                    cache[key] = fitness
        _evict_cache(cache)

    results = [(i, tokens, cache[key]) for i, tokens, key in to_eval if key is not None]
    results.extend(cached[i] for i in sorted(cached))
    results.sort(key=lambda r: r[0])
    return results, evals_used


# ---------------------------------------------------------------------------
# Correlation pruning (conservative diversity filter over factor panels).
# ---------------------------------------------------------------------------
class _BuildBudget:
    """Mutable remaining-panel-build counter (avoided a list-in-closure hack)."""

    __slots__ = ("remaining",)

    def __init__(self, n: int) -> None:
        self.remaining = n


def _panel_spearman(fa: pd.DataFrame, fb: pd.DataFrame) -> float:
    """Spearman correlation of two factor panels over their aligned rows."""
    merged = fa.merge(fb, on=["trade_date", "symbol"], suffixes=("_a", "_b"))
    x = merged["factor_a"].to_numpy(dtype=float)
    y = merged["factor_b"].to_numpy(dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 2 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return 0.0  # no correlation signal → never prune (conservative)
    return float(stats.spearmanr(x, y)[0])


def _get_panel(
    entry: FormulaEntry,
    data_by_symbol: dict[str, pd.DataFrame],
    ret_by_symbol: dict[str, pd.DataFrame | pd.Series],
    panel_cache: dict[str, pd.DataFrame],
    budget: _BuildBudget,
) -> pd.DataFrame | None:
    """Return the factor panel for ``entry`` (cached), or None when over budget."""
    panel = panel_cache.get(entry.canonical_hash)
    if panel is None:
        if budget.remaining <= 0:
            return None
        panel, _ = build_factor_panel(entry.tokens, data_by_symbol, ret_by_symbol)
        panel_cache[entry.canonical_hash] = panel
        budget.remaining -= 1
        if len(panel_cache) > _PANEL_CACHE_SIZE:
            keys = list(panel_cache.keys())[: len(panel_cache) // 2]
            for k in keys:
                del panel_cache[k]
    return panel


def _correlation_prune(
    population: list[FormulaEntry],
    data_by_symbol: dict[str, pd.DataFrame],
    ret_by_symbol: dict[str, pd.DataFrame | pd.Series],
    corr_prune_threshold: float,
    corr_prune_margin: float,
    corr_prune_max_builds: int,
    panel_cache: dict[str, pd.DataFrame],
) -> list[FormulaEntry]:
    """Greedy diversity filter — conservative ("when in doubt, keep both")."""
    ordered = _sort_population(population)
    budget = _BuildBudget(corr_prune_max_builds)
    kept: list[FormulaEntry] = []
    for entry in ordered:
        panel = _get_panel(entry, data_by_symbol, ret_by_symbol, panel_cache, budget)
        dropped = False
        if panel is not None:
            for kept_entry in kept:
                kept_panel = _get_panel(
                    kept_entry, data_by_symbol, ret_by_symbol, panel_cache, budget
                )
                if kept_panel is None:
                    continue
                corr = _panel_spearman(panel, kept_panel)
                gap = kept_entry.fitness.score - entry.fitness.score
                if abs(corr) >= corr_prune_threshold and gap > corr_prune_margin:
                    dropped = True
                    break
        if not dropped:
            kept.append(entry)
    return kept


# ---------------------------------------------------------------------------
# Parallel worker (module-level: win32 loky spawn requirement).
# ---------------------------------------------------------------------------
#: Per-process cache of the shared panel, keyed by the temp file path.
_WORKER_DATA_CACHE: dict[str, tuple[dict[str, pd.DataFrame], dict]] = {}


def _worker_load_data(
    data_path: str,
) -> tuple[dict[str, pd.DataFrame], dict]:
    cached = _WORKER_DATA_CACHE.get(data_path)
    if cached is None:
        cached = joblib.load(data_path, mmap_mode="r")
        _WORKER_DATA_CACHE[data_path] = cached
    return cached


def _eval_worker(
    tokens: list[int], data_path: str, score_kwargs: dict[str, Any]
) -> _EvalResult:
    """Score one formula inside a loky worker (module-level: spawn requirement)."""
    data_by_symbol, ret_by_symbol = _worker_load_data(data_path)
    fitness = score(list(tokens), data_by_symbol, ret_by_symbol, **score_kwargs)
    return _EvalResult(tokens=tuple(tokens), fitness=fitness)


# ---------------------------------------------------------------------------
# Public entry point.
# ---------------------------------------------------------------------------
def reward_score_kwargs(config: SearchConfig) -> dict[str, Any]:
    """Explicit config-field → ``fitness.score`` parameter map (US-03).

    Not a blind pass-through of every config field — only the keyword names
    :func:`fitness.score` accepts, paired with their ``SearchConfig`` source
    fields. Frozen config ⇒ one deterministic reward weighting per run;
    ``test_search_config_reward`` asserts the field↔param correspondence and
    that the weights stay in sync with the ``fitness`` module constants (C2).
    """
    return {
        "w_ic": config.w_ic,
        "w_sharpe": config.w_sharpe,
        "sortino_weight": config.w_sortino,
        "calmar_weight": config.w_calmar,
        "stability_weight": config.w_stability,
        "symbol_agreement_weight": config.w_symbol_agreement,
        "ic_std_ref": config.ic_stability_ref,
        "turnover_penalty_weight": config.turnover_penalty_weight,
        "subperiod_consistency_weight": config.subperiod_consistency_weight,
        "min_subperiod_consistency": config.min_subperiod_consistency,
        "n_subperiods": config.n_subperiods,
        "cost_stress_multipliers": config.cost_stress_multipliers,
        "cost_stress_weight": config.cost_stress_weight,
    }


def run_search(
    data_by_symbol: dict[str, pd.DataFrame],
    ret_by_symbol: dict[str, pd.DataFrame | pd.Series],
    config: Any,
    *,
    seed_formulas: list[list[int]] | None = None,
    blocked_hashes: Collection[str] | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> SearchResult:
    """Run the deterministic steady-state GA and return a :class:`SearchResult`.

    Args:
        data_by_symbol: ``{symbol: DataFrame}`` (OHLCV + DatetimeIndex) — the
            ``fitness.score`` panel contract.
        ret_by_symbol: ``{symbol: Series}`` forward returns — the
            ``fitness.score`` contract.
        config: A frozen :class:`SearchConfig`.
        seed_formulas: Optional valid-RPN seed formulas prepended to the random
            initial population (malformed seeds are skipped).
        blocked_hashes: Optional canonical hashes to exclude from the search
            space (known formulas from the research ledger). Prefilled into
            ``seen_hashes`` — blocked formulas are never seeded, generated,
            or evaluated, so they spend zero eval budget. Exact-hash blocking
            only: near-relatives (mutations of a blocked formula) are still
            explored. When the whole space is blocked the search ends via the
            existing ``search_space_exhausted`` path.
        progress: Optional callback invoked once per generation with a dict
            ``{generation, evaluations_used, best_score, population_size}``.

    Returns:
        A :class:`SearchResult` whose ``top_formulas`` are the best
        ``config.top_n`` formulas by ``fitness.score``.
    """
    rng = random.Random(config.seed)
    cache: dict = {}
    panel_cache: dict[str, pd.DataFrame] = {}
    # Explicit config-field → fitness.score parameter map (frozen config ⇒ one
    # deterministic reward weighting per run).
    score_kwargs: dict[str, Any] = reward_score_kwargs(config)

    # Shared-panel temp file for loky workers (serial path never creates it).
    tmp_dir: tempfile.TemporaryDirectory[str] | None = None
    data_path: str | None = None
    if getattr(config, "n_jobs", 1) > 1:
        tmp_dir = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        data_path = os.path.join(tmp_dir.name, "search_data.joblib")
        joblib.dump((data_by_symbol, ret_by_symbol), data_path)

    population: list[FormulaEntry] = []
    # Blocked formulas (ledger-known) prefilled here: every dedup site below
    # (seed filter / random fill / offspring generation / eval dedup) checks
    # this set, so a blocked hash spends zero eval budget.
    seen_hashes: set[str] = set(blocked_hashes or ())
    evals_used = 0
    generations = 0
    stagnation = 0
    best_entry: FormulaEntry | None = None
    stopped_reason = "budget_exhausted"

    try:
        # ---- initial population (seeds + random fills) ----
        init_tokens: list[list[int]] = []
        if seed_formulas:
            for tokens in seed_formulas:
                t = list(tokens)
                h = _safe_hash(t)
                if h is not None and validate(t).valid and h not in seen_hashes:
                    init_tokens.append(t)
                    seen_hashes.add(h)
        init_cap = max(1, min(config.population_size, config.eval_budget))
        max_fill = init_cap * 30 + 60
        fill_attempts = 0
        while len(init_tokens) < init_cap and fill_attempts < max_fill:
            fill_attempts += 1
            ft = random_formula(
                rng,
                _FEATURE_IDS,
                OP_BY_ARITY,
                config.min_formula_len,
                config.max_formula_len,
            )
            h = _safe_hash(ft)
            if h is not None and h not in seen_hashes:
                init_tokens.append(ft)
                seen_hashes.add(h)

        results, evals_used = _evaluate_batch(
            init_tokens,
            data_by_symbol,
            ret_by_symbol,
            cache,
            config.n_jobs,
            data_path,
            score_kwargs,
            config.eval_budget,
        )
        for _, tokens, fitness in results:
            population.append(_make_entry(tokens, fitness))
        population = _sort_population(population)
        if population:
            best_entry = population[0]

        # ---- generation loop (mu+lambda steady state) ----
        while evals_used < config.eval_budget:
            if not population:
                break
            offspring = _generate_offspring(
                rng,
                population,
                config.lambda_offspring,
                config.tournament_size,
                config.crossover_rate,
                config.mutation_rate,
                config.min_formula_len,
                config.max_formula_len,
                seen_hashes,
            )
            if not offspring:
                stopped_reason = "search_space_exhausted"  # no unseen candidates
                break
            results, batch_evals = _evaluate_batch(
                offspring,
                data_by_symbol,
                ret_by_symbol,
                cache,
                config.n_jobs,
                data_path,
                score_kwargs,
                config.eval_budget - evals_used,
            )
            evals_used += batch_evals
            for _, tokens, fitness in results:
                entry = _make_entry(tokens, fitness)
                if entry.canonical_hash not in seen_hashes:
                    seen_hashes.add(entry.canonical_hash)
                    population.append(entry)
            population = _sort_population(population)[: config.population_size]

            if (
                config.corr_prune
                and (generations + 1) % config.corr_prune_interval == 0
            ):
                population = _correlation_prune(
                    population,
                    data_by_symbol,
                    ret_by_symbol,
                    config.corr_prune_threshold,
                    config.corr_prune_margin,
                    config.corr_prune_max_builds,
                    panel_cache,
                )
                population = _sort_population(population)

            generations += 1
            current_best = population[0] if population else best_entry
            if best_entry is None or (
                current_best is not None
                and current_best.fitness.score > best_entry.fitness.score
            ):
                best_entry = current_best
                stagnation = 0
            else:
                stagnation += 1
                if stagnation >= config.stagnation_generations:
                    stopped_reason = "stagnation"
                    break
            if progress is not None:
                progress(
                    {
                        "generation": generations,
                        "evaluations_used": evals_used,
                        "best_score": best_entry.fitness.score if best_entry else None,
                        "population_size": len(population),
                    }
                )
        else:  # while-condition false → budget exhausted
            stopped_reason = "budget_exhausted"
    finally:
        if tmp_dir is not None:
            try:
                tmp_dir.cleanup()
            except Exception:
                pass  # loky workers may hold the mmap open — ignore PermissionError

    final = _sort_population(population)
    top = final[: config.top_n]
    if best_entry is not None and not any(
        e.canonical_hash == best_entry.canonical_hash for e in top
    ):
        top = [best_entry] + top[: config.top_n - 1]
    return SearchResult(
        top_formulas=top,
        evaluations_used=evals_used,
        generations=generations,
        stopped_reason=stopped_reason,
        seed=config.seed,
    )
