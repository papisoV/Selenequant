"""Operator registry — name/arity only, no torch dependency.

This module is safe to import from any context (tests, pandas-only, etc.).
The torch-native callables live in ops.py and build OPS_CONFIG from this registry.
"""

# (name, arity)
OPERATOR_DEFS: list[tuple[str, int]] = [
    ("ADD", 2),
    ("SUB", 2),
    ("MUL", 2),
    ("DIV", 2),
    ("NEG", 1),
    ("ABS", 1),
    ("SIGN", 1),
    ("GATE", 3),
    ("JUMP", 1),
    ("DECAY", 1),
    ("DELAY1", 1),
    ("MAX3", 1),
    ("MEAN2", 2),
    # V4: Time-series operators
    ("TS_MEAN", 1),
    ("TS_STD", 1),
    ("TS_RANK", 1),
    ("TS_CORR", 2),
    ("RANK", 1),
    ("TS_DELTA", 1),
    ("TS_MAX", 1),
    ("TS_MIN", 1),
    # Windowed std variant (20-bar) for artifact chains — a
    # distinct operator so TS_STD stays burned in at its 5-bar window.
    ("TS_STD_20", 1),
    # Industry neutralization (DataFrame-level, variable arity)
    ("IND_NEUTRALIZE", -1),
]
