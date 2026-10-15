"""Tests for Smart Order Router."""

from __future__ import annotations

import pytest

from selene_space_exchange.routing.router import (
    ExchangeInfo,
    SmartOrderRouter,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def binance_quote() -> ExchangeInfo:
    return ExchangeInfo(
        exchange_id="binance",
        fee_rate=0.001,
        estimated_slippage_bps=5.0,
        available_liquidity=50_000.0,
        latency_ms=50,
    )


@pytest.fixture()
def okx_quote() -> ExchangeInfo:
    return ExchangeInfo(
        exchange_id="okx",
        fee_rate=0.0008,
        estimated_slippage_bps=8.0,
        available_liquidity=80_000.0,
        latency_ms=80,
    )


@pytest.fixture()
def polymarket_quote() -> ExchangeInfo:
    return ExchangeInfo(
        exchange_id="polymarket",
        fee_rate=0.002,
        estimated_slippage_bps=2.0,
        available_liquidity=10_000.0,
        latency_ms=30,
    )


@pytest.fixture()
def all_quotes(
    binance_quote: ExchangeInfo,
    okx_quote: ExchangeInfo,
    polymarket_quote: ExchangeInfo,
) -> list[ExchangeInfo]:
    return [binance_quote, okx_quote, polymarket_quote]


# ---------------------------------------------------------------------------
# Strategy: best_price  (minimize fee + slippage)
# ---------------------------------------------------------------------------

class TestBestPriceStrategy:
    """best_price should pick the exchange with the lowest total cost."""

    def test_picks_lowest_total_cost(
        self, all_quotes: list[ExchangeInfo]
    ) -> None:
        # Total costs:
        #   binance:    0.001 + 5/10000   = 0.0015
        #   okx:        0.0008 + 8/10000  = 0.0016
        #   polymarket: 0.002 + 2/10000   = 0.0022
        router = SmartOrderRouter(strategy="best_price")
        decision = router.route_order(all_quotes)
        assert decision.selected_exchange == "binance"
        assert decision.reason == "lowest total cost"
        assert decision.all_quotes == all_quotes


# ---------------------------------------------------------------------------
# Strategy: lowest_fee  (minimize fee rate)
# ---------------------------------------------------------------------------

class TestLowestFeeStrategy:
    """lowest_fee should pick the exchange with the smallest fee rate."""

    def test_picks_lowest_fee_rate(
        self, all_quotes: list[ExchangeInfo]
    ) -> None:
        # fee rates: binance=0.001, okx=0.0008, polymarket=0.002
        router = SmartOrderRouter(strategy="lowest_fee")
        decision = router.route_order(all_quotes)
        assert decision.selected_exchange == "okx"
        assert decision.reason == "lowest fee rate"


# ---------------------------------------------------------------------------
# Strategy: best_liquidity  (maximize available liquidity)
# ---------------------------------------------------------------------------

class TestBestLiquidityStrategy:
    """best_liquidity should pick the exchange with the most liquidity."""

    def test_picks_highest_liquidity(
        self, all_quotes: list[ExchangeInfo]
    ) -> None:
        # liquidity: binance=50k, okx=80k, polymarket=10k
        router = SmartOrderRouter(strategy="best_liquidity")
        decision = router.route_order(all_quotes)
        assert decision.selected_exchange == "okx"
        assert decision.reason == "best liquidity"


# ---------------------------------------------------------------------------
# Strategy: lowest_latency  (minimize latency)
# ---------------------------------------------------------------------------

class TestLowestLatencyStrategy:
    """lowest_latency should pick the exchange with the lowest latency."""

    def test_picks_lowest_latency(
        self, all_quotes: list[ExchangeInfo]
    ) -> None:
        # latency: binance=50ms, okx=80ms, polymarket=30ms
        router = SmartOrderRouter(strategy="lowest_latency")
        decision = router.route_order(all_quotes)
        assert decision.selected_exchange == "polymarket"
        assert decision.reason == "lowest latency"


# ---------------------------------------------------------------------------
# compare_fees
# ---------------------------------------------------------------------------

class TestCompareFees:
    """compare_fees should return fee amounts sorted ascending."""

    def test_correct_fee_calculations(
        self, all_quotes: list[ExchangeInfo]
    ) -> None:
        router = SmartOrderRouter()
        notional = 10_000.0
        result = router.compare_fees(all_quotes, notional)
        # okx: 0.0008 * 10000 = 8.0
        # binance: 0.001 * 10000 = 10.0
        # polymarket: 0.002 * 10000 = 20.0
        assert result == [
            ("okx", 8.0),
            ("binance", 10.0),
            ("polymarket", 20.0),
        ]


# ---------------------------------------------------------------------------
# compare_total_cost
# ---------------------------------------------------------------------------

class TestCompareTotalCost:
    """compare_total_cost should include both fee and slippage."""

    def test_includes_fee_and_slippage(
        self, all_quotes: list[ExchangeInfo]
    ) -> None:
        router = SmartOrderRouter()
        notional = 10_000.0
        result = router.compare_total_cost(all_quotes, notional)
        # binance:   0.001*10000 + (5/10000)*10000 = 10.0 + 5.0 = 15.0
        # okx:       0.0008*10000 + (8/10000)*10000 = 8.0 + 8.0 = 16.0
        # polymarket: 0.002*10000 + (2/10000)*10000 = 20.0 + 2.0 = 22.0
        assert result == [
            ("binance", 15.0),
            ("okx", 16.0),
            ("polymarket", 22.0),
        ]


# ---------------------------------------------------------------------------
# compare_slippage
# ---------------------------------------------------------------------------

class TestCompareSlippage:
    """compare_slippage should return slippage values sorted ascending."""

    def test_correct_ordering(
        self, all_quotes: list[ExchangeInfo]
    ) -> None:
        router = SmartOrderRouter()
        result = router.compare_slippage(all_quotes)
        # polymarket=2.0, binance=5.0, okx=8.0
        assert result == [
            ("polymarket", 2.0),
            ("binance", 5.0),
            ("okx", 8.0),
        ]


# ---------------------------------------------------------------------------
# split_order
# ---------------------------------------------------------------------------

class TestSplitOrder:
    """split_order should allocate by liquidity with a minimum floor."""

    def test_sum_equals_total(
        self, all_quotes: list[ExchangeInfo]
    ) -> None:
        router = SmartOrderRouter()
        total_qty = 1000.0
        splits = router.split_order(all_quotes, total_qty, min_split_pct=0.1)
        allocated = sum(qty for _, qty in splits)
        assert abs(allocated - total_qty) < 1e-9

    def test_min_split_pct_respected(
        self, all_quotes: list[ExchangeInfo]
    ) -> None:
        router = SmartOrderRouter()
        total_qty = 1000.0
        min_pct = 0.1
        splits = router.split_order(all_quotes, total_qty, min_split_pct=min_pct)
        for _, qty in splits:
            assert qty >= total_qty * min_pct - 1e-9

    def test_liquidity_weighted_allocation(self) -> None:
        """Exchanges with more liquidity should get a larger share."""
        router = SmartOrderRouter()
        quotes = [
            ExchangeInfo("a", fee_rate=0.001, estimated_slippage_bps=1.0,
                         available_liquidity=80_000.0, latency_ms=10),
            ExchangeInfo("b", fee_rate=0.001, estimated_slippage_bps=1.0,
                         available_liquidity=20_000.0, latency_ms=10),
        ]
        total_qty = 1000.0
        splits = router.split_order(quotes, total_qty, min_split_pct=0.1)
        alloc_map = dict(splits)
        # a has 4x liquidity of b so it should get 4x the allocation
        assert alloc_map["a"] > alloc_map["b"]
        # Rough ratio check: 800 vs 200
        assert abs(alloc_map["a"] - 800.0) < 1e-6
        assert abs(alloc_map["b"] - 200.0) < 1e-6

    def test_min_split_enforced_for_small_exchange(self) -> None:
        """A very small exchange must still receive the minimum allocation."""
        router = SmartOrderRouter()
        quotes = [
            ExchangeInfo("big", fee_rate=0.001, estimated_slippage_bps=1.0,
                         available_liquidity=99_000.0, latency_ms=10),
            ExchangeInfo("tiny", fee_rate=0.001, estimated_slippage_bps=1.0,
                         available_liquidity=1_000.0, latency_ms=10),
        ]
        total_qty = 1000.0
        min_pct = 0.1
        splits = router.split_order(quotes, total_qty, min_split_pct=min_pct)
        alloc_map = dict(splits)
        assert alloc_map["tiny"] >= total_qty * min_pct - 1e-9
        # Total must still sum to total_qty
        assert abs(sum(alloc_map.values()) - total_qty) < 1e-9


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Edge-case scenarios: single exchange, identical quotes, zero liquidity."""

    def test_single_exchange(self) -> None:
        """With one exchange, it is always selected."""
        quote = ExchangeInfo("solo", 0.001, 5.0, 50_000.0, 50)
        for strategy in ("best_price", "lowest_fee", "best_liquidity", "lowest_latency"):
            router = SmartOrderRouter(strategy=strategy)
            decision = router.route_order([quote])
            assert decision.selected_exchange == "solo"

    def test_identical_quotes(self) -> None:
        """When all quotes are identical, any exchange may be selected."""
        quotes = [
            ExchangeInfo("a", 0.001, 5.0, 50_000.0, 50),
            ExchangeInfo("b", 0.001, 5.0, 50_000.0, 50),
            ExchangeInfo("c", 0.001, 5.0, 50_000.0, 50),
        ]
        router = SmartOrderRouter(strategy="best_price")
        decision = router.route_order(quotes)
        assert decision.selected_exchange in {"a", "b", "c"}

    def test_zero_liquidity_split(self) -> None:
        """With zero liquidity on all exchanges, split equally."""
        quotes = [
            ExchangeInfo("a", 0.001, 1.0, 0.0, 10),
            ExchangeInfo("b", 0.001, 1.0, 0.0, 10),
        ]
        router = SmartOrderRouter()
        splits = router.split_order(quotes, total_qty=100.0, min_split_pct=0.1)
        alloc_map = dict(splits)
        assert abs(alloc_map["a"] - 50.0) < 1e-9
        assert abs(alloc_map["b"] - 50.0) < 1e-9

    def test_empty_quotes_raises(self) -> None:
        """Routing with an empty quotes list must raise ValueError."""
        router = SmartOrderRouter()
        with pytest.raises(ValueError, match="quotes list is empty"):
            router.route_order([])

    def test_invalid_strategy_raises(self) -> None:
        """An unknown strategy must raise ValueError."""
        with pytest.raises(ValueError, match="Unknown strategy"):
            SmartOrderRouter(strategy="nonsense")

    def test_split_empty_quotes_raises(self) -> None:
        """Splitting with an empty quotes list must raise ValueError."""
        router = SmartOrderRouter()
        with pytest.raises(ValueError, match="quotes list is empty"):
            router.split_order([], total_qty=100.0)

    def test_split_non_positive_qty_raises(self) -> None:
        """Splitting with zero or negative quantity must raise ValueError."""
        router = SmartOrderRouter()
        quote = ExchangeInfo("a", 0.001, 1.0, 10_000.0, 10)
        with pytest.raises(ValueError, match="total_qty must be positive"):
            router.split_order([quote], total_qty=0.0)
