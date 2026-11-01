"""Multi-strategy ensemble: independent runtimes, one fused decision.

Every member strategy gets its own :class:`StrategyRuntime` fed the same
kline stream. Per bar the ensemble gathers each runtime's (possibly None)
signal, groups them by side, and applies the configured fusion method to
arrive at a single :class:`EnsembleSignal`.

Beyond plain voting, this module layers:

- *regime awareness* — :func:`detect_market_regime` classifies a price
  series statistically, and per-regime weight presets re-rank strategies
  that historically perform well in each environment;
- *adaptive thresholds* — :class:`DynamicThresholdConfig` tightens or
  loosens consensus requirements from recent signal history and volatility;
- *event-driven adaptation* — ``enable_regime_auto_adapt`` hooks the
  optional core EventBus so regime changes re-weight without polling.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field
from enum import Enum
from time import time as _wall_clock
from typing import TYPE_CHECKING, Any

import numpy as np

from selene_space_strategy.compiler import CompiledStrategy
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide

if TYPE_CHECKING:
    import pandas as pd


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------


class MarketRegime(Enum):
    """Statistical market environment buckets used for weight presets."""

    BULL = "bull"
    BEAR = "bear"
    SIDEWAYS = "sideways"
    TRANSITION = "transition"


def detect_market_regime(prices: "pd.Series", lookback: int = 60) -> MarketRegime:
    """Classify recent price action into a :class:`MarketRegime`.

    Uses annualized mean return and annualized volatility of the trailing
    ``lookback`` bars: strongly positive + quiet → BULL; strongly negative +
    not wild → BEAR; very volatile regardless of direction → TRANSITION;
    everything else → SIDEWAYS. Series shorter than ``lookback`` default to
    SIDEWAYS.
    """
    if len(prices) < lookback:
        return MarketRegime.SIDEWAYS

    window = prices.iloc[-lookback:]
    bar_returns = window.pct_change().dropna()
    if len(bar_returns) < 2:
        return MarketRegime.SIDEWAYS

    annualized_return = float(bar_returns.mean()) * 252.0
    annualized_vol = float(bar_returns.std()) * float(np.sqrt(252.0))

    if annualized_return > 0.15 and annualized_vol < 0.25:
        return MarketRegime.BULL
    if annualized_return < -0.15 and annualized_vol < 0.35:
        return MarketRegime.BEAR
    if annualized_vol > 0.35:
        return MarketRegime.TRANSITION
    return MarketRegime.SIDEWAYS


# ---------------------------------------------------------------------------
# Fusion vocabulary
# ---------------------------------------------------------------------------


class FusionMethod(Enum):
    """How individual strategy signals collapse into one decision."""

    MAJORITY_VOTE = "majority_vote"  # more than half of active signals agree
    WEIGHTED_AVERAGE = "weighted_average"  # weights decide between sides
    UNANIMOUS = "unanimous"  # every firing strategy must agree
    ANY_SIGNAL = "any_signal"  # a single signal passes through
    CONSENSUS_THRESHOLD = "consensus_threshold"  # custom fraction of members


@dataclass
class EnsembleConfig:
    """Static fusion settings.

    ``conflict_resolution`` picks behavior when both sides fire on the same
    bar: ``no_signal`` (drop), ``stronger`` (larger side wins), or ``first``
    (earliest timestamp wins).
    """

    fusion_method: FusionMethod = FusionMethod.MAJORITY_VOTE
    consensus_threshold: float = 0.6
    min_strategies_for_signal: int = 1
    conflict_resolution: str = "no_signal"


@dataclass
class DynamicThresholdConfig:
    """Knobs for volatility/history-aware threshold adaptation."""

    base_threshold: float = 0.5
    volatility_multiplier: float = 0.2
    min_threshold: float = 0.3
    max_threshold: float = 0.9
    lookback_bars: int = 20
    confidence_boost: float = 1.5
    decay_rate: float = 0.85


@dataclass
class EnsembleSignal:
    """Outcome of fusing one bar's worth of strategy signals."""

    signal: Signal | None = None
    contributing_strategies: list[str] = field(default_factory=list)
    signal_counts: dict[str, int] = field(default_factory=dict)
    confidence: float = 0.0
    method: FusionMethod = FusionMethod.MAJORITY_VOTE


# ---------------------------------------------------------------------------
# Regime → strategy weight presets
# ---------------------------------------------------------------------------

