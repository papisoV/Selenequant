"""Pandas-native factor research toolkit for SeleneQuant.

Ported from AlphaPurify (MIT); see THIRD_PARTY_NOTICES.md in this package.

- ``preprocessing``: the 42-method preprocessing toolbox (12 winsorize /
  15 neutralize / 15 standardize), registered in :data:`~.preprocessing.METHOD_REGISTRY`.
- ``mining``: the weight-drift quantile backtest and the IC diagnostic panel.

Independence: this package depends only on pandas, numpy, scipy and
scikit-learn. It does **not** import torch or polars (unlike
``selene_space_alpha.mining``).
"""

from .preprocessing import (
    METHOD_REGISTRY,
    PreprocessConfig,
    PreprocessError,
    PreprocessMethod,
    PreprocessMethodError,
    PreprocessParamError,
    apply,
    list_methods,
)

__all__ = [
    "METHOD_REGISTRY",
    "PreprocessConfig",
    "PreprocessError",
    "PreprocessMethod",
    "PreprocessMethodError",
    "PreprocessParamError",
    "apply",
    "list_methods",
]
