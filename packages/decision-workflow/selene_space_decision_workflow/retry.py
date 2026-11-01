"""Retry + cheat-detection helpers. See docs/DECISION_WORKFLOW_METHODOLOGY.md §2.4, §7.2."""
from __future__ import annotations

from collections.abc import Callable

from .exceptions import CheatError, SemanticError, ValidationError
from .models import NodeAnswer, Stage1Result, Stage2Result, ValidationReport

# Methodology §2.4 line 309 — fields that must not mutate between retries.
IMMUTABLE_FIELDS: tuple[str, ...] = ("bar_range", "program_prefill_nodes", "diagnosis_summary")


def _summary(result: Stage1Result | Stage2Result) -> dict | None:
    """Return the summary dict regardless of stage variant (diagnosis vs decision)."""
    if isinstance(result, Stage1Result):
        return result.diagnosis_summary
    return result.decision_summary


def detect_cheat(
    previous: Stage1Result | Stage2Result | None,
    current: Stage1Result | Stage2Result,
) -> list[str]:
    """Return the list of immutable field names that differ between previous and current.

    If previous is None, returns [] (no baseline to compare against). Field comparison rules:
      - bar_range: simple equality on NodeAnswer.trace[i].
      - program_prefill_nodes: list-equality on the node-ID list (order-sensitive).
      - diagnosis_summary: deep dict-equality (value-compared).
    """
    if previous is None:
        return []

    cheats: list[str] = []
    seen: set[str] = set()

    prev_trace = list(previous.trace)
    cur_trace = list(current.trace)
    max_idx = min(len(prev_trace), len(cur_trace))

    for i in range(max_idx):
        prev_node: NodeAnswer = prev_trace[i]
        cur_node: NodeAnswer = cur_trace[i]

        if prev_node.bar_range != cur_node.bar_range and "bar_range" not in seen:
            cheats.append("bar_range")
            seen.add("bar_range")

        if (
            list(prev_node.program_prefill_nodes) != list(cur_node.program_prefill_nodes)
            and "program_prefill_nodes" not in seen
        ):
            cheats.append("program_prefill_nodes")
            seen.add("program_prefill_nodes")

    prev_summary = _summary(previous)
    cur_summary = _summary(current)
    if prev_summary is not None and cur_summary is not None and prev_summary != cur_summary:
        if "diagnosis_summary" not in seen:
            cheats.append("diagnosis_summary")
            seen.add("diagnosis_summary")

    return cheats


def validate_with_retry(
    call_llm: Callable[[], dict],
    validator: Callable[[dict], tuple[ValidationReport, Stage1Result | Stage2Result | None]],
    *,
    max_retry: int = 3,
    stage_label: str = "stage",
) -> tuple[Stage1Result | Stage2Result, None]:
    """Generic retry wrapper for two-stage LLM validation.

    - call_llm: returns a raw JSON-parsed dict.
    - validator: returns (ValidationReport, parsed-result-or-None).

    On valid output → returns (result, None). On cheat (report flag or detect_cheat)
    → raises CheatError. On semantic-layer error → raises SemanticError (not retryable).
    Otherwise retries with structured feedback appended. After max_retry exhausted,
    raises ValidationError(layer="json", field="max_retry", ...).
    """
    for attempt in range(1, max_retry + 1):
        raw = call_llm()
        report, result = validator(raw)

        if report.is_valid and result is not None:
            return result, None

        # Cheat detection (explicit flag OR immutable-field mutation).
        if any(e.cheat_detected for e in report.errors):
            cheat_errs = [e for e in report.errors if e.cheat_detected]
            raise CheatError(
                field=cheat_errs[0].field,
                message=cheat_errs[0].message,
            )

        # Semantic errors are NOT retryable per §7.2 line 277.
        semantic_errs = [e for e in report.errors if e.layer == "semantic"]
        if semantic_errs:
            first = semantic_errs[0]
            raise SemanticError(message=first.message, node_id=first.node_id)

        # Loop continues: next call_llm() invocation gets fresh input. The
        # orchestrator's own retry loop (orchestrator.py) handles structured
        # feedback construction; this generic wrapper keeps the contract
        # minimal and lets the caller decide how to feed back errors.

    raise ValidationError(
        layer="json",
        field="max_retry",
        message=f"{stage_label} failed after {max_retry} retries",
    )
