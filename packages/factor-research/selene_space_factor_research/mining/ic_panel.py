# Ported from AlphaPurify (MIT)
"""IC diagnostic panel — rolling IC / IR, autocorrelation, QQ, monthly heatmap, sector IC.

Faithful pandas re-implementation of the IC-diagnostic section of AlphaPurify's
``FactorAnalyzer.calc_stats_for_horizon`` (per-date spearman IC, lag autocorrelation,
distribution stats, monthly aggregation, industry contribution), complementing
:mod:selene_space_alpha.mining.ic_decay (which measures decay across holding-period
lags).

Semantics are ported from the polars original:

- **rolling_ic**: per-date spearman correlation between the factor and the
  (pre-aligned) forward return, ``drop_nans``'d exactly like the source's
  ``.group_by(date).agg(pl.corr(..., method="spearman")).drop_nans()``.
- **autocorr**: per-date pearson correlation between the per-date dense factor
  rank and its ``horizon``-lagged value within each symbol (source L1339-1352).
- **ic_distribution**: mean / std (ddof=1) / skew / excess-kurtosis / t-test / IR
  computed with scipy on the cleaned IC series (source L1483-1499, keys verbatim).
- **monthly_heatmap**: per-(year, month) ``sum`` of daily IC pivoted into a
  year x month frame (source ``_aggregation`` uses ``pl.col(type).sum()``).
- **sector_ic**: per-(date, group) spearman IC weighted by group share of the
  date's stock count, then averaged per group and sorted descending
  (source L1439-1464).

Known differences from the original (all documented):

- **``_aggregation`` 恒真 bug fixed**: the source's ``if type == "ic" or "rank_ic":``
  is always true because ``"rank_ic"`` is a truthy literal; here the equivalent
  guard is explicit and the empty-frequency fallback returns an empty frame
  instead of a polars Datatype.
- **qq_stats is a Selene extension** (the original exposes no QQ diagnostics):
  a scipy ``probplot`` fit against the normal plus a D'Agostino-Pearson
  ``normaltest``, so a user can judge normality of the IC distribution directly.
- ``by_group`` names the column carrying per-row group labels (e.g. ``"sector"``);
  the original instead maps symbols through a dict.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats

_DATE_COL = "trade_date"
_SYM_COL = "symbol"
_FACTOR_COL = "factor"
_RET_COL = "fut_ret_1"


@dataclass(frozen=True)
class ICPanelResult:
    """Diagnostic panel for a single factor.

    Attributes:
        rolling_ic: Daily spearman IC, indexed by trade date (NaN dates dropped).
        rolling_ir: ``rolling_window`` rolling mean of IC / rolling std of IC.
        autocorr: Daily pearson autocorrelation of the ``horizon``-lagged rank.
        ic_distribution: Mean/Std/Skewness/Kurtosis/t-stat/p-Value/IR of the IC.
        qq_stats: QQ normal diagnostics — fitted slope/intercept/r_value plus a
            D'Agostino-Pearson normality statistic and p-value.
        monthly_heatmap: Sum of daily IC per (year, month), pivoted to a
            year x month frame.
        sector_ic: Per-group mean IC contribution, sorted descending.
    """

    rolling_ic: pd.Series
    rolling_ir: pd.Series
    autocorr: pd.Series
    ic_distribution: dict[str, float]
    qq_stats: dict[str, float]
    monthly_heatmap: pd.DataFrame
    sector_ic: pd.DataFrame


def _spearman_pair(x: np.ndarray, y: np.ndarray) -> float:
    """Spearman rho of two arrays over the finite intersection (source ``pl.corr``)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 2 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return np.nan
    return float(stats.spearmanr(x, y)[0])


