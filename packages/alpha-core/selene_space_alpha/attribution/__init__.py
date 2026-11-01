"""Factor risk attribution — exposure estimation, PnL decomposition, risk decomposition,
and Paper-to-Live fill deviation analysis."""

from .factor_model import (
    CRYPTO_FACTORS,
    FactorAttribution,
    FactorExposure,
    RiskDecomposition,
    build_crypto_factor_returns,
    build_factor_exposures,
    decompose_risk,
    factor_attribution,
)
from .sim_live_deviation import (
    DeviationReport,
    FillRecord,
    aggregate_deviations,
    compare_fills,
    detect_anomalies,
)

__all__ = [
    "CRYPTO_FACTORS",
    "DeviationReport",
    "FactorAttribution",
    "FactorExposure",
    "FillRecord",
    "RiskDecomposition",
    "aggregate_deviations",
    "build_crypto_factor_returns",
    "build_factor_exposures",
    "compare_fills",
    "decompose_risk",
    "detect_anomalies",
    "factor_attribution",
]
