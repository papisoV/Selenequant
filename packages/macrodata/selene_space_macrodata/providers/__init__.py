"""Macro data providers — P0 set: FRED, BIS, BLS.

Importing this package auto-registers all providers.
"""

from selene_space_macrodata.providers import (
    altme_fng,
    bis,
    bls,
    cnn_fng,
    coingecko_global,
    fred,  # noqa: F401
)

__all__ = ["fred", "bis", "bls", "altme_fng", "cnn_fng", "coingecko_global"]
