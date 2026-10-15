"""KF Index engine — custom composite index creator and calculator.

Allows users to define custom indices by combining analysis factors
(CVD, TOF, LiqRatio, GEX, funding, book_imbalance) with user-defined weights.
Indices are persisted in memory and can be computed on demand.
"""

from __future__ import annotations

import logging
import time

from selene_space_types.derivatives import (
    KFIndexConfig,
    KFIndexValue,
    SignalFactor,
)

logger = logging.getLogger(__name__)

# Available factors for index building
AVAILABLE_FACTORS = [
    "cvd",
    "tof",
    "liqmap",
    "gex",
    "funding",
    "book_imbalance",
]


class KFIndexEngine:
    """Create, manage, and compute custom KF Indices.

    Usage::

        engine = KFIndexEngine(market_service)
        engine.create_index(KFIndexConfig(
            id="kf-momentum",
            name="KF Momentum Index",
            factors={"cvd": 0.4, "tof": 0.3, "liqmap": 0.2, "funding": 0.1},
        ))
        values = engine.compute_index("kf-momentum", "BTC/USDT")
    """

    def __init__(self, market_service: object | None = None) -> None:
        self._svc = market_service
        self._indices: dict[str, KFIndexConfig] = {}
        self._options_fetcher: object | None = None

    def set_service(self, market_service: object) -> None:
        self._svc = market_service

    def set_options_fetcher(self, fetcher: object) -> None:
        self._options_fetcher = fetcher

    def create_index(self, config: KFIndexConfig) -> None:
        """Create or update a KF Index configuration."""
        if config.created_at == 0:
            config = config.model_copy(update={"created_at": int(time.time() * 1000)})
        self._indices[config.id] = config
        logger.info("KF Index created/updated: %s (%s)", config.name, config.id)

    def delete_index(self, index_id: str) -> bool:
        """Delete a KF Index configuration."""
        if index_id in self._indices:
            del self._indices[index_id]
            return True
        return False

    def get_index(self, index_id: str) -> KFIndexConfig | None:
        """Get a KF Index configuration."""
        return self._indices.get(index_id)

    def list_indices(self) -> list[KFIndexConfig]:
        """List all KF Index configurations."""
        return list(self._indices.values())

    def compute_index(
        self,
        index_id: str,
        symbol: str,
        exchange: str = "binance",
    ) -> KFIndexValue | None:
        """Compute current value of a KF Index for a symbol."""
        config = self._indices.get(index_id)
        if config is None or self._svc is None:
            return None

        now_ms = int(time.time() * 1000)
        factors: list[SignalFactor] = []
        factor_values: dict[str, float] = {}

        total_weight = sum(config.factors.values()) or 1.0
        total_contribution = 0.0

        for factor_name, raw_weight in config.factors.items():
            normalized_weight = raw_weight / total_weight
            value, direction = self._get_factor_value(exchange, symbol, factor_name)
            factor_values[factor_name] = value

            # Normalize factor value to -100 to +100 scale
            normalized = self._normalize_factor(factor_name, value)
            contribution = normalized * normalized_weight

            factors.append(SignalFactor(
                name=factor_name,
                value=value,
                weight=round(normalized_weight, 4),
                contribution=round(contribution, 2),
                direction=direction,
            ))

            total_contribution += contribution

        # Score: 50 (neutral) + total_contribution, clamped to 0-100
        score = max(0, min(100, 50 + total_contribution))

        if score >= config.threshold_bullish:
            direction = "bullish"
        elif score <= config.threshold_bearish:
            direction = "bearish"
        else:
            direction = "neutral"

        return KFIndexValue(
            index_id=config.id,
            index_name=config.name,
            symbol=symbol,
            score=round(score, 2),
            direction=direction,
            factor_values=factor_values,
            factors=factors,
            timestamp=now_ms,
        )

    def compute_all_symbols(
        self,
        index_id: str,
        exchange: str = "binance",
    ) -> list[KFIndexValue]:
        """Compute a KF Index for all tracked symbols."""
        config = self._indices.get(index_id)
        if config is None or self._svc is None:
            return []

        symbols: set[str] = set()
        try:
            for (ex, sym, _) in self._svc.cvd_aggregator._bars:
                if ex == exchange:
                    symbols.add(sym)
        except Exception:
            pass

        # Also include configured symbols
        for sym in config.symbols:
            symbols.add(sym)

        results: list[KFIndexValue] = []
        for symbol in sorted(symbols):
            value = self.compute_index(index_id, symbol, exchange)
            if value is not None:
                results.append(value)

        return results

    def _get_factor_value(self, exchange: str, symbol: str, factor_name: str) -> tuple[float, str]:
        """Get raw factor value and direction."""
        try:
            if factor_name == "cvd":
                bar = self._svc.cvd_aggregator.get_current_bar(exchange, symbol, "5m")
                if bar is not None:
                    v = bar.cvd_usd
                    d = "bullish" if v > 100_000 else "bearish" if v < -100_000 else "neutral"
                    return v, d

            elif factor_name == "tof":
                tof = self._svc.tof_engine.compute(exchange, symbol)
                if tof is not None:
                    return tof.toxicity_score, tof.direction

            elif factor_name == "liqmap":
                result = self._svc.liqmap_engine.compute_map(exchange, symbol, "all_leverage")
                if result is not None:
                    net = result.short_total_density - result.long_total_density
                    d = "bullish" if net > 0 else "bearish" if net < 0 else "neutral"
                    return net, d

            elif factor_name == "gex":
                # GEX requires async fetch — return 0 for sync compute
                return 0.0, "neutral"

            elif factor_name == "funding":
                fr = self._svc._funding_rate_store.get(f"{exchange}:{symbol}")
                if fr is not None:
                    v = fr.funding_rate
                    d = "bearish" if v > 0.0005 else "bullish" if v < -0.0005 else "neutral"
                    return v, d

            elif factor_name == "book_imbalance":
                for mt in ("futures", "spot"):
                    ob = self._svc.get_orderbook(symbol, exchange=exchange, market_type=mt)
                    if ob is not None and ob.bids and ob.asks:
                        bid_vol = sum(l.quantity for l in ob.bids[:20])
                        ask_vol = sum(l.quantity for l in ob.asks[:20])
                        total = bid_vol + ask_vol
                        if total > 0:
                            v = (bid_vol - ask_vol) / total
                            d = "bullish" if v > 0.1 else "bearish" if v < -0.1 else "neutral"
                            return v, d
        except Exception:
            pass

        return 0.0, "neutral"

    @staticmethod
    def _normalize_factor(name: str, value: float) -> float:
        """Normalize a factor value to -100..+100 scale."""
        if name == "cvd":
            return min(abs(value) / 1_000_000, 1.0) * 100 * (1 if value >= 0 else -1)
        elif name == "tof":
            # TOF toxicity 0-100, direction matters
            return value  # already 0-100
        elif name == "liqmap":
            return min(abs(value) / 5.0, 1.0) * 100 * (1 if value >= 0 else -1)
        elif name == "gex":
            return 0.0  # async, not available in sync path
        elif name == "funding":
            return min(abs(value) / 0.003, 1.0) * 100 * (1 if value < 0 else -1)  # negative funding = bullish
        elif name == "book_imbalance":
            return value * 100  # -1..+1 → -100..+100
        return 0.0
