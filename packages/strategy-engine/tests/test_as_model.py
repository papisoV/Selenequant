"""Tests for the Avellaneda-Stoikov market making model."""

from __future__ import annotations

import math

import pytest

from selene_space_strategy.market_making.as_model import (
    ASParams,
    as_optimal_depths,
    as_optimal_spread,
    as_quotes,
    as_quotes_with_params,
    as_reservation_price,
)


# ---------------------------------------------------------------------------
# as_reservation_price
# ---------------------------------------------------------------------------


class TestReservationPrice:
    """Tests for as_reservation_price."""

    def test_zero_inventory_equals_mid(self) -> None:
        """With zero inventory, reservation price equals mid."""
        r = as_reservation_price(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, q=0.0, t=0.0)
        assert r == 100.0

    def test_positive_inventory_below_mid(self) -> None:
        """Positive inventory (long) pulls reservation price below mid."""
        r = as_reservation_price(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, q=1.0, t=0.0)
        assert r < 100.0

    def test_negative_inventory_above_mid(self) -> None:
        """Negative inventory (short) pushes reservation price above mid."""
        r = as_reservation_price(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, q=-1.0, t=0.0)
        assert r > 100.0

    def test_larger_inventory_larger_deviation(self) -> None:
        """Larger inventory causes larger deviation from mid."""
        r1 = as_reservation_price(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, q=1.0, t=0.0)
        r2 = as_reservation_price(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, q=2.0, t=0.0)
        assert abs(r2 - 100.0) > abs(r1 - 100.0)

    def test_higher_risk_aversion_larger_deviation(self) -> None:
        """Higher gamma (risk aversion) causes larger deviation from mid."""
        r_low = as_reservation_price(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, q=1.0, t=0.0)
        r_high = as_reservation_price(mid=100.0, sigma=0.1, T=1.0, gamma=0.5, q=1.0, t=0.0)
        assert abs(r_high - 100.0) > abs(r_low - 100.0)


# ---------------------------------------------------------------------------
# as_optimal_spread
# ---------------------------------------------------------------------------


class TestOptimalSpread:
    """Tests for as_optimal_spread."""

    def test_increases_with_volatility(self) -> None:
        """Higher volatility produces a wider spread."""
        s_low = as_optimal_spread(sigma=0.1, T=1.0, gamma=0.1, k=1.5, t=0.0)
        s_high = as_optimal_spread(sigma=0.3, T=1.0, gamma=0.1, k=1.5, t=0.0)
        assert s_high > s_low

    def test_decreases_over_time(self) -> None:
        """Spread narrows as time t approaches T (less uncertainty)."""
        s_early = as_optimal_spread(sigma=0.1, T=1.0, gamma=0.1, k=1.5, t=0.0)
        s_late = as_optimal_spread(sigma=0.1, T=1.0, gamma=0.1, k=1.5, t=0.9)
        assert s_late < s_early

    def test_positive(self) -> None:
        """Optimal spread is always positive."""
        s = as_optimal_spread(sigma=0.2, T=1.0, gamma=0.1, k=1.5, t=0.5)
        assert s > 0

    def test_at_terminal_time(self) -> None:
        """At t=T, only the depth component remains (inventory term vanishes)."""
        s = as_optimal_spread(sigma=0.2, T=1.0, gamma=0.1, k=1.5, t=1.0)
        expected = (2.0 / 0.1) * math.log(1.0 + 0.1 / 1.5)
        assert abs(s - expected) < 1e-10

    def test_beyond_terminal_time_clamped(self) -> None:
        """At t > T, remaining time is clamped to 0 (same as t=T)."""
        s_at_T = as_optimal_spread(sigma=0.2, T=1.0, gamma=0.1, k=1.5, t=1.0)
        s_past_T = as_optimal_spread(sigma=0.2, T=1.0, gamma=0.1, k=1.5, t=2.0)
        assert s_past_T == s_at_T


# ---------------------------------------------------------------------------
# as_optimal_depths
# ---------------------------------------------------------------------------


class TestOptimalDepths:
    """Tests for as_optimal_depths."""

    def test_positive(self) -> None:
        """Both depths are positive."""
        db, da = as_optimal_depths(gamma=0.1, k=1.5)
        assert db > 0
        assert da > 0

    def test_symmetric(self) -> None:
        """In the symmetric case, bid and ask depths are equal."""
        db, da = as_optimal_depths(gamma=0.1, k=1.5)
        assert db == da

    def test_higher_gamma_smaller_depth(self) -> None:
        """Higher risk aversion (gamma) leads to tighter depths."""
        d_low, _ = as_optimal_depths(gamma=0.1, k=1.5)
        d_high, _ = as_optimal_depths(gamma=0.5, k=1.5)
        assert d_high < d_low

    def test_higher_k_smaller_depth(self) -> None:
        """Higher order book intensity (k) leads to tighter depths."""
        d_low, _ = as_optimal_depths(gamma=0.1, k=1.0)
        d_high, _ = as_optimal_depths(gamma=0.1, k=5.0)
        assert d_high < d_low

    def test_known_value(self) -> None:
        """Verify against a hand-computed value."""
        gamma, k = 0.1, 1.5
        expected = (1.0 / gamma) * math.log(1.0 + gamma / k)
        db, da = as_optimal_depths(gamma, k)
        assert abs(db - expected) < 1e-10
        assert abs(da - expected) < 1e-10


