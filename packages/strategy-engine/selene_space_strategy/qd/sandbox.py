"""Restricted exec (cleanroom QD-style sandbox) for strategy scripts.

Strategy sources written for the QuantDinger script runtime execute as
opaque Python here, so every capability outside the whitelisted surface
is refused deterministically at compile time, with a matching runtime
``__import__`` shim as defense in depth:

* **Dunder access** — any attribute whose name starts with ``__`` (e.g.
  ``().__class__``) is refused, blocking the classic object-graph escape.
* **Format traversal** — ``str.format`` / ``format_map`` calls are refused;
  these were an actual Python sandbox escape vector in the wild.
* **Imports** — only ``math / pandas / numpy / json / collections`` may be
  imported inside a strategy; the lowercase name is compared after rebinding
  of ``import x as y`` and ``from x import y`` aliases.

The whitelist mirrors the *capabilities* QuantDinger grants strategy code
(``safe_exec.py``) but is implemented independently for this codebase.
Strategies that stay on numeric/pandas operators compile and run unchanged.
"""

from __future__ import annotations

import ast
import builtins
import collections
import json
import math
from types import CodeType

import numpy as np
import pandas as pd

IMPORT_WHITELIST = frozenset({"math", "pandas", "numpy", "json", "collections"})
# modules are pre-imported here so ``import math`` inside the sandbox binds
# the already-loaded object instead of paying a fresh import (or worse,
# observing a partially-initialized module).
_IMPORT_RESOLUTION = {
    "math": math,
    "pandas": pd,
    "numpy": np,
    "json": json,
    "collections": collections,
}

# Numeric / container builtins available to strategy code. Deliberately
# excludes open/eval/exec/compile/globals/locals/vars/getattr/setattr/type/
# super/__import__, anything that can reach interpreter or filesystem internals.
#
# ``hasattr`` is allowed although ``getattr`` is not: it returns only a bool and
# never hands back a reference, so combined with the dunder ban in
# ``_validate_ast`` it cannot walk the object graph. QuantDinger's own sandbox
# (``safe_exec.py``) classifies it the same way ("type checking, safe, no
# mutation"); QD strategies use ``if not hasattr(g, "x")`` to lazily initialise
# state on ``g``. The sibling type-checking builtins QD also allows
# (``isinstance`` / ``issubclass`` / ``callable``) are omitted until a strategy
# needs them -- same bool-only reasoning applies.
_BUILTIN_NAMES = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "bytes",
        "dict",
        "enumerate",
        "float",
        "frozenset",
        "hasattr",
        "int",
        "len",
        "list",
        "max",
        "min",
        "pow",
        "print",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "slice",
        "sorted",
        "str",
        "sum",
        "tuple",
        "zip",
    }
)


class SecurityError(Exception):
    """A strategy source used a capability outside the sandbox surface."""


def restricted_builtins() -> dict[str, object]:
    """Builtins dict for a sandbox namespace: whitelisted names + import shim."""
    surface: dict[str, object] = {
        name: getattr(builtins, name) for name in _BUILTIN_NAMES
    }
    surface["__import__"] = _restricted_import
    return surface


def _restricted_import(
    name: str,
    globals_: dict | None = None,
    locals_: dict | None = None,
    fromlist: tuple[str, ...] | None = None,
    level: int = 0,
) -> object:
    """``__import__`` shim: only the whitelisted modules resolve."""
    if level != 0 or name.split(".")[0] not in IMPORT_WHITELIST:
        raise SecurityError(f"import of {name!r} is not allowed in strategy source")
    stem = name.split(".")[0]
    module = _IMPORT_RESOLUTION[stem]
    if fromlist:
        return module
    return module


def _validate_ast(tree: ast.Module) -> None:
    """Reject dunder/format forgeries and out-of-whitelist imports."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr.startswith("__"):
            raise SecurityError(
                f"dunder attribute access {node.attr!r} is not allowed in strategy source"
            )
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in ("format", "format_map"):
                raise SecurityError(
                    f"str.{node.func.attr}() is not allowed in strategy source"
                )
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] not in IMPORT_WHITELIST:
                    raise SecurityError(
                        f"import of {alias.name!r} is not allowed in strategy source"
                    )
        elif isinstance(node, ast.ImportFrom):
            if node.level or (node.module or "").split(".")[0] not in IMPORT_WHITELIST:
                raise SecurityError(
                    f"from-import of {node.module!r} is not allowed in strategy source"
                )


def compile_strategy_source(
    source: str, *, filename: str = "<qd-strategy>"
) -> CodeType:
    """Parse + validate + compile one strategy source; raises SecurityError."""
    tree = ast.parse(source, filename=filename)
    _validate_ast(tree)
    return compile(tree, filename, "exec")


def make_namespace() -> dict[str, object]:
    """Fresh sandbox globals for exec(): whitelisted builtins only."""
    return {
        "__builtins__": restricted_builtins(),
        "__name__": "<qd-strategy>",
    }
