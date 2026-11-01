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
    prior_outcomes: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any] | None]:
    """Assemble the full-window input and optional HTF summary for one symbol.

    Returns ``(input_data, htf_summary_data, direction_signal)``;
    ``htf_summary_data`` is None when no HTF interval was requested or the
    HTF series was too thin. ``direction_signal`` is the program-owned
    direction call from the same bars (never inserted into ``input_data`` —
    it travels as a side channel so Stage 1's ``json.dumps(input_data)``
    prompt cannot see it). ``prior_outcomes`` (settled prior diagnosis
    outcomes, built by the caller via ``build_prior_outcome_block``) is
    threaded into the shared enrichment so both arms feed the identical shape.
    """
    from selene_space_decision_workflow.trading_pipeline import (
        build_full_input_data,
        compute_direction_signal,
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
    input_data, _overlays_hit = build_full_input_data(
        all_bars, symbol, interval, prior_outcomes=prior_outcomes
    )
    input_data["snapshot"] = {
        "requested_bars": bars,
        "bars_total": len(all_bars),
        "fetched_at": all_bars[-1]["ts"],
    }
    direction_signal = compute_direction_signal(all_bars)
    return input_data, htf_summary_data, direction_signal


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


# Terminal outcome states — a record whose ``outcome`` is one of these has a
# settled verdict to feed back; ``still_open`` never attaches an outcome.
_CLOSED_OUTCOME_STATES = frozenset({"win", "loss", "expired", "timeout_open"})


def build_prior_outcome_block(
    records: list[dict[str, Any]],
    symbol: str,
    interval: str,
    *,
    limit: int = 3,
) -> list[dict[str, Any]]:
    """Distil settled prior diagnoses into a precedent block for the prompt.

    The third leg of the W2 audit chain (diagnosis → backtest → outcome) is
    written back onto the record by ``pa_outcome``. This turns those settled
    outcomes into recallable precedent so the next diagnosis on the same
    symbol+interval can say "last time this symbol was called long, it went
    to the stop" — closing the continuous-analysis loop.

    Pure: takes already-fetched ``records`` (newest-first, same shape the
    repository returns) and reads only settled outcomes. Returns [] when
    nothing qualifies — the caller omits the key rather than injecting an
    empty block (CLI parity: an empty material block is left absent so it
    cannot dilute the prompt). Interval is matched here because the
    repository filters by symbol only, mirroring ``resolve_prior_record``.
    """
    out: list[dict[str, Any]] = []
    for record in records:
        if len(out) >= limit:
            break
        summary = (record.get("input_data") or {}).get("summary") or {}
        if summary.get("symbol") != symbol or summary.get("interval") != interval:
            continue
        outcome = record.get("outcome")
        if not isinstance(outcome, dict):
            continue
        status = outcome.get("status")
        if status not in _CLOSED_OUTCOME_STATES:
            continue
        stage2 = record.get("stage2") or {}
        nested = (
            stage2.get("diagnosis_or_decision") if isinstance(stage2, dict) else None
        )
        decision = (
            nested.get("decision")
            if isinstance(nested, dict)
            else stage2.get("decision")
        )
        order = nested.get("order") if isinstance(nested, dict) else stage2.get("order")
        direction = None
        if isinstance(order, dict):
            direction = order.get("direction") or order.get("side")
        out.append(
            {
                "decided_at": (record.get("meta") or {}).get("timestamp"),
                "decision": decision,
                "direction": direction,
                "outcome": status,
                "pnl_r": outcome.get("pnl_r"),
            }
        )
    return out


async def fetch_recent_records(
    svc: Any,
    symbol: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]] | None:
    """Newest ``symbol`` records from the record repo, or None when the repo
    is unwired or unavailable.

    ``50`` is ``list_records``' default scan window. Best-effort: a missing
    repo or any read failure yields None — the precedent block is an
    enhancement, never a prerequisite, so this must not raise. Callers that
    need several derived views (the incremental prior *and* the settled-
    outcome block) read once here and derive both, so the record repo is
    queried a single time per request.
    """
    getter = getattr(svc, "_get_record_repo", None)
    if getter is None:
        return None
    try:
        repo = await getter()
        if repo is None:
            return None
        records: list[dict[str, Any]] = await repo.list_records(
            symbol=symbol, limit=limit
        )
        return records
    except Exception:  # noqa: BLE001 - precedent must never break a diagnosis
        logger.warning("recent-record fetch failed for %s", symbol, exc_info=True)
        return None


async def fetch_prior_outcomes(
    svc: Any,
    symbol: str,
    interval: str,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Fetch this symbol's recent records and distil settled outcomes.

    Shared by both arms (heartbeat + POST /analyze-trading) so a diagnosis
    cannot differ by which arm assembled it. Best-effort: a missing repo or
    any read failure yields [] (the block is simply absent) — never raises,
    because the precedent block is an enhancement, not a prerequisite.
    """
    records = await fetch_recent_records(svc, symbol, limit=limit)
    if not records:
        return []
    return build_prior_outcome_block(records, symbol, interval)


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
