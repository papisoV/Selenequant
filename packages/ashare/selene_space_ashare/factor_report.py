"""Factor self-evidence report: IC/quantile/correlation for all factors.

batch4 US-3. One-shot panel builder + report generator — evaluates both
technical and fundamental factors on real cached data and answers "is this
factor actually predictive?" with IC_mean/IR/t-stat, quantile spread and
cross-factor correlation. CLI entry: `ashare report`.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import numpy as np
import pandas as pd

from selene_space_ashare.factor_calc import (
    SIZE_KLINE_COLS,
    calculate_technical_factors,
    derive_size_factor,
)
from selene_space_ashare.factor_eval import (
    calc_forward_returns,
    factor_correlation_matrix,
    factor_evaluation_report,
    orthogonalize_greedy,
    quantile_returns,
)

from selene_space_ashare.fundamentals_pit import FUND_COLS, attach_fundamentals

logger = logging.getLogger(__name__)

PASSTHROUGH_FUND_FACTORS: dict[str, str] = {
    # factor column -> PIT-merged financial column (EP excluded: derived
    # epsTTM/close, not a passthrough — see _attach_and_derive)
    "YOY_NI": "YOYNI",
    "ROE": "roeAvg",
    "GP_MARGIN": "gpMargin",
    "LEVERAGE": "liabilityToAsset",
    "CURRENT_RATIO": "currentRatio",
}


def _attach_and_derive(panel: pd.DataFrame, financials: list[dict]) -> list[str]:
    """Attach PIT financials and derive fundamental factor columns in place,
    mirroring the API's fund_candidates pattern (ashare.py)."""
    merged = attach_fundamentals(panel, financials)
    for col in FUND_COLS:
        panel[col] = merged[col].values

    candidates: list[str] = []
    if panel["epsTTM"].notna().any():
        panel["EP"] = panel["epsTTM"] / panel["close"]
        candidates.append("EP")
    if panel["BPS"].notna().any():
        # BP = BPS/close — same quantity as the 1/pbMRQ branch in ashare.py
        panel["BP"] = panel["BPS"] / panel["close"]
        candidates.append("BP")
    for factor, src in PASSTHROUGH_FUND_FACTORS.items():
        if panel[src].notna().any():
            panel[factor] = panel[src]
            candidates.append(factor)
    # SIZE from EM daily float market cap (default on since batch8; set
    # SELENE_SIZE_DIRECT=0 to fall back to kline-derived). Hit rows get
    # ln(float_mv); miss rows stay NaN — direct and derived values are never
    # mixed on one symbol's timeline (ADR-4). <95% symbol coverage → the
    # whole batch falls back to derived.
    if os.environ.get("SELENE_SIZE_DIRECT", "") != "0":
        from selene_space_ashare.data_fetcher import fetch_market_cap

        try:
            mcap = fetch_market_cap(
                symbols=sorted(panel["symbol"].unique().tolist()),
                start_date=panel["date"].min().strftime("%Y%m%d"),
                end_date=panel["date"].max().strftime("%Y%m%d"),
            )
        except Exception:
            logger.warning("fetch_market_cap failed, SIZE falls back to derived")
            mcap = pd.DataFrame()
        if not mcap.empty:
            mcap = mcap.rename(columns={"float_mv": "_float_mv"})
            mcap["date"] = pd.to_datetime(mcap["date"])
            lookup = mcap.dropna(subset=["_float_mv"]).set_index(["symbol", "date"])[
                "_float_mv"
            ]
            lookup = np.log(lookup.where(lookup > 0))
            mi = pd.MultiIndex.from_arrays([panel["symbol"], panel["date"]])
            direct = pd.Series(lookup.reindex(mi).to_numpy(), index=panel.index)
            hit = direct.notna()
            sym_total = panel["symbol"].nunique()
            sym_hit = panel.loc[hit, "symbol"].nunique()
            if sym_total and sym_hit / sym_total >= 0.95:
                # column assignment keeps the in-place contract with callers
                panel["SIZE"] = direct
                candidates.append("SIZE")
                logger.info(
                    "SIZE direct: %d/%d rows hit (%d/%d symbols)",
                    int(hit.sum()),
                    len(panel),
                    sym_hit,
                    sym_total,
                )
            else:
                logger.warning(
                    "SIZE direct hit rate %d/%d symbols <95%% — whole batch falls back to derived",
                    sym_hit,
                    sym_total,
                )
    if (
        "SIZE" not in panel.columns
        and "turnover" in panel.columns
        and panel["turnover"].notna().any()
    ):
        panel["SIZE"] = (
            panel.groupby("symbol", group_keys=False)[SIZE_KLINE_COLS]
            .apply(lambda g: derive_size_factor(g))
            .rename("SIZE")
        )
        candidates.append("SIZE")
    return candidates


