"""Analytics providers — auto-register all on import.

Importing this package auto-registers: quantstats, pyportfolioopt, skfolio,
statsmodels, ffn, tsmoothie, gluonts, finrl.
"""

from selene_space_alpha.analytics.providers import (  # noqa: F401
    ffn_provider,
    finrl_provider,
    gluonts_provider,
    pyportfolioopt_provider,
    quantstats_provider,
    skfolio_provider,
    statsmodels_provider,
    tsmoothie_provider,
)

__all__ = [
    "quantstats_provider",
    "pyportfolioopt_provider",
    "skfolio_provider",
    "statsmodels_provider",
    "ffn_provider",
    "tsmoothie_provider",
    "gluonts_provider",
    "finrl_provider",
]
