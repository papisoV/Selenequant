"""Analytics subpackage — provider-based integration of 8 high-value libraries.

Wraps quantstats / pyportfolioopt / skfolio / statsmodels / ffn / tsmoothie /
gluonts / finrl into a unified AnalyticsProvider Protocol. Each provider is a
thin adapter that exposes a small surface area (compute / describe / healthcheck)
so the API layer can route requests without knowing library specifics.

Design aligned with selene_space_macrodata.source (Protocol + Registry pattern).
"""

from selene_space_alpha.analytics.base import (
    AnalyticsProvider,
    AnalyticsProviderConfig,
    AnalyticsReport,
    AnalyticsReportKind,
)
from selene_space_alpha.analytics.registry import (
    get_provider,
    list_registered,
    register_provider,
)
from selene_space_alpha.analytics import providers  # noqa: F401  — auto-register

__all__ = [
    "AnalyticsProvider",
    "AnalyticsProviderConfig",
    "AnalyticsReport",
    "AnalyticsReportKind",
    "get_provider",
    "list_registered",
    "register_provider",
    "providers",
]
