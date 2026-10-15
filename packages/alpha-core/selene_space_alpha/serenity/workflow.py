"""Two-tier Serenity research workflow.

Tier 1 (Quick Scan): Fetch DeFi Llama + GitHub data, run scoring, generate narrative.
Tier 2 (Deep Verification): For high-conviction results, cross-reference with
derivatives data and generate disproof conditions.
"""

from __future__ import annotations

import asyncio
import logging
import time

from .config import SerenityConfig
from .fetcher import SerenityFetcher
from .models import SerenityResearchReport, SerenityScore, SerenitySnapshot
from .scorer import SerenityScorer

logger = logging.getLogger(__name__)

# Single deep-verify refresh is bounded so a stalled external source (DeFi
# Llama / GitHub via Clash 7897 in CN) can't hold the requesting API call
# for the full 30s client timeout. On timeout we fall back to the tier-1
# report instead of leaving the user hanging.
_DEEP_VERIFY_TIMEOUT_S = 15.0


class SerenityWorkflow:
    """Two-tier research workflow: quick scan + deep verification."""

    def __init__(
        self,
        fetcher: SerenityFetcher,
        scorer: SerenityScorer,
        config: SerenityConfig | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._scorer = scorer
        self._config = config or SerenityConfig()

    async def quick_scan(self, symbol: str) -> SerenityResearchReport:
        """Tier 1: Quick scan with DeFi Llama + GitHub data."""
        start = time.time()
        symbol = symbol.upper().replace("/USDT", "").replace("USDT", "")

        snapshot = await self._fetcher.get_serenity_snapshot(symbol)
        score = self._scorer.score(snapshot)

        elapsed = time.time() - start
        logger.info("Quick scan %s: score=%.3f tier=%s (%.1fs)", symbol, score.normalized_score, score.tier, elapsed)

        # Create score with enrichment fields from snapshot
        score = SerenityScore(
            symbol=score.symbol,
            consensus_scarcity=score.consensus_scarcity,
            liquidity_scarcity=score.liquidity_scarcity,
            trust_scarcity=score.trust_scarcity,
            replication_difficulty=score.replication_difficulty,
            certification_progress=score.certification_progress,
            prepayment_signal=score.prepayment_signal,
            decentralization=score.decentralization,
            evidence_strength=score.evidence_strength,
            penalty_fork_risk=score.penalty_fork_risk,
            penalty_tvl_concentration=score.penalty_tvl_concentration,
            penalty_dev_exodus=score.penalty_dev_exodus,
            penalty_revenue_decline=score.penalty_revenue_decline,
            penalty_oi_divergence=score.penalty_oi_divergence,
            penalty_whale_exodus=score.penalty_whale_exodus,
            penalty_governance_risk=score.penalty_governance_risk,
            penalty_liquidity_trap=score.penalty_liquidity_trap,
            raw_score=score.raw_score,
            normalized_score=score.normalized_score,
            tier=score.tier,
            timestamp=score.timestamp,
            funding_rate=snapshot.funding_rate,
            oi_change=snapshot.oi_change,
            ls_ratio=snapshot.ls_ratio,
            price_change_7d=snapshot.price_change_7d,
            volume_change_7d=snapshot.volume_change_7d,
        )

        narrative = self._generate_narrative(snapshot, score)
        system_change = self._infer_system_change(snapshot, score)
        scarce = self._identify_scarce_constraints(score)
        blind = self._identify_blind_spots(score)
        disproof = self._identify_disproof_conditions(snapshot, score)
        direction = self._signal_direction(score)
        strength = abs(score.normalized_score - 0.5) * 2.0

        return SerenityResearchReport(
            symbol=symbol,
            tier1=score,
            tier2=None,
            narrative=narrative,
            system_change=system_change,
            scarce_constraints=scarce,
            blind_spots=blind,
            disproof_conditions=disproof,
            signal_direction=direction,
            signal_strength=strength,
            elapsed_seconds=elapsed,
            portfolio=None,
            ablation=self._run_ablation_if_enabled([snapshot]) if self._config.ablation_enabled else None,
        )

    async def deep_verify(self, symbol: str, tier1: SerenityResearchReport) -> SerenityResearchReport:
        """Tier 2: Deep verification for high-conviction results."""
        start = time.time()
        symbol = symbol.upper().replace("/USDT", "").replace("USDT", "")

        try:
            async with asyncio.timeout(_DEEP_VERIFY_TIMEOUT_S):
                snapshot = await self._fetcher.get_serenity_snapshot(symbol, force_refresh=True)
        except asyncio.TimeoutError:
            logger.warning(
                "Serenity deep_verify refresh timed out for %s (>=%.0fs), falling back to tier-1",
                symbol, _DEEP_VERIFY_TIMEOUT_S,
            )
            return tier1
        except Exception as exc:
            logger.warning("Serenity deep_verify refresh failed for %s: %s", symbol, exc)
            return tier1

        # Re-score with potential additional data
        tier2_score = self._scorer.score(snapshot)

        # Enrich score with snapshot fields
        tier2_score = SerenityScore(
            symbol=tier2_score.symbol,
            consensus_scarcity=tier2_score.consensus_scarcity,
            liquidity_scarcity=tier2_score.liquidity_scarcity,
            trust_scarcity=tier2_score.trust_scarcity,
            replication_difficulty=tier2_score.replication_difficulty,
            certification_progress=tier2_score.certification_progress,
            prepayment_signal=tier2_score.prepayment_signal,
            decentralization=tier2_score.decentralization,
            evidence_strength=tier2_score.evidence_strength,
            penalty_fork_risk=tier2_score.penalty_fork_risk,
            penalty_tvl_concentration=tier2_score.penalty_tvl_concentration,
            penalty_dev_exodus=tier2_score.penalty_dev_exodus,
            penalty_revenue_decline=tier2_score.penalty_revenue_decline,
            penalty_oi_divergence=tier2_score.penalty_oi_divergence,
            penalty_whale_exodus=tier2_score.penalty_whale_exodus,
            penalty_governance_risk=tier2_score.penalty_governance_risk,
            penalty_liquidity_trap=tier2_score.penalty_liquidity_trap,
            raw_score=tier2_score.raw_score,
            normalized_score=tier2_score.normalized_score,
            tier=tier2_score.tier,
            timestamp=tier2_score.timestamp,
            funding_rate=snapshot.funding_rate,
            oi_change=snapshot.oi_change,
            ls_ratio=snapshot.ls_ratio,
            price_change_7d=snapshot.price_change_7d,
            volume_change_7d=snapshot.volume_change_7d,
        )

        # Cross-validate with tier1
        tier1_score = tier1.tier1
        divergence = abs(tier2_score.normalized_score - tier1_score.normalized_score)
        if divergence > 0.15:
            logger.warning(
                "Deep verify %s: significant divergence %.3f vs %.3f",
                symbol, tier1_score.normalized_score, tier2_score.normalized_score,
            )

        elapsed = time.time() - start
        logger.info(
            "Deep verify %s: score=%.3f tier=%s (%.1fs)",
            symbol, tier2_score.normalized_score, tier2_score.tier, elapsed,
        )

        # Update narrative with deeper analysis
        narrative = self._generate_narrative(snapshot, tier2_score)
        if divergence > 0.1:
            narrative += f" [Divergence from quick scan: {divergence:.1%}]"

        disproof = self._identify_disproof_conditions(snapshot, tier2_score)
        if tier1.disproof_conditions:
            disproof = list(set(disproof + tier1.disproof_conditions))

        direction = self._signal_direction(tier2_score)
        strength = abs(tier2_score.normalized_score - 0.5) * 2.0

        return SerenityResearchReport(
            symbol=symbol,
            tier1=tier1_score,
            tier2=tier2_score,
            narrative=narrative,
            system_change=tier1.system_change,
            scarce_constraints=tier1.scarce_constraints,
            blind_spots=self._identify_blind_spots(tier2_score),
            disproof_conditions=disproof,
            signal_direction=direction,
            signal_strength=strength,
            elapsed_seconds=tier1.elapsed_seconds + elapsed,
            portfolio=None,
            ablation=None,
        )

    async def scan_and_verify(self, symbol: str) -> SerenityResearchReport:
        """Run Tier 1, then Tier 2 if score is high-conviction."""
        report = await self.quick_scan(symbol)
        if abs(report.tier1.normalized_score - 0.5) > 0.15:
            report = await self.deep_verify(symbol, report)
        return report

    # --- Narrative generation (rule-based, no LLM) ---

    def _generate_narrative(self, snapshot: SerenitySnapshot, score: SerenityScore) -> str:
        parts: list[str] = []
        sym = snapshot.symbol

        if score.consensus_scarcity > 0.7:
            parts.append("strong consensus scarcity")
        elif score.consensus_scarcity < 0.3:
            parts.append("weak consensus scarcity (easily replicable)")

        if score.liquidity_scarcity > 0.7:
            parts.append("deep TVL lockup vs FDV")
        elif score.liquidity_scarcity < 0.3:
            parts.append("thin TVL relative to valuation")

        if score.prepayment_signal > 0.6:
            parts.append("staking/revenue momentum accelerating")
        elif score.prepayment_signal < 0.3:
            parts.append("staking/revenue momentum declining")

        if score.replication_difficulty > 0.7:
            parts.append("high replication barrier")

        if score.trust_scarcity > 0.6:
            parts.append(f"strong dev trust ({snapshot.github_activity.stars:,} stars)" if snapshot.github_activity else "strong trust profile")

        if score.evidence_strength < 0.5:
            parts.append("limited on-chain data")

        if score.penalty_fork_risk > 0.5:
            parts.append("elevated fork risk")
        if score.penalty_oi_divergence > 0.4:
            parts.append("OI divergence (bearish)")
        if score.penalty_whale_exodus > 0.4:
            parts.append("large TVL outflows")
        if score.penalty_liquidity_trap > 0.4:
            parts.append("liquidity trap risk")
        if score.penalty_dev_exodus > 0.5:
            parts.append("developer exodus risk")

        if not parts:
            tier_label = score.tier.replace("_", " ")
            return f"{sym}: {tier_label} fundamental profile (score {score.normalized_score:.2f})"

        return f"{sym}: " + "; ".join(parts)

    def _infer_system_change(self, snapshot: SerenitySnapshot, score: SerenityScore) -> str:
        if snapshot.protocol_metrics is None:
            return "Insufficient data to identify system change"
        pm = snapshot.protocol_metrics
        if pm.tvl_change_7d > 0.1:
            return f"Rapid TVL growth (+{pm.tvl_change_7d:.0%} 7d) indicates demand surge"
        if pm.tvl_change_7d < -0.1:
            return f"TVL decline ({pm.tvl_change_7d:.0%} 7d) signals potential regime shift"
        if score.prepayment_signal > 0.6:
            return "Staking/revenue growth suggests ecosystem maturation"
        return "No clear system change identified"

    def _identify_scarce_constraints(self, score: SerenityScore) -> list[str]:
        constraints: list[str] = []
        if score.consensus_scarcity > 0.6:
            constraints.append("Consensus mechanism is scarce and hard to replicate")
        if score.liquidity_scarcity > 0.6:
            constraints.append("Deep TVL creates liquidity moat")
        if score.trust_scarcity > 0.6:
            constraints.append("High developer trust and security track record")
        if score.replication_difficulty > 0.6:
            constraints.append("Fork cannot replicate TVL + user base")
        if not constraints:
            constraints.append("No dominant scarce constraint identified")
        return constraints

    def _identify_blind_spots(self, score: SerenityScore) -> list[str]:
        spots: list[str] = []
        if score.evidence_strength < 0.5:
            spots.append("Limited on-chain data available — market may underprice uncertainty")
        if score.liquidity_scarcity > 0.6 and score.prepayment_signal > 0.5:
            spots.append("TVL growth + staking momentum not yet reflected in price")
        if score.consensus_scarcity > 0.7 and score.decentralization > 0.5:
            spots.append("Decentralized + scarce consensus — undervalued network effect")
        if not spots:
            spots.append("No obvious blind spots identified")
        return spots

    def _identify_disproof_conditions(self, snapshot: SerenitySnapshot, score: SerenityScore) -> list[str]:
        conditions: list[str] = []
        if score.liquidity_scarcity > 0.5:
            conditions.append("TVL drops >20% in 7 days would disprove scarcity thesis")
        if score.prepayment_signal > 0.5:
            conditions.append("Revenue declining 3 consecutive weeks would break momentum thesis")
        if score.penalty_fork_risk > 0.4:
            conditions.append("Successful competitive fork capturing >10% TVL would break moat thesis")
        if score.consensus_scarcity > 0.6:
            conditions.append("Chain halt or consensus failure would break trust thesis")
        if not conditions:
            conditions.append("Generic: price dropping >30% on no news would disprove bullish thesis")
        return conditions

    def _signal_direction(self, score: SerenityScore) -> int:
        ns = score.normalized_score
        if ns > 0.6:
            return 1
        if ns < 0.4:
            return -1
        return 0

    def _run_ablation_if_enabled(self, snapshots: list[SerenitySnapshot]):
        """Run ablation analysis if enabled in config. Returns AblationReport or None."""
        if not self._config.ablation_enabled or len(snapshots) < 5:
            return None
        try:
            from .ablation import SerenityAblation
            ablation = SerenityAblation(
                self._scorer,
                vif_threshold=self._config.ablation_vif_threshold,
                marginal_r2_threshold=self._config.ablation_marginal_r2_threshold,
            )
            report = ablation.analyze(snapshots)
            if report.n_redundant > 0:
                logger.warning(
                    "Ablation: %d/%d factors are redundant (recommendation: %s)",
                    report.n_redundant, len(report.factor_results), report.recommendation,
                )
            return report
        except Exception as e:
            logger.warning("Ablation analysis failed: %s", e)
            return None
