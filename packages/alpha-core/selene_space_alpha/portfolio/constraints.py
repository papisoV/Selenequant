"""Portfolio constraint builder for scipy SLSQP optimization."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Constraints:
    n: int
    bounds: tuple[tuple[float, float], ...]
    scipy_constraints: tuple[dict, ...] = ()


def build_constraints(
    n: int,
    long_only: bool = True,
    full_investment: bool = True,
    weight_cap: float | None = None,
    sectors: np.ndarray | None = None,
    sector_targets: dict[int, float] | None = None,
    exposures: np.ndarray | None = None,
    w_prev: np.ndarray | None = None,
    turnover_limit: float | None = None,
    net_exposure: float | None = None,
) -> Constraints:
    """Build constraint dictionaries for scipy.optimize.minimize (SLSQP)."""
    bounds: list[tuple[float, float]] = []
    for _ in range(n):
        lo = 0.0 if long_only else (-weight_cap if weight_cap else -1.0)
        hi = weight_cap if weight_cap else 1.0
        bounds.append((lo, hi))

    cons: list[dict] = []

    if full_investment and net_exposure is None:
        cons.append({"type": "eq", "fun": lambda w: np.sum(w) - 1.0})
    elif net_exposure is not None:
        cons.append({"type": "eq", "fun": lambda w: np.sum(w) - net_exposure})

    if sectors is not None and sector_targets is not None:
        for sec_id, target in sector_targets.items():
            mask = (sectors == sec_id).astype(float)
            cons.append({
                "type": "eq",
                "fun": lambda w, m=mask, t=target: float(m @ w) - t,
            })

    if exposures is not None:
        for j in range(exposures.shape[1]):
            col = exposures[:, j].copy()
            cons.append({
                "type": "eq",
                "fun": lambda w, c=col: float(c @ w),
            })

    if w_prev is not None and turnover_limit is not None:
        cons.append({
            "type": "ineq",
            "fun": lambda w: turnover_limit - np.sum(np.abs(w - w_prev)),
        })

    return Constraints(
        n=n,
        bounds=tuple(bounds),
        scipy_constraints=tuple(cons),
    )
