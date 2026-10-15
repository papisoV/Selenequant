"""Pytest conftest for the pulse package.

Registers `ai_market_pulse` as a backward-compat alias for `selene_space_pulse`
before any pulse test module is imported. Legacy tests import from
`ai_market_pulse.*`; the alias redirects those imports to the current package.
"""

from __future__ import annotations

import importlib
import sys


class _AiMarketPulseAliasLoader:
    """Loader that returns the corresponding selene_space_pulse module.

    Critical: must return the SAME module object (not re-execute source),
    otherwise dataclass `__eq__` breaks because classes are defined twice
    under different module names.
    """

    def __init__(self, real_name: str):
        self._real_name = real_name

    def create_module(self, spec):
        return importlib.import_module(self._real_name)

    def exec_module(self, module):
        # Module is already fully loaded by create_module — no exec needed.
        pass


class _AiMarketPulseAliasFinder:
    """Meta-path finder: ai_market_pulse[.X] → selene_space_pulse[.X].

    Uses a custom loader that returns the already-imported real module so
    Python doesn't re-execute the source and create duplicate class objects.
    """

    def find_spec(self, fullname, path=None, target=None):
        from importlib.util import spec_from_loader

        if fullname != "ai_market_pulse" and not fullname.startswith("ai_market_pulse."):
            return None
        if fullname == "ai_market_pulse":
            real_name = "selene_space_pulse"
        else:
            real_name = "selene_space_pulse" + fullname[len("ai_market_pulse"):]
        return spec_from_loader(
            fullname,
            loader=_AiMarketPulseAliasLoader(real_name),
            origin=getattr(importlib.import_module(real_name), "__file__", None),
        )


if "ai_market_pulse" not in sys.modules:
    sys.meta_path.insert(0, _AiMarketPulseAliasFinder())
