"""Counterfactual scoring for records that never produced an order (B2-a).

The outcome loop (``pa_outcome.judge_outcome``) can only settle records that
carry an ``order`` — gate-blocked and Stage-2 ``wait`` decisions are therefore
permanently unscored, which is exactly the population a confidence threshold
would block. This module scores the *counterfactual*: what the record's own
forward-looking judgment said, against what the market actually did afterwards.

Estimand: ``stage1.diagnosis_or_decision.next_period.direction`` (the Stage-1
judgment is present on gated records too — measured 2026-09-17, 4 of 7
persisted decision-workflow records carry it) against the realised forward
close change over a fixed horizon. A direction hits when its sign agrees with
the realised move; ``neutral`` is recorded but not scored (it asserts no
directional edge). When the judgment carries an entry suggestion the horizon
is still the plain close-over-close move — no execution assumptions are
invented here.

Pure, no I/O: bars are supplied by the caller (``ts/close`` dicts, the same
keys ``load_bars`` emits and ``judge_outcome`` consumes). Missing or thin bars
yield ``None`` verdicts — never a fabricated 0. Companion replay CLI:
``scripts/eval_counterfactual.py``.
"""

from __future__ import annotations

from typing import Any

# Direction vocabulary seen in the wild plus conservative aliases. The
# contract text (trading_pipeline.py next_period clause) names the field but
# the vocabulary is LLM-chosen, so the scorer accepts both the neutral/
# long/short observed in real records and the up/down family from the
# eval_next_bar precedent. Unknown strings are not scored — they are reported
# as unscorable rather than guessed.
_BULLISH = frozenset({"up", "long"})
_BEARISH = frozenset({"down", "short"})
_NEUTRAL = frozenset({"neutral", "flat"})
_KNOWN_DIRECTIONS = _BULLISH | _BEARISH | _NEUTRAL


def next_period_of(record: dict[str, Any]) -> dict[str, Any] | None:
    """The Stage-1 ``next_period`` payload, or None.

    Persisted path is ``stage1.diagnosis_or_decision.next_period`` — the
    orchestrator stores ``diagnosis_or_decision=stage1.diagnosis_summary``
    (orchestrator.py:143), and the Stage-1 LLM writes ``next_period`` inside
    that summary. Measured 2026-09-17: present on 4/7 DW records, including
    gate short-circuits. A Stage-2 fallback
    (``stage2...decision_summary.next_period``) exists for future records
    where Stage 2 emits its own; Stage 1 wins when both are present.
    """
    for stage_key in ("stage1", "stage2"):
        stage = record.get(stage_key)
        if not isinstance(stage, dict):
            continue
        payload = stage.get("diagnosis_or_decision")
        if not isinstance(payload, dict):
            continue
        source = payload.get("decision_summary") if stage_key == "stage2" else payload
        if not isinstance(source, dict):
            continue
        np_ = source.get("next_period")
        if isinstance(np_, dict):
            return np_
    return None


def direction_of(next_period: dict[str, Any]) -> str | None:
    """Normalised direction token, or None when absent/unscorable."""
    value = next_period.get("direction")
    if not isinstance(value, str):
        return None
    value = value.strip().lower()
    return value if value in _KNOWN_DIRECTIONS else None


def forward_close(bars: list[dict[str, Any]], *, horizon: int) -> float | None:
    """The close of the ``horizon``-th bar after the decision window.

    ``bars`` must be the *forward* series (strictly after the decision's last
    bar, oldest first — what ``load_bars(after_ms=...)`` returns). Returns
    None when fewer than ``horizon`` bars exist: the honest verdict is "not
    yet settled", never a 0.
    """
    if horizon < 1 or len(bars) < horizon:
        return None
    close = bars[horizon - 1].get("close")
    if not isinstance(close, (int, float)) or isinstance(close, bool):
        return None
    return float(close)


def score_record(
    record: dict[str, Any],
    forward_bars: list[dict[str, Any]],
    *,
    horizon: int = 24,
) -> dict[str, Any] | None:
    """Score one record's next_period.direction against realised forward bars.

    Returns a verdict dict, or None when the record carries no next_period at
    all (not in the counterfactual population). A verdict is always returned
    (never raises) for records *in* the population, even when bars are
    missing — ``settled`` then says False and ``pnl_pct`` is None.

    Verdict shape:
        direction    — normalised token (up/long/down/short/neutral)
        decision_close — the record's own last_close (the anchor)
        settled_close  — close of the horizon-th forward bar (None when unset)
        horizon      — bars used
        pnl_pct      — signed return anchor→settled (None until settled)
        hit          — True/False once settled; neutral is never a hit or miss
        settled      — bars existed for the full horizon
    """
    np_ = next_period_of(record)
    if np_ is None:
        return None
    direction = direction_of(np_)
    if direction is None:
        return {
            "direction": None,
            "decision_close": _anchor_of(record),
            "settled_close": None,
            "horizon": horizon,
            "pnl_pct": None,
            "hit": None,
            "settled": False,
            "unscorable": True,
        }

    anchor = _anchor_of(record)
    settled = forward_close(forward_bars, horizon=horizon)
    if settled is None or anchor is None:
        return {
            "direction": direction,
            "decision_close": anchor,
            "settled_close": settled,
            "horizon": horizon,
            "pnl_pct": None,
            "hit": None,
            "settled": False,
        }

    pnl_pct = (settled - anchor) / anchor if anchor != 0 else None
    if direction in _NEUTRAL:
        hit = None  # neutral asserts no edge: counted, not scored
    elif direction in _BULLISH:
        hit = pnl_pct is not None and pnl_pct > 0
    else:
        hit = pnl_pct is not None and pnl_pct < 0
    return {
        "direction": direction,
        "decision_close": anchor,
        "settled_close": settled,
        "horizon": horizon,
        "pnl_pct": pnl_pct,
        "hit": hit,
        "settled": True,
    }


def summarize(verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate verdicts: population, settled count, directional hit rates.

    Neutral verdicts are excluded from hit rates by construction (they assert
    no directional edge) but reported, because "how often is Stage 1 flat" is
    itself calibration-relevant.
    """
    in_population = len(verdicts)
    unscorable = sum(1 for v in verdicts if v.get("unscorable"))
    settled = [v for v in verdicts if v.get("settled") and not v.get("unscorable")]
    directional = [v for v in settled if v["direction"] not in _NEUTRAL]
    neutral = [v for v in settled if v["direction"] in _NEUTRAL]
    hits = sum(1 for v in directional if v["hit"])

    def _mean_pnl(rows: list[dict[str, Any]]) -> float | None:
        vals = [
            v["pnl_pct"] for v in rows if isinstance(v.get("pnl_pct"), (int, float))
        ]
        return sum(vals) / len(vals) if vals else None

    return {
        "population": in_population,
        "unscorable_direction": unscorable,
        "settled": len(settled),
        "unsettled": in_population - unscorable - len(settled),
        "directional_settled": len(directional),
        "neutral_settled": len(neutral),
        "hit_rate": hits / len(directional) if directional else None,
        "mean_pnl_pct_directional": _mean_pnl(directional),
        "mean_pnl_pct_neutral": _mean_pnl(neutral),
    }


def _anchor_of(record: dict[str, Any]) -> float | None:
    anchor = ((record.get("input_data") or {}).get("summary") or {}).get("last_close")
    if not isinstance(anchor, (int, float)) or isinstance(anchor, bool) or anchor == 0:
        return None
    return float(anchor)


__all__ = [
    "next_period_of",
    "direction_of",
    "forward_close",
    "score_record",
    "summarize",
]