# Relative influence per strategy id in each environment. Derived from
# parameter-tuning outcomes and strategy category:
#   BULL       — trend followers lead; counter-trend and grid shrink
#   BEAR       — mean reversion leads; trend following mostly stands down
#   SIDEWAYS   — grid/calendar/channel strategies lead; trend whipsaws
#   TRANSITION — momentum with short memory survives; overall reduced
_BULL_WEIGHTS: dict[str, float] = {
    "keltner_v2": 2.0,
    "ema_triple_cross": 1.8,
    "pyramid_ma": 1.5,
    "bb_atr_trend": 1.5,
    "alpha_meme_formula": 1.5,
    "dmi_delta": 1.3,
    "vw_ema_cross": 1.2,
    "rsi_ma_delta": 1.0,
    "p_signal": 1.0,
    "stoch_supertrend": 0.6,
    "bb_v3": 0.8,
    "tema_channel": 0.8,
    "ma_extended_runs": 0.5,
    "vwap_fibo_bands": 0.4,
    "dca_grid": 0.4,
    "calendar_effect": 0.3,
}

_BEAR_WEIGHTS: dict[str, float] = {
    "ma_extended_runs": 2.0,
    "vwap_fibo_bands": 1.5,
    "rsi_ma_delta": 1.3,
    "bb_v3": 1.2,
    "keltner_v2": 1.0,
    "alpha_meme_formula": 0.6,
    "dmi_delta": 0.8,
    "p_signal": 0.7,
    "tema_channel": 0.5,
    "stoch_supertrend": 0.6,
    "dca_grid": 0.3,
    "calendar_effect": 0.3,
    "ema_triple_cross": 0.3,
    "bb_atr_trend": 0.3,
    "vw_ema_cross": 0.3,
    "pyramid_ma": 0.2,
}

_SIDEWAYS_WEIGHTS: dict[str, float] = {
    "dca_grid": 2.0,
    "calendar_effect": 1.5,
    "vwap_fibo_bands": 1.5,
    "ma_extended_runs": 1.8,
    "alpha_meme_formula": 0.8,
    "bb_v3": 1.3,
    "tema_channel": 1.2,
    "rsi_ma_delta": 1.0,
    "p_signal": 0.8,
    "stoch_supertrend": 0.8,
    "keltner_v2": 0.5,
    "dmi_delta": 0.5,
    "ema_triple_cross": 0.4,
    "bb_atr_trend": 0.4,
    "vw_ema_cross": 0.4,
    "pyramid_ma": 0.3,
}

_TRANSITION_WEIGHTS: dict[str, float] = {
    "p_signal": 1.2,
    "alpha_meme_formula": 1.0,
    "stoch_supertrend": 1.0,
    "rsi_ma_delta": 1.0,
    "dmi_delta": 0.9,
    "tema_channel": 0.8,
    "bb_v3": 0.7,
    "ma_extended_runs": 0.7,
    "keltner_v2": 0.6,
    "dca_grid": 0.5,
    "vwap_fibo_bands": 0.5,
    "bb_atr_trend": 0.4,
    "vw_ema_cross": 0.4,
    "ema_triple_cross": 0.3,
    "pyramid_ma": 0.3,
    "calendar_effect": 0.3,
}

_DEFAULT_REGIME_WEIGHTS: dict[MarketRegime, dict[str, float]] = {
    MarketRegime.BULL: _BULL_WEIGHTS,
    MarketRegime.BEAR: _BEAR_WEIGHTS,
    MarketRegime.SIDEWAYS: _SIDEWAYS_WEIGHTS,
    MarketRegime.TRANSITION: _TRANSITION_WEIGHTS,
}

# Labels produced by the ADX-based regime detector mapped onto ensemble
# regimes; bare enum values are accepted for convenience.
REGIME_LABEL_MAP: dict[str, MarketRegime] = {
    "trending": MarketRegime.BULL,
    "ranging": MarketRegime.SIDEWAYS,
    "volatile": MarketRegime.TRANSITION,
    "bull": MarketRegime.BULL,
    "bear": MarketRegime.BEAR,
    "sideways": MarketRegime.SIDEWAYS,
    "transition": MarketRegime.TRANSITION,
}


# ---------------------------------------------------------------------------
# Ensemble
# ---------------------------------------------------------------------------