# ---------------------------------------------------------------------------
# as_quotes
# ---------------------------------------------------------------------------


class TestASQuotes:
    """Tests for as_quotes."""

    def test_bid_below_reservation(self) -> None:
        """Bid price is below the reservation price."""
        q = as_quotes(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, k=1.5, q=0.0, t=0.0)
        assert q.bid_price < q.reservation_price

    def test_ask_above_reservation(self) -> None:
        """Ask price is above the reservation price."""
        q = as_quotes(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, k=1.5, q=0.0, t=0.0)
        assert q.ask_price > q.reservation_price

    def test_spread_positive(self) -> None:
        """Spread (ask - bid) is positive."""
        q = as_quotes(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, k=1.5, q=0.0, t=0.0)
        assert q.spread > 0

    def test_spread_equals_ask_minus_bid(self) -> None:
        """Spread equals ask_price - bid_price."""
        q = as_quotes(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, k=1.5, q=1.0, t=0.3)
        assert abs(q.spread - (q.ask_price - q.bid_price)) < 1e-10

    def test_reservation_price_matches_formula(self) -> None:
        """Reservation price in quotes matches standalone formula."""
        mid, sigma, T, gamma, k, q, t = 100.0, 0.1, 1.0, 0.1, 1.5, 0.5, 0.3
        quotes = as_quotes(mid, sigma, T, gamma, k, q, t)
        expected_r = as_reservation_price(mid, sigma, T, gamma, q, t)
        assert abs(quotes.reservation_price - expected_r) < 1e-10

    def test_default_sizes_are_one(self) -> None:
        """Default bid/ask sizes are 1.0."""
        q = as_quotes(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, k=1.5, q=0.0, t=0.0)
        assert q.bid_size == 1.0
        assert q.ask_size == 1.0


# ---------------------------------------------------------------------------
# as_quotes_with_params
# ---------------------------------------------------------------------------


class TestASQuotesWithParams:
    """Tests for as_quotes_with_params."""

    def test_matches_as_quotes(self) -> None:
        """as_quotes_with_params produces identical results to as_quotes."""
        params = ASParams(sigma=0.1, T=1.0, gamma=0.1, k=1.5, q_max=10.0)
        q_direct = as_quotes(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, k=1.5, q=0.5, t=0.3)
        q_params = as_quotes_with_params(mid=100.0, q=0.5, t=0.3, params=params)
        assert q_direct.bid_price == q_params.bid_price
        assert q_direct.ask_price == q_params.ask_price
        assert q_direct.spread == q_params.spread
        assert q_direct.reservation_price == q_params.reservation_price

    def test_params_frozen(self) -> None:
        """ASParams is frozen (immutable)."""
        params = ASParams(sigma=0.1, T=1.0, gamma=0.1, k=1.5, q_max=10.0)
        with pytest.raises(AttributeError):
            params.sigma = 0.2  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge case tests for the AS model."""

    def test_t_zero(self) -> None:
        """At t=0, full trading period remains."""
        q = as_quotes(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, k=1.5, q=0.0, t=0.0)
        assert q.reservation_price == 100.0  # q=0 so r=mid
        assert q.spread > 0

    def test_t_equals_T(self) -> None:
        """At t=T, inventory component vanishes; only depth remains."""
        q = as_quotes(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, k=1.5, q=1.0, t=1.0)
        # reservation price = mid (remaining time is 0)
        assert q.reservation_price == 100.0
        # spread is only the depth component
        expected_depth = (1.0 / 0.1) * math.log(1.0 + 0.1 / 1.5)
        assert abs(q.spread - 2.0 * expected_depth) < 1e-10

    def test_q_zero_reservation_equals_mid(self) -> None:
        """With q=0, reservation price equals mid regardless of other params."""
        r = as_reservation_price(mid=50000.0, sigma=1.0, T=1.0, gamma=10.0, q=0.0, t=0.0)
        assert r == 50000.0

    def test_large_inventory(self) -> None:
        """Large inventory causes significant reservation price shift."""
        r = as_reservation_price(mid=100.0, sigma=0.5, T=1.0, gamma=0.5, q=10.0, t=0.0)
        # r = 100 - 10 * 0.5 * 0.25 * 1.0 = 100 - 1.25 = 98.75
        expected = 100.0 - 10.0 * 0.5 * 0.25 * 1.0
        assert abs(r - expected) < 1e-10

    def test_quotes_with_large_inventory_bid_lowered(self) -> None:
        """Large long inventory significantly lowers the bid price."""
        q_flat = as_quotes(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, k=1.5, q=0.0, t=0.0)
        q_long = as_quotes(mid=100.0, sigma=0.1, T=1.0, gamma=0.1, k=1.5, q=5.0, t=0.0)
        # With long inventory, reservation price drops, pulling both bid and ask down
        assert q_long.reservation_price < q_flat.reservation_price
        assert q_long.bid_price < q_flat.bid_price
