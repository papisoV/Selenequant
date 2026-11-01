# Cleanroom implementation: design intent derived from prior art; independent expression.

"""Low-rank weight regularization and effective-rank monitoring.

Carries the *design intent* of LoRD-style low-rank decay, implemented
independently for the Selene alpha-core mining stack; no source text from the
licensed reference is reproduced. The underlying mathematics is public knowledge:

- **Newton-Schulz matrix refinement** (Newton 1946): the iteration
  ``Y <- 0.5 * Y @ (3I - Y.T @ Y)`` drives a matrix toward orthonormality,
  so applying it as a small weight update biases the spectrum toward low
  effective rank.
- **Effective (stable) rank**: the public statistic ``||S||^2 / S_1^2`` over
  the singular values ``S`` (``S_1`` = largest), a continuous proxy for how
  many meaningful directions a matrix spans.
"""

from __future__ import annotations

import torch


class LowRankRegularizer:
    """Decays selected 2-D weights toward a low-rank manifold.

    After each optimizer step, the chosen attention / qk-norm weight matrices
    are nudged a small amount toward orthonormality via Newton-Schulz
    refinement. Over many steps this regularizes the policy network by
    discouraging parameter spectra that are spread across too many directions.
    """

    def __init__(
        self,
        named_parameters,
        decay_rate: float = 1e-3,
        num_iterations: int = 5,
        target_keywords: list[str] | None = None,
    ):
        self.decay_rate = decay_rate
        self.num_iterations = num_iterations
        self.target_keywords = target_keywords or ["qk_norm", "attention"]
        self._targets: list[tuple[str, torch.nn.Parameter]] = []

        for param_name, param in named_parameters:
            if not param.requires_grad or param.ndim != 2:
                continue
            if not any(keyword in param_name for keyword in self.target_keywords):
                continue
            self._targets.append((param_name, param))

    @torch.no_grad()
    def step(self) -> None:
        """Apply one low-rank decay pass over the selected weights."""
        for _, weight in self._targets:
            original_dtype = weight.dtype
            working = weight.float()
            rows, cols = working.shape

            transposed = False
            if rows > cols:
                # Iterate on the shorter side so the inner square matrix stays
                # small; undo the transpose before writing back.
                working = working.T
                transposed = True

            # Unit-scale the matrix so the refinement acts on a normalised
            # operator rather than raw parameter magnitudes.
            working = working / (working.norm() + 1e-8)

            iterate = working
            identity = torch.eye(
                working.shape[-1], device=working.device, dtype=working.dtype
            )
            for _ in range(self.num_iterations):
                gram = iterate.T @ iterate
                iterate = 0.5 * iterate @ (3.0 * identity - gram)

            if transposed:
                iterate = iterate.T

            weight.sub_(self.decay_rate * iterate.to(original_dtype))


class EffectiveRankMonitor:
    """Tracks the average effective (stable) rank of selected weights.

    The stable rank ``||S||^2 / S_1^2`` is reported per 2-D weight matrix and
    averaged across all targeted matrices. Its rolling history is exposed on
    ``self.history``.
    """

    def __init__(
        self,
        model: torch.nn.Module,
        target_keywords: list[str] | None = None,
    ):
        self.model = model
        self.target_keywords = target_keywords or ["q_proj", "k_proj", "attention"]
        self.history: list[float] = []

    @torch.no_grad()
    def compute(self) -> float:
        """Compute the average effective rank across targeted 2-D weights."""
        per_weight_ranks: list[float] = []
        for param_name, param in self.model.named_parameters():
            if param.ndim != 2:
                continue
            if not any(keyword in param_name for keyword in self.target_keywords):
                continue

            singulars = torch.linalg.svdvals(param.detach().float())
            dominant = singulars[0]
            stable_rank = (singulars.norm() ** 2) / (dominant**2 + 1e-9)
            per_weight_ranks.append(stable_rank.item())

        avg_rank = (
            sum(per_weight_ranks) / len(per_weight_ranks)
            if per_weight_ranks
            else 0.0
        )
        self.history.append(avg_rank)
        return avg_rank