def _pearson_pair(x: np.ndarray, y: np.ndarray) -> float:
    """Pearson r of two arrays over the finite intersection (source ``pl.corr``)."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    mask = np.isfinite(x) & np.isfinite(y)
    x, y = x[mask], y[mask]
    if len(x) < 2 or np.ptp(x) == 0 or np.ptp(y) == 0:
        return np.nan
    return float(np.corrcoef(x, y)[0, 1])


class ICPanel:
    """Computes the IC diagnostic panel for a factor over a panel of forward returns.

    Usage:
        panel = ICPanel().compute(factor_df, ret_df, horizon=1, rolling_window=60)
    """

    def compute(
        self,
        factor_df: pd.DataFrame,
        ret_df: pd.DataFrame,
        horizon: int = 1,
        rolling_window: int = 60,
        by_group: str | None = None,
        *,
        date_col: str = _DATE_COL,
        symbol_col: str = _SYM_COL,
        factor_col: str = _FACTOR_COL,
        ret_col: str = _RET_COL,
    ) -> ICPanelResult:
        """Build the diagnostic panel for ``factor_df`` against ``ret_df``.

        Args:
            factor_df: Panel with ``[date, symbol, factor]`` columns.
            ret_df: Panel with ``[date, symbol, forward_return]`` — the forward
                return must already be aligned to its factor bar (the last bar
                of each symbol typically has a NaN forward return and is dropped).
            horizon: Lag (in bars) used for the factor-rank autocorrelation.
            rolling_window: Rolling window (bars) for ``rolling_ir``.
            by_group: Optional column name in ``factor_df`` holding per-row group
                labels (e.g. ``"sector"``). When set, ``sector_ic`` is populated.

        Returns:
            An :class:`ICPanelResult`.
        """
        if horizon < 1:
            raise ValueError(f"horizon must be >= 1, got {horizon}")
        if rolling_window < 2:
            raise ValueError(f"rolling_window must be >= 2, got {rolling_window}")
        for df, name in ((factor_df, "factor_df"), (ret_df, "ret_df")):
            if not isinstance(df, pd.DataFrame):
                raise TypeError(f"{name} must be a pandas DataFrame")
        for col in (date_col, symbol_col):
            if col not in factor_df.columns or col not in ret_df.columns:
                raise ValueError(f"column {col!r} missing from input panel")
        if factor_col not in factor_df.columns:
            raise ValueError(f"factor column {factor_col!r} not in factor_df")
        if ret_col not in ret_df.columns:
            raise ValueError(f"return column {ret_col!r} not in ret_df")
        if by_group is not None and by_group not in factor_df.columns:
            raise ValueError(f"by_group column {by_group!r} not in factor_df")

        factor_cols = [date_col, symbol_col, factor_col]
        if by_group is not None:
            factor_cols = [*factor_cols, by_group]
        panel = factor_df[factor_cols].merge(
            ret_df[[date_col, symbol_col, ret_col]],
            on=[date_col, symbol_col],
            how="inner",
        )

        # --- per-date spearman IC, NaN dates dropped (source drop_nans) ---
        ic = (
            panel.groupby(date_col)
            .apply(
                lambda g: _spearman_pair(g[factor_col].to_numpy(), g[ret_col].to_numpy()),
                include_groups=False,
            )
            .dropna()
        )
        rolling_ic = ic.rename("ic")
        rolling_ic.index = pd.to_datetime(rolling_ic.index)
        rolling_ic = rolling_ic.sort_index()

        # --- autocorrelation of the horizon-lagged dense factor rank ---
        panel["_rank"] = panel.groupby(date_col)[factor_col].rank(method="dense")
        panel["_lag_rank"] = panel.groupby(symbol_col)["_rank"].shift(horizon)
        autocorr = (
            panel.groupby(date_col)
            .apply(
                lambda g: _pearson_pair(g["_rank"].to_numpy(), g["_lag_rank"].to_numpy()),
                include_groups=False,
            )
            .rename("autocorr")
        )
        autocorr.index = pd.to_datetime(autocorr.index)
        autocorr = autocorr.reindex(rolling_ic.index)

        # --- rolling IR: rolling mean / rolling std of IC ---
        rolling_ir = (
            rolling_ic.rolling(rolling_window).mean()
            / rolling_ic.rolling(rolling_window).std(ddof=1)
        ).rename("ir")

        ic_values = rolling_ic.dropna().to_numpy(dtype=float)
        ic_distribution = self._distribution(ic_values)

        return ICPanelResult(
            rolling_ic=rolling_ic,
            rolling_ir=rolling_ir,
            autocorr=autocorr,
            ic_distribution=ic_distribution,
            qq_stats=self._qq_stats(ic_values),
            monthly_heatmap=self._monthly_heatmap(rolling_ic),
            sector_ic=self._sector_ic(panel, by_group, date_col, factor_col, ret_col),
        )

    @staticmethod
    def _distribution(ic_values: np.ndarray) -> dict[str, float]:
        """Mean/Std/Skew/Kurt/t/p/IR of the IC series (source L1483-1499)."""
        mean_ic = float(np.mean(ic_values)) if len(ic_values) else np.nan
        std = float(np.nanstd(ic_values, ddof=1)) if len(ic_values) else np.nan
        skew = float(stats.skew(ic_values)) if len(ic_values) >= 3 else np.nan
        kurt = (
            float(stats.kurtosis(ic_values, fisher=True)) if len(ic_values) >= 4 else np.nan
        )
        t_val, p_val = np.nan, np.nan
        if len(ic_values) >= 2:
            try:
                t_val, p_val = stats.ttest_1samp(ic_values, 0.0, nan_policy="omit")
            except ValueError:
                pass
        ir = mean_ic / std if std and not np.isnan(std) and std != 0 else np.nan
        return {
            "Mean IC": mean_ic,
            "Std": std,
            "Skewness": skew,
            "Kurtosis": kurt,
            "t-stat": t_val,
            "p-Value": p_val,
            "IR": ir,
        }

    @staticmethod
    def _qq_stats(ic_values: np.ndarray) -> dict[str, float]:
        """QQ normal diagnostics (Selene extension to the original source)."""
        if len(ic_values) < 8:
            return {
                "slope": np.nan,
                "intercept": np.nan,
                "r_value": np.nan,
                "normality_stat": np.nan,
                "normality_p": np.nan,
            }
        slope, intercept, r_value = stats.probplot(ic_values, dist="norm")[1]
        # D'Agostino-Pearson is unreliable below ~20 observations; leave NaN.
        if len(ic_values) >= 20:
            norm_stat, norm_p = stats.normaltest(ic_values)
        else:
            norm_stat, norm_p = np.nan, np.nan
        return {
            "slope": float(slope),
            "intercept": float(intercept),
            "r_value": float(r_value),
            "normality_stat": float(norm_stat),
            "normality_p": float(norm_p),
        }

    @staticmethod
    def _monthly_heatmap(rolling_ic: pd.Series) -> pd.DataFrame:
        """Year x month pivot of per-month IC sums (source monthly_ic_sum)."""
        s = rolling_ic.dropna()
        if len(s) == 0:
            return pd.DataFrame()
        heat = pd.DataFrame({"year": s.index.year, "month": s.index.month, "ic": s.to_numpy()})
        return heat.pivot_table(index="year", columns="month", values="ic", aggfunc="sum")

    @staticmethod
    def _sector_ic(
        panel: pd.DataFrame,
        by_group: str | None,
        date_col: str,
        factor_col: str,
        ret_col: str,
    ) -> pd.DataFrame:
        """Per-group mean IC contribution weighted by stock-count share (L1439-1464)."""
        if by_group is None:
            return pd.DataFrame(columns=["group", "contrib"])
        # Row count per (date, group) mirrors the source's ``pl.count()``: it
        # includes NaN-factor rows, which feed the date-share weight.
        grp = (
            panel.groupby([date_col, by_group])
            .apply(
                lambda gg: pd.Series(
                    {
                        "_ic": _spearman_pair(
                            gg[factor_col].to_numpy(), gg[ret_col].to_numpy()
                        ),
                        "_n": len(gg),
                    }
                ),
                include_groups=False,
            )
            .reset_index()
        )
        grp = grp.dropna(subset=["_ic"])
        if grp.empty:
            return pd.DataFrame(columns=["group", "contrib"])
        grp["_weight"] = grp["_n"] / grp.groupby(date_col)["_n"].transform("sum")
        grp["_contrib"] = grp["_ic"] * grp["_weight"]
        out = grp.groupby(by_group)["_contrib"].mean().sort_values(ascending=False)
        return out.reset_index().rename(columns={by_group: "group", "_contrib": "contrib"})