_ID_PREFIXES_TO_STRIP = ("template-", "template_")


class StrategyEnsemble:
    """Run many compiled strategies and fuse their per-bar verdicts."""

    def __init__(
        self,
        compiled_strategies: list[CompiledStrategy],
        config: EnsembleConfig | None = None,
        weights: dict[str, float] | None = None,
        dynamic_threshold: DynamicThresholdConfig | None = None,
    ) -> None:
        self._compiled_strategies = compiled_strategies
        self._config = config or EnsembleConfig()
        self._weights: dict[str, float] = dict(weights or {})
        self._dynamic_threshold = dynamic_threshold
        self._runtimes: dict[str, StrategyRuntime] = {}
        self._last_signals: dict[str, Signal | None] = {}
        self._signal_history: list[dict] = []
        self._regime_subscribed = False
        self._regime_handler: Any = None
        self._regime_weights_presets: dict[MarketRegime, dict[str, float]] | None = None

        for compiled in compiled_strategies:
            sid = compiled.config.id
            self._runtimes[sid] = StrategyRuntime(compiled)
            self._last_signals[sid] = None
            self._weights.setdefault(sid, 1.0)

    # -- introspection -------------------------------------------------------

    @property
    def config(self) -> EnsembleConfig:
        """Fusion configuration in force."""
        return self._config

    @property
    def strategy_ids(self) -> list[str]:
        """Member strategy ids, insertion order."""
        return list(self._runtimes)

    @property
    def strategy_count(self) -> int:
        """Number of member strategies."""
        return len(self._runtimes)

    @property
    def dynamic_threshold_config(self) -> DynamicThresholdConfig | None:
        """Adaptive-threshold settings, when configured."""
        return self._dynamic_threshold

    def get_runtime(self, strategy_id: str) -> StrategyRuntime | None:
        """Runtime backing ``strategy_id``, or None."""
        return self._runtimes.get(strategy_id)

    def get_position(self, strategy_id: str) -> str:
        """Current position label ('flat'/'long'/'short') of one member."""
        runtime = self._runtimes.get(strategy_id)
        return runtime.position if runtime else "flat"

    def set_weights(self, weights: dict[str, float]) -> None:
        """Overwrite weights for known strategies only."""
        for sid, w in weights.items():
            if sid in self._runtimes:
                self._weights[sid] = w

    # -- weighting helpers -----------------------------------------------------

    def get_weights_distribution(self) -> dict[str, float]:
        """Weights normalized to sum 1.0 (uniform fallback when all zero)."""
        total = sum(self._weights.values())
        if total <= 0:
            uniform = 1.0 / max(len(self._weights), 1)
            return {sid: uniform for sid in self._weights}
        return {sid: w / total for sid, w in self._weights.items()}

    def get_qbot_threshold(self, min_agreeing: int = 3) -> float:
        """Average combined weight over all ``min_agreeing``-sized subsets.

        With fewer members than ``min_agreeing`` the plain mean weight is
        returned instead.
        """
        values = list(self._weights.values())
        if len(values) < min_agreeing:
            return sum(values) / max(len(values), 1)

        subset_sums = [sum(subset) for subset in itertools.combinations(values, min_agreeing)]
        if not subset_sums:
            return 0.5
        return sum(subset_sums) / len(subset_sums)

    def get_adaptive_threshold(self, market_volatility: float = 0.0) -> float:
        """Consensus threshold adjusted for volatility and recent history.

        Volatility pushes the threshold up (be pickier in chaos); a history
        of failed signals does the same, successful history relaxes it.
        Without :class:`DynamicThresholdConfig` the static consensus value is
        returned untouched.
        """
        if self._dynamic_threshold is None:
            return self._config.consensus_threshold

        cfg = self._dynamic_threshold
        recent = self._signal_history[-cfg.lookback_bars :] if self._signal_history else []

        success_ratio = 0.0
        if recent:
            wins = sum(1 for record in recent if record.get("success", False))
            success_ratio = wins / len(recent)

        vol_term = market_volatility * cfg.volatility_multiplier
        history_term = (0.5 - success_ratio) * 0.1
        adapted = cfg.base_threshold + vol_term + history_term
        return max(cfg.min_threshold, min(cfg.max_threshold, adapted))

    def get_confidence_weights(self) -> dict[str, float]:
        """Weight each strategy by the decayed strength of its recent signals.

        Members whose recent signal strengths average high (above 0.7 after
        exponential decay) receive the configured confidence boost. Output is
        normalized to sum 1.0 whenever any weight is positive; an empty
        history short-circuits to the static weights.
        """
        if not self._signal_history:
            return dict(self._weights)

        cfg = self._dynamic_threshold or DynamicThresholdConfig()

        strengths_by_sid: dict[str, list[float]] = {}
        for record in self._signal_history[-cfg.lookback_bars :]:
            for sid, sig in record.get("signals", {}).items():
                strength = sig.get("strength", 0.5) if isinstance(sig, dict) else getattr(sig, "strength", 0.5)
                strengths_by_sid.setdefault(sid, []).append(strength)

        adjusted: dict[str, float] = {}
        for sid, strengths in strengths_by_sid.items():
            base_weight = self._weights.get(sid, 1.0)
            if not strengths:
                adjusted[sid] = base_weight
                continue

            decay_factors = [cfg.decay_rate ** (len(strengths) - position - 1) for position in range(len(strengths))]
            decayed_mean = (
                sum(s * d for s, d in zip(strengths, decay_factors)) / sum(decay_factors)
            )
            boost = cfg.confidence_boost if decayed_mean > 0.7 else 1.0
            adjusted[sid] = base_weight * boost * decayed_mean

        total = sum(adjusted.values())
        if total > 0:
            return {sid: w / total for sid, w in adjusted.items()}
        return adjusted

    def update_signal_history(self, signals: dict[str, Signal | None], success: bool | None = None) -> None:
        """Append one observation to adaptive-history (bounded at 1000)."""
        self._signal_history.append(
            {
                "timestamp": int(_wall_clock() * 1000),
                "signals": signals,
                "success": success,
            }
        )
        if len(self._signal_history) > 1000:
            del self._signal_history[: len(self._signal_history) - 1000]

    # -- stream entry point -----------------------------------------------

    def on_kline(self, event: KlineEvent) -> EnsembleSignal:
        """Feed one candle to every member and fuse what comes back."""
        for sid, runtime in self._runtimes.items():
            self._last_signals[sid] = runtime.on_kline(event)

        grouped: dict[str, list[Signal]] = {"BUY": [], "SELL": []}
        contributors: list[str] = []
        for sid, sig in self._last_signals.items():
            if sig is None:
                continue
            contributors.append(sid)
            side_key = sig.side.value if hasattr(sig.side, "value") else str(sig.side)
            grouped[side_key].append(sig)

        buy_n, sell_n = len(grouped["BUY"]), len(grouped["SELL"])
        fired_total = buy_n + sell_n

        if fired_total < self._config.min_strategies_for_signal:
            return EnsembleSignal(
                contributing_strategies=contributors,
                signal_counts={"BUY": buy_n, "SELL": sell_n},
                method=self._config.fusion_method,
            )

        fused = self._fuse_signals(grouped, contributors)

        if self._dynamic_threshold:
            live_weights = self.get_confidence_weights()
            weight_sum = sum(
                live_weights.get(sid, 1.0)
                for sid in contributors
                if self._last_signals.get(sid) is not None
            )
            confidence = min(1.0, weight_sum / len(self._runtimes)) if self._runtimes else 0.0
        else:
            confidence = fired_total / len(self._runtimes) if self._runtimes else 0.0

        outcome = EnsembleSignal(
            signal=fused,
            contributing_strategies=contributors,
            signal_counts={"BUY": buy_n, "SELL": sell_n},
            confidence=confidence,
            method=self._config.fusion_method,
        )
        self.update_signal_history(dict(self._last_signals), None)
        return outcome

    # -- fusion internals -------------------------------------------------

    def _estimate_market_volatility(self) -> float:
        """Normalized close-to-close volatility of the first runtime's buffer.

        Standard deviation of absolute per-bar returns, scaled so a 5% bar is
        already "maximum volatility"; needs at least two usable returns.
        """
        if not self._runtimes:
            return 0.0
        sample = next(iter(self._runtimes.values()))
        bars = getattr(sample, "_bars", [])
        if len(bars) < 10:
            return 0.0

        closes = [bar.get("close", 0) for bar in bars[-20:]]
        abs_returns = [
            abs(closes[i] - closes[i - 1]) / closes[i - 1]
            for i in range(1, len(closes))
            if closes[i - 1] > 0
        ]
        if not abs_returns:
            return 0.0

        mean_r = sum(abs_returns) / len(abs_returns)
        variance = sum((r - mean_r) ** 2 for r in abs_returns) / len(abs_returns)
        return min(1.0, (variance ** 0.5) / 0.05)

    def _fuse_signals(
        self,
        grouped: dict[str, list[Signal]],
        contributors: list[str],
    ) -> Signal | None:
        """Apply the configured fusion method to side-grouped signals."""
        buy_n, sell_n = len(grouped["BUY"]), len(grouped["SELL"])
        fired_total = buy_n + sell_n
        membership = len(self._runtimes)

        if buy_n and sell_n:
            return self._resolve_conflict(grouped)

        method = self._config.fusion_method

        if method == FusionMethod.MAJORITY_VOTE:
            majority_mark = fired_total / 2
            if buy_n > majority_mark:
                return self._create_fused_signal(grouped["BUY"], OrderSide.BUY)
            if sell_n > majority_mark:
                return self._create_fused_signal(grouped["SELL"], OrderSide.SELL)
            return None

        if method == FusionMethod.UNANIMOUS:
            if buy_n == fired_total and buy_n > 0:
                return self._create_fused_signal(grouped["BUY"], OrderSide.BUY)
            if sell_n == fired_total and sell_n > 0:
                return self._create_fused_signal(grouped["SELL"], OrderSide.SELL)
            return None

        if method == FusionMethod.ANY_SIGNAL:
            if buy_n:
                return self._create_fused_signal(grouped["BUY"], OrderSide.BUY)
            if sell_n:
                return self._create_fused_signal(grouped["SELL"], OrderSide.SELL)
            return None

        if method == FusionMethod.CONSENSUS_THRESHOLD:
            needed = self._config.consensus_threshold * membership
            if self._dynamic_threshold:
                needed = self.get_adaptive_threshold(self._estimate_market_volatility()) * membership
            if buy_n >= needed:
                return self._create_fused_signal(grouped["BUY"], OrderSide.BUY)
            if sell_n >= needed:
                return self._create_fused_signal(grouped["SELL"], OrderSide.SELL)
            return None

        if method == FusionMethod.WEIGHTED_AVERAGE:
            buy_w = sum(
                self._weights.get(sid, 1.0)
                for sid in contributors
                if self._last_signals.get(sid) is not None and self._last_signals[sid].side == OrderSide.BUY
            )
            sell_w = sum(
                self._weights.get(sid, 1.0)
                for sid in contributors
                if self._last_signals.get(sid) is not None and self._last_signals[sid].side == OrderSide.SELL
            )
            combined = buy_w + sell_w
            if combined == 0:
                return None
            if buy_w > sell_w and buy_w > combined / 2:
                return self._create_fused_signal(grouped["BUY"], OrderSide.BUY)
            if sell_w > buy_w and sell_w > combined / 2:
                return self._create_fused_signal(grouped["SELL"], OrderSide.SELL)
            return None

        return None

    def _resolve_conflict(self, grouped: dict[str, list[Signal]]) -> Signal | None:
        """Break a simultaneous BUY/SELL tie according to configuration."""
        policy = self._config.conflict_resolution

        if policy == "stronger":
            buys, sells = grouped["BUY"], grouped["SELL"]
            if len(buys) > len(sells):
                return self._create_fused_signal(buys, OrderSide.BUY)
            if len(sells) > len(buys):
                return self._create_fused_signal(sells, OrderSide.SELL)
            return None

        if policy == "first":
            buys, sells = grouped["BUY"], grouped["SELL"]
            if buys and sells:
                earliest_buy = min(s.timestamp for s in buys)
                earliest_sell = min(s.timestamp for s in sells)
                winner = buys if earliest_buy <= earliest_sell else sells
                side = OrderSide.BUY if winner is buys else OrderSide.SELL
                return self._create_fused_signal(winner, side)
            return None

        return None  # default policy: conflict suppresses the signal entirely

    def _create_fused_signal(self, signals: list[Signal], side: OrderSide) -> Signal:
        """Average the strengths of agreeing signals into one ensemble Signal."""
        if not signals:
            raise ValueError("Cannot create fused signal from empty list")

        anchor = signals[0]
        mean_strength = sum(s.strength for s in signals) / len(signals)
        participant_ids = ", ".join(s.strategy_id for s in signals)

        return Signal(
            strategy_id=f"ensemble-{anchor.symbol}",
            symbol=anchor.symbol,
            side=side,
            strength=mean_strength,
            timestamp=anchor.timestamp,
            reason=f"Ensemble ({len(signals)} strategies): {participant_ids}",
        )

    # -- state management -----------------------------------------------

    def reset(self) -> None:
        """Clear every member runtime and forget last-bar signals."""
        for runtime in self._runtimes.values():
            runtime.reset()
        for sid in self._runtimes:
            self._last_signals[sid] = None

    # -- regime-aware weighting ------------------------------------------

    @staticmethod
    def _match_regime_key(strategy_id: str, preset_map: dict[str, float]) -> float | None:
        """Preset weight for ``strategy_id``, tolerating template- prefixes."""
        direct = preset_map.get(strategy_id)
        if direct is not None:
            return direct
        for prefix in _ID_PREFIXES_TO_STRIP:
            if strategy_id.startswith(prefix):
                stripped_weight = preset_map.get(strategy_id[len(prefix) :])
                if stripped_weight is not None:
                    return stripped_weight
        return None

    def _apply_regime_weights(
        self,
        regime: MarketRegime,
        regime_weights: dict[MarketRegime, dict[str, float]] | None = None,
    ) -> dict[str, float]:
        """Rewrite current weights using one regime's preset table.

        TRANSITION additionally scales every weight down by 0.7 so the bar
        for agreement effectively rises during unstable conditions.
        """
        presets = regime_weights or self._regime_weights_presets or _DEFAULT_REGIME_WEIGHTS
        adapted = dict(self._weights)

        table = presets.get(regime) or {}
        if table:
            for sid in adapted:
                matched = self._match_regime_key(sid, table)
                if matched is not None:
                    adapted[sid] = matched

        if regime == MarketRegime.TRANSITION:
            adapted = {sid: w * 0.7 for sid, w in adapted.items()}

        return adapted

    def adapt_weights_by_regime(
        self,
        prices: "pd.Series",
        regime_weights: dict[MarketRegime, dict[str, float]] | None = None,
    ) -> dict[str, float]:
        """Detect the regime from ``prices`` and apply its weight preset."""
        regime = detect_market_regime(prices)
        return self._apply_regime_weights(regime, regime_weights)

    def get_regime_aware_threshold(
        self,
        prices: "pd.Series",
        min_agreeing: int = 3,
    ) -> float:
        """Qbot-style threshold modulated by the detected regime.

        TRANSITION raises it 30% (demand extra proof), SIDEWAYS trims 10%
        (mean-reverting members agree more often).
        """
        base = self.get_qbot_threshold(min_agreeing)
        regime = detect_market_regime(prices)

        if regime == MarketRegime.TRANSITION:
            return base * 1.3
        if regime == MarketRegime.SIDEWAYS:
            return base * 0.9
        return base

    # -- event-bus integration (optional dependency) ----------------------

    def enable_regime_auto_adapt(
        self,
        regime_weights: dict[MarketRegime, dict[str, float]] | None = None,
    ) -> None:
        """Re-weight automatically whenever RegimeChangedEvent fires.

        Idempotent: a second call while subscribed is a no-op. Silently does
        nothing when the optional ``selene_space_core`` EventBus package is
        absent.
        """
        if self._regime_subscribed:
            return

        self._regime_weights_presets = regime_weights

        def _handle_regime_event(event: Any) -> None:
            label = getattr(event, "new_regime", None)
            mapped = REGIME_LABEL_MAP.get(label) if label is not None else None
            if mapped is None:
                return
            self._weights = self._apply_regime_weights(mapped)

        self._regime_handler = _handle_regime_event

        try:
            from selene_space_core.events import RegimeChangedEvent, get_event_bus

            get_event_bus().subscribe(RegimeChangedEvent, self._regime_handler)
            self._regime_subscribed = True
        except ImportError:
            pass

    def disable_regime_auto_adapt(self) -> None:
        """Detach the regime-event handler (no-op when never attached)."""
        if not self._regime_subscribed or self._regime_handler is None:
            return

        try:
            from selene_space_core.events import RegimeChangedEvent, get_event_bus

            get_event_bus().unsubscribe(RegimeChangedEvent, self._regime_handler)
        except ImportError:
            pass

        self._regime_subscribed = False
        self._regime_handler = None
