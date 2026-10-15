"""Serenity data models — frozen dataclasses for fundamental analysis."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ProtocolMetrics:
    """Snapshot of a DeFi protocol from DeFi Llama."""

    protocol_id: str
    name: str
    category: str
    chains: tuple[str, ...]
    tvl_usd: float
    tvl_change_1d: float
    tvl_change_7d: float
    mcap_to_tvl: float
    fdv_to_tvl: float
    revenue_24h: float
    revenue_7d: float
    timestamp: int


@dataclass(frozen=True)
class GitHubActivity:
    """GitHub development activity for a protocol."""

    protocol_id: str
    repo_name: str
    stars: int
    commits_4w: int
    active_devs_4w: int
    open_issues: int
    closed_issues_4w: int
    timestamp: int


@dataclass(frozen=True)
class SerenitySnapshot:
    """Complete data snapshot for scoring a protocol/token."""

    symbol: str
    protocol_metrics: ProtocolMetrics | None
    github_activity: GitHubActivity | None
    funding_rate: float | None
    oi_change: float | None
    ls_ratio: float | None
    tvl_chain_share: float
    price_change_7d: float | None
    volume_change_7d: float | None
    timestamp: int


@dataclass(frozen=True)
class SerenityScore:
    """Result of Serenity 8-factor + 8-penalty scoring."""

    symbol: str
    # 8 positive factors (0..1 each)
    consensus_scarcity: float
    liquidity_scarcity: float
    trust_scarcity: float
    replication_difficulty: float
    certification_progress: float
    prepayment_signal: float
    decentralization: float
    evidence_strength: float
    # 8 penalties (0..1 each)
    penalty_fork_risk: float
    penalty_tvl_concentration: float
    penalty_dev_exodus: float
    penalty_revenue_decline: float
    penalty_oi_divergence: float
    penalty_whale_exodus: float
    penalty_governance_risk: float
    penalty_liquidity_trap: float
    # Composites
    raw_score: float
    normalized_score: float
    tier: str
    timestamp: int
    # Enrichment fields (from local DBs, None if unavailable)
    funding_rate: float | None = None
    oi_change: float | None = None
    ls_ratio: float | None = None
    price_change_7d: float | None = None
    volume_change_7d: float | None = None


@dataclass(frozen=True)
class SerenityResearchReport:
    """Full research output from a two-tier scan."""

    symbol: str
    tier1: SerenityScore
    tier2: SerenityScore | None
    narrative: str
    system_change: str
    scarce_constraints: list[str]
    blind_spots: list[str]
    disproof_conditions: list[str]
    signal_direction: int
    signal_strength: float
    elapsed_seconds: float
    portfolio: Any | None = None
    ablation: Any | None = None
