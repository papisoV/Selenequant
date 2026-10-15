"""Structural validation of formula token sequences.

Locks the advisory rules in
:mod:`selene_space_factor_research.mining.structure_validation`: RPN
well-formedness (known tokens, no arity underflow, single final stack value)
and the positive-infection chain rule (consecutive always-positive operators).
"""
from __future__ import annotations

from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.structure_validation import validate


def _tok(name: str) -> int:
    return list(FORMULA_VOCAB.token_names).index(name)


RET = _tok("RET")
LIQ = _tok("LIQ_SCORE")
ADD = _tok("ADD")
SUB = _tok("SUB")
DECAY = _tok("DECAY")
TS_RANK = _tok("TS_RANK")
TS_MEAN = _tok("TS_MEAN")
TS_MAX = _tok("TS_MAX")


def test_positive_infection_chain_violation_operator_stream() -> None:
    # Operator-only stream: three always-positive operators in a row.
    result = validate([TS_RANK, TS_MEAN, TS_MAX])
    assert not result.valid
    assert any("infection" in reason for reason in result.reasons)


def test_positive_infection_chain_violation_full_formula() -> None:
    # Full valid RPN: RET -> TS_RANK -> TS_MEAN -> TS_MAX (run length 3).
    result = validate([RET, TS_RANK, TS_MEAN, TS_MAX])
    assert not result.valid
    assert any("infection" in reason for reason in result.reasons)


def test_break_operator_resets_infection() -> None:
    # TS_RANK, then SUB resets, then DECAY: run length 1 -> no infection reason.
    result = validate([TS_RANK, SUB, DECAY])
    assert not any("infection" in reason for reason in result.reasons)


def test_break_operator_full_formula_is_valid() -> None:
    # RET -> TS_RANK; LIQ, then SUB combines; DECAY. Valid RPN, run length 1.
    result = validate([RET, TS_RANK, LIQ, SUB, DECAY])
    assert result.valid, result.reasons


def test_rpn_underflow_invalid() -> None:
    result = validate([ADD])
    assert not result.valid
    assert any("underflow" in reason for reason in result.reasons)


def test_rpn_leftover_stack_invalid() -> None:
    result = validate([RET, LIQ])
    assert not result.valid
    assert any("left on stack" in reason for reason in result.reasons)


def test_unknown_token_invalid() -> None:
    result = validate([RET, 99999])
    assert not result.valid
    assert any("unknown token" in reason for reason in result.reasons)


def test_threshold_configurable() -> None:
    # Run length 2: flagged at threshold 2, not at the default threshold 3.
    tokens = [RET, TS_RANK, TS_MEAN]
    strict = validate(tokens, threshold=2)
    assert not strict.valid
    assert any("infection" in reason for reason in strict.reasons)
    default = validate(tokens)
    assert default.valid, default.reasons
