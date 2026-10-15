"""
Multi-factor composite scoring using IC/IR-weighted combination.

Combines multiple factors into a single composite score using
cross-sectional rank normalization and IR-weighted blending.
"""

import numpy as np
import pandas as pd

from selene_space_ashare.factor_eval import calc_factor_ic_series, calc_factor_ir
from selene_space_ashare.factors import ALL_FACTORS
from selene_space_types.stock import CompositeConfig


def compute_ic_weights(
    cross_section: pd.DataFrame,
    factor_names: list[str],
    forward_col: str = "fwd_5d",
    method: str = "ir",
    min_ir: float = 0.05,
) -> dict[str, float]:
    """Compute factor weights based on IC/IR analysis.

    Args:
        cross_section: DataFrame with [date, symbol, factor cols, forward returns]
        factor_names: factors to include in composite
        forward_col: forward return column for IC calculation
        method: "ir" (weight by |IR|), "ic" (weight by |IC_mean|), "equal"
        min_ir: minimum |IR| threshold; factors below this get weight 0

    Returns:
        {factor_name: weight} with |weights| summing to 1.0
    """
    valid_methods = {"equal", "ic", "ir"}
    if method not in valid_methods:
        raise ValueError(
            f"unknown weight method {method!r}; valid: {sorted(valid_methods)} "
            "(an unrecognized method would silently produce all-zero weights)"
        )

    weights: dict[str, float] = {}

    if method == "equal":
        n = len(factor_names)
        for name in factor_names:
            direction = ALL_FACTORS[name].direction if name in ALL_FACTORS else 1
            weights[name] = direction / n
        return weights

    for name in factor_names:
        ic_series = calc_factor_ic_series(cross_section, name, forward_col)
        ic_clean = ic_series.dropna()
        if ic_clean.empty:
            weights[name] = 0.0
            continue

        ic_mean = ic_clean.mean()
        ir = calc_factor_ir(ic_clean)

        direction = ALL_FACTORS[name].direction if name in ALL_FACTORS else 1

        if method == "ir":
            if abs(ir) < min_ir:
                weights[name] = 0.0
            else:
                weights[name] = direction * abs(ir)
        elif method == "ic":
            weights[name] = direction * abs(ic_mean)

    total = sum(abs(w) for w in weights.values())
    if total > 0:
        weights = {k: v / total for k, v in weights.items()}

    return weights


def _forward_horizon(forward_col: str) -> int:
    """Extract holding horizon from a forward-return column name (fwd_5d -> 5)."""
    try:
        return int(forward_col.rsplit("_", 1)[-1].rstrip("d"))
    except ValueError:
        return 0


def compute_ic_weights_walkforward(
    cross_section: pd.DataFrame,
    factor_names: list[str],
    forward_col: str = "fwd_5d",
    min_obs: int = 12,
) -> dict[str, pd.Series]:
    """Compute walk-forward IC weights: at date t, only ICs realized before t.

    For each date t the weight uses historical ICs with
    ic_date < t - forward_horizon, so the forward return window that
    produced the IC has fully materialized before t (no look-ahead).
    Dates with fewer than min_obs valid IC observations fall back to
    equal weights (signed by factor direction).

    Returns:
        {factor_name: weight Series indexed by date}
    """
    horizon = _forward_horizon(forward_col)

    ic_frames = {
        name: calc_factor_ic_series(cross_section, name, forward_col)
        for name in factor_names
    }
    ic_wide = pd.DataFrame(ic_frames).sort_index()

    # Date t may only use ICs realized strictly before t - horizon.
    cutoffs = ic_wide.index.to_series().shift(horizon)

    direction = {
        name: (ALL_FACTORS[name].direction if name in ALL_FACTORS else 1)
        for name in factor_names
    }

    weights: dict[str, pd.Series] = {
        name: pd.Series(np.nan, index=ic_wide.index) for name in factor_names
    }
    for t in ic_wide.index:
        cutoff = cutoffs.loc[t]
        if pd.isna(cutoff):
            hist = ic_wide.iloc[0:0]
        else:
            hist = ic_wide[ic_wide.index < cutoff]
        n_valid = int(hist.notna().sum().max()) if not hist.empty else 0
        if n_valid < min_obs:
            n = len(factor_names)
            for name in factor_names:
                weights[name].loc[t] = direction[name] / n
            continue
        # IR per factor over the history window
        for name in factor_names:
            col = hist[name].dropna()
            if len(col) < 2 or col.std(ddof=1) == 0:
                weights[name].loc[t] = 0.0
                continue
            weights[name].loc[t] = direction[name] * abs(col.mean() / col.std(ddof=1))

    # Normalize per date so |weights| sum to 1; all-zero dates fall back to equal.
    for t in ic_wide.index:
        total = sum(abs(weights[name].loc[t]) for name in factor_names)
        if total > 0:
            for name in factor_names:
                weights[name].loc[t] = weights[name].loc[t] / total
        else:
            n = len(factor_names)
            for name in factor_names:
                weights[name].loc[t] = direction[name] / n

    return weights


