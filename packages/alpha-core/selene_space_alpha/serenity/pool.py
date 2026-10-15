"""Serenity theme candidate-pool scanner.

Adapts the supply-chain-bottleneck methodology onto the existing crypto
8-factor scorer: given a theme, build a >=20 candidate universe from DeFi
Llama /protocols, score each with SerenityScorer, rank scarce-layer-first
(the 6 structural factors, before the demand-momentum factor), and surface
top 3-7 picks with evidence labels and bear-case notes.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from .config import SerenityConfig
from .fetcher import SerenityFetcher
from .models import SerenityScore
from .scorer import SerenityScorer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Theme registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThemeSpec:
    """How to build a theme's candidate universe from DeFi Llama protocols."""

    name: str
    categories: tuple[str, ...] = ()
    chains: tuple[str, ...] = ()
    keywords: tuple[str, ...] = ()
    curated_symbols: tuple[str, ...] = ()


THEMES: dict[str, ThemeSpec] = {
    "lending": ThemeSpec(
        name="Lending",
        categories=("Lending", "CDP", "NFT Lending"),
    ),
    "dex": ThemeSpec(name="DEX", categories=("DEX",)),
    "yield": ThemeSpec(
        name="Yield",
        categories=("Yield", "Yield Aggregator"),
    ),
    "restaking": ThemeSpec(
        name="Restaking / Staking",
        categories=("Restaking", "Liquid Staking", "Staking"),
    ),
    "derivatives": ThemeSpec(
        name="Derivatives",
        categories=("Derivatives",),
    ),
    "l2": ThemeSpec(
        name="Layer 2",
        categories=("Chain",),
        chains=(
            "Arbitrum", "Optimism", "Base", "Starknet", "zkSync Era", "Linea",
            "Scroll", "Mantle", "Metis", "opBNB",
        ),
        curated_symbols=("ARB", "OP", "STRK", "LRC"),
    ),
    "ai": ThemeSpec(
        name="AI / Agents",
        keywords=("ai", "agent", "neural", "intelligence", "fetch"),
    ),
}


# ---------------------------------------------------------------------------
# Pool data models
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PoolCandidate:
    """One scored candidate in a theme pool, ranked cross-sectionally."""

    symbol: str
    name: str
    category: str
    chains: tuple[str, ...]
    normalized_score: float
    tier: str
    scarce_layer_score: float
    evidence_strength: float
    evidence_label: str
    rationale: str
    top_penalty: str
    disproof: tuple[str, ...] = ()


@dataclass(frozen=True)
class PoolLayerSummary:
    """Which category the scarce candidates cluster in (the "bottleneck layer")."""

    layer: str
    count: int
    top_scarce_symbols: tuple[str, ...]


@dataclass(frozen=True)
class SerenityPoolScan:
    """Full result of a theme pool scan."""

    theme: str
    theme_name: str
    scanned_at: int
    universe_size: int
    layer_summary: tuple[PoolLayerSummary, ...]
    ranked: tuple[PoolCandidate, ...]
    top_picks: tuple[str, ...]
    weak_evidence_count: int


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------


# The 6 "structural moat" factors. prepayment_signal is demand momentum and
# evidence_strength is data quality — neither is a scarcity signal, so both
# are excluded from the scarce-layer rank.
_STRUCTURAL_FACTOR_NAMES = (
    "consensus_scarcity",
    "liquidity_scarcity",
    "trust_scarcity",
    "replication_difficulty",
    "certification_progress",
    "decentralization",
)


