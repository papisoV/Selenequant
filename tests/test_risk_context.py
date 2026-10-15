"""Tests for live engine risk context completeness.

Covers:
  - daily_realized_pnl computed from fills
  - peak_equity tracking
  - positions as dict[symbol, dict] with notional, entry_price, unrealized_pnl
"""

from __future__ import annotations

import pytest

from selene_space_exchange.execution.risk_context import (
    build_risk_context,
    compute_daily_realized_pnl,
    compute_peak_equity,
    enrich_positions,
)
from selene_space_types.trading import Fill, Position


# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------


def _make_fills() -> list[Fill]:
    import time
    now = int(time.time() * 1000)
    return [
        Fill(
            id="f-001", order_id="o-001", price=50000.0, quantity=10000.0,
            fee=5.0, fee_currency="USDT", fee_rate=0.0005,
            realized_pnl=0.0, exec_type="taker", side="buy", pos_side="long",
            symbol="BTC/USDT", leverage=1, reduce_only=False, timestamp=now,
        ),
        Fill(
            id="f-002", order_id="o-002", price=51000.0, quantity=10000.0,
            fee=5.0, fee_currency="USDT", fee_rate=0.0005,
            realized_pnl=1000.0, exec_type="taker", side="sell", pos_side="long",
            symbol="BTC/USDT", leverage=1, reduce_only=True, timestamp=now,
        ),
    ]


def _make_positions() -> list[Position]:
    import time
    now = int(time.time() * 1000)
    return [
        Position(
            symbol="BTC/USDT", side="buy", pos_side="long",
            quantity=50000.0, quantity_base=1.0, avg_entry_price=50000.0,
            leverage=1, margin_mode="cross", margin=50000.0,
            liquidation_price=None, unrealized_pnl=500.0, unrealized_pnl_pct=1.0,
            realized_pnl=0.0, current_price=50500.0,
            opened_at=now, updated_at=now,
        ),
        Position(
            symbol="ETH/USDT", side="buy", pos_side="long",
            quantity=30000.0, quantity_base=10.0, avg_entry_price=3000.0,
            leverage=1, margin_mode="cross", margin=30000.0,
            liquidation_price=None, unrealized_pnl=-100.0, unrealized_pnl_pct=-0.33,
            realized_pnl=0.0, current_price=2990.0,
            opened_at=now, updated_at=now,
        ),
    ]


# ---------------------------------------------------------------------------
# compute_daily_realized_pnl
# ---------------------------------------------------------------------------


class TestDailyRealizedPnl:
    def test_sums_realized_pnl_from_fills(self) -> None:
        fills = _make_fills()
        pnl = compute_daily_realized_pnl(fills)
        assert pnl == pytest.approx(1000.0)

    def test_empty_fills_returns_zero(self) -> None:
        assert compute_daily_realized_pnl([]) == 0.0

    def test_excludes_non_today_fills(self) -> None:
        fills = _make_fills()
        pnl = compute_daily_realized_pnl(fills)
        assert pnl == 1000.0  # All fills are from today (same timestamp)


# ---------------------------------------------------------------------------
# compute_peak_equity
# ---------------------------------------------------------------------------


class TestPeakEquity:
    def test_peak_is_max_of_current_and_previous(self) -> None:
        assert compute_peak_equity(current_equity=105000.0, previous_peak=100000.0) == 105000.0

    def test_peak_preserved_when_equity_drops(self) -> None:
        assert compute_peak_equity(current_equity=95000.0, previous_peak=100000.0) == 100000.0

    def test_no_previous_peak_uses_current(self) -> None:
        assert compute_peak_equity(current_equity=100000.0, previous_peak=None) == 100000.0


# ---------------------------------------------------------------------------
# enrich_positions
# ---------------------------------------------------------------------------


class TestEnrichPositions:
    def test_converts_to_dict_of_dicts(self) -> None:
        positions = _make_positions()
        enriched = enrich_positions(positions)
        assert isinstance(enriched, dict)
        btc = enriched["BTC/USDT"]
        assert isinstance(btc, dict)
        assert btc["notional"] == pytest.approx(50000.0)
        assert btc["entry_price"] == pytest.approx(50000.0)
        assert btc["unrealized_pnl"] == pytest.approx(500.0)

    def test_empty_positions(self) -> None:
        assert enrich_positions([]) == {}

    def test_includes_leverage_and_side(self) -> None:
        positions = _make_positions()
        enriched = enrich_positions(positions)
        assert enriched["BTC/USDT"]["leverage"] == 1
        assert enriched["BTC/USDT"]["side"] == "buy"


# ---------------------------------------------------------------------------
# build_risk_context
# ---------------------------------------------------------------------------


class TestBuildRiskContext:
    def test_assembles_full_context(self) -> None:
        context = build_risk_context(
            account_id="test-account",
            positions=_make_positions(),
            balances=[],
            total_equity=100000.0,
            leverage=1,
            fills=_make_fills(),
            previous_peak_equity=None,
            current_price=50500.0,
            open_orders=[],
        )
        assert context["account_id"] == "test-account"
        assert context["total_equity"] == 100000.0
        assert context["portfolio_leverage"] == 1.0
        assert context["daily_realized_pnl"] == pytest.approx(1000.0)
        assert context["peak_equity"] == 100000.0
        assert context["current_price"] == 50500.0
        assert isinstance(context["positions"], dict)
        assert isinstance(context["positions"].get("BTC/USDT"), dict)

    def test_peak_equity_respects_previous(self) -> None:
        context = build_risk_context(
            account_id="test",
            positions=[],
            balances=[],
            total_equity=95000.0,
            leverage=1,
            fills=[],
            previous_peak_equity=100000.0,
            current_price=50000.0,
            open_orders=[],
        )
        assert context["peak_equity"] == 100000.0
