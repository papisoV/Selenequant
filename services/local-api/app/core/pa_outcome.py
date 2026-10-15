"""PA outcome review — resolve a prior trade decision against bars since.

The third leg of the W2 audit chain: the v009 record already captures the
diagnosis (stage1/stage2) and the historical recheck (backtest_verification,
T2a); this module closes the loop by asking what the market *actually did*
to the last trade decision (T3a). Runs inside the heartbeat round before the
fresh diagnosis attempt, so a backfill costs no LLM spend and never consumes
budget.

The SL/TP touch scan is delegated to ``setup_backtest._resolve_setup`` so
the outcome verdict and the historical simulation can never drift apart
(same conservative same-bar ordering: long checks SL first, short checks SL
first).

State machine (Architect D4):

- ``win`` / ``loss`` — SL or TP touched after entry triggered; ``pnl_r`` is
  +RR / -1 computed from the decision's own risk/reward.
- ``expired`` — entry never triggered within the review window: the setup
  simply never came. Terminal; ``pnl_r`` is None (no position ever opened).
- ``timeout_open`` — entry triggered but neither SL nor TP hit within the
  window. Terminal once the window has fully elapsed; ``pnl_r`` is None.
- ``still_open`` — window not yet fully elapsed. Nothing is written; the
  next heartbeat re-examines with a longer bar history.

Honest-refusal clause (D4 gap rule): when the decision timestamp predates
the earliest loaded bar, the bars immediately following the decision are not
in hand (the 120-bar window may have moved past the decision) — no verdict
is forced and nothing is written. Marking such a record ``expired`` would be
a fabricated fact, not an audit.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from selene_space_market_analysis.setup_backtest import (
    _resolve_setup,
    _rr_of,
)

logger = logging.getLogger(__name__)

# Review window: bars of runway given to the setup before calling it dead
# (entry never triggered → expired; entry triggered but neither exit hit →
# timeout_open). 20 bars ≈ the 20×interval horizon of the continuity rules.
_ENTRY_WINDOW_BARS = 20

# How many bars to load for the review — comfortably more than the entry
# window so a "still open" verdict is honest (the window truly has not
# elapsed yet, rather than being cut short by the load).
_REVIEW_BARS = 120

_OUTCOME_SOURCE = "heartbeat_review"


def _parse_ts(raw: Any) -> datetime | None:
    """RecordMeta.timestamp is an ISO-8601 string with timezone."""
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        return None


def _order_of(record: dict[str, Any]) -> dict[str, Any] | None:
    """The prior Stage2 trade order, if any.

    Real trading records carry the full Stage2 dump under
    ``stage2.diagnosis_or_decision`` (orchestrator.py:153); legacy/market
    records keep a flat ``stage2`` dict. Read both shapes. ``no_trade`` and
    records without a decision never qualify — there is nothing to resolve.
    """
    stage2 = record.get("stage2")
    if not isinstance(stage2, dict):
        return None
    nested = stage2.get("diagnosis_or_decision")
    decision = (
        nested.get("decision") if isinstance(nested, dict) else stage2.get("decision")
    )
    if decision in (None, "no_trade"):
        return None
    order = nested.get("order") if isinstance(nested, dict) else stage2.get("order")
    if not isinstance(order, dict):
        return None
    return order


def _wants_review(record: dict[str, Any]) -> bool:
    """A trade decision that has no outcome yet — the review target."""
    if record.get("outcome") is not None:
        return False
    return _order_of(record) is not None


def judge_outcome(
    record: dict[str, Any], bars: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Resolve one prior trade record against post-decision bars.

    Returns the outcome payload to attach, or None when no honest verdict
    is possible yet (still open / bar-coverage gap / unreadable meta). Pure
    function — no I/O, fully unit-testable.
    """
    order = _order_of(record)
    if order is None or not bars:
        return None
    decided_at = _parse_ts((record.get("meta") or {}).get("timestamp"))
    if decided_at is None:
        return None

    entry = order.get("entry")
    sl = order.get("stop_loss") or order.get("stop_price")
    tp = order.get("take_profit")
    direction = order.get("direction") or order.get("side")
    if not (
        isinstance(entry, (int, float))
        and isinstance(sl, (int, float))
        and isinstance(tp, (int, float))
    ):
        return None
    if direction not in ("long", "short"):
        return None

    decided_ms = decided_at.timestamp() * 1000.0
    # Honest-refusal (D4 gap rule): the decision must sit just before the
    # loaded window (the previous round's decision, bars since = window).
    # A decision far older than the first bar means the SL/TP outcome may
    # have played out entirely in bars we do not hold — forcing "expired"
    # there would fabricate a fact. Tolerance 1.5 bar-intervals lets the
    # natural "decided at the previous window's last bar close" case pass.
    first_ts = bars[0].get("ts")
    second_ts = bars[1].get("ts") if len(bars) > 1 else None
    if not isinstance(first_ts, (int, float)):
        return None
    step = (
        second_ts - first_ts
        if isinstance(second_ts, (int, float)) and second_ts > first_ts
        else 3_600_000.0
    )
    if decided_ms < first_ts - 1.5 * step:
        return None
    start = next((i for i, b in enumerate(bars) if _is_after(b, decided_ms)), None)
    if start is None:
        return None  # decision newer than every bar — bars not in yet
    elapsed_bars = len(bars) - start
    window_elapsed = elapsed_bars > _ENTRY_WINDOW_BARS

    highs = [float(b["high"]) for b in bars]
    lows = [float(b["low"]) for b in bars]
    # Same semantics as the simulation: the setup begins when price first
    # touches the entry level; SL/TP are then scanned from that bar on.
    triggered_idx = next(
        (j for j in range(start, len(bars)) if lows[j] <= float(entry) <= highs[j]),
        None,
    )
    if triggered_idx is None:
        if not window_elapsed:
            return None  # entry window not over — still open, next round re-sees
        return {
            "status": "expired",
            "pnl_r": None,
            "entry_triggered": False,
            "exit_ts": None,
            "source": _OUTCOME_SOURCE,
        }

    resolved, exit_idx = _resolve_setup(
        highs, lows, triggered_idx, float(sl), float(tp), direction
    )
    if resolved is not None:
        rr = _rr_of(float(entry), float(sl), float(tp))
        exit_ts = bars[exit_idx].get("ts") if exit_idx is not None else None
        return {
            "status": resolved,
            "pnl_r": rr if resolved == "win" else -1.0,
            "entry_triggered": True,
            "exit_ts": exit_ts,
            "source": _OUTCOME_SOURCE,
        }
    if not window_elapsed:
        return None  # position live, window not over — still open
    return {
        "status": "timeout_open",
        "pnl_r": None,
        "entry_triggered": True,
        "exit_ts": None,
        "source": _OUTCOME_SOURCE,
    }


