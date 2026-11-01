"""AlphaPurify-compatible preprocessing toolbox — 42 methods + registry.

Ported from AlphaPurify (MIT, Copyright (c) 2026 Elias Wu). Selene uses a
pandas/numpy/scipy/sklearn rewrite (no polars) so the toolbox can be consumed
with the existing alpha-core stack.

Public API:
    - :func:`list_methods` — 42 registered method names (12w + 15n + 15s)
    - :func:`apply` — chain :class:`PreprocessConfig` steps over a DataFrame
    - :func:`winsorize` / :func:`neutralize` / :func:`standardize` — method modules
    - :class:`PreprocessConfig` / :class:`PreprocessMethod`
"""
from __future__ import annotations

from . import neutralize, standardize, winsorize
from .config import PreprocessConfig
from .errors import PreprocessError, PreprocessMethodError, PreprocessParamError
from .registry import METHOD_REGISTRY, PreprocessMethod, apply, list_methods

__all__ = [
    "PreprocessConfig",
    "PreprocessError",
    "PreprocessMethod",
    "PreprocessMethodError",
    "PreprocessParamError",
    "METHOD_REGISTRY",
    "apply",
    "list_methods",
    "neutralize",
    "standardize",
    "winsorize",
]
