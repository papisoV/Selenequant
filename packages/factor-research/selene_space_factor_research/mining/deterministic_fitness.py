# Cleanroom implementation: design intent derived from prior art; independent expression.
"""Deterministic fitness adapter for the RL engine (torch-free).

Bridges the deterministic search reward (``fitness.score``, WS-A composite) into
the RL engine's ``FitnessProtocol``. The mixed exploitation layer — deterministic
scoring inside a REINFORCE loop — is a **Selene extension**: the reference
design is pure cold-start RL with no deterministic adapter (registered in
PROVENANCE.md, WS-B).

Contract
--------
``evaluate(formula_tokens, vocab) -> float`` — the exact ``FitnessProtocol``
shape ``RealFitnessAlphaEngine`` calls, so this adapter is a drop-in for the
engine's ``fitness`` argument (the second parameter is accepted for
signature-compatibility and unused here).

``score_kwargs`` (optional)
    ``fitness.score`` keywords — the output of
    ``genetic_search.reward_score_kwargs(config)``. When omitted, ``score``'s
    module defaults apply. The search and the RL side optimize the *identical*
    composite only when the same config's kwargs are supplied on both sides.

Floor and the no-gradient trap
------------------------------
``floor=-5.0`` is returned for any invalid or degenerate formula (``valid=False``
or a non-finite ``score``), matching the engine's own -5.0 reject reward. **If a
whole REINFORCE batch landed at ``floor``**, the engine's advantage
normalization ``adv = (rewards - rewards.mean()) / (rewards.std() + 1e-5)``
yields a constant vector -> zero gradients -> the policy silently stops learning.
The engine therefore *depends* on its warm-start injection (``warm_start_steps``
seeds the batch with proven formulas, which score ``> floor``). Removing that
injection would silently break training; keep it. ``test_mining_rl``'s
``test_warm_start_injection`` asserts at least one ``> floor`` reward per run.

Torch-free (HARD): imports ``math`` + the torch-free ``fitness`` module only —
no torch import here, so ``test_no_torch_in_search_import.py`` stays green.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from selene_space_factor_research.mining.fitness import FitnessProtocol, score

if TYPE_CHECKING:  # pragma: no cover - annotation-only, never evaluated at runtime
    import pandas as pd

    from selene_space_alpha.vocab import FormulaVocab

    RetFrame = pd.Series | pd.DataFrame

__all__ = ["DeterministicFitnessAdapter"]


class DeterministicFitnessAdapter(FitnessProtocol):
    """FitnessProtocol adapter scoring formulas with the deterministic composite.

    ``evaluate`` runs the same multi-component ``score`` as the search layer, so
    the RL policy and the deterministic GA optimize one shared reward.
    """

    def __init__(
        self,
        data_by_symbol: dict[str, "pd.DataFrame"],
        ret_by_symbol: dict[str, "RetFrame"],
        *,
        score_kwargs: dict[str, Any] | None = None,
        floor: float = -5.0,
    ) -> None:
        self.data_by_symbol = data_by_symbol
        self.ret_by_symbol = ret_by_symbol
        self.score_kwargs = dict(score_kwargs) if score_kwargs else {}
        self.floor = floor

    def evaluate(self, formula_tokens: list[int], _vocab: "FormulaVocab") -> float:
        f = score(
            formula_tokens,
            self.data_by_symbol,
            self.ret_by_symbol,
            **self.score_kwargs,
        )
        if not f.valid or not math.isfinite(f.score):
            return self.floor
        return f.score
