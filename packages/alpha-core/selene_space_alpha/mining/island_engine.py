"""Multi-population factor mining via the Island Model.

Design based on the Island Model genetic algorithm, a standard CS
curriculum concept since the 1970s-80s (see Cantú-Paz, "A Survey of
Parallel Genetic Algorithms", IlliGAL Report No. 98003, 1998; and
Skolicki & De Jong, "Improving Multi-Optima Optimization by Island
Models", 2004). The model maintains ``n_islands`` independent
populations that each explore the formula space, and every
``migration_interval`` training steps the top ``migration_top_k``
formulas across all islands are broadcast into every island's
warm-start pool for the next phase. This preserves population
diversity and reduces premature convergence.

This is algorithm-level parallelism (rotational island training on
one CPU), not multiprocessing. Each phase rotates through islands
and trains them for ``migration_interval`` steps; between phases
the migration step exchanges elites.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import torch

from ..config import AlphaCoreConfig
from .real_engine import RealFitnessAlphaEngine


@dataclass(frozen=True)
class EliteRecord:
    """A single elite formula broadcast across islands during migration.

    ``score`` is the fitness at which the formula was originally
    discovered; it is kept for downstream ranking and debugging
    and is NOT used to overwrite a higher island-local score.
    """

    score: float
    tokens: tuple[int, ...]
    origin_island: int


@dataclass
class IslandTrainingState:
    """Mutable state tracked per island across migration phases."""

    island_id: int
    engine: RealFitnessAlphaEngine
    elite_pool: list[EliteRecord] = field(default_factory=list)
    best_score: float = -float("inf")
    best_formula: tuple[int, ...] | None = None


class IslandAlphaEngine:
    """Orchestrates multiple :class:`RealFitnessAlphaEngine` islands
    with periodic elite migration.

    Parameters
    ----------
    vocab, fitness:
        Forwarded to each island's :class:`RealFitnessAlphaEngine`.
    config:
        Hyperparameters. ``TRAIN_STEPS`` is split across phases.
    n_islands:
        Number of independent island populations. Defaults to 4.
    migration_interval:
        Number of training steps per phase before elites migrate.
        Must be > 0. Defaults to one quarter of ``TRAIN_STEPS``
        (i.e. 4 phases).
    migration_top_k:
        Number of top formulas broadcast across islands per migration.
        Must be > 0 and <= total elites collected. Defaults to 3.
    output_dir:
        Directory where the global best formula is saved at the
        end of training.
    use_lord_regularization:
        Forwarded to each island's engine.
    """

    def __init__(
        self,
        vocab,
        fitness,
        config: AlphaCoreConfig | None = None,
        *,
        n_islands: int = 4,
        migration_interval: int | None = None,
        migration_top_k: int = 3,
        output_dir: str | Path = ".",
        use_lord_regularization: bool = True,
    ) -> None:
        if n_islands < 1:
            raise ValueError("n_islands must be >= 1")
        if migration_top_k < 1:
            raise ValueError("migration_top_k must be >= 1")

        self.config = config or AlphaCoreConfig()
        self.vocab = vocab
        self.fitness = fitness
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.n_islands = n_islands
        self.migration_top_k = migration_top_k
        self.migration_interval = migration_interval or max(
            1, self.config.TRAIN_STEPS // max(n_islands, 1)
        )
        if self.migration_interval <= 0:
            raise ValueError("migration_interval must be > 0")

        self.islands: list[IslandTrainingState] = []
        for island_id in range(n_islands):
            torch.manual_seed(2026 + island_id * 17)
            engine = RealFitnessAlphaEngine(
                vocab=vocab,
                fitness=fitness,
                config=self.config,
                use_lord_regularization=use_lord_regularization,
                output_dir=self.output_dir / f"island_{island_id}",
            )
            self.islands.append(IslandTrainingState(island_id=island_id, engine=engine))

        self.global_best_score: float = -float("inf")
        self.global_best_formula: list[int] | None = None
        self.global_best_island: int = -1
        self.migration_log: list[dict] = []

    def _collect_elites(self) -> list[EliteRecord]:
        """Collect the current best formula from every island.

        Returns at most ``n_islands`` records (one per island that has
        produced at least one valid formula)."""
        elites: list[EliteRecord] = []
        for state in self.islands:
            tokens = state.engine.get_elite_formula()
            if tokens is None:
                continue
            score = state.engine.get_elite_score()
            record = EliteRecord(
                score=score,
                tokens=tuple(tokens),
                origin_island=state.island_id,
            )
            state.elite_pool.append(record)
            state.best_score = max(state.best_score, score)
            if state.best_formula is None or score >= state.best_score:
                state.best_formula = tuple(tokens)
            elites.append(record)
        return elites

    def _dedupe_and_rank(self, elites: list[EliteRecord]) -> list[EliteRecord]:
        """Dedupe by formula token sequence, keeping the highest score
        per unique formula, then sort descending and truncate to
        ``migration_top_k``."""
        best_by_formula: dict[tuple[int, ...], EliteRecord] = {}
        for record in elites:
            key = record.tokens
            existing = best_by_formula.get(key)
            if existing is None or record.score > existing.score:
                best_by_formula[key] = record
        ranked = sorted(best_by_formula.values(), key=lambda r: r.score, reverse=True)
        return ranked[: self.migration_top_k]

    def _migrate_elites(self, phase: int, step: int) -> int:
        """Broadcast Top-K elites across all islands as warm-start
        formulas for the next phase. Returns the number of elites
        actually injected (0 if fewer than 2 unique elites exist).
        """
        all_elites = self._collect_elites()
        if len(all_elites) < 2:
            return 0
        top_elites = self._dedupe_and_rank(all_elites)
        if not top_elites:
            return 0
        warm_start = [list(record.tokens) for record in top_elites]
        injected_total = 0
        for state in self.islands:
            state.engine.warm_start_formulas = warm_start
            injected_total += len(warm_start)
        self.migration_log.append({
            "phase": phase,
            "step": step,
            "collected": len(all_elites),
            "deduped": len({r.tokens for r in all_elites}),
            "injected": injected_total,
            "top_scores": [r.score for r in top_elites],
        })
        return injected_total

    def _update_global_best(self) -> None:
        for state in self.islands:
            if state.engine.get_elite_score() > self.global_best_score:
                self.global_best_score = state.engine.get_elite_score()
                tokens = state.engine.get_elite_formula()
                self.global_best_formula = list(tokens) if tokens is not None else None
                self.global_best_island = state.island_id

    def train(self) -> None:
        """Run rotational island training with periodic migration.

        Splits ``config.TRAIN_STEPS`` into phases of
        ``migration_interval`` steps each. Each phase trains every
        island for that step range, then migrates elites. After the
        final phase, saves the global best formula.
        """
        total_steps = self.config.TRAIN_STEPS
        interval = self.migration_interval
        n_phases = max(1, (total_steps + interval - 1) // interval)

        print(
            f"\n{'=' * 60}\n"
            f"  Island Alpha Training\n"
            f"  islands={self.n_islands}  migration_every={interval}\n"
            f"  total_steps={total_steps}  phases={n_phases}\n"
            f"{'=' * 60}\n"
        )

        for phase in range(n_phases):
            start = phase * interval
            end = min((phase + 1) * interval, total_steps)
            for state in self.islands:
                print(
                    f">>> Phase {phase + 1}/{n_phases} — Island "
                    f"{state.island_id + 1}/{self.n_islands} steps [{start}:{end}]"
                )
                warm_start = state.engine.warm_start_formulas
                state.engine.train_range(start, end, warm_start)
                self._update_global_best()
            if end < total_steps:
                injected = self._migrate_elites(phase + 1, end)
                print(
                    f"\n[Island Migration @ step {end}] injected "
                    f"{injected} elites across {self.n_islands} islands\n"
                )

        self._update_global_best()
        self._save_global_best()

    def _save_global_best(self) -> None:
        if self.global_best_formula is None:
            print("\nIsland training complete! No valid formula discovered.")
            return
        vocab_version = "unknown"
        payload = {
            "vocab_version": vocab_version,
            "formula": self.global_best_formula,
            "best_score": self.global_best_score,
            "island_engine": True,
            "n_islands": self.n_islands,
            "migration_interval": self.migration_interval,
            "migration_top_k": self.migration_top_k,
            "global_best_island": self.global_best_island,
            "migration_log": self.migration_log,
        }
        save_path = self.output_dir / "best_island_strategy.json"
        with open(save_path, "w") as fp:
            json.dump(payload, fp, indent=2)
        print(
            f"\nIsland training complete!\n"
            f"  Global best score : {self.global_best_score:.4f}\n"
            f"  From island       : {self.global_best_island + 1}\n"
            f"  Formula           : {self.global_best_formula}\n"
            f"  Saved to          : {save_path}"
        )

    def get_global_best(self) -> tuple[list[int] | None, float]:
        return self.global_best_formula, self.global_best_score
