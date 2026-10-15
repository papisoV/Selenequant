"""Sandbox gates: AST dunder/format rejection and import whitelist.

Every rejected capability must raise :class:`SecurityError` at compile time;
a well-formed strategy (params, initialize, handle_data) compiles and its
handlers resolve for use by the runtime.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from selene_space_strategy.qd import (
    SecurityError,
    compile_qd_strategy,
    compile_strategy_source,
    parse_params,
)
from selene_space_strategy.qd.sandbox import make_namespace, restricted_builtins

_VALID_SOURCE = """\
# @param entry_threshold float 0.05
# @param target_pct float 0.95
import math
import pandas as pd

def _helper(close):
    return close.astype(float)

def initialize(context):
    g.symbol = "Crypto:XLM/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(5)
    context.set_metadata(direction_mode="both")
    context.allow_leverage(max_leverage=3)

def handle_data(context, data):
    close = _helper(get_history(5, "1h", ["close"], g.symbol)["close"])
    if len(close) < 5 or g.counter == 0:
        return
    order_target_percent(g.symbol, abs(math.tanh(float(close.iloc[-1]))), position_side="long", reason="x")
"""


def test_valid_source_compiles_and_resolves_handlers() -> None:
    compiled = compile_qd_strategy(_VALID_SOURCE, source_name="mini")
    assert compiled.handle_data is not None
    assert compiled.initialize is not None
    assert compiled.param_defaults == {"entry_threshold": 0.05, "target_pct": 0.95}
    assert compiled.module_globals["_helper"] is not None
    state = compiled.new_g()
    assert "g" in compiled.module_globals and compiled.module_globals["g"] is state


def test_parse_params_ignores_non_param_comments() -> None:
    source = "# @param a float 1.0\n# not a param\n# @param b int 2\n"
    assert parse_params(source) == {"a": 1.0, "b": 2.0}


def test_dunder_attribute_access_rejected() -> None:
    with pytest.raises(SecurityError):
        compile_strategy_source("x = ().__class__\n")


def test_format_map_rejected() -> None:
    with pytest.raises(SecurityError):
        compile_strategy_source('x = "{}".format(1)\n')


def test_format_rejected() -> None:
    with pytest.raises(SecurityError):
        compile_strategy_source('x = "{}".format_map({})\n')


def test_forbidden_import_rejected() -> None:
    for forbidden in ("import os", "import sys", "from pathlib import Path"):
        with pytest.raises(SecurityError):
            compile_strategy_source(f"{forbidden}\n")


@pytest.mark.parametrize(
    "prefix", ["import math", "from math import sqrt", "import numpy"]
)
def test_whitelisted_import_compiles(prefix: str) -> None:
    # no raise == the whitelisted import passed the AST gate
    compile_strategy_source(f"{prefix}\nx = 1\n")


def test_missing_handle_data_rejected() -> None:
    with pytest.raises(ValueError):
        compile_qd_strategy("g.x = 1\n", source_name="no-handler")


def test_hasattr_runs_lazy_state_init() -> None:
    """QD sources lazily initialise state via ``if not hasattr(g, "x")``."""
    ns = make_namespace()
    ns["g"] = SimpleNamespace()
    exec(
        compile_strategy_source('if not hasattr(g, "f_count"):\n    g.f_count = 0\n'),
        ns,
    )
    g_state = ns["g"]
    assert isinstance(g_state, SimpleNamespace)
    assert g_state.f_count == 0


@pytest.mark.parametrize(
    "name", ["getattr", "setattr", "type", "eval", "exec", "open", "globals", "locals"]
)
def test_escape_builtins_remain_unavailable(name: str) -> None:
    """``hasattr`` is bool-only; the object-graph builtins stay out."""
    assert name not in restricted_builtins()