def build_factor_panel(
    symbols: list[str],
    start_date: str = "20250101",
    end_date: str = "",
    klines_fetch: Any = None,
    include_industry: bool = True,
) -> pd.DataFrame:
    """Build a cross-sectional panel with technical + fundamental factors.

    Args:
        symbols: 6-digit codes
        start_date: YYYYMMDD
        end_date: YYYYMMDD (default: today)
        klines_fetch: injectable kline loader (symbol) -> DataFrame for tests;
            defaults to data_fetcher.get_daily_klines (parquet-backed cache)
    """
    from selene_space_ashare.data_fetcher import get_daily_klines

    fetch = klines_fetch or (
        lambda sym: get_daily_klines(sym, start_date=start_date, end_date=end_date)
    )

    frames: list[pd.DataFrame] = []
    for sym in symbols:
        k = fetch(sym)
        if k is None or k.empty or len(k) < 60:
            logger.warning("skip %s: insufficient klines", sym)
            continue
        tech = calculate_technical_factors(k)
        tech["symbol"] = sym
        # carry raw kline columns through: fundamental/size derivation and
        # forward returns all need them (calculate_technical_factors drops
        # everything except date + factor columns)
        for col in ("open", "high", "low", "close", "volume", "turnover", "amount"):
            if col in k.columns:
                tech[col] = k[col].values
        frames.append(tech)
    if not frames:
        raise ValueError("no usable kline data for any symbol")

    panel = pd.concat(frames, ignore_index=True)
    panel["date"] = pd.to_datetime(panel["date"])

    from selene_space_ashare.fundamentals_pit import load_financials

    financials = load_financials()
    if financials:
        _attach_and_derive(panel, financials)
    else:
        logger.warning(
            "no financials cache — fundamental factors absent from panel "
            "(run /ashare/fundamentals-backfill)"
        )

    if include_industry:
        from selene_space_ashare.data_fetcher import fetch_industry_map

        try:
            industry_map = fetch_industry_map()
        except Exception:
            logger.warning("industry map fetch failed, panel has no industry col")
            industry_map = pd.DataFrame(columns=["symbol", "industry"])
        if not industry_map.empty:
            # string col: excluded from factor auto-detect (numeric check),
            # NaN rows skipped inside neutralize_cross_section
            panel = panel.merge(industry_map, on="symbol", how="left")

    for p in (5, 21):
        fwd = panel.groupby("symbol")["close"].transform(
            lambda s: calc_forward_returns(s, [p])["fwd_%dd" % p]
        )
        panel["fwd_%dd" % p] = fwd
    return panel


def generate_factor_report(
    panel: pd.DataFrame,
    factors: list[str] | None = None,
    forward_col: str = "fwd_5d",
    neutralize: bool = True,
) -> dict[str, Any]:
    """IC report + quantile returns + correlation for the given factors.

    factors=None → every non-fwd/non-ident column in the panel.
    Returns a json.dumps-able dict.
    """
    skip = {
        "date",
        "symbol",
        "close",
        "open",
        "high",
        "low",
        "volume",
        "amount",
        "turnover",
        "code",
        # raw PIT financial fields (factors are the derived columns above)
        *FUND_COLS,
    }
    if factors is None:
        factors = [
            c
            for c in panel.columns
            if c not in skip
            and not c.startswith("fwd_")
            and panel[c].notna().any()
            and pd.api.types.is_numeric_dtype(panel[c])
        ]

    report_df = factor_evaluation_report(panel, factors, forward_col=forward_col)
    report_df = report_df.replace([np.inf, -np.inf], np.nan)

    # size_col must stay None: neutralize_cross_section expects raw market cap
    # (takes np.log internally) — panel SIZE is already ln, passing it would
    # double-log (factor_eval.py:77-78/:99). Raw IC stats above stay untouched;
    # neutralized stats merge in as *_neutral columns.
    if neutralize and "industry" in panel.columns:
        neut_df = factor_evaluation_report(
            panel,
            factors,
            forward_col=forward_col,
            neutralize=True,
            industry_col="industry",
            size_col=None,
        )
        neut_df = neut_df.replace([np.inf, -np.inf], np.nan)
        report_df = report_df.merge(
            neut_df, on="factor", suffixes=("", "_neutral"), how="left"
        )

    quantiles: dict[str, Any] = {}
    for name in factors:
        qr = quantile_returns(panel, name, forward_col=forward_col, n_quantiles=5)
        quantiles[name] = [] if qr.empty else qr.to_dict(orient="records")

    corr_df = factor_correlation_matrix(panel, factors)
    corr_json = corr_df.round(4).where(corr_df.notna(), None).to_dict()

    orthogonal: dict[str, Any] | None = None
    if len(factors) >= 3:
        orthogonal = orthogonalize_greedy(panel, factors, forward_col=forward_col)

    return {
        "forward_col": forward_col,
        "n_days": int(panel["date"].nunique()),
        "n_symbols": int(panel["symbol"].nunique()),
        "ic_report": report_df.round(4)
        .where(report_df.notna(), None)
        .to_dict(orient="records"),
        "quantile_returns": quantiles,
        "factor_correlation": corr_json,
        "orthogonal": orthogonal,
    }
