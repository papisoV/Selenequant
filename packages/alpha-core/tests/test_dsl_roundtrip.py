"""Formula DSL: render / canonical-hash round-trip tests.

Verifies :mod:`selene_space_factor_research.mining.dsl` renders RPN token
sequences into readable nested expressions, normalizes commutative operands in
:func:`canonical_hash`, and round-trips a set of known valid formulas without
raising.
"""
from __future__ import annotations

from selene_space_alpha.vocab import FORMULA_VOCAB
from selene_space_factor_research.mining.dsl import canonical_hash, render


def _tok(name: str) -> int:
    return list(FORMULA_VOCAB.token_names).index(name)


RET = _tok("RET")
LIQ = _tok("LIQ_SCORE")
TS_MEAN = _tok("TS_MEAN")
TS_RANK = _tok("TS_RANK")
SIGN = _tok("SIGN")
ADD = _tok("ADD")
MUL = _tok("MUL")
NEG = _tok("NEG")
GATE = _tok("GATE")


def test_render_readable_expression() -> None:
    expr = render([RET, TS_MEAN, SIGN])
    assert expr == "SIGN(TS_MEAN(RET))"
    assert "SIGN" in expr
    assert "TS_MEAN" in expr
    assert "RET" in expr


def test_canonical_hash_commutative_add() -> None:
    assert canonical_hash([RET, LIQ, ADD]) == canonical_hash([LIQ, RET, ADD])


def test_canonical_hash_commutative_mul() -> None:
    assert canonical_hash([RET, LIQ, MUL]) == canonical_hash([LIQ, RET, MUL])


def test_canonical_hash_different_formulas_differ() -> None:
    assert canonical_hash([RET, NEG]) != canonical_hash([LIQ, NEG])
    assert canonical_hash([RET, LIQ, ADD]) != canonical_hash([RET, LIQ, MUL])
    assert canonical_hash([RET, TS_RANK]) != canonical_hash([RET, TS_MEAN])


def test_render_round_trips_known_formulas() -> None:
    formulas = [
        [RET, LIQ, ADD],
        [RET, NEG],
        [RET, TS_RANK],
        [RET, TS_RANK, SIGN],
        [RET, LIQ, TS_MEAN, ADD],
        [RET, LIQ, RET, GATE],
    ]
    for tokens in formulas:
        expr = render(tokens)
        assert isinstance(expr, str)
        assert expr
