"""Toxic Orderflow (TOF) engine — composite toxicity score.

Combines CVD delta, liquidation imbalance, and orderbook imbalance
into a single 0-100 toxicity score per symbol. Higher scores indicate
more toxic (informed) orderflow — i.e. aggressive selling into bids
or buying into asks with liquidation cascades.

This is a compute-on-demand engine (not a streaming aggregator).
It reads current state from CVDAggregator, LiquidationAggregator,
and the latest orderbook snapshots.
"""

from __future__ import annotations

import logging
import time

from selene_space_types.derivatives import ToxicOrderflowScore

logger = logging.getLogger(__name__)

# Weight configuration for composite score
_W_CVD = 0.40
_W_LIQ = 0.35
_W_BOOK = 0.25


class ToxicOrderflowEngine:
    """Compute toxic orderflow scores on demand.

    Usage::

        engine = ToxicOrderflowEngine(market_service)
        scores = engine.compute_all("binance")
        score = engine.compute("binance", "BTC/USDT")
    """

    def __init__(self, market_service: object | None = None) -> None:
        self._svc = market_service

    def set_service(self, market_service: object) -> None:
        self._svc = market_service

    def compute(
        self,
        exchange: str,
        symbol: str,
        cvd_interval: str = "5m",
        liq_window: str = "1h",
    ) -> ToxicOrderflowScore | None:
        """Compute TOF score for a single symbol."""
        if self._svc is None:
            return None

        now_ms = int(time.time() * 1000)

        # --- CVD delta ---
        cvd_delta = 0.0
        try:
            bar = self._svc.cvd_aggregator.get_current_bar(exchange, symbol, cvd_interval)
            if bar is not None:
                cvd_delta = bar.cvd_usd
        except Exception:
            logger.debug("TOF: CVD unavailable for %s/%s", exchange, symbol)

        # --- Liquidation imbalance ---
        liq_imbalance = 0.0
        try:
            stats = self._svc._liquidation_aggregator.get_stats(liq_window, symbol)
            if stats is not None:
                liq_imbalance = stats.long_liquidation_usd - stats.short_liquidation_usd
        except Exception:
            logger.debug("TOF: Liquidation stats unavailable for %s/%s", exchange, symbol)

        # --- Orderbook imbalance ---
        book_imbalance = 0.0
        mark_price = 0.0
        try:
            for mt in ("futures", "spot"):
                ob = self._svc.get_orderbook(symbol, exchange=exchange, market_type=mt)
                if ob is not None and ob.bids and ob.asks:
                    bid_vol = sum(level.quantity for level in ob.bids[:20])
                    ask_vol = sum(level.quantity for level in ob.asks[:20])
                    total = bid_vol + ask_vol
                    if total > 0:
                        book_imbalance = (bid_vol - ask_vol) / total
                    mark_price = ob.bids[0].price if ob.bids else 0.0
                    break
        except Exception:
            logger.debug("TOF: Orderbook unavailable for %s/%s", exchange, symbol)

        if mark_price == 0.0:
            # Try mark price from LiqMap engine
            try:
                state = self._svc.liqmap_engine._state.get((exchange, symbol))
                if state and "mark_price" in state:
                    mark_price = state["mark_price"]
            except Exception:
                pass

        if mark_price == 0.0:
            return None

        # --- Composite toxicity score ---
        # Normalize each component to 0-1 range, then weight and scale to 0-100
        cvd_norm = min(abs(cvd_delta) / 1_000_000, 1.0)  # $1M = max signal
        liq_norm = min(abs(liq_imbalance) / 5_000_000, 1.0)  # $5M = max signal
        book_norm = abs(book_imbalance)

        raw_score = _W_CVD * cvd_norm + _W_LIQ * liq_norm + _W_BOOK * book_norm
        toxicity_score = min(raw_score * 100, 100.0)

        # --- Direction inference ---
        # Negative CVD + long liquidations + bid weakness = bearish toxicity
        # Positive CVD + short liquidations + ask weakness = bullish toxicity
        direction_signal = 0.0
        if cvd_delta != 0:
            direction_signal += -1 if cvd_delta < 0 else 1
        if liq_imbalance != 0:
            direction_signal += -1 if liq_imbalance > 0 else 1  # long liq = bearish
        if book_imbalance != 0:
            direction_signal += 1 if book_imbalance > 0 else -1  # more bids = bullish

        if direction_signal >= 2:
            direction = "bullish"
        elif direction_signal <= -2:
            direction = "bearish"
        else:
            direction = "neutral"

        return ToxicOrderflowScore(
            exchange=exchange,
            symbol=symbol,
            cvd_delta=cvd_delta,
            liq_imbalance=liq_imbalance,
            book_imbalance=book_imbalance,
            toxicity_score=round(toxicity_score, 2),
            direction=direction,
            mark_price=mark_price,
            timestamp=now_ms,
        )

    def compute_all(
        self,
        exchange: str,
        cvd_interval: str = "5m",
        liq_window: str = "1h",
    ) -> list[ToxicOrderflowScore]:
        """Compute TOF scores for all tracked symbols on an exchange."""
        if self._svc is None:
            return []

        symbols: set[str] = set()
        # Collect symbols from CVD aggregator
        try:
            for (ex, sym, _ivl) in self._svc.cvd_aggregator._bars:
                if ex == exchange:
                    symbols.add(sym)
        except Exception:
            pass
        # Collect symbols from LiqMap engine
        try:
            for (ex, sym) in self._svc.liqmap_engine._state:
                if ex == exchange:
                    symbols.add(sym)
        except Exception:
            pass

        results: list[ToxicOrderflowScore] = []
        for symbol in sorted(symbols):
            score = self.compute(exchange, symbol, cvd_interval, liq_window)
            if score is not None:
                results.append(score)

        results.sort(key=lambda s: s.toxicity_score, reverse=True)
        return results
