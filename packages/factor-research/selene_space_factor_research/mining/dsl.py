# Cleanroom implementation: design intent derived from prior art; independent expression.
"""Formula DSL utilities — RPN parse / render / canonical hash (pandas-only).

Tier-1 infrastructure for deterministic genetic formula search on top of
:mod:`selene_space_alpha`'s stack VM. Tokens are the vocabulary ids from
``selene_space_alpha.vocab.FORMULA_VOCAB``; operator arity is resolved from
``selene_space_alpha.ops.registry.OPERATOR_DEFS`` so the DSL always agrees with
the executor (e.g. ``MAX3`` carries arity 1 in the registry).

This module is deliberately torch-free: it imports only the vocab and the
operator registry, never ``torch``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from selene_space_alpha.ops.registry import OPERATOR_DEFS
from selene_space_alpha.vocab import FORMULA_VOCAB

__all__ = ["Node", "parse", "render", "canonical_hash"]

#: operator name -> arity, from the executor's registry (source of truth).
_ARITY_MAP: dict[str, int] = {name: arity for name, arity in OPERATOR_DEFS}

#: commutative binary operators — operand order is not semantically meaningful.
_COMMUTATIVE_OPS: frozenset[str] = frozenset({"ADD", "MUL", "MEAN2", "TS_CORR"})

_VOCAB_SIZE: int = FORMULA_VOCAB.size
_OPERATOR_OFFSET: int = FORMULA_VOCAB.operator_offset


@dataclass(frozen=True)
class Node:
    """Immutable AST node: a feature leaf (no children) or an operator."""

    name: str
    children: tuple["Node", ...] = ()


def parse(tokens: list[int]) -> Node:
    """Parse an RPN token list into an AST (:class:`Node`).

    ``IND_NEUTRALIZE`` (variable arity, -1) consumes the whole remaining stack.

    Raises:
        ValueError: on structurally invalid RPN — unknown token, arity
            underflow, or a stack that does not end with exactly one value.
    """
    stack: list[Node] = []
    for raw in tokens:
        token = int(raw)
        if not 0 <= token < _VOCAB_SIZE:
            raise ValueError(f"unknown token {token}")
        if token < _OPERATOR_OFFSET:
            stack.append(Node(FORMULA_VOCAB.token_names[token]))
            continue
        name = FORMULA_VOCAB.token_names[token]
        arity = _ARITY_MAP[name]
        if arity < 0:
            if not stack:
                raise ValueError(f"arity underflow at {name}: empty stack")
            stack = [Node(name, tuple(stack))]
            continue
        if len(stack) < arity:
            raise ValueError(
                f"arity underflow at {name}: need {arity}, have {len(stack)}"
            )
        children = stack[-arity:]
        del stack[-arity:]
        stack.append(Node(name, tuple(children)))
    if len(stack) != 1:
        raise ValueError(
            f"invalid RPN: {len(stack)} values left on stack (expected 1)"
        )
    return stack[0]


def render(tokens: list[int]) -> str:
    """Render an RPN token list as a human-readable nested expression.

    Example: ``[RET, TS_MEAN, SIGN]`` -> ``"SIGN(TS_MEAN(RET))"``.
    """
    return _render_node(parse(tokens))


def canonical_hash(tokens: list[int]) -> str:
    """Stable hash of a formula modulo commutative-operand reordering.

    ADD / MUL / MEAN2 / TS_CORR child operands are sorted by their canonical
    serialization, so ``ADD(a, b)`` and ``ADD(b, a)`` hash identically while
    genuinely different formulas (almost surely) hash differently.
    """
    canonical = _canonical(parse(tokens))
    return hashlib.sha1(canonical.encode("utf-8")).hexdigest()


def _render_node(node: Node) -> str:
    if not node.children:
        return node.name
    inner = ", ".join(_render_node(child) for child in node.children)
    return f"{node.name}({inner})"


def _canonical(node: Node) -> str:
    if not node.children:
        return node.name
    parts = [_canonical(child) for child in node.children]
    if node.name in _COMMUTATIVE_OPS:
        parts.sort()
    return f"{node.name}({','.join(parts)})"
