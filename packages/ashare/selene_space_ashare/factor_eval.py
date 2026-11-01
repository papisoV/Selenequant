"""
Factor evaluation: IC/IR analysis, factor returns, correlation, and ranking.

IC (Information Coefficient): Rank correlation between factor values and forward returns.
IR (Information Ratio): IC mean / IC std, measures factor stability.
"""

from typing import Any

import numpy as np
import pandas as pd


def calc_forward_returns(
    close: pd.Series, periods: list[int] | None = None
) -> pd.DataFrame:
    """Calculate forward returns for multiple holding periods."""
    if periods is None:
        periods = [1, 5, 10, 21]
    result = {}
    for p in periods:
        result[f"fwd_{p}d"] = close.shift(-p) / close - 1
    return pd.DataFrame(result)


def calc_ic(
    factor_values: pd.Series,
    forward_returns: pd.Series,
    method: str = "spearman",
) -> float:
    """Calculate Information Coefficient between factor and forward returns."""
    mask = factor_values.notna() & forward_returns.notna()
    if mask.sum() < 10:
        return np.nan
    if method == "spearman":
        return float(factor_values[mask].rank().corr(forward_returns[mask].rank()))
    return float(factor_values[mask].corr(forward_returns[mask]))


def calc_factor_ic_series(
    cross_section: pd.DataFrame,
    factor_name: str,
    forward_col: str = "fwd_5d",
) -> pd.Series:
    """Calculate daily IC for a factor across stocks.

    Args:
        cross_section: DataFrame with [date, symbol, factor_name, forward_col]
        factor_name: column name of the factor
        forward_col: column name of forward returns

    Returns:
        Series of IC values indexed by date
    """
    ic_values = {}
    for date, group in cross_section.groupby("date"):
        ic = calc_ic(group[factor_name], group[forward_col])
        ic_values[date] = ic
    return pd.Series(ic_values, name=f"IC_{factor_name}")


def calc_factor_ir(ic_series: pd.Series) -> float:
    """Information Ratio: mean(IC) / std(IC)."""
    if ic_series.std() == 0 or ic_series.isna().all():
        return np.nan
    return float(ic_series.mean() / ic_series.std())


def neutralize_cross_section(
    values: pd.Series,
    industry: pd.Series | None = None,
    size: pd.Series | None = None,
) -> pd.Series:
    """Neutralize a cross-section by industry demeaning + size regression residual.

    industry: same-index Series of industry labels -> within-industry demeaning
        (equivalent to industry-dummy OLS).
    size: same-index Series of market cap -> residual from cross-sectional OLS
        of values on standardized ln(size).

    Either dimension is skipped when its input is None or has <10 valid
    overlapping observations. Both can be applied (industry first).
    """
    vals = values.copy()
    valid = vals.dropna()
    if len(valid) < 10:
        return vals

    if industry is not None:
        ind = industry.reindex(vals.index)
        ok = vals.notna() & ind.notna()
        if ok.sum() >= 10:
            grouped = vals[ok].groupby(ind[ok])
            vals.loc[ok] = vals[ok] - grouped.transform("mean")

    if size is not None:
        sz = size.reindex(vals.index)
        ok = vals.notna() & sz.notna()
        if ok.sum() >= 10 and (sz[ok] > 0).all():
            log_sz = np.log(sz[ok].astype(float))
            std = log_sz.std()
            if std > 0:
                z = (log_sz - log_sz.mean()) / std
                X = np.column_stack([np.ones(len(z)), z.to_numpy()])
                try:
                    beta = np.linalg.lstsq(
                        X, vals[ok].to_numpy(dtype=float), rcond=None
                    )[0]
                    vals.loc[ok] = vals[ok].to_numpy(dtype=float) - X @ beta
                except Exception:
                    pass

    return vals


def calc_ic_neutralized(
    cross_section: pd.DataFrame,
    factor_name: str,
    forward_col: str = "fwd_5d",
    industry_col: str | None = None,
    size_col: str | None = None,
) -> pd.Series:
    """Daily IC with optional industry/size neutralization.

    Args:
        cross_section: [date, symbol, factor_name, forward_col] plus optional
            industry_col / size_col columns
        industry_col: column name of industry labels (None or missing -> skip)
        size_col: column name of market cap (None or missing -> skip)

    Returns:
        Series of neutralized daily ICs indexed by date
    """
    ind = (
        cross_section[industry_col]
        if (industry_col and industry_col in cross_section.columns)
        else None
    )
    sz = (
        cross_section[size_col]
        if (size_col and size_col in cross_section.columns)
        else None
    )

    ic_values: dict = {}
    for date, group in cross_section.groupby("date"):
        neutralized = neutralize_cross_section(
            group[factor_name],
            ind.loc[group.index] if ind is not None else None,
            sz.loc[group.index] if sz is not None else None,
        )
        ic_values[date] = calc_ic(neutralized, group[forward_col])
    return pd.Series(ic_values, name=f"IC_{factor_name}_neutral")


