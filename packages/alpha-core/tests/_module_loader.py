"""Shared synthetic-package loader for path-loaded factor-research modules.

The weight-drift and IC-panel mining modules live in the standalone
``factor-research`` package. These tests load them by file path under a
synthetic package name so the suite runs without installing the package;
coverage then attributes them via path-form ``--cov``. ``ic_decay`` stays
in alpha-core and is loaded from its original dir.
"""

from __future__ import annotations

import importlib.util
import pathlib
import sys
import types

MINING_DIR = (
    pathlib.Path(__file__).resolve().parent.parent.parent
    / "factor-research"
    / "selene_space_factor_research"
    / "mining"
)
ALPHA_MINING_DIR = pathlib.Path(__file__).resolve().parent.parent / "selene_space_alpha" / "mining"

_LOADED: dict[str, types.ModuleType] = {}


def load_module(name: str, src_dir: pathlib.Path, package: str) -> types.ModuleType:
    if name in _LOADED:
        return _LOADED[name]
    pkg = sys.modules.get(package)
    if pkg is None:
        pkg = types.ModuleType(package)
        pkg.__path__ = [str(src_dir)]
        sys.modules[package] = pkg
    spec = importlib.util.spec_from_file_location(f"{package}.{name}", src_dir / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"{package}.{name}"] = mod
    spec.loader.exec_module(mod)
    _LOADED[name] = mod
    return mod
