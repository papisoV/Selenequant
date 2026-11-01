"""Composite signal engine — multi-factor trading signal generator.

Combines CVD, LiqMap, TOF, GEX, funding rate, and orderbook imbalance
into a composite trading signal with per-factor breakdowns.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from selene_space_types.derivatives import CompositeSignal, SignalFactor

logger = logging.getLogger(__name__)

# Default factor weights
_DEFAULT_WEIGHTS: dict[str, float] = {
    "cvd": 0.25,
    "tof": 0.25,
    "liqmap": 0.20,
    "gex": 0.15,
    "funding": 0.10,
    "book_imbalance": 0.05,
}


class CompositeSignalEngine:
    """Generate composite trading signals from all analysis engines.

    Usage::

        engine = CompositeSignalEngine(market_service)
        signals = engine.compute_all("binance")
        signal = engine.compute("binance", "BTC/USDT")
    """

    def __init__(self, market_service: Any | None = None) -> None:
        self._svc = market_service
        self._options_fetcher: Any | None = None

    def set_service(self, market_service: Any) -> None:
        self._svc = market_service

    def set_options_fetcher(self, fetcher: Any) -> None:
        self._options_fetcher = fetcher

    def compute(
        self,
        exchange: str,
        symbol: str,
        weights: dict[str, float] | None = None,
        cvd_interval: str = "5m",
    ) -> CompositeSignal | None:
        """Compute composite signal for a single symbol."""
        if self._svc is None:
            return None

        now_ms = int(time.time() * 1000)
        w = weights or _DEFAULT_WEIGHTS
        factors: list[SignalFactor] = []

        # --- CVD factor ---
        cvd_direction = "neutral"
        cvd_value = 0.0
        # must exist even when the CVD bar is unavailable (ws gap) — the
        # factor append below references it unconditionally
        cvd_normalized = 0.0
        try:
            bar = self._svc.cvd_aggregator.get_current_bar(
                exchange, symbol, cvd_interval
            )
            if bar is not None:
                cvd_value = bar.cvd_usd
                if cvd_value > 100_000:
                    cvd_direction = "bullish"
                elif cvd_value < -100_000:
                    cvd_direction = "bearish"
                # Normalize to 0-100 scale
                cvd_normalized = min(abs(cvd_value) / 1_000_000, 1.0) * 100
                if cvd_value < 0:
                    cvd_normalized = -cvd_normalized
        except Exception:
            pass

        factors.append(
            SignalFactor(
                name="cvd",
                value=cvd_value,
                weight=w.get("cvd", 0),
                contribution=cvd_normalized * w.get("cvd", 0),
                direction=cvd_direction,
            )
        )

        # --- TOF factor ---
        tof_direction = "neutral"
        tof_value = 0.0
        tof_contribution = 0.0
        try:
            tof = self._svc.tof_engine.compute(exchange, symbol)
            if tof is not None:
                tof_value = tof.toxicity_score
                tof_direction = tof.direction
                # Invert: bearish toxicity = bearish signal, but high toxicity = strong signal
                if tof_direction == "bearish":
                    tof_contribution = -tof_value * w.get("tof", 0)
                elif tof_direction == "bullish":
                    tof_contribution = tof_value * w.get("tof", 0)
                else:
                    tof_contribution = 0.0
        except Exception:
            pass

        factors.append(
            SignalFactor(
                name="tof",
                value=tof_value,
                weight=w.get("tof", 0),
                contribution=tof_contribution,
                direction=tof_direction,
            )
        )

        # --- LiqMap factor ---
        liqmap_direction = "neutral"
        liqmap_value = 0.0
        liqmap_contribution = 0.0
        try:
            result = self._svc.liqmap_engine.compute_map(
                exchange, symbol, "all_leverage"
            )
            if result is not None:
                net = result.short_total_density - result.long_total_density
                liqmap_value = net
                # Positive net = short density > long = bearish (shorts at risk = support)
                # Actually: short liq density means shorts being liquidated = bullish push
                if net > 0:
                    liqmap_direction = "bullish"  # shorts being liquidated → upward
                elif net < 0:
                    liqmap_direction = "bearish"  # longs being liquidated → downward
                liqmap_normalized = min(abs(net) / 5.0, 1.0) * 100
                liqmap_contribution = (
                    liqmap_normalized * w.get("liqmap", 0) * (1 if net > 0 else -1)
                )
        except Exception:
            pass

        factors.append(
            SignalFactor(
                name="liqmap",
                value=liqmap_value,
                weight=w.get("liqmap", 0),
                contribution=liqmap_contribution,
                direction=liqmap_direction,
            )
        )

        # --- GEX factor (if options fetcher available) ---
        gex_direction = "neutral"
        gex_value = 0.0
        gex_contribution = 0.0
        try:
            if self._options_fetcher is not None:
                currency = symbol.split("/")[0]
                gex_result = (
                    self._options_fetcher.fetch_gex_sync(currency)
                    if hasattr(self._options_fetcher, "fetch_gex_sync")
                    else None
                )
                if gex_result is not None:
                    gex_value = gex_result.net_gex
                    if gex_result.dealer_positioning == "long_gamma":
                        gex_direction = "neutral"  # mean-reversion environment
                        gex_contribution = 0.0
                    elif gex_result.dealer_positioning == "short_gamma":
                        gex_direction = "bearish"  # momentum/trend environment
                        gex_contribution = -50 * w.get("gex", 0)
        except Exception:
            pass

        factors.append(
            SignalFactor(
                name="gex",
                value=gex_value,
                weight=w.get("gex", 0),
                contribution=gex_contribution,
                direction=gex_direction,
            )
        )

        # --- Funding rate factor ---
        funding_direction = "neutral"
        funding_value = 0.0
        funding_contribution = 0.0
        try:
            fr_key = f"{exchange}:{symbol}"
            fr = self._svc._funding_rate_store.get(fr_key)
            if fr is not None:
                funding_value = fr.funding_rate
                # High positive funding = overleveraged longs = bearish contrarian
                if funding_value > 0.0005:
                    funding_direction = "bearish"
                    funding_contribution = (
                        -min(funding_value / 0.003, 1.0) * 100 * w.get("funding", 0)
                    )
                elif funding_value < -0.0005:
                    funding_direction = "bullish"
                    funding_contribution = (
                        min(abs(funding_value) / 0.003, 1.0) * 100 * w.get("funding", 0)
                    )
        except Exception:
            pass

        factors.append(
            SignalFactor(
                name="funding",
                value=funding_value,
                weight=w.get("funding", 0),
                contribution=funding_contribution,
                direction=funding_direction,
            )
        )

        # --- Book imbalance factor ---
        book_direction = "neutral"
        book_value = 0.0
        book_contribution = 0.0
        try:
            for mt in ("futures", "spot"):
                ob = self._svc.get_orderbook(symbol, exchange=exchange, market_type=mt)
                if ob is not None and ob.bids and ob.asks:
                    bid_vol = sum(lvl.quantity for lvl in ob.bids[:20])
                    ask_vol = sum(lvl.quantity for lvl in ob.asks[:20])
                    total = bid_vol + ask_vol
                    if total > 0:
                        book_value = (bid_vol - ask_vol) / total
                    if book_value > 0.1:
                        book_direction = "bullish"
                    elif book_value < -0.1:
                        book_direction = "bearish"
                    book_contribution = book_value * 100 * w.get("book_imbalance", 0)
                    break
        except Exception:
            pass

        factors.append(
            SignalFactor(
                name="book_imbalance",
                value=book_value,
                weight=w.get("book_imbalance", 0),
                contribution=book_contribution,
                direction=book_direction,
            )
        )

        # --- Composite score ---
        total_contribution = sum(f.contribution for f in factors)
        # Map to 0-100 scale: 50 = neutral
        composite_raw = 50 + total_contribution
        composite_score = max(0, min(100, composite_raw))

        # --- Determine side ---
        if composite_score >= 65:
            side = "buy"
        elif composite_score <= 35:
            side = "sell"
        else:
            side = "neutral"

        # --- Confidence: how much factors agree ---
        bullish_count = sum(1 for f in factors if f.direction == "bullish")
        bearish_count = sum(1 for f in factors if f.direction == "bearish")
        total_with_direction = bullish_count + bearish_count
        if total_with_direction > 0:
            agreement = max(bullish_count, bearish_count) / total_with_direction
        else:
            agreement = 0.0

        # Get mark price
        mark_price = 0.0
        try:
            for f in factors:
                if f.name == "tof" and hasattr(self._svc, "tof_engine"):
                    tof = self._svc.tof_engine.compute(exchange, symbol)
                    if tof is not None:
                        mark_price = tof.mark_price
                        break
        except Exception:
            pass

        if mark_price == 0.0:
            try:
                state = self._svc.liqmap_engine._state.get((exchange, symbol))
                if state and "mark_price" in state:
                    mark_price = state["mark_price"]
            except Exception:
                pass

        if mark_price == 0.0:
            return None

        return CompositeSignal(
            symbol=symbol,
            exchange=exchange,
            side=side,
            strength=round(
                abs(composite_score - 50) * 2, 1
            ),  # 0-100, distance from neutral
            factors=factors,
            confidence=round(agreement, 3),
            mark_price=mark_price,
            timestamp=now_ms,
        )

    def compute_all(
        self,
        exchange: str,
        weights: dict[str, float] | None = None,
    ) -> list[CompositeSignal]:
        """Compute composite signals for all tracked symbols."""
        if self._svc is None:
            return []

        symbols: set[str] = set()
        try:
            for ex, sym, _ivl in self._svc.cvd_aggregator._bars:
                if ex == exchange:
                    symbols.add(sym)
        except Exception:
            pass
        try:
            for ex, sym in self._svc.liqmap_engine._state:
                if ex == exchange:
                    symbols.add(sym)
        except Exception:
            pass

        results: list[CompositeSignal] = []
        for symbol in sorted(symbols):
            signal = self.compute(exchange, symbol, weights)
            if signal is not None:
                results.append(signal)

        results.sort(key=lambda s: s.strength, reverse=True)
        return results
