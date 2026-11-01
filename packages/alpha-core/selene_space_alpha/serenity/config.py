"""Serenity configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SerenityConfig:
    """Configuration for Serenity fundamental analysis."""

    # Data sources
    defi_llama_base_url: str = "https://api.llama.xyz"
    github_api_base_url: str = "https://api.github.com"
    github_token: str = ""
    glassnode_api_key: str = ""

    # Cache
    cache_ttl_seconds: int = 300

    # Scoring weights (must sum to 1.0 for factors, 1.0 for penalties)
    factor_weights: tuple[float, ...] = (
        0.15, 0.15, 0.10, 0.12, 0.10, 0.15, 0.10, 0.13,
    )
    penalty_weights: tuple[float, ...] = (
        0.15, 0.15, 0.10, 0.15, 0.10, 0.10, 0.10, 0.15,
    )

    # Tier thresholds
    strong_bullish_th: float = 0.70
    bullish_th: float = 0.55
    bearish_th: float = 0.35
    strong_bearish_th: float = 0.20

    # Sigmoid steepness: normalized = sigmoid(raw * sigmoid_slope).
    # 6.0 put the bullish threshold (0.55) at raw ~0.033 — a 0.5/0.5 profile with
    # a sliver of data read bullish. 3.0 raises it to raw ~0.067 and strong_bullish
    # (0.70) to raw ~0.28, so tiers align with meaningful raw values.
    sigmoid_slope: float = 3.0

    # Consensus scarcity constants (previously hardcoded in scorer)
    btc_consensus_score: float = 0.95
    eth_consensus_score: float = 0.70
    # TVL log10 range [min, max] mapping a protocol's TVL to consensus scarcity
    consensus_tvl_log10_range: tuple[float, float] = (6.0, 10.0)
    # mcap/fdv-to-TVL ratio bounds for liquidity scarcity (1/ratio mapped in)
    liquidity_ratio_bounds: tuple[float, float] = (0.1, 2.0)
    # mcap_to_tvl above this flags governance risk
    governance_mcap_tvl_threshold: float = 20.0

    # Theme pool scanner
    pool_max_universe: int = 30
    pool_min_pool: int = 20
    pool_github_enabled: bool = True
    pool_rate_limit_seconds: float = 0.0
    pool_top_n: int = 7
    # Below this evidence_strength a candidate is flagged weak-evidence and is
    # listed in the full ranking but ineligible for top_picks.
    evidence_top_pick_threshold: float = 0.15

    # Workflow time budgets
    quick_scan_budget_seconds: float = 1800.0
    deep_verify_budget_seconds: float = 3600.0

    # Ablation settings
    ablation_enabled: bool = False
    ablation_vif_threshold: float = 10.0
    ablation_marginal_r2_threshold: float = 0.01

    def __post_init__(self) -> None:
        # Allow env var overrides
        env_defi_llama = os.environ.get("SELENE_DEFI_LLAMA_BASE_URL", "")
        if env_defi_llama:
            object.__setattr__(self, "defi_llama_base_url", env_defi_llama)
        if not self.github_token:
            object.__setattr__(self, "github_token", os.environ.get("GITHUB_TOKEN", ""))
        if not self.glassnode_api_key:
            object.__setattr__(
                self, "glassnode_api_key", os.environ.get("GLASSNODE_API_KEY", "")
            )
