"""Aggregator coverage — large-trade / large-order detection and the
liquidation statistics rollup across windows."""

from __future__ import annotations

import time

import pytest

from selene_space_market.aggregators.large_order import LargeOrderDetector
from selene_space_market.aggregators.large_trade import LargeTradeDetector
from selene_space_market.aggregators.liquidation import LiquidationAggregator
from selene_space_types.derivatives import (
    LargeTradeEvent,
    LiquidationEvent,
    LiquidationStats,
)
from selene_space_types.market import OrderBookL2Snapshot, PriceLevel, TradeEvent

WALL_PRICE = 68000.0


def _now_ms() -> int:
    return int(time.time() * 1000)


def _trade(
    notional: float, *, side: str = "buy", price: float = WALL_PRICE
) -> TradeEvent:
    return TradeEvent(
        exchange="binance",
        symbol="BTC/USDT",
        market_type="futures",
        timestamp=_now_ms(),
        trade_id="1",
        price=price,
        quantity=notional / price,
        side=side,
    )


def _liquidation(side: str, notional: float) -> LiquidationEvent:
    return LiquidationEvent(
        exchange="binance",
        symbol="BTC/USDT",
        side=side,
        quantity=notional / WALL_PRICE,
        price=WALL_PRICE,
        avg_price=WALL_PRICE,
        notional_usd=notional,
        timestamp=_now_ms(),
    )


def _level(notionals: list[tuple[float, float]]) -> list[PriceLevel]:
    return [PriceLevel(price=p, quantity=q) for p, q in notionals]


def _book(
    bids: list[tuple[float, float]], asks: list[tuple[float, float]]
) -> OrderBookL2Snapshot:
    return OrderBookL2Snapshot(
        exchange="binance",
        symbol="BTC/USDT",
        market_type="futures",
        timestamp=_now_ms(),
        sequence_id=1,
        bids=_level(bids),
        asks=_level(asks),
    )


class TestLargeTradeDetector:
    """notional = price × quantity; promotion happens above threshold."""

    def test_below_threshold_swallowed(self) -> None:
        assert (
            LargeTradeDetector(threshold_usd=100_000).process(_trade(68_000.0)) is None
        )

    def test_above_threshold_promoted(self) -> None:
        promoted = LargeTradeDetector(threshold_usd=50_000).process(_trade(68_000.0))
        assert isinstance(promoted, LargeTradeEvent)
        assert promoted.notional_usd == pytest.approx(68_000.0)

    def test_callback_receives_promoted_event(self) -> None:
        seen: list[LargeTradeEvent] = []
        detector = LargeTradeDetector(threshold_usd=10_000)
        detector.on_large_trade(seen.append)
        detector.process(_trade(34_000.0))
        assert [ev.side for ev in seen] == ["buy"]

    @pytest.mark.parametrize(("requested", "expected_len"), [(5, 5), (20, 10)])
    def test_recent_window_bounded_by_history(
        self, requested: int, expected_len: int
    ) -> None:
        detector = LargeTradeDetector(threshold_usd=1_000)
        for _ in range(10):
            detector.process(_trade(68_000.0))  # each clears the 1K gate
        assert len(detector.get_recent(requested)) == expected_len

    def test_threshold_setter_roundtrip(self) -> None:
        detector = LargeTradeDetector()
        detector.threshold_usd = 200_000
        assert detector.threshold_usd == 200_000