def calc_composite_score(
    cross_section: pd.DataFrame,
    config: CompositeConfig,
) -> pd.Series:
    """Calculate composite factor score using cross-sectional rank normalization.

    For each date:
    1. Rank each factor cross-sectionally (percentile rank 0-1)
    2. For direction=-1 factors, rank ascending (low values get high rank)
    3. Multiply rank by weight and sum across factors
    """
    scores = pd.Series(0.0, index=cross_section.index)

    for factor_name, weight in config.factor_weights.items():
        if factor_name not in cross_section.columns:
            continue
        direction = (
            ALL_FACTORS[factor_name].direction if factor_name in ALL_FACTORS else 1
        )

        def rank_group(group: pd.DataFrame) -> pd.Series:
            vals = group[factor_name]
            valid = vals.notna()
            ranks = pd.Series(np.nan, index=group.index)
            if valid.sum() < 2:
                return ranks
            ascending = direction == -1
            ranks[valid] = vals[valid].rank(ascending=ascending, pct=True)
            return ranks

        factor_ranks = cross_section.groupby("date", group_keys=False).apply(
            rank_group, include_groups=False
        )
        if isinstance(factor_ranks, pd.DataFrame):
            factor_ranks = factor_ranks.squeeze()
        scores += weight * factor_ranks

    return scores


def build_composite_panel(
    cross_section: pd.DataFrame,
    factor_names: list[str],
    forward_col: str = "fwd_5d",
    weight_method: str = "ir",
    min_ir: float = 0.05,
    min_obs: int = 12,
) -> tuple[pd.DataFrame, CompositeConfig]:
    """Build cross-section with composite factor column added.

    weight_method:
        "ir" / "ic" / "equal" — full-sample weights (backward-compatible default)
        "ir_walkforward" — per-date weights from ICs realized before
            date - forward_horizon (no look-ahead; see compute_ic_weights_walkforward)

    Returns:
        Tuple of (panel with composite_score column, CompositeConfig used)
    """
    panel = cross_section.copy()
    if weight_method == "ir_walkforward":
        wf = compute_ic_weights_walkforward(panel, factor_names, forward_col, min_obs)
        direction = {
            name: (ALL_FACTORS[name].direction if name in ALL_FACTORS else 1)
            for name in factor_names
        }
        scores = pd.Series(0.0, index=panel.index)
        for date, group in panel.groupby("date"):
            date_key = str(date)
            for name in factor_names:
                if name not in group.columns or date_key not in wf[name].index:
                    continue
                w = float(wf[name].loc[date_key])
                # NaN ranks (factor missing for some rows on this date, e.g.
                # sparse-coverage columns that fall back to zero weight or
                # equal-weight) must not poison the row score via IEEE NaN
                # propagation — only accumulate valid ranks.
                if w == 0.0:
                    continue
                ranks = group[name].rank(ascending=(direction[name] == -1), pct=True)
                valid = ranks.dropna()
                scores.loc[valid.index] += w * valid
        panel["composite_score"] = scores
        config = CompositeConfig(
            name=f"composite_{weight_method}",
            factor_weights={
                name: float(wf[name].dropna().abs().mean()) for name in factor_names
            },
            direction=1,
        )
        return panel, config

    weights = compute_ic_weights(
        cross_section, factor_names, forward_col, weight_method, min_ir
    )
    config = CompositeConfig(
        name=f"composite_{weight_method}",
        factor_weights=weights,
        direction=1,
    )
    panel = cross_section.copy()
    panel["composite_score"] = calc_composite_score(panel, config)
    return panel, config


MOMENTUM_QUALITY_FACTORS = ["VOLATILITY_20D", "MA_DEVIATION", "HIGH_52W_PROXIMITY"]
MEAN_REVERSION_FACTORS = ["RSI_14D", "BB_WIDTH", "VOLUME_CLIMAX"]


def momentum_quality_composite(
    cross_section: pd.DataFrame,
    forward_col: str = "fwd_5d",
) -> tuple[pd.DataFrame, CompositeConfig]:
    """Momentum + Quality: low-correlation top factors."""
    return build_composite_panel(
        cross_section,
        MOMENTUM_QUALITY_FACTORS,
        forward_col,
        weight_method="ir",
    )


def mean_reversion_composite(
    cross_section: pd.DataFrame,
    forward_col: str = "fwd_5d",
) -> tuple[pd.DataFrame, CompositeConfig]:
    """Mean Reversion: only one from the 0.89-correlated MR cluster."""
    return build_composite_panel(
        cross_section,
        MEAN_REVERSION_FACTORS,
        forward_col,
        weight_method="ir",
    )