def factor_evaluation_report(
    cross_section: pd.DataFrame,
    factor_names: list[str],
    forward_col: str = "fwd_5d",
    neutralize: bool = False,
    industry_col: str | None = None,
    size_col: str | None = None,
) -> pd.DataFrame:
    """Generate evaluation report for multiple factors.

    Args:
        cross_section: DataFrame with [date, symbol, factor cols, forward returns]
        factor_names: list of factor column names to evaluate
        forward_col: forward return column
        neutralize: use industry/size neutralized IC (skips missing dims)
        industry_col / size_col: column names for neutralization inputs

    Returns:
        DataFrame with IC_mean, IC_std, IR, IC>0%, t-stat for each factor
    """
    results = []
    for name in factor_names:
        if neutralize:
            ic_series = calc_ic_neutralized(
                cross_section, name, forward_col, industry_col, size_col
            )
        else:
            ic_series = calc_factor_ic_series(cross_section, name, forward_col)
        if ic_series.empty or ic_series.isna().all():
            results.append(
                {
                    "factor": name,
                    "IC_mean": np.nan,
                    "IC_std": np.nan,
                    "IR": np.nan,
                    "IC_pos_rate": np.nan,
                    "t_stat": np.nan,
                }
            )
            continue
        ic_clean = ic_series.dropna()
        results.append(
            {
                "factor": name,
                "IC_mean": ic_clean.mean(),
                "IC_std": ic_clean.std(),
                "IR": calc_factor_ir(ic_clean),
                "IC_pos_rate": (ic_clean > 0).mean(),
                "t_stat": ic_clean.mean() / (ic_clean.std() / np.sqrt(len(ic_clean)))
                if len(ic_clean) > 1
                else np.nan,
            }
        )

    report = pd.DataFrame(results)
    if not report.empty:
        report = report.sort_values("IR", ascending=False, key=abs)
    return report


def factor_correlation_matrix(
    cross_section: pd.DataFrame,
    factor_names: list[str],
) -> pd.DataFrame:
    """Cross-sectional correlation between factors (averaged over time)."""
    factor_data = cross_section[factor_names].dropna()
    return factor_data.corr()


def quantile_returns(
    cross_section: pd.DataFrame,
    factor_name: str,
    forward_col: str = "fwd_5d",
    n_quantiles: int = 5,
) -> pd.DataFrame:
    """Calculate average forward returns by factor quantile.

    Args:
        cross_section: DataFrame with factor and forward return columns
        factor_name: factor to sort on
        forward_col: forward return column
        n_quantiles: number of quantile groups

    Returns:
        DataFrame with quantile labels and mean returns
    """
    results = []
    for date, group in cross_section.groupby("date"):
        mask = group[factor_name].notna() & group[forward_col].notna()
        valid = group[mask]
        if len(valid) < n_quantiles * 2:
            continue
        labels = pd.qcut(
            valid[factor_name], n_quantiles, labels=False, duplicates="drop"
        )
        for q in range(n_quantiles):
            q_mask = labels == q
            if q_mask.sum() > 0:
                results.append(
                    {
                        "date": date,
                        "quantile": q,
                        "return": valid.loc[q_mask, forward_col].mean(),
                    }
                )

    if not results:
        return pd.DataFrame()

    df = pd.DataFrame(results)
    return df.groupby("quantile")["return"].agg(["mean", "std", "count"]).reset_index()


