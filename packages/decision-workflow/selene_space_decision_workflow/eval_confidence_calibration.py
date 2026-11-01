"""Stage-2 confidence × outcome emit-yield audit (Phase A of the C2 plan).

Read-only, pure analysis of persisted decision-workflow records: how many
records carry a confidence at the contract path, how many carry an outcome,
how many records are excluded from those counts because they are a different
record species, and where the literal key ``confidence`` actually lives in the
corpus. The module exists to *measure* a channel that is wired but may never
have written a value (0 of 67 persisted records on 2026-09-17 cross-checks), so
an all-zero report is a finding (``reachability``), not a failure — provided
``confidence_key_present_anywhere`` says the key exists somewhere in the corpus,
which separates "channel never written" from "I read the wrong path".

The only confidence channel decision-workflow persists is
``stage2.diagnosis_or_decision.decision_summary.confidence`` (0-1 float),
pinned by ``tests/test_pa_confidence.py:113-117``. Any other ``confidence``
(e.g. market-analysis ``signal_patterns[].confidence``) is a different species
and must not count here — it only feeds the any-path key scan.

Companion CLI: ``scripts/eval_confidence_calibration.py``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

# The one contract path. Everything else named "confidence" is a different
# species and must not count towards with_confidence.
CONTRACT_PATH = ("stage2", "diagnosis_or_decision", "decision_summary", "confidence")

# Outcome statuses that carry no pnl_r by construction (pa_outcome.py:164-193):
# the position never opened or never closed within the window. Always reported
# separately, never coerced to 0.
PNL_NONE_STATUSES = frozenset({"expired", "timeout_open"})

WIN_LOSS = frozenset({"win", "loss"})

# Fixed confidence bins over [0,1]. Right-open except the last (holds 1.0).
BIN_LIMITS = ((0.0, 0.25), (0.25, 0.5), (0.5, 0.75), (0.75, 1.0))
BIN_LABELS = ("[0.0,0.25)", "[0.25,0.5)", "[0.5,0.75)", "[0.75,1.0]")


def _dig(obj: Any, path: tuple[str, ...]) -> Any:
    """Descend ``obj`` along ``path`` with .get semantics; None on any miss."""
    cur: Any = obj
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _has_key_anywhere(obj: Any, key: str) -> bool:
    """True when ``key`` occurs as a dict/JSON key at any depth."""
    if isinstance(obj, dict):
        if key in obj:
            return True
        return any(_has_key_anywhere(v, key) for v in obj.values())
    if isinstance(obj, list):
        return any(_has_key_anywhere(v, key) for v in obj)
    return False


def confidence_of(record: dict[str, Any]) -> float | None:
    """Contract-path confidence, or None when absent / not a numeric in [0,1].

    Booleans are rejected (``True`` is an int in Python and must not be read as
    1.0 confidence); out-of-range numerics are rejected as impossible under the
    schema guard (``Field(ge=0.0, le=1.0)``).
    """
    value = _dig(record, CONTRACT_PATH)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    if not 0.0 <= value <= 1.0:
        return None
    return value


def outcome_of(record: dict[str, Any]) -> dict[str, Any] | None:
    """The persisted top-level ``outcome`` payload, or None."""
    out = record.get("outcome")
    return out if isinstance(out, dict) else None


def is_dw_shape(record: dict[str, Any]) -> bool:
    """True when the record is the decision-workflow species.

    A record can only carry the contract path when ``stage2`` is a dict whose
    ``diagnosis_or_decision`` is a dict. Everything else — market-analysis
    records, gate short-circuits with a flat ``stage2``, records with no
    ``stage2`` at all — is excluded from the confidence/outcome numerators and
    counts towards ``coverage.excluded_by_shape``.
    """
    stage2 = record.get("stage2")
    return isinstance(stage2, dict) and isinstance(
        stage2.get("diagnosis_or_decision"), dict
    )


def gate_result_of(record: dict[str, Any]) -> str | None:
    """The Stage-1 gate verdict as it is actually persisted, or None.

    Two persisted locations, in priority order:

    1. ``stage1.response.gate_result`` — the authoritative one. The orchestrator
       persists ``response=stage1_raw`` (orchestrator.py:142), the validated
       Stage1Result dump, whose ``gate_result`` field is guaranteed by the
       schema. The gate branch itself reads this path
       (``orchestrator.py:113``, ``stage1.gate_result``).
    2. ``stage1.diagnosis_or_decision.gate_result`` — fallback. ``StageRecord``
       (models.py:83-89) persists ``diagnosis_or_decision=stage1.diagnosis_summary``
       (orchestrator.py:143), and the LLM *usually* echoes the gate into that
       summary — but does not have to (measured 2026-09-17: the BTC 4h record
       wrote ``gate_node``/``gate_reason`` and no ``gate_result``). The in-repo
       consumer reads this path (scripts/run_pa_diagnosis.py:384), so it stays
       supported — as fallback, never as primary.

    The chains are defensive so a truthy non-dict ``stage1`` yields None rather
    than raising.
    """
    stage1 = record.get("stage1")
    if not isinstance(stage1, dict):
        return None
    response = stage1.get("response")
    if isinstance(response, dict):
        gate = response.get("gate_result")
        if isinstance(gate, str):
            return gate
    payload = stage1.get("diagnosis_or_decision")
    if isinstance(payload, dict):
        gate = payload.get("gate_result")
        if isinstance(gate, str):
            return gate
    return None


def _bin_index(c: float) -> int:
    for i, (lo, hi) in enumerate(BIN_LIMITS):
        if lo <= c < hi:
            return i
    return len(BIN_LIMITS) - 1  # c == 1.0


def build_calibration_report(records: list[dict[str, Any]]) -> dict[str, Any]:
    """Reduce persisted records to the emit-yield audit report. Pure, no I/O.

    Coverage counts every input record; the reliability table and the
    counterfactual sweep only see decision-workflow-shaped records. Nothing
    here coerces a ``pnl_r is None`` to 0, and an empty / all-zero corpus
    renders as a zero-filled report (never raises).
    """
    records = list(records)
    total = len(records)
    dw_records = [r for r in records if is_dw_shape(r)]

    with_confidence = 0
    with_outcome = 0
    with_both = 0
    key_anywhere = 0

    # Per-bin counts, plus a separate pnl sum so mean_pnl_r runs over the
    # {win, loss} subset only (pnl_r is None for expired / timeout_open).
    rel: dict[int, Counter[str]] = {}
    pnl_sum: dict[int, float] = {}
    # (confidence, status) for proceed-records with a win/loss outcome → the
    # sweep's universe; confidence values double as the data-derived thresholds.
    sweep_universe: list[tuple[float, str]] = []

    for rec in records:
        if _has_key_anywhere(rec, "confidence"):
            key_anywhere += 1
        if not is_dw_shape(rec):
            continue

        out = outcome_of(rec)
        confidence = confidence_of(rec)
        has_confidence = confidence is not None
        has_outcome = out is not None
        if has_confidence:
            with_confidence += 1
        if has_outcome:
            with_outcome += 1
        if has_confidence and has_outcome:
            with_both += 1

        # Guard on the value itself, not on has_confidence: mypy narrows only
        # the direct `is not None` form, and the value is used below.
        if confidence is not None:
            idx = _bin_index(confidence)
            counts = rel.setdefault(idx, Counter())
            counts["n"] += 1
            status = (out or {}).get("status")
            if status in WIN_LOSS:
                counts[f"n_{status}"] += 1
                pnl = (out or {}).get("pnl_r")
                if isinstance(pnl, (int, float)) and not isinstance(pnl, bool):
                    pnl_sum[idx] = pnl_sum.get(idx, 0.0) + float(pnl)
                    counts["pnl_n"] += 1
            elif status in PNL_NONE_STATUSES:
                counts["n_pending"] += 1
            else:
                # "never reviewed" / "still open" (outcome is None) or an
                # unrecognised status — one bucket, as decided for this plan.
                counts["n_no_outcome"] += 1

            if status in WIN_LOSS and gate_result_of(rec) == "proceed":
                sweep_universe.append((confidence, str(status)))

    reliability = []
    for i in range(len(BIN_LIMITS)):
        counts = rel.get(i) or Counter()
        decided = counts["n_win"] + counts["n_loss"]
        reliability.append(
            {
                "bin": BIN_LABELS[i],
                "n": counts["n"],
                "n_win": counts["n_win"],
                "n_loss": counts["n_loss"],
                "n_pending": counts["n_pending"],
                "n_no_outcome": counts["n_no_outcome"],
                "win_rate": (counts["n_win"] / decided) if decided else None,
                "mean_pnl_r": (
                    pnl_sum.get(i, 0.0) / counts["pnl_n"] if counts["pnl_n"] else None
                ),
            }
        )

    sweep = []
    if sweep_universe:
        total_win = sum(1 for _, s in sweep_universe if s == "win")
        total_loss = sum(1 for _, s in sweep_universe if s == "loss")
        for threshold in sorted({c for c, _ in sweep_universe}):
            excluded_win = sum(
                1 for c, s in sweep_universe if c < threshold and s == "win"
            )
            excluded_loss = sum(
                1 for c, s in sweep_universe if c < threshold and s == "loss"
            )
            sweep.append(
                {
                    "threshold": threshold,
                    "total_win": total_win,
                    "total_loss": total_loss,
                    "excluded_win": excluded_win,
                    "excluded_loss": excluded_loss,
                    "win_share_excluded": (
                        excluded_win / total_win if total_win else None
                    ),
                    "loss_share_excluded": (
                        excluded_loss / total_loss if total_loss else None
                    ),
                }
            )

    coverage = {
        "total": total,
        "total_dw": len(dw_records),
        "with_stage2": len(dw_records),  # pinned := total_dw (review MINOR 2)
        "with_confidence": with_confidence,
        "with_outcome": with_outcome,
        "with_both": with_both,
        "excluded_by_shape": total - len(dw_records),
        "confidence_key_present_anywhere": key_anywhere,
    }

    # Name WHICH side is empty: with_both == 0 has three distinct causes, and
    # the most likely next state (Phase C funded, no review cycle yet) has
    # confidences but no outcomes — a note claiming "the channel never wrote a
    # value" would then contradict the numbers in its own report.
    note = None
    if with_both == 0:
        if with_confidence == 0 and with_outcome == 0:
            missing = "neither a contract-path confidence nor an outcome"
        elif with_confidence == 0:
            missing = "no contract-path confidence (outcomes do exist)"
        else:
            missing = "no outcome (confidences do exist)"
        note = (
            f"reachability finding, not calibration: {missing} in this corpus, so the "
            "reliability table and the sweep are zero-filled. "
            "confidence_key_present_anywhere separates an unwritten channel from a "
            "wrong accessor (keys exist but not at the contract path)."
        )
    return {
        "generated_by": "eval_confidence_calibration",
        "contract_path": ".".join(CONTRACT_PATH),
        "coverage": coverage,
        "reliability": reliability,
        "counterfactual_sweep": sweep,
        "note": note,
    }
