# Cleanroom implementation: design intent derived from prior art; independent expression.
"""DSL configuration constants for the formula search layer.

**冻结后不可变 (frozen, never mutate after import).** The search and validation
layers read these module constants at import time and assume they never change
for the life of the process. Treat them as a frozen contract: if a constant
must change, bump :data:`DSL_VERSION` and treat the new value as a new
contract — do not silently mutate the existing bindings.

``FEATURE_TOKEN_NAMES`` / ``OPERATOR_TOKEN_NAMES`` are direct views of
``selene_space_alpha.vocab.FORMULA_VOCAB`` (immutable tuples), and
``MAX_FORMULA_LENGTH`` caps the token-sequence length the genetic search is
allowed to emit.
"""

from __future__ import annotations

from selene_space_alpha.vocab import FORMULA_VOCAB

__all__ = [
    "DSL_VERSION",
    "FEATURE_TOKEN_NAMES",
    "OPERATOR_TOKEN_NAMES",
    "MAX_FORMULA_LENGTH",
]

DSL_VERSION: str = "1.0"

FEATURE_TOKEN_NAMES: tuple[str, ...] = FORMULA_VOCAB.feature_names
OPERATOR_TOKEN_NAMES: tuple[str, ...] = FORMULA_VOCAB.operator_names

MAX_FORMULA_LENGTH: int = 64
