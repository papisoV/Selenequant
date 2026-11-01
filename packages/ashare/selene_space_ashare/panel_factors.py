"""Panel-level (cross-section + time-series) factor computation.

These factors need data beyond a single stock's klines (index returns,
cross-sectional ranks), so they live outside TECHNICAL_CALCULATORS and are
wired into the API layer's panel before build_composite_panel.
"""

import logging

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

TRADING_DAYS_PER_YEAR = 252


def compute_resid_vol_60d(
    panel: pd.DataFrame, index_daily: pd.DataFrame, window: int = 60
) -> pd.Series:
    """Annualized rolling residual vol of stock returns vs index returns.

    Per symbol: OLS of daily stock returns on daily index returns over a
    rolling ``window``; result is residual std * sqrt(252). Windows with
    fewer than ``window`` valid paired observations yield NaN. Dates align
    by inner join on ``date`` — both sides must share the same format
    (the API layer uses "%Y%m%d" strings on both).
    """
    out = pd.Series(np.nan, index=panel.index, dtype=float)
    if panel.empty or index_daily.empty:
        return out
    if not {"date", "symbol", "close"}.issubset(panel.columns):
        return out
    if not {"date", "close"}.issubset(index_daily.columns):
        return out

    idx = index_daily[["date", "close"]].sort_values("date")
    idx_ret = pd.Series(
        idx["close"].pct_change().to_numpy(), index=idx["date"].to_numpy()
    )
    idx_ret = idx_ret[~idx_ret.index.duplicated(keep="last")]

    work = panel[["date", "symbol", "close"]].copy()
    work["_row"] = np.arange(len(work))
    work["stk_ret"] = work.groupby("symbol")["close"].pct_change()
    work = work.merge(
        idx_ret.rename("idx_ret"), left_on="date", right_index=True, how="inner"
    )
    if work.empty:
        return out

    pieces: list[pd.Series] = []
    for _, sub in work.groupby("symbol", sort=False):
        y = sub["stk_ret"]
        x = sub["idx_ret"]
        var_y = y.rolling(window, min_periods=window).var()
        var_x = x.rolling(window, min_periods=window).var()
        cov = y.rolling(window, min_periods=window).cov(x)
        var_x_safe = var_x.where(var_x > 0)
        beta = cov / var_x_safe
        # OLS residual variance within the window: var_y - beta^2 * var_x
        resid_var = var_y - beta * cov
        resid = np.sqrt(resid_var.clip(lower=0)) * np.sqrt(TRADING_DAYS_PER_YEAR)
        pieces.append(resid)

    vals = pd.Series(np.nan, index=work.index, dtype=float)
    stacked = pd.concat(pieces)
    vals.loc[stacked.index] = stacked.to_numpy()

    out.iloc[work["_row"].to_numpy()] = vals.to_numpy()
    return out


def compute_crowding_score(panel: pd.DataFrame) -> pd.Series:
    """Cross-sectional crowding: amount percentile rank x turnover Z.

    Composite from the A-share multifactor system: stocks with extreme
    turnover vs peers AND heavy amount rank are crowded. Requires
    ``amount`` and ``turnover`` columns on the panel; missing columns
    yield an all-NaN series (warning logged), never an exception.
    """
    out = pd.Series(np.nan, index=panel.index, dtype=float)
    missing = [c for c in ("amount", "turnover") if c not in panel.columns]
    if missing:
        logger.warning(
            "crowding score: missing panel columns %s, returning NaN", missing
        )
        return out
    if panel.empty:
        return out

    amt_rank = panel.groupby("date")["amount"].rank(pct=True)

    def _zscore(s: pd.Series) -> pd.Series:
        std = s.std()
        if pd.isna(std) or std == 0:
            return s * np.nan
        return (s - s.mean()) / std

    turn_z = panel.groupby("date")["turnover"].transform(_zscore)
    return amt_rank * turn_z
