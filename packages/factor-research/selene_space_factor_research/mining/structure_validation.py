# Cleanroom implementation: design intent derived from prior art; independent expression.
"""Advisory structural validation for formula token sequences.

Two checks feed a :class:`ValidationResult`:

(a) **RPN validity** — every token is a known vocab id, no operator arity
    underflow, and the stack ends with exactly one value.

(b) **恒正感染链 (positive-infection chain)** — operators are classified into
    three categories and a violation is flagged when ``>= threshold``
    always-positive operators appear consecutively in the operator stream
    (feature tokens between them are ignored):

    - always-positive (恒正): TS_RANK, TS_MAX, TS_MIN, TS_MEAN, TS_STD,
      ABS, MEAN2
    - breaks the chain (恢复): SUB, DIV, NEG, TS_DELTA
    - propagates (传播): everything else (ADD, MUL, DECAY, DELAY1, GATE,
      JUMP, MAX3, SIGN, TS_CORR, RANK, IND_NEUTRALIZE)

    A propagator keeps the current run alive without extending it; a breaking
    operator resets it. A long run of always-positive operators indicates a
    degenerate formula whose output sign carries no information.

Validation is ADVISORY — it reports reasons and never rejects or deletes the
formula; the fitness layer decides later. This module never raises for formula
content.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from selene_space_alpha.ops.registry import OPERATOR_DEFS
from selene_space_alpha.vocab import FORMULA_VOCAB

__all__ = [
    "POSITIVE_ALWAYS_OPS",
    "POSITIVE_BREAK_OPS",
    "POSITIVE_PROPAGATE_OPS",
    "ValidationResult",
    "validate",
]

# Tunable operator classification (module level, imported by the search layer).
POSITIVE_ALWAYS_OPS: frozenset[str] = frozenset(
    {"TS_RANK", "TS_MAX", "TS_MIN", "TS_MEAN", "TS_STD", "ABS", "MEAN2"}
)
POSITIVE_BREAK_OPS: frozenset[str] = frozenset(
    {"SUB", "DIV", "NEG", "TS_DELTA"}
)
# 传播 = every operator not explicitly classified otherwise.
POSITIVE_PROPAGATE_OPS: frozenset[str] = (
    frozenset(name for name, _ in OPERATOR_DEFS)
    - POSITIVE_ALWAYS_OPS
    - POSITIVE_BREAK_OPS
)

_ARITY_MAP: dict[str, int] = {name: arity for name, arity in OPERATOR_DEFS}
_VOCAB_SIZE: int = FORMULA_VOCAB.size
_OPERATOR_OFFSET: int = FORMULA_VOCAB.operator_offset


@dataclass
class ValidationResult:
    """Outcome of :func:`validate` — advisory, never fatal."""

    valid: bool
    reasons: list[str] = field(default_factory=list)


def validate(tokens: list[int], threshold: int = 3) -> ValidationResult:
    """Validate ``tokens`` and return reasons (never raises for content).

    ``threshold`` is the minimum consecutive-run length of always-positive
    operators that triggers a positive-infection reason.
    """
    threshold = max(1, int(threshold))
    reasons: list[str] = []
    reasons.extend(_rpn_reasons(tokens))
    reasons.extend(_infection_reasons(tokens, threshold))
    return ValidationResult(valid=not reasons, reasons=reasons)


def _rpn_reasons(tokens: list[int]) -> list[str]:
    reasons: list[str] = []
    stack = 0
    underflow = False
    for raw in tokens:
        token = int(raw)
        if not 0 <= token < _VOCAB_SIZE:
            reasons.append(f"unknown token {token}")
            continue
        if token < _OPERATOR_OFFSET:
            stack += 1
            continue
        name = FORMULA_VOCAB.token_names[token]
        arity = _ARITY_MAP[name]
        if arity < 0:
            if stack < 1:
                reasons.append(f"arity underflow at {name}: empty stack")
                underflow = True
                break
            stack = 1  # consume all remaining values, push one result
            continue
        if stack < arity:
            reasons.append(f"arity underflow at {name}: need {arity}, have {stack}")
            underflow = True
            break
        stack = stack - arity + 1
    if not underflow and stack != 1:
        reasons.append(f"invalid RPN: {stack} values left on stack (expected 1)")
    return reasons


def _infection_reasons(tokens: list[int], threshold: int) -> list[str]:
    reasons: list[str] = []
    run = 0
    max_run = 0
    for raw in tokens:
        token = int(raw)
        if not 0 <= token < _VOCAB_SIZE or token < _OPERATOR_OFFSET:
            continue  # unknown / feature tokens do not affect the operator stream
        name = FORMULA_VOCAB.token_names[token]
        if name in POSITIVE_ALWAYS_OPS:
            run += 1
            if run > max_run:
                max_run = run
        elif name in POSITIVE_BREAK_OPS:
            run = 0
        # else: propagator — keeps the current run alive without extending it.
    if max_run >= threshold:
        reasons.append(
            f"positive-infection chain: {max_run} always-positive operators in a "
            f"row (threshold {threshold})"
        )
    return reasons
