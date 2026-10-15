"""Serenity scoring engine — 8-factor + 8-penalty model adapted for crypto.

Each factor and penalty produces a float in [0, 1] using bounded normalization.
Composite: weighted_sum(factors) - weighted_sum(penalties), then sigmoid -> normalized_score.

Missing-data convention: an absent data source defaults to 0.5 (neutral) for
both factors and penalties, so unknown inputs neither boost nor sink the score.
The lone exception is evidence_strength, which measures data presence itself
and drops to 0.0 when nothing is available — the combined effect keeps a
no-data symbol below the neutral midpoint instead of drifting bullish.
"""

from __future__ import annotations

import math

from .config import SerenityConfig
from .models import SerenityScore, SerenitySnapshot


def _bounded(value: float, low: float, high: float) -> float:
    """Map value to 0..1 within [low, high] range, clipping outside."""
    return max(0.0, min(1.0, (value - low) / (high - low + 1e-9)))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


class SerenityScorer:
    """8-factor + 8-penalty scoring engine adapted for crypto."""

    def __init__(self, config: SerenityConfig | None = None) -> None:
        self._config = config or SerenityConfig()

    def score(self, snapshot: SerenitySnapshot) -> SerenityScore:
        """Score a SerenitySnapshot into a SerenityScore."""
        factors = (
            self._consensus_scarcity(snapshot),
            self._liquidity_scarcity(snapshot),
            self._trust_scarcity(snapshot),
            self._replication_difficulty(snapshot),
            self._certification_progress(snapshot),
            self._prepayment_signal(snapshot),
            self._decentralization(snapshot),
            self._evidence_strength(snapshot),
        )
        penalties = (
            self._penalty_fork_risk(snapshot),
            self._penalty_tvl_concentration(snapshot),
            self._penalty_dev_exodus(snapshot),
            self._penalty_revenue_decline(snapshot),
            self._penalty_oi_divergence(snapshot),
            self._penalty_whale_exodus(snapshot),
            self._penalty_governance_risk(snapshot),
            self._penalty_liquidity_trap(snapshot),
        )

        fw = self._config.factor_weights
        pw = self._config.penalty_weights
        raw = sum(f * w for f, w in zip(factors, fw)) - sum(
            p * w for p, w in zip(penalties, pw)
        )
        normalized = _sigmoid(raw * self._config.sigmoid_slope)
        tier = self._classify_tier(normalized)

        return SerenityScore(
            symbol=snapshot.symbol,
            consensus_scarcity=factors[0],
            liquidity_scarcity=factors[1],
            trust_scarcity=factors[2],
            replication_difficulty=factors[3],
            certification_progress=factors[4],
            prepayment_signal=factors[5],
            decentralization=factors[6],
            evidence_strength=factors[7],
            penalty_fork_risk=penalties[0],
            penalty_tvl_concentration=penalties[1],
            penalty_dev_exodus=penalties[2],
            penalty_revenue_decline=penalties[3],
            penalty_oi_divergence=penalties[4],
            penalty_whale_exodus=penalties[5],
            penalty_governance_risk=penalties[6],
            penalty_liquidity_trap=penalties[7],
            raw_score=raw,
            normalized_score=normalized,
            tier=tier,
            timestamp=snapshot.timestamp,
        )

    # --- 8 Positive Factors ---

    def _consensus_scarcity(self, s: SerenitySnapshot) -> float:
        """How scarce is the consensus mechanism.

        BTC PoW = high (energy cost), ETH PoS = medium, forked chains = low.
        """
        sym = s.symbol.upper()
        if sym.startswith("BTC"):
            return self._config.btc_consensus_score
        if sym.startswith("ETH"):
            return self._config.eth_consensus_score
        if s.protocol_metrics is not None:
            tvl = s.protocol_metrics.tvl_usd
            low, high = self._config.consensus_tvl_log10_range
            return _bounded(math.log10(tvl + 1), low, high)
        return 0.5

    def _liquidity_scarcity(self, s: SerenitySnapshot) -> float:
        """TVL / FDV ratio. Higher = more real capital locked relative to valuation."""
        if s.protocol_metrics is None:
            return 0.5
        mcap_tvl = s.protocol_metrics.mcap_to_tvl
        fdv_tvl = s.protocol_metrics.fdv_to_tvl
        ratio = min(mcap_tvl, fdv_tvl)
        if ratio <= 0:
            return 0.5
        low, high = self._config.liquidity_ratio_bounds
        return _bounded(1.0 / ratio, low, high)

    def _trust_scarcity(self, s: SerenitySnapshot) -> float:
        """Time-since-launch, audit count, bug bounty. Approximated from GitHub."""
        if s.github_activity is None:
            return 0.5
        ga = s.github_activity
        score = 0.0
        score += _bounded(math.log10(ga.stars + 1), 2, 5) * 0.4
        score += _bounded(ga.active_devs_4w, 5, 50) * 0.3
        score += _bounded(ga.commits_4w, 20, 200) * 0.3
        return min(1.0, score)

    def _replication_difficulty(self, s: SerenitySnapshot) -> float:
        """Fork easy, TVL/trust hard. High TVL with low volatility = hard to replicate."""
        if s.protocol_metrics is None:
            return 0.5
        pm = s.protocol_metrics
        tvl_score = _bounded(math.log10(pm.tvl_usd + 1), 7, 10) * 0.6
        stability = 1.0 - _bounded(abs(pm.tvl_change_7d), 0.0, 0.3) * 0.4
        return tvl_score * stability + 0.2

    def _certification_progress(self, s: SerenitySnapshot) -> float:
        """How far along security audit + liquidity accumulation curve."""
        if s.github_activity is None:
            return 0.5
        ga = s.github_activity
        close_rate = ga.closed_issues_4w / (ga.open_issues + ga.closed_issues_4w + 1)
        dev_score = _bounded(ga.active_devs_4w * ga.commits_4w / (ga.open_issues + 1), 0, 500)
        return close_rate * 0.5 + dev_score * 0.5

    def _prepayment_signal(self, s: SerenitySnapshot) -> float:
        """TVL lockup growth + protocol revenue momentum."""
        if s.protocol_metrics is None:
            return 0.5
        pm = s.protocol_metrics
        tvl_mom = _bounded(pm.tvl_change_7d, -0.2, 0.3)
        rev_score = _bounded(math.log10(pm.revenue_7d + 1), 3, 7)
        return tvl_mom * 0.5 + rev_score * 0.5

    def _decentralization(self, s: SerenitySnapshot) -> float:
        """Developer + chain diversity. Higher = more decentralized = better."""
        if s.github_activity is None:
            return 0.5
        ga = s.github_activity
        dev_score = _bounded(ga.active_devs_4w, 3, 50) * 0.5
        chains = len(s.protocol_metrics.chains) if s.protocol_metrics else 1
        chain_score = _bounded(chains, 1, 5) * 0.5
        return dev_score + chain_score

    def _evidence_strength(self, s: SerenitySnapshot) -> float:
        """Quality-weighted evidence: informative data counts, not just present fields.

        A protocol record with zero TVL or an inactive repo carries far less
        evidence than a rich record — presence alone would overstate trust in a
        no-data symbol (P1-2). Derivatives scalars count whenever present:
        db_accessor returns None for no-data, so a non-None 0.0 is a genuine
        flat reading (P0-3), not missing data.
        """
        score = 0.0
        if s.protocol_metrics is not None:
            pm = s.protocol_metrics
            if pm.tvl_usd > 0:
                score += 0.3
            elif pm.revenue_7d > 0 or len(pm.chains) > 0:
                score += 0.1
        if s.github_activity is not None:
            ga = s.github_activity
            if ga.stars > 0 or ga.commits_4w > 0 or ga.active_devs_4w > 0:
                score += 0.25
            else:
                score += 0.05
        if s.funding_rate is not None:
            score += 0.15
        if s.oi_change is not None:
            score += 0.15
        if s.ls_ratio is not None:
            score += 0.15
        return min(1.0, score)

    # --- 8 Penalties ---

    def _penalty_fork_risk(self, s: SerenitySnapshot) -> float:
        """Easy to fork = penalty. L1 chains are harder to fork than DEX tokens."""
        if s.protocol_metrics is None:
            return 0.5
        cat = s.protocol_metrics.category.lower()
        if cat in ("chain", "bridge"):
            return 0.15
        if cat in ("dex", "lending"):
            return 0.55
        return 0.35

    def _penalty_tvl_concentration(self, s: SerenitySnapshot) -> float:
        """Single-chain TVL concentration."""
        if s.protocol_metrics is None:
            return 0.5
        chains = s.protocol_metrics.chains
        if len(chains) <= 1:
            return 0.7
        return _bounded(1.0 / len(chains), 0.1, 0.5)

    def _penalty_dev_exodus(self, s: SerenitySnapshot) -> float:
        """Declining developer activity."""
        if s.github_activity is None:
            return 0.5
        ga = s.github_activity
        if ga.active_devs_4w < 3:
            return 0.8
        if ga.commits_4w < 10:
            return 0.6
        return _bounded(1.0 - ga.active_devs_4w / 50.0, 0.0, 0.5)

    def _penalty_revenue_decline(self, s: SerenitySnapshot) -> float:
        """Declining protocol revenue."""
        if s.protocol_metrics is None:
            return 0.5
        pm = s.protocol_metrics
        if pm.revenue_7d <= 0:
            return 0.7
        if pm.revenue_24h > 0 and pm.revenue_7d > 0:
            daily_avg = pm.revenue_7d / 7.0
            ratio = pm.revenue_24h / (daily_avg + 1e-9)
            return _bounded(1.0 - ratio, 0.0, 1.0)
        return 0.5

    def _penalty_oi_divergence(self, s: SerenitySnapshot) -> float:
        """OI increasing while price falling = bearish divergence."""
        if s.oi_change is None or s.price_change_7d is None:
            return 0.5
        if s.oi_change > 0.05 and s.price_change_7d < -0.05:
            return _bounded(abs(s.oi_change) + abs(s.price_change_7d), 0.0, 0.3)
        return 0.1

    def _penalty_whale_exodus(self, s: SerenitySnapshot) -> float:
        """Large TVL outflows."""
        if s.protocol_metrics is None:
            return 0.5
        tvl_drop = s.protocol_metrics.tvl_change_7d
        if tvl_drop < -0.1:
            return _bounded(abs(tvl_drop), 0.1, 0.4)
        return 0.1

    def _penalty_governance_risk(self, s: SerenitySnapshot) -> float:
        """Low participation, whale-dominated governance. Heuristic."""
        if s.protocol_metrics is None:
            return 0.5
        mcap_tvl = s.protocol_metrics.mcap_to_tvl
        if mcap_tvl > self._config.governance_mcap_tvl_threshold:
            return 0.6
        return 0.2

    def _penalty_liquidity_trap(self, s: SerenitySnapshot) -> float:
        """TVL increasing but volume decreasing = money trapped, not flowing."""
        if s.protocol_metrics is None or s.volume_change_7d is None:
            return 0.5
        pm = s.protocol_metrics
        tvl_up = pm.tvl_change_7d > 0.02
        vol_down = s.volume_change_7d < -0.05
        if tvl_up and vol_down:
            return 0.6
        return 0.15

    # --- Helpers ---

    def _classify_tier(self, score: float) -> str:
        c = self._config
        if score >= c.strong_bullish_th:
            return "strong_bullish"
        if score >= c.bullish_th:
            return "bullish"
        if score <= c.strong_bearish_th:
            return "strong_bearish"
        if score <= c.bearish_th:
            return "bearish"
        return "neutral"