class SerenityPoolScanner:
    """Build and rank a theme candidate pool with the Serenity 8-factor scorer."""

    def __init__(
        self,
        fetcher: SerenityFetcher,
        scorer: SerenityScorer,
        config: SerenityConfig | None = None,
        score_db: Any | None = None,
        pool_db: Any | None = None,
    ) -> None:
        self._fetcher = fetcher
        self._scorer = scorer
        self._config = config or SerenityConfig()
        self._score_db = score_db
        self._pool_db = pool_db

    async def scan(self, theme: str, top_n: int | None = None) -> SerenityPoolScan:
        """Scan a theme: build universe, score each candidate, rank, pick top."""
        top_n = top_n or self._config.pool_top_n
        universe = await self._build_universe(theme)

        candidates: list[PoolCandidate] = []
        for i, entry in enumerate(universe):
            if i > 0 and self._config.pool_rate_limit_seconds > 0:
                await asyncio.sleep(self._config.pool_rate_limit_seconds)
            try:
                snapshot = await self._fetcher.get_pool_snapshot(
                    entry, fetch_github=self._config.pool_github_enabled
                )
                score = self._scorer.score(snapshot)
                self._persist(score)
                candidates.append(self._to_candidate(entry, score))
            except Exception:
                logger.warning(
                    "Pool scan skipped candidate %s", entry.get("symbol"), exc_info=True
                )

        candidates.sort(
            key=lambda c: (c.scarce_layer_score, c.normalized_score), reverse=True
        )

        if len(candidates) < self._config.pool_min_pool:
            logger.warning(
                "Pool scan %s: universe %d < min_pool %d",
                theme, len(candidates), self._config.pool_min_pool,
            )

        threshold = self._config.evidence_top_pick_threshold
        eligible = [c for c in candidates if c.evidence_strength >= threshold]
        weak_count = len(candidates) - len(eligible)

        scan = SerenityPoolScan(
            theme=theme,
            theme_name=THEMES[theme].name,
            scanned_at=int(time.time() * 1000),
            universe_size=len(candidates),
            layer_summary=tuple(self._layer_summary(candidates)),
            ranked=tuple(candidates),
            top_picks=tuple(c.symbol for c in eligible[:top_n]),
            weak_evidence_count=weak_count,
        )
        self._persist_scan(scan)
        return scan

    async def _build_universe(self, theme: str) -> list[dict[str, Any]]:
        """Resolve a theme's candidate universe from DeFi Llama /protocols.

        Matches curated_symbols (padding) ∪ protocol entries matching the
        theme's categories / chains / keywords. Drops dead, misrepresented, and
        tiny-TVL entries; caps at pool_max_universe by TVL.
        """
        spec = THEMES.get(theme)
        if spec is None:
            raise ValueError(f"unknown theme: {theme}")

        protocols = await self._fetcher.get_protocols()
        by_symbol: dict[str, dict[str, Any]] = {}
        valid: list[dict[str, Any]] = []
        for entry in protocols:
            if entry.get("dead") or entry.get("misrepresentedTokens"):
                continue
            if not entry.get("id") or not entry.get("symbol"):
                continue
            try:
                if float(entry.get("tvl") or 0) < 1e5:
                    continue
            except (TypeError, ValueError):
                continue
            valid.append(entry)
            symbol = str(entry.get("symbol") or "").strip().upper()
            if symbol:
                prev = by_symbol.get(symbol)
                if prev is None or float(entry.get("tvl") or 0) > float(prev.get("tvl") or 0):
                    by_symbol[symbol] = entry

        matched: list[dict[str, Any]] = []
        matched_ids: set[str] = set()

        def _take(entry: dict[str, Any]) -> None:
            eid = str(entry.get("id") or "")
            if eid and eid not in matched_ids:
                matched_ids.add(eid)
                matched.append(entry)

        # Second pass: only the max-TVL entry per symbol survives, so a low-TVL
        # duplicate never pre-empts its higher-TVL twin.
        for entry in valid:
            symbol = str(entry.get("symbol") or "").strip().upper()
            if by_symbol.get(symbol) is not entry:
                continue

            name = str(entry.get("name") or "").lower()
            category = str(entry.get("category") or "")
            chains = [str(c) for c in (entry.get("chains") or [])]

            in_category = any(
                c.lower() == category.lower() for c in spec.categories
            ) if spec.categories else False
            in_chain = any(
                ch and any(ch == c for c in chains) for ch in spec.chains
            ) if spec.chains else False
            has_keyword = any(
                k in name or k in symbol.lower() for k in spec.keywords
            ) if spec.keywords else False

            if spec.categories:
                # Category-gated: the entry must be in a category, then
                # chains/keywords refine within it. l2 uses this so only
                # Chains *on* an L2 match — not every Chain (Ethereum, Solana).
                matches = in_category
                if spec.chains and not in_chain:
                    matches = False
                if spec.keywords and not has_keyword:
                    matches = False
            else:
                matches = in_chain or has_keyword

            if matches:
                _take(entry)

        for sym in spec.curated_symbols:
            entry = by_symbol.get(sym.upper())
            if entry is not None:
                _take(entry)

        matched.sort(key=lambda e: float(e.get("tvl") or 0), reverse=True)
        return matched[: self._config.pool_max_universe]

    def _persist(self, score: SerenityScore) -> None:
        if self._score_db is None:
            return
        try:
            self._score_db.open()
            self._score_db.put_score(score)
        except Exception:
            logger.debug("Pool candidate persist failed", exc_info=True)

    def _persist_scan(self, scan: SerenityPoolScan) -> None:
        if self._pool_db is None:
            return
        try:
            self._pool_db.open()
            self._pool_db.put_scan(scan)
        except Exception:
            logger.debug("Pool scan persist failed", exc_info=True)

    def _to_candidate(self, entry: dict[str, Any], score: SerenityScore) -> PoolCandidate:
        scarce = self._scarce_layer_score(score)
        eligible = score.evidence_strength >= self._config.evidence_top_pick_threshold
        return PoolCandidate(
            symbol=score.symbol,
            name=str(entry.get("name") or score.symbol),
            category=str(entry.get("category") or ""),
            chains=tuple(str(c) for c in (entry.get("chains") or [])),
            normalized_score=score.normalized_score,
            tier=score.tier,
            scarce_layer_score=scarce,
            evidence_strength=score.evidence_strength,
            evidence_label=self._evidence_label(score.evidence_strength),
            rationale=self._rationale(score),
            top_penalty=self._top_penalty(score),
            disproof=self._disproof(score) if eligible else (),
        )

    def _layer_summary(self, candidates: list[PoolCandidate]) -> list[PoolLayerSummary]:
        by_layer: dict[str, list[PoolCandidate]] = {}
        for c in candidates:
            by_layer.setdefault(c.category or "其他", []).append(c)
        summaries: list[PoolLayerSummary] = []
        for layer, members in sorted(
            by_layer.items(), key=lambda kv: -len(kv[1])
        ):
            top = sorted(
                members, key=lambda c: c.scarce_layer_score, reverse=True
            )[:3]
            summaries.append(
                PoolLayerSummary(
                    layer=layer,
                    count=len(members),
                    top_scarce_symbols=tuple(c.symbol for c in top),
                )
            )
        return summaries

    # --- candidate scoring helpers ---

    def _scarce_layer_score(self, score: SerenityScore) -> float:
        structural = tuple(getattr(score, name) for name in _STRUCTURAL_FACTOR_NAMES)
        return sum(structural) / len(structural)

    def _evidence_label(self, strength: float) -> str:
        if strength >= 0.5:
            return "强"
        if strength >= self._config.evidence_top_pick_threshold:
            return "中"
        return "弱"

    def _rationale(self, score: SerenityScore) -> str:
        parts: list[str] = []
        if score.consensus_scarcity > 0.7:
            parts.append("高共识稀缺")
        elif score.consensus_scarcity > 0.55:
            parts.append("共识偏稀缺")
        if score.liquidity_scarcity > 0.7:
            parts.append("深流动性护城河")
        if score.trust_scarcity > 0.6:
            parts.append("强开发信任")
        if score.replication_difficulty > 0.7:
            parts.append("高复制壁垒")
        if score.certification_progress > 0.6:
            parts.append("认证进度领先")
        if score.decentralization > 0.6:
            parts.append("去中心化程度高")
        if score.prepayment_signal > 0.6:
            parts.append("预付/收入动量强")
        if not parts:
            return "无明显卡点特征"
        return "卡点:" + "、".join(parts)

    def _top_penalty(self, score: SerenityScore) -> str:
        penalties = {
            "fork_risk": score.penalty_fork_risk,
            "tvl_concentration": score.penalty_tvl_concentration,
            "dev_exodus": score.penalty_dev_exodus,
            "revenue_decline": score.penalty_revenue_decline,
            "oi_divergence": score.penalty_oi_divergence,
            "whale_exodus": score.penalty_whale_exodus,
            "governance_risk": score.penalty_governance_risk,
            "liquidity_trap": score.penalty_liquidity_trap,
        }
        top = max(penalties, key=penalties.get)
        return f"{top}:{penalties[top]:.2f}"

    def _disproof(self, score: SerenityScore) -> tuple[str, ...]:
        out: list[str] = []
        if score.liquidity_scarcity > 0.5:
            out.append("TVL 7日跌 >20% 证伪稀缺")
        if score.prepayment_signal > 0.5:
            out.append("收入连续 3 周下滑打破动量")
        if score.penalty_fork_risk > 0.4:
            out.append("竞品分叉捕获 >10% TVL 打破护城河")
        if score.consensus_scarcity > 0.6:
            out.append("链停摆/共识失败打破信任")
        return tuple(out[:2])