def _is_after(bar: dict[str, Any], decided_ms: float) -> bool:
    ts = bar.get("ts")
    return isinstance(ts, (int, float)) and ts > decided_ms


async def review_prior_decision(
    svc: Any,
    symbol: str,
    interval: str,
    *,
    market_type: str = "futures",
    klines_db: Any = None,
) -> str | None:
    """Find the latest reviewable prior record for ``symbol`` and attach
    its outcome. Returns the backfilled input_id, or None (nothing to do /
    no honest verdict yet / attach lost the optimistic-lock race — all
    silent, the next round retries idempotently). Zero LLM spend.
    """
    getter = getattr(svc, "_get_record_repo", None)
    if getter is None:
        return None
    record_repo = await getter()
    if record_repo is None or klines_db is None:
        return None
    try:
        records = await record_repo.list_records(symbol=symbol, limit=10)
    except Exception:  # noqa: BLE001 - review must never break the round
        logger.warning("PA outcome review: list failed for %s", symbol, exc_info=True)
        return None

    prior = next((r for r in records if _wants_review(r)), None)
    if prior is None:
        return None
    input_id = (prior.get("meta") or {}).get("input_id")
    if not isinstance(input_id, str) or not input_id:
        return None

    try:
        from selene_space_decision_workflow.trading_pipeline import load_bars

        bars = await asyncio.to_thread(
            load_bars,
            klines_db,
            symbol,
            interval,
            _REVIEW_BARS,
            market_type=market_type,
            min_bars=1,
        )
    except Exception:  # noqa: BLE001
        logger.warning(
            "PA outcome review: bars unavailable for %s", symbol, exc_info=True
        )
        return None

    payload = judge_outcome(prior, bars)
    if payload is None:
        return None
    payload["closed_at"] = datetime.now().astimezone().isoformat()
    try:
        attached = await record_repo.append_payload(input_id, "outcome", payload)
    except Exception:  # noqa: BLE001
        logger.warning(
            "PA outcome review: attach failed for %s", input_id, exc_info=True
        )
        return None
    if not attached:
        return None  # optimistic-lock race — retried next round
    logger.info(
        "PA outcome backfilled %s: input_id=%s status=%s",
        symbol,
        input_id,
        payload["status"],
    )
    return input_id


__all__ = [
    "judge_outcome",
    "review_prior_decision",
]
