"""Style rotation monitor: SIZE / VALUE / GROWTH daily long-short spreads.

batch4 US-4. Cross-sectional style spread series — each day stocks are sorted
into quantiles by the style factor; the spread is the return difference between
the high and low quantile groups, signed so that positive = "allocating by the
factor's direction made money" (direction=-1 factors like SIZE flip the sign).
Uses factor_eval.quantile_returns (qcut route) — NOT construct_portfolio,
whose ascending flip is a known naming trap.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from selene_space_ashare.factor_eval import quantile_returns

STYLE_FACTORS: dict[str, int] = {
    "SIZE": -1,  # small cap premium: low SIZE (Q_low) should win
    "BP": 1,  # value premium: high BP (Q_high) should win
    "YOY_NI": 1,  # growth premium: high growth (Q_high) should win
}


def style_spread_series(
    panel: pd.DataFrame,
    style_factor: str,
    forward_col: str = "fwd_5d",
    n_quantiles: int = 3,
    direction: int | None = None,
) -> pd.Series:
    """Daily long-short spread for one style factor.

    spread = direction x (mean(Q_high) - mean(Q_low)), quantiles are 0-indexed
    ascending (qcut labels=False). For SIZE (direction=-1) a positive spread
    means the small-cap group outperformed.
    """
    if direction is None:
        direction = STYLE_FACTORS[style_factor]
    qr = quantile_returns(panel, style_factor, forward_col, n_quantiles)
    if qr.empty:
        return pd.Series(dtype=float, name=f"spread_{style_factor}")
    means = qr.groupby("quantile")["mean"].mean()
    q_low, q_high = means.index.min(), means.index.max()
    return pd.Series(
        (means[q_high] - means[q_low]) * direction,
        index=[style_factor],
        name=f"spread_{style_factor}",
    )


def style_spread_by_day(
    panel: pd.DataFrame,
    style_factor: str,
    forward_col: str = "fwd_5d",
    n_quantiles: int = 3,
    direction: int | None = None,
) -> pd.Series:
    """Daily spread time series. Same qcut route as factor_eval.quantile_returns
    (which only emits aggregated stats, so the per-day spread is computed here)."""
    if direction is None:
        direction = STYLE_FACTORS[style_factor]
    spreads: dict[Any, float] = {}
    for date, group in panel.groupby("date"):
        mask = group[style_factor].notna() & group[forward_col].notna()
        valid = group[mask]
        if len(valid) < n_quantiles * 2:
            continue
        labels = pd.qcut(
            valid[style_factor], n_quantiles, labels=False, duplicates="drop"
        )
        q_low_ret = valid.loc[labels == 0, forward_col].mean()
        q_high_ret = valid.loc[labels == labels.max(), forward_col].mean()
        spreads[date] = (q_high_ret - q_low_ret) * direction
    return pd.Series(spreads, name=f"spread_{style_factor}")


def style_regime_summary(
    spread_by_day: pd.Series,
    lookback: int = 20,
) -> dict[str, Any]:
    """Recent regime: cumulative spread + leading/lagging classification."""
    recent = spread_by_day.dropna().tail(lookback)
    if recent.empty:
        return {"cum_spread": None, "n_days": 0, "regime": "no_data"}
    cum = float(recent.sum())
    return {
        "cum_spread": round(cum, 6),
        "n_days": int(len(recent)),
        "regime": "leading" if cum > 0 else "lagging",
    }


def full_style_report(
    panel: pd.DataFrame,
    forward_col: str = "fwd_5d",
    lookback: int = 20,
) -> dict[str, Any]:
    """All three styles: per-day spreads + regime summaries, JSON-ready."""
    out: dict[str, Any] = {"forward_col": forward_col, "styles": {}}
    for style in STYLE_FACTORS:
        if style not in panel.columns or not panel[style].notna().any():
            out["styles"][style] = {"regime": "factor_absent"}
            continue
        by_day = style_spread_by_day(panel, style, forward_col)
        summary = style_regime_summary(by_day, lookback)
        out["styles"][style] = {
            **summary,
            "spread_series_tail": [
                {"date": str(d), "spread": round(float(v), 6)}
                for d, v in by_day.dropna().tail(10).items()
            ],
        }
    return out
