"""Bridge from Serenity research scores to alpha-core factor pipeline.

Produces pandas Series that can be registered in ALL_FACTORS and
consumed by PandasStackVM / Optuna formula search.

ser_* factors are LIVE-ONLY: they require SERENITY_* columns merged into the
DataFrame via merge_serenity_into_df() (populated from live scans). Feeding a
bare kline DataFrame silently returned constant neutral scores (0.5 / 0.0),
which polluted historical backtests with fake-neutral signals that looked real
but carried no information. The compute functions now raise when their
SERENITY_* column is absent instead.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .models import SerenityScore
from .pool import SerenityPoolScan

# ser_* factors must not be fed into historical backtests — Serenity scores are
# point-in-time live measurements with no persisted time series to backfill with.
SERENITY_LIVE_ONLY = True


class SerenityDataMissingError(RuntimeError):
    """Raised when a ser_* factor is computed without its SERENITY_* column.

    Serenity scores are live-only: merge them into the DataFrame via
    merge_serenity_into_df() before calling the factor functions. A bare kline
    DataFrame has no SERENITY_* columns, so returning a constant (the old
    silent 0.5/0.0 fallback) fabricated a neutral-looking factor.
    """


def _require_column(
    data: pd.DataFrame, col: str, hint: str = "merge_serenity_into_df()"
) -> pd.Series:
    if col not in data.columns:
        raise SerenityDataMissingError(
            f"ser_* factor requires column {col!r} in the DataFrame; "
            "Serenity scores are live-only (SERENITY_LIVE_ONLY) — merge them "
            f"via {hint} before computing factors."
        )
    return data[col]


def compute_serenity_score(data: pd.DataFrame) -> pd.Series:
    """Serenity composite normalized score (0..1, live-only)."""
    return _require_column(data, "SERENITY_SCORE").fillna(0.5)


def compute_serenity_consensus(data: pd.DataFrame) -> pd.Series:
    """Consensus scarcity factor from Serenity (live-only)."""
    return _require_column(data, "SERENITY_CONSENSUS").fillna(0.5)


def compute_serenity_liquidity(data: pd.DataFrame) -> pd.Series:
    """Liquidity scarcity factor (TVL/FDV based, live-only)."""
    return _require_column(data, "SERENITY_LIQUIDITY").fillna(0.5)


def compute_serenity_preallocation(data: pd.DataFrame) -> pd.Series:
    """Prepayment/staking signal from Serenity (live-only)."""
    return _require_column(data, "SERENITY_PREPAY").fillna(0.5)


def compute_serenity_evidence(data: pd.DataFrame) -> pd.Series:
    """Evidence strength factor from Serenity (live-only)."""
    return _require_column(data, "SERENITY_EVIDENCE").fillna(0.5)


def compute_serenity_penalty(data: pd.DataFrame) -> pd.Series:
    """Composite penalty factor from Serenity (0=none, 1=severe, live-only)."""
    return _require_column(data, "SERENITY_PENALTY").fillna(0.0)


SERENITY_FACTORS: dict[str, tuple[object, str]] = {
    "ser_score": (compute_serenity_score, "Serenity composite score (live-only)"),
    "ser_consensus": (compute_serenity_consensus, "Consensus scarcity (live-only)"),
    "ser_liquidity": (compute_serenity_liquidity, "Liquidity scarcity TVL/FDV (live-only)"),
    "ser_prepay": (compute_serenity_preallocation, "Prepayment/staking signal (live-only)"),
    "ser_evidence": (compute_serenity_evidence, "Evidence strength (live-only)"),
    "ser_penalty": (compute_serenity_penalty, "Composite penalty (live-only)"),
}


def _to_datetime(values: pd.Series) -> pd.Series:
    """Normalize epoch-seconds / epoch-ms / datetime series to datetime64.

    Kline frames carry epoch-ms timestamps; SerenityScore.timestamp is epoch
    seconds. merge_asof requires matching key dtypes, so both sides are
    normalized before the join.
    """
    if pd.api.types.is_datetime64_any_dtype(values.dtype):
        return values
    if pd.api.types.is_integer_dtype(values.dtype):
        unit = "ms" if values.abs().max() > 1e11 else "s"
        return pd.to_datetime(values, unit=unit)
    return pd.to_datetime(values)


def merge_serenity_into_df(
    df: pd.DataFrame,
    scores: list[SerenityScore],
    timestamp_col: str = "timestamp",
) -> pd.DataFrame:
    """Merge Serenity scores into a kline DataFrame using merge_asof.

    Serenity data is low-frequency (5 min to 1 hr),
    so backward merge (most recent score before kline) is correct.

    Both timestamp sides are normalized to datetime64 (epoch seconds, epoch ms,
    and datetime inputs are all supported) so the asof join aligns temporally.
    """
    serenity_cols = [
        "SERENITY_SCORE", "SERENITY_CONSENSUS", "SERENITY_LIQUIDITY",
        "SERENITY_PREPAY", "SERENITY_EVIDENCE", "SERENITY_PENALTY",
    ]

    out = df.copy()

    if not scores:
        for col in serenity_cols:
            out[col] = np.nan
        return out

    score_df = pd.DataFrame([
        {
            "timestamp": s.timestamp,
            "SERENITY_SCORE": s.normalized_score,
            "SERENITY_CONSENSUS": s.consensus_scarcity,
            "SERENITY_LIQUIDITY": s.liquidity_scarcity,
            "SERENITY_PREPAY": s.prepayment_signal,
            "SERENITY_EVIDENCE": s.evidence_strength,
            "SERENITY_PENALTY": (
                s.penalty_fork_risk + s.penalty_tvl_concentration
                + s.penalty_dev_exodus + s.penalty_revenue_decline
                + s.penalty_oi_divergence + s.penalty_whale_exodus
                + s.penalty_governance_risk + s.penalty_liquidity_trap
            ) / 8.0,
        }
        for s in scores
    ])

    out = out.assign(**{timestamp_col: _to_datetime(out[timestamp_col])})
    score_df["timestamp"] = _to_datetime(score_df["timestamp"])

    score_df = score_df.sort_values("timestamp")
    out = pd.merge_asof(
        out.sort_values("timestamp"),
        score_df,
        on="timestamp",
        direction="backward",
    )
    return out


# ---------------------------------------------------------------------------
# Theme-pool rank seam (F4 — dead until a live caller wires it)
# ---------------------------------------------------------------------------


def compute_serenity_pool_rank(data: pd.DataFrame) -> pd.Series:
    """Serenity theme-pool rank factor — top-of-pool → 1.0 (live-only).

    Rows whose symbol is not in the scanned pool carry a NaN rank and resolve
    to the neutral 0.5, consistent with the other ser_* factors.
    """
    rank = _require_column(
        data, "SERENITY_POOL_RANK", hint="merge_pool_rank_into_df()"
    )
    size = _require_column(
        data, "SERENITY_POOL_SIZE", hint="merge_pool_rank_into_df()"
    )
    score = 1.0 - (rank - 1.0) / size
    return score.where(rank.notna(), 0.5)


def merge_pool_rank_into_df(
    df: pd.DataFrame,
    scan: SerenityPoolScan | None,
    symbol_col: str = "symbol",
) -> pd.DataFrame:
    """Merge a theme pool scan into a DataFrame by symbol (live-only).

    Adds SERENITY_POOL_RANK (1-based position within the theme pool) and
    SERENITY_POOL_SIZE (pool size). Rows whose symbol is not in the pool get a
    NaN rank (the factor resolves them to neutral 0.5). Unlike
    merge_serenity_into_df this is a static snapshot lookup, not a time join —
    a pool scan carries one ranking, not per-row timestamps.

    This is a dead seam (F4): nothing feeds the pool ranking into the live
    factor pipeline yet. A bare kline DataFrame still raises
    SerenityDataMissingError from compute_serenity_pool_rank.
    """
    out = df.copy()
    if scan is None:
        out["SERENITY_POOL_RANK"] = np.nan
        out["SERENITY_POOL_SIZE"] = np.nan
        return out

    size = len(scan.ranked)
    rank_by_symbol = {c.symbol: i for i, c in enumerate(scan.ranked, 1)}
    col = out[symbol_col].astype(str).str.upper()
    out["SERENITY_POOL_RANK"] = col.map(rank_by_symbol).astype("float64")
    out["SERENITY_POOL_SIZE"] = float(size) if size else np.nan
    return out


SERENITY_POOL_FACTORS: dict[str, tuple[object, str]] = {
    "ser_theme_rank": (
        compute_serenity_pool_rank,
        "Serenity theme-pool rank, top-of-pool → 1.0 (live-only, dead seam until wired)",
    ),
}
