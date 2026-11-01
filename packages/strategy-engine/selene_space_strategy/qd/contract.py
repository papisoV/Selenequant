"""Compile a QuantDinger-style strategy source into callable handlers.

The source is validated + exec'd once into a sandbox namespace; the
``initialize`` / ``handle_data`` functions and any module-level helpers they
reference are resolved from that namespace and made available through
:class:`QDCompiledStrategy`. ``# @param name type default ...`` lines are
parsed into ``param_defaults`` so ``context.params.get(name, default)`` has
real values at run time.

The ``g`` object is a plain ``SimpleNamespace`` that the runtime re-creates
and re-injects per run/session (see ``QDStrategyRuntime.reset``) — the
functions resolve the *name* ``g`` from the module globals at call time, so
rebinding the global gives the compiled source a fresh cross-bar state.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable

from selene_space_strategy.qd.sandbox import (
    SecurityError,
    compile_strategy_source,
    make_namespace,
)

_PARAM_RE = re.compile(
    r"^#\s*@param\s+([A-Za-z_]\w*)\s+([A-Za-z_]+)\s+(-?\d+(?:\.\d+)?)\b"
)

#: QD ``# @param`` declarations carry a type token; ``int``-declared params
#: must come back as ``int`` -- sources slice frames with them
#: (``h["high"].iloc[-(lookback + 1):-1]``) and float slice bounds raise.
_INT_TYPES = frozenset({"int", "Int", "INT"})


def parse_params(source: str) -> dict[str, float | int]:
    """Extract ``# @param name type default`` defaults from source comments."""
    params: dict[str, float | int] = {}
    for line in source.splitlines():
        match = _PARAM_RE.match(line.strip())
        if match:
            try:
                value = float(match.group(3))
            except ValueError:
                continue
            params[match.group(1)] = (
                int(value) if match.group(2) in _INT_TYPES else value
            )
    return params


@dataclass(frozen=True)
class QDCompiledStrategy:
    """One exec'd strategy: callable handlers + parsed defaults + source name."""

    source_name: str
    module_globals: dict[str, object]
    param_defaults: dict[str, float | int]
    initialize: Callable[..., Any] | None
    handle_data: Callable[..., Any]

    def new_g(self) -> SimpleNamespace:
        """Create a fresh cross-bar state and bind it as the global ``g``."""
        state = SimpleNamespace()
        self.module_globals["g"] = state
        return state


def compile_qd_strategy(
    source: str, *, source_name: str = "<qd-strategy>"
) -> QDCompiledStrategy:
    """Validate, exec, and resolve a strategy source into handlers."""
    code = compile_strategy_source(source, filename=source_name)
    globals_ = make_namespace()
    try:
        exec(code, globals_)  # noqa: S102 - intentionally runs sandboxed source
    except SecurityError:
        raise
    except Exception as exc:  # noqa: BLE001 - surface source errors verbatim
        raise ValueError(f"strategy source failed at import: {exc}") from exc

    handle_data = globals_.get("handle_data")
    if not callable(handle_data):
        raise ValueError("strategy source defines no callable handle_data")

    initialize = globals_.get("initialize")
    return QDCompiledStrategy(
        source_name=source_name,
        module_globals=globals_,
        param_defaults=parse_params(source),
        initialize=initialize if callable(initialize) else None,
        handle_data=handle_data,
    )