def orthogonalize_greedy(
    cross_section: pd.DataFrame,
    factor_names: list[str],
    forward_col: str = "fwd_5d",
    min_abs_t: float = 2.0,
    max_factors: int | None = None,
) -> dict[str, Any]:
    """Greedy residual orthogonalization of factors by raw |t_stat| priority.

    Factors are ranked by raw IC t_stat (descending, NaN-t factors dropped
    first). The first factor keeps its original values; each subsequent
    candidate is regressed day-by-day on the *original* values of all kept
    factors (cross-sectional OLS per date), and its daily Spearman IC series
    is computed from the residuals. A candidate is kept only if the
    residual-based |t_stat| >= min_abs_t.

    Returns a dict with "kept" (list of stat dicts in greedy order),
    "dropped" (list of {factor, reason}), "order" (greedy factor order),
    and "params".
    """
    raw_report = factor_evaluation_report(cross_section, factor_names, forward_col)
    raw_stats = {row["factor"]: row for _, row in raw_report.iterrows()}

    ranked = sorted(
        factor_names,
        key=lambda f: (
            -abs(raw_stats[f]["t_stat"])
            if pd.notna(raw_stats[f]["t_stat"]) and np.isfinite(raw_stats[f]["t_stat"])
            else np.inf
        ),
    )
    order: list[str] = []
    kept: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    for name in ranked:
        order.append(name)
        if pd.isna(raw_stats[name]["t_stat"]):
            dropped.append({"factor": name, "reason": "raw IC t_stat is NaN"})

    # candidates with valid raw t_stat, in greedy order
    candidates = [f for f in ranked if pd.notna(raw_stats[f]["t_stat"])]

    corr_df = factor_correlation_matrix(cross_section, factor_names)

    kept_names: list[str] = []
    for name in candidates:
        if max_factors is not None and len(kept_names) >= max_factors:
            dropped.append({"factor": name, "reason": "max_factors reached"})
            continue

        if not kept_names:
            residual: pd.Series | None = None
        else:
            residual = _greedy_residuals(cross_section, name, kept_names, forward_col)
            if residual is None:
                dropped.append({"factor": name, "reason": "insufficient valid days"})
                continue

        if residual is None:
            ic_series = calc_factor_ic_series(cross_section, name, forward_col)
        else:
            ic_values: dict = {}
            for date, group in cross_section.groupby("date"):
                res = residual.reindex(group.index)
                ic_values[date] = calc_ic(res, group[forward_col])
            ic_series = pd.Series(ic_values, name=f"IC_{name}_orthogonal")

        if ic_series.empty or ic_series.isna().all():
            dropped.append({"factor": name, "reason": "orthogonal IC series empty"})
            continue

        ic_clean = ic_series.dropna()
        t_stat = (
            ic_clean.mean() / (ic_clean.std() / np.sqrt(len(ic_clean)))
            if len(ic_clean) > 1
            else np.nan
        )

        if pd.isna(t_stat) or abs(t_stat) < min_abs_t:
            max_corr_factor: str | None = None
            max_corr: float = 0.0
            for kf in kept_names:
                try:
                    c = float(abs(corr_df.loc[name, kf]))
                except (KeyError, ValueError):
                    continue
                if pd.notna(c) and c > max_corr:
                    max_corr = c
                    max_corr_factor = kf
            reason = f"orthogonal t={t_stat:.3f} < {min_abs_t}"
            if max_corr_factor is not None:
                reason += (
                    f"; max corr with kept: {max_corr_factor} (corr={max_corr:.3f})"
                )
            dropped.append({"factor": name, "reason": reason})
            continue

        kept_names.append(name)
        kept.append(
            {
                "factor": name,
                "IC_mean": ic_clean.mean(),
                "IC_std": ic_clean.std(),
                "IR": calc_factor_ir(ic_clean),
                "IC_pos_rate": (ic_clean > 0).mean(),
                "t_stat": t_stat,
            }
        )

    kept_df = pd.DataFrame(kept)
    if not kept_df.empty:
        stat_cols = [c for c in kept_df.columns if c != "factor"]
        kept_df[stat_cols] = kept_df[stat_cols].round(4)
        kept_df = kept_df.where(kept_df.notna(), None)
    kept_rows: list[dict[str, Any]] = kept_df.to_dict(orient="records")

    return {
        "kept": kept_rows,
        "dropped": dropped,
        "order": order,
        "params": {"min_abs_t": min_abs_t, "max_factors": max_factors},
    }


def _greedy_residuals(
    cross_section: pd.DataFrame,
    candidate: str,
    kept_names: list[str],
    forward_col: str,
) -> pd.Series | None:
    """Per-date cross-sectional OLS residual of candidate on kept factors.

    Joint non-NaN mask over candidate + all kept factors + forward_col. Days
    with <10 valid rows are skipped (their IC becomes NaN downstream — calc_ic
    also needs forward returns present). Regression basis is the ORIGINAL kept
    factor values.
    """
    residual_parts: list[pd.Series] = []
    for _date, group in cross_section.groupby("date"):
        cols = [candidate, *kept_names, forward_col]
        sub = group[cols]
        mask = sub.notna().all(axis=1)
        vals = sub[mask]
        if len(vals) < 10:
            continue
        y = vals[candidate].to_numpy(dtype=float)
        X = np.column_stack(
            [np.ones(len(vals)), vals[kept_names].to_numpy(dtype=float)]
        )
        try:
            beta = np.linalg.lstsq(X, y, rcond=None)[0]
        except Exception:
            continue
        residual_parts.append(pd.Series(y - X @ beta, index=vals.index, name=candidate))

    if not residual_parts:
        return None
    return pd.concat(residual_parts).sort_index()
