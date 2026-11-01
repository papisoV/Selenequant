"""Three-layer validator (json / consistency / semantic). See docs/DECISION_WORKFLOW_METHODOLOGY.md §2.4, §7.2."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .models import (
    DecisionTreeGraph,
    NodeAnswer,
    Stage1Result,
    Stage2Result,
    ValidationErrorModel,
    ValidationReport,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_GATE_RESULTS: frozenset[str] = frozenset({"proceed", "wait", "unknown"})
_VALID_TERMINAL_OUTCOMES: frozenset[str] = frozenset(
    {"trade", "wait", "reject", "proceed"}
)

# Methodology §2.4 line 272 — forbidden boilerplate "reason" tokens.
_REASON_BLOCKLIST: frozenset[str] = frozenset(
    {
        "是",
        "否",
        "通过",
        "结构清晰",
        "风险高",
        "不符合要求",
        "交易异常",
        "可疑行为",
    }
)

# bar_range must look like "K{n}-K{m}" with n>=1 (forbid K0).
_BAR_RANGE_RE: re.Pattern[str] = re.compile(r"^K(\d+)-K(\d+)$")
# Extract all "K\d+" citations from a free-form reason string.
_K_CITATION_RE: re.Pattern[str] = re.compile(r"K(\d+)")

# Direction -> set of decisions that contradict it (methodology §2.4 line 261).
# direction="long" contradicts "sell"/"reject"; direction="short" contradicts "buy"/"approve".
_DIRECTION_CONFLICTS: dict[str, frozenset[str]] = {
    "long": frozenset({"sell", "reject"}),
    "short": frozenset({"buy", "approve"}),
    "bullish": frozenset({"sell", "reject"}),
    "bearish": frozenset({"buy", "approve"}),
}

# Program-signal direction -> declared directions that stand in direct
# opposition to it (item 5). A program call on record plus a declared
# direction from the opposite camp is a *retryable* consistency error: the
# model may disagree with the program, but it must not do so silently —
# restating forces the disagreement into the record. A neutral program call
# imposes no constraint (it asserts no side).
_PROGRAM_OPPOSED: dict[str, frozenset[str]] = {
    "long": frozenset({"short", "down", "bearish"}),
    "short": frozenset({"long", "up", "bullish"}),
    "bullish": frozenset({"short", "down", "bearish"}),
    "bearish": frozenset({"long", "up", "bullish"}),
}


@dataclass(frozen=True)
class ValidatorConfig:
    """Validator policy knobs — wired by the orchestrator, not by the LLM.

    ``numeric_reason_nodes`` — node_ids whose ``reason`` must cite a number
    (methodology §2.4 line 274, e.g. trader-equation nodes).
    ``high_risk_nodes``     — reserved for future structural checks (not yet
                              wired in Layer 1-3 logic; tracked for API parity
                              with the methodology §2.4 line 240).
    ``immutable_nodes``     — node_ids whose answer must not be overridden
                              (methodology §2.4 line 241); consulted by the
                              orchestrator's override path, not the validator.
    ``min_risk_reward``     — numeric R:R hard gate for a ``trade`` order
                              (item 6). ``None`` (default) disables the gate,
                              leaving behaviour byte-identical to before this
                              field existed; the orchestrator opts in by
                              passing a threshold. Recomputed from the order's
                              own prices so a model-declared ratio cannot
                              override the arithmetic (mirrors market-analysis
                              ``validator.py``'s ``min_risk_reward``).
    """

    numeric_reason_nodes: frozenset[str] = frozenset()
    high_risk_nodes: frozenset[str] = frozenset()
    immutable_nodes: frozenset[str] = frozenset()
    min_risk_reward: float | None = None


# ---------------------------------------------------------------------------
# Layer 1 — JSON Schema (enum / format / conditional)
# ---------------------------------------------------------------------------


def _validate_bar_range_format(
    bar_range: str | None,
    *,
    errors: list[ValidationErrorModel],
    field_name: str = "bar_range",
    node_id: str | None = None,
) -> None:
    """Layer 1 helper — bar_range must match ``^K\\d+-K\\d+$`` and forbid ``K0``."""
    if bar_range is None:
        return
    m = _BAR_RANGE_RE.match(bar_range)
    if m is None:
        errors.append(
            ValidationErrorModel(
                layer="json",
                field=field_name,
                message=(
                    f"bar_range '{bar_range}' does not match required format "
                    "'K{n}-K{m}' (e.g. 'K1-K10')"
                ),
                node_id=node_id,
            )
        )
        return
    lo, hi = int(m.group(1)), int(m.group(2))
    if lo == 0 or hi == 0:
        errors.append(
            ValidationErrorModel(
                layer="json",
                field=field_name,
                message=(
                    f"bar_range '{bar_range}' forbids K0 (K-line indices are 1-based)"
                ),
                node_id=node_id,
            )
        )


def _validate_stage1_json(
    result: Stage1Result, errors: list[ValidationErrorModel]
) -> None:
    """Layer 1 — JSON enum/format checks for Stage 1."""
    # Pydantic Literal already narrows gate_result, but the orchestrator may
    # construct Stage1Result via model_validate with coercion; re-check explicitly
    # so a typo surfaces as a structured ValidationErrorModel rather than a
    # raw ValueError upstream.
    if result.gate_result not in _VALID_GATE_RESULTS:
        errors.append(
            ValidationErrorModel(
                layer="json",
                field="gate_result",
                message=(
                    f"gate_result '{result.gate_result}' is not in "
                    f"{sorted(_VALID_GATE_RESULTS)}"
                ),
                node_id=None,
            )
        )

    for ans in result.trace:
        _validate_bar_range_format(
            ans.bar_range, errors=errors, field_name="bar_range", node_id=ans.node_id
        )


def _validate_stage2_json(
    result: Stage2Result, errors: list[ValidationErrorModel]
) -> None:
    """Layer 1 — JSON enum/format/conditional checks for Stage 2."""
    if not isinstance(result.decision, str) or not result.decision.strip():
        errors.append(
            ValidationErrorModel(
                layer="json",
                field="decision",
                message="decision must be a non-empty string",
                node_id=None,
            )
        )

    if result.terminal_outcome not in _VALID_TERMINAL_OUTCOMES:
        errors.append(
            ValidationErrorModel(
                layer="json",
                field="terminal_outcome",
                message=(
                    f"terminal_outcome '{result.terminal_outcome}' is not in "
                    f"{sorted(_VALID_TERMINAL_OUTCOMES)}"
                ),
                node_id=None,
            )
        )

    for ans in result.trace:
        _validate_bar_range_format(
            ans.bar_range, errors=errors, field_name="bar_range", node_id=ans.node_id
        )

    # Conditional: decision=reject requires reject_reason in decision_summary.
    if result.decision == "reject":
        if not result.decision_summary.get("reject_reason"):
            errors.append(
                ValidationErrorModel(
                    layer="json",
                    field="reject_reason",
                    message=(
                        "decision='reject' requires a non-empty "
                        "decision_summary['reject_reason']"
                    ),
                    node_id=None,
                )
            )


# ---------------------------------------------------------------------------
# Layer 2 — Consistency (cross-field / cross-stage / required nodes / terminal)
# ---------------------------------------------------------------------------


def _check_required_nodes(
    trace: list[NodeAnswer],
    required_nodes: frozenset[str],
    errors: list[ValidationErrorModel],
) -> None:
    """If gate_result=proceed, every required node must appear in the trace."""
    present = {ans.node_id for ans in trace}
    for missing in sorted(required_nodes - present):
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="trace",
                message=(
                    f"required node '{missing}' is missing from trace "
                    f"(gate_result=proceed requires all {len(required_nodes)} "
                    f"gate nodes)"
                ),
                node_id=missing,
            )
        )


def _check_direction_consistency(
    decision: str, decision_summary: dict, errors: list[ValidationErrorModel]
) -> None:
    """direction in decision_summary must not contradict the decision."""
    direction = decision_summary.get("direction")
    if not isinstance(direction, str):
        return
    conflicts = _DIRECTION_CONFLICTS.get(direction.lower())
    if conflicts and decision in conflicts:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="direction",
                message=(
                    f"decision_summary.direction='{direction}' contradicts "
                    f"decision='{decision}'"
                ),
                node_id=None,
            )
        )


def _check_terminal_decision_match(
    result: Stage2Result, errors: list[ValidationErrorModel]
) -> None:
    """terminal_outcome must agree with whether an order is present."""
    outcome = result.terminal_outcome
    if outcome == "trade" and result.order is None:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="order",
                message="terminal_outcome='trade' but order is None",
                node_id=None,
            )
        )
    elif outcome == "reject" and result.order is not None:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="order",
                message="terminal_outcome='reject' but order is not None",
                node_id=None,
            )
        )


def _validate_stage1_consistency(
    result: Stage1Result,
    required_nodes: frozenset[str],
    errors: list[ValidationErrorModel],
) -> None:
    if result.gate_result == "proceed":
        _check_required_nodes(result.trace, required_nodes, errors)


def _check_program_direction(
    result: Stage2Result,
    expected_direction: str | None,
    errors: list[ValidationErrorModel],
) -> None:
    """Flag a declared direction that opposes the program signal (item 5).

    ``decision_summary.direction`` is the model's declared direction (the same
    field ``_check_direction_consistency`` reads). A declaration standing in
    direct opposition to the program arm is a *retryable* consistency error,
    deliberately NOT a cheat flag: disagreeing with the program is a
    legitimate model position, but it must be explicit. Absent or neutral
    expectations impose no constraint.
    """
    if not expected_direction:
        return
    opposes = _PROGRAM_OPPOSED.get(expected_direction.lower())
    if not opposes:
        return
    declared = result.decision_summary.get("direction")
    if not isinstance(declared, str):
        return
    if declared.strip().lower() in opposes:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="direction",
                message=(
                    f"decision_summary.direction='{declared}' contradicts "
                    f"program direction_signal='{expected_direction}'"
                ),
                node_id=None,
            )
        )


def _check_risk_reward(
    result: Stage2Result,
    config: ValidatorConfig,
    errors: list[ValidationErrorModel],
) -> None:
    """Numeric R:R gate on a trade order (item 6).

    The strategy files state the R:R rule in prose ("止损距离与首要止盈目标之比
    >= 1:1.5") with no code behind it — the model reports whatever ratio it
    likes. This recomputes the ratio from the order's own prices so a declared
    number cannot override the arithmetic (same discipline as market-analysis's
    ``min_risk_reward`` gate).

    Only runs when ``config.min_risk_reward`` is set (opt-in; ``None`` keeps the
    gate off so existing behaviour is unchanged) and the order is a ``trade``
    with the four concrete price fields. Missing/non-numeric prices skip
    silently — the JSON layer owns field-presence checks, and a half-specified
    order is not this gate's to reject.
    """
    if config.min_risk_reward is None:
        return
    order = result.order
    if not isinstance(order, dict):
        return

    def _num(key: str) -> float | None:
        v = order.get(key)
        if isinstance(v, bool):  # bool is an int subclass; never a price
            return None
        return float(v) if isinstance(v, (int, float)) else None

    entry = _num("entry")
    stop = _num("stop_loss")
    target = _num("take_profit")
    if entry is None or stop is None or target is None:
        return

    direction = order.get("direction")
    if isinstance(direction, str):
        d = direction.strip().lower()
        # Price ordering per side (mirrors market-analysis's trade_setup check).
        if d in ("long", "buy") and not (stop < entry < target):
            errors.append(
                ValidationErrorModel(
                    layer="consistency",
                    field="order.price_order",
                    message=(
                        f"long 价格顺序错误：需 stop_loss < entry < take_profit"
                        f"（sl={stop} entry={entry} tp={target}）"
                    ),
                    node_id=None,
                )
            )
        elif d in ("short", "sell") and not (target < entry < stop):
            errors.append(
                ValidationErrorModel(
                    layer="consistency",
                    field="order.price_order",
                    message=(
                        f"short 价格顺序错误：需 take_profit < entry < stop_loss"
                        f"（tp={target} entry={entry} sl={stop}）"
                    ),
                    node_id=None,
                )
            )

    risk = abs(entry - stop)
    if risk == 0:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="order.stop_loss",
                message="stop_loss 与 entry 相同（risk=0，无法算 RR）",
                node_id=None,
            )
        )
        return
    rr = abs(target - entry) / risk
    if rr < config.min_risk_reward:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="order.risk_reward_ratio",
                message=(
                    f"风险回报比 {rr:.2f} < 最小 {config.min_risk_reward}"
                    "（须提高目标价或收紧止损）"
                ),
                node_id=None,
            )
        )


def _validate_stage2_consistency(
    result: Stage2Result,
    stage1: Stage1Result,
    required_nodes: frozenset[str],
    errors: list[ValidationErrorModel],
    expected_direction: str | None = None,
    config: ValidatorConfig | None = None,
) -> None:
    _check_direction_consistency(result.decision, result.decision_summary, errors)
    _check_terminal_decision_match(result, errors)
    _check_program_direction(result, expected_direction, errors)
    _check_risk_reward(result, config or ValidatorConfig(), errors)
    if result.gate_result == "proceed" if hasattr(result, "gate_result") else False:
        # Stage2Result has no gate_result; required-nodes check is Stage1-only.
        pass

    # Cross-stage: decision_summary['diagnosis_summary'] must deep-equal
    # stage1.diagnosis_summary (methodology §2.4 line 262). Any divergence is a
    # cheat signal (AI tampered with an immutable field).
    carried = result.decision_summary.get("diagnosis_summary")
    if carried != stage1.diagnosis_summary:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="diagnosis_summary",
                cheat_detected=True,
                message=(
                    "Stage 2 diagnosis_summary differs from Stage 1 "
                    "(immutable field tampering suspected)"
                ),
                node_id=None,
            )
        )


# ---------------------------------------------------------------------------
# Layer 3 — Semantic (NOT retryable per §7.2 line 277)
# ---------------------------------------------------------------------------


def _check_reason_not_boilerplate(
    ans: NodeAnswer, errors: list[ValidationErrorModel]
) -> None:
    """reason must not be a bare boilerplate token (§2.4 line 272)."""
    if ans.reason in _REASON_BLOCKLIST:
        errors.append(
            ValidationErrorModel(
                layer="semantic",
                field="reason",
                node_id=ans.node_id,
                message=(
                    f"reason='{ans.reason}' is boilerplate; node {ans.node_id} "
                    "requires a specific structural justification"
                ),
            )
        )


def _check_reason_cites_within_bar_range(
    ans: NodeAnswer, idx: int, errors: list[ValidationErrorModel]
) -> None:
    """If ans.bar_range is set, every K-citation in reason must fall in range."""
    if not ans.bar_range:
        return
    m = _BAR_RANGE_RE.match(ans.bar_range)
    if m is None:
        # Malformed bar_range already reported by Layer 1; skip range check here.
        return
    lo, hi = int(m.group(1)), int(m.group(2))
    cited = [int(x) for x in _K_CITATION_RE.findall(ans.reason)]
    for c in cited:
        if c < lo or c > hi:
            errors.append(
                ValidationErrorModel(
                    layer="semantic",
                    field="reason",
                    node_id=ans.node_id,
                    message=(
                        f"trace[{idx}].reason cites K{c} outside bar_range "
                        f"'{ans.bar_range}'"
                    ),
                )
            )
            return


def _check_numeric_reason(
    ans: NodeAnswer,
    numeric_reason_nodes: frozenset[str],
    errors: list[ValidationErrorModel],
) -> None:
    """Nodes flagged requires_numeric_reason must cite at least one digit."""
    if ans.node_id not in numeric_reason_nodes:
        return
    if not re.search(r"\d", ans.reason):
        errors.append(
            ValidationErrorModel(
                layer="semantic",
                field="reason",
                node_id=ans.node_id,
                message=(
                    f"node {ans.node_id} requires a numeric reason but "
                    f"reason='{ans.reason}' contains no digit"
                ),
            )
        )


def _validate_semantic(
    trace: list[NodeAnswer],
    config: ValidatorConfig,
    errors: list[ValidationErrorModel],
) -> None:
    for idx, ans in enumerate(trace):
        _check_reason_not_boilerplate(ans, errors)
        _check_reason_cites_within_bar_range(ans, idx, errors)
        _check_numeric_reason(ans, config.numeric_reason_nodes, errors)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_stage1(
    result: Stage1Result,
    *,
    graph: DecisionTreeGraph,
    required_nodes: frozenset[str],
    config: ValidatorConfig | None = None,
) -> ValidationReport:
    """Run all 3 validation layers on a Stage 1 result.

    Parameters
    ----------
    result
        Stage 1 structured output to validate.
    graph
        Decision tree graph (used for structural checks; currently consulted
        only for API parity — the methodology §2.4 graph-walk checks are
        wired through :mod:`routing`).
    required_nodes
        Node ids that must appear in ``result.trace`` when
        ``gate_result == 'proceed'``.
    config
        Optional :class:`ValidatorConfig` for Layer 3 numeric-reason policy.
    """
    cfg = config or ValidatorConfig()
    errors: list[ValidationErrorModel] = []
    _validate_stage1_json(result, errors)
    _validate_stage1_consistency(result, required_nodes, errors)
    _validate_semantic(result.trace, cfg, errors)
    return _assemble_report(errors)


def validate_stage2(
    result: Stage2Result,
    *,
    stage1: Stage1Result,
    graph: DecisionTreeGraph,
    required_nodes: frozenset[str],
    config: ValidatorConfig | None = None,
    expected_direction: str | None = None,
) -> ValidationReport:
    """Run all 3 validation layers on a Stage 2 result.

    The ``required_nodes`` parameter is accepted for API symmetry with
    :func:`validate_stage1`; Stage 2 does not re-run the gate-node check
    (that is Stage 1's responsibility), but the orchestrator may pass the
    same set for audit logging.

    ``expected_direction`` is the program-owned direction signal (item 5);
    when supplied, a declared direction in direct opposition is a retryable
    consistency error (see :func:`_check_program_direction`).
    """
    cfg = config or ValidatorConfig()
    errors: list[ValidationErrorModel] = []
    _validate_stage2_json(result, errors)
    _validate_stage2_consistency(
        result, stage1, required_nodes, errors, expected_direction, cfg
    )
    _validate_semantic(result.trace, cfg, errors)
    return _assemble_report(errors)


def _assemble_report(errors: list[ValidationErrorModel]) -> ValidationReport:
    """Build the immutable :class:`ValidationReport` from collected errors."""
    cheat_fields = tuple(sorted({e.field for e in errors if e.cheat_detected}))
    return ValidationReport(
        errors=tuple(errors),
        is_valid=(len(errors) == 0),
        cheat_fields=cheat_fields,
    )


__all__ = [
    "ValidatorConfig",
    "validate_stage1",
    "validate_stage2",
]
