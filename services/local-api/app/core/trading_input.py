"""Shared trading-input assembly for the PA two-stage pipeline.

POST /analyze-trading and the PA heartbeat (SELENE_PA_HEARTBEAT) must feed the
workflow the *identical* input shape — otherwise an automatic diagnosis would
silently diverge from the one the panel shows for the same symbol. The
assembly rules therefore live here, shared by both callers:

- bars come from the local klines.db (never the network)
- the higher-timeframe summary degrades to "omitted" below 50 bars (CLI
  parity) instead of aborting the primary diagnosis
- ``snapshot`` records what the model actually saw

Callers own their error policy: ``load_bars`` raises
``RuntimeError("insufficient bars…")`` for a thin series — the endpoint maps
that to 422, the heartbeat skips the symbol and moves on.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Below this many higher-timeframe bars the HTF context is omitted rather
# than fed as a misleadingly thin series (CLI parity: run_pa_diagnosis B2).
_MIN_HTF_BARS = 50


def build_trading_input(
    klines_db: Path,
    symbol: str,
    interval: str,
    *,
    bars: int,
    market_type: str = "futures",
    htf_interval: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Assemble the full-window input and optional HTF summary for one symbol.

    Returns ``(input_data, htf_summary_data)``; ``htf_summary_data`` is None
    when no HTF interval was requested or the HTF series was too thin.
    """
    from selene_space_decision_workflow.trading_pipeline import (
        build_full_input_data,
        load_bars,
    )

    all_bars = load_bars(klines_db, symbol, interval, bars, market_type=market_type)
    htf_summary_data = build_htf_summary(
        klines_db,
        symbol,
        bars=bars,
        market_type=market_type,
        htf_interval=htf_interval,
    )
    input_data, _overlays_hit = build_full_input_data(all_bars, symbol, interval)
    input_data["snapshot"] = {
        "requested_bars": bars,
        "bars_total": len(all_bars),
        "fetched_at": all_bars[-1]["ts"],
    }
    return input_data, htf_summary_data


def build_htf_summary(
    klines_db: Path,
    symbol: str,
    *,
    bars: int,
    market_type: str = "futures",
    htf_interval: str | None = None,
) -> dict[str, Any] | None:
    """Aggregate the optional higher-timeframe context, or None.

    Shared by the full-window and incremental assemblies so a diagnosis
    cannot differ by which branch built it. A thin HTF series degrades to
    "omitted" (CLI parity: run_pa_diagnosis B2) rather than aborting the
    primary diagnosis.
    """
    if not htf_interval:
        return None
    from selene_space_decision_workflow.trading_pipeline import htf_summary, load_bars

    htf_bars = load_bars(
        klines_db,
        symbol,
        htf_interval,
        bars,
        market_type=market_type,
        min_bars=0,
    )
    if len(htf_bars) >= _MIN_HTF_BARS:
        # Bind first: the pipeline helper is untyped, and returning its Any
        # straight out of an annotated signature trips mypy's no-any-return.
        summary: dict[str, Any] = htf_summary(htf_bars, symbol, htf_interval)
        return summary
    logger.warning(
        "htf_warning: insufficient %s bars for %s: %d < %d — HTF context omitted",
        htf_interval,
        symbol,
        len(htf_bars),
        _MIN_HTF_BARS,
    )
    return None


def resolve_prior_record(
    records: list[dict[str, Any]],
    symbol: str,
    interval: str,
) -> dict[str, Any] | None:
    """Newest record usable as an incremental prior for symbol/interval.

    Deliberately NOT the outcome-review predicate (``pa_outcome._wants_review``
    needs an order and no outcome): an incremental diagnosis continues
    whatever the last diagnosis concluded, whether or not its outcome has
    since been judged.

    ``records`` arrive newest-first and are filtered by symbol only at the
    repository (the table has an interval column but ``list_records`` does
    not query it), so the interval match happens here.
    """
    for record in records:
        summary = (record.get("input_data") or {}).get("summary") or {}
        if summary.get("symbol") != symbol or summary.get("interval") != interval:
            continue
        if not (record.get("stage1") or {}).get("diagnosis_or_decision"):
            continue
        return record
    return None


def latest_bar_snapshot(
    klines_db: Path,
    symbol: str,
    interval: str,
    market_type: str = "futures",
) -> dict[str, Any] | None:
    """Read the single newest bar for one symbol/interval (W3 hybrid follow-up).

    The follow-up endpoint assembles ``live_context`` from this so the model
    can answer "what about *now*" without pretending the frozen record saw it.
    Returns None when the db is missing or the series is empty — the caller
    degrades to the existing fully-anchored prompt, never raises.
    """
    if not klines_db.exists():
        return None
    import sqlite3

    try:
        db = sqlite3.connect(f"file:{klines_db}?mode=ro", uri=True)
        try:
            row = db.execute(
                """
                SELECT timestamp, close
                FROM klines
                WHERE symbol = ? AND interval = ? AND exchange = 'binance'
                  AND market_type = ?
                ORDER BY timestamp DESC LIMIT 1
                """,
                (symbol, interval, market_type),
            ).fetchone()
        finally:
            db.close()
    except sqlite3.Error:
        logger.warning(
            "latest_bar_snapshot: failed reading %s for %s %s",
            klines_db,
            symbol,
            interval,
        )
        return None
    if row is None:
        return None
    return {"last_bar_ts": int(row[0]), "last_close": float(row[1])}
