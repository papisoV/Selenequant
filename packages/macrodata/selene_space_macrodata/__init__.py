"""selene_space_macrodata — macro economic data sources for crypto quant platform.

Sources (P0):
- FRED (U.S. Federal Reserve Economic Data)
- BIS (Bank for International Settlements)
- BLS (U.S. Bureau of Labor Statistics)

Sources (P1, fallback):
- IMF / DBnomics / akshare (selected crypto-relevant endpoints)

Each provider implements the `MacroDataSource` Protocol (see source.py).
All providers have healthcheck + retry + cached fallback (see fallback.py).
"""

from selene_space_macrodata.source import (
    MacroDataSource,
    MacroDataSourceConfig,
    MacroSeries,
    SeriesKind,
)
from selene_space_macrodata.registry import get_provider, register_provider
from selene_space_macrodata import providers  # noqa: F401  — auto-register FRED/BIS/BLS

__all__ = [
    "MacroDataSource",
    "MacroDataSourceConfig",
    "MacroSeries",
    "SeriesKind",
    "get_provider",
    "register_provider",
    "providers",
]
