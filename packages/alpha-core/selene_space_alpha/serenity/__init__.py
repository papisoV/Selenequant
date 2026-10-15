"""Serenity — Crypto fundamental analysis framework for SeleneQuant.

Implements the Serenity manufacturing investment methodology adapted for
crypto: consensus scarcity, liquidity scarcity, trust scarcity, and
on-chain evidence chains.
"""

from .config import SerenityConfig
from .fetcher import SerenityFetcher
from .models import (
    GitHubActivity,
    ProtocolMetrics,
    SerenityResearchReport,
    SerenityScore,
    SerenitySnapshot,
)
from .scorer import SerenityScorer
from .signal_bridge import (
    SERENITY_FACTORS,
    SERENITY_LIVE_ONLY,
    SERENITY_POOL_FACTORS,
    SerenityDataMissingError,
)
from .workflow import SerenityWorkflow

__all__ = [
    "GitHubActivity",
    "ProtocolMetrics",
    "SERENITY_FACTORS",
    "SERENITY_LIVE_ONLY",
    "SERENITY_POOL_FACTORS",
    "SerenityDataMissingError",
    "SerenityConfig",
    "SerenityFetcher",
    "SerenityResearchReport",
    "SerenityScore",
    "SerenityScorer",
    "SerenitySnapshot",
    "SerenityWorkflow",
]