class TestLargeOrderDetector:
    """Walls are order-book levels whose notional clears the threshold."""

    def test_bid_wall_appearance_emitted_once(self) -> None:
        events = LargeOrderDetector(threshold_usd=100_000).process(
            _book(
                bids=[(68000.0, 2.0), (67999.0, 0.5)],  # notionals 136K, 34K
                asks=[(68001.0, 0.1)],
            )
        )
        assert len(events) == 1
        first = events[0]
        assert (first.side, first.event_type) == ("bid", "appeared")
        assert first.notional_usd == pytest.approx(136_000.0)

    def test_wall_shrink_reports_disappeared(self) -> None:
        detector = LargeOrderDetector(threshold_usd=100_000)
        thick = _book(bids=[(68000.0, 2.0)], asks=[])
        thin = _book(bids=[(68000.0, 0.1)], asks=[])

        detector.process(thick)
        gone = [ev for ev in detector.process(thin) if ev.event_type == "disappeared"]
        assert len(gone) == 1

    def test_snapshot_walls_partitioned_per_side(self) -> None:
        walls = LargeOrderDetector(threshold_usd=50_000).get_current_walls(
            _book(
                bids=[(68000.0, 2.0), (67999.0, 0.01)],
                asks=[(68001.0, 3.0), (68002.0, 0.01)],
            )
        )
        assert {side: len(levels) for side, levels in walls.items()} == {
            "bid_walls": 1,
            "ask_walls": 1,
        }


LIQUIDATION_FEED = [("long", 100_000), ("short", 50_000)]


class TestLiquidationStatsRollup:
    def test_both_sides_aggregated(self) -> None:
        agg = LiquidationAggregator()
        for side, notional in LIQUIDATION_FEED:
            agg.process(_liquidation(side, notional))

        stats = agg.get_stats("1h")
        assert isinstance(stats, LiquidationStats)
        assert stats.long_liquidated_usd == pytest.approx(100_000)
        assert stats.short_liquidated_usd == pytest.approx(50_000)
        assert stats.total_liquidated_usd == pytest.approx(150_000)
        assert stats.long_count + stats.short_count == 2

    def test_window_coverage_complete(self) -> None:
        agg = LiquidationAggregator()
        agg.process(_liquidation("long", 200_000))
        assert set(agg.get_all_stats()) >= {"15m", "30m", "1h", "4h", "24h"}

    def test_one_callback_per_window(self) -> None:
        updates: list[LiquidationStats] = []
        agg = LiquidationAggregator()
        agg.on_stats_update(updates.append)
        agg.process(_liquidation("long", 100_000))
        # five tracked windows → five callbacks per event
        assert len(updates) == 5

    def test_recent_events_truncates(self) -> None:
        agg = LiquidationAggregator()
        for _ in range(5):
            agg.process(_liquidation("long", 10_000))
        assert len(agg.get_recent_events(3)) == 3

    def test_largest_single_survives_mixed_sides(self) -> None:
        agg = LiquidationAggregator()
        for side, notional in (("long", 50_000), ("short", 200_000), ("long", 30_000)):
            agg.process(_liquidation(side, notional))
        stats = agg.get_stats("1h")
        assert stats is not None
        assert stats.largest_single_usd == pytest.approx(200_000)


class TestCompositeSignalEngineCVDGap:
    """Regression: cvd bar unavailable (ws gap) must not UnboundLocalError.

    The /signals/dashboard 500 (2026-09-20 + 2026-09-25 live) — bar=None fell
    through without assigning cvd_normalized, then the SignalFactor append
    referenced it.
    """

    def test_compute_survives_missing_cvd_bar(self) -> None:
        from unittest.mock import MagicMock

        from selene_space_market.aggregators.signal_engine import CompositeSignalEngine

        engine = CompositeSignalEngine()
        svc = MagicMock()
        svc.cvd_aggregator.get_current_bar.return_value = None  # ws gap, no raise
        svc.liqmap_engine._state = {("binance", "BTC/USDT"): {"mark_price": 50000.0}}
        svc.liqmap_engine.compute_map.return_value = None
        svc.tof_engine.compute.return_value = None
        svc._funding_rate_store = {}
        svc.get_orderbook.return_value = None
        engine.set_service(svc)

        signal = engine.compute("binance", "BTC/USDT")
        assert signal is not None
        cvd = next(f for f in signal.factors if f.name == "cvd")
        assert cvd.value == 0.0
        assert cvd.direction == "neutral"
