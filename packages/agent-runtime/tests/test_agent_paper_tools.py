"""Behaviour locks for paper trading tools: account creation, order
placement, position listing, and PnL reporting."""

from __future__ import annotations

from dataclasses import dataclass, field

from selene_space_agent.tools.paper_tools import (
    PaperCreateAccountTool,
    PaperPlaceOrderTool,
    PaperPnlTool,
    PaperPositionsTool,
)
from selene_space_paper.accounts import AccountManager
from selene_space_paper.orders import PaperOrderManager
from selene_space_paper.positions import PositionManager
from selene_space_types.market import TickerEvent
from selene_space_types.trading import Fill, OrderSide

_TS = 1_710_000_000_000


@dataclass
class _StubMarket:
    """Ticker lookup stand-in for PnL valuation."""

    tickers: dict[str, TickerEvent] = field(default_factory=dict)

    def get_ticker(self, symbol: str) -> TickerEvent | None:
        return self.tickers.get(symbol)


class TestPaperCreateAccountTool:
    def test_successful_open(self) -> None:
        out = PaperCreateAccountTool(AccountManager()).execute(
            {"name": "Sandbox One", "initial_balance": 25_000}
        )
        checks = (
            out.error is None,
            "Sandbox One" in out.output,
            "25,000.00" in out.output,
        )
        assert all(checks)

    def test_name_required(self) -> None:
        out = PaperCreateAccountTool(AccountManager()).execute(
            {"initial_balance": 25_000}
        )
        assert out.error is not None

    def test_negative_balance_rejected(self) -> None:
        out = PaperCreateAccountTool(AccountManager()).execute(
            {"name": "Bad", "initial_balance": -50}
        )
        assert out.error is not None


class TestPaperPlaceOrderTool:
    _ORDER = {
        "account_id": "acct-9",
        "symbol": "ETH/USDT",
        "side": "buy",
        "order_type": "market",
        "quantity": 2.0,
        "user_confirmed": True,  # pass the hard gate (covered in test_guardrail_wiring)
    }

    def test_market_order_accepted(self) -> None:
        out = PaperPlaceOrderTool(PaperOrderManager()).execute(dict(self._ORDER))
        checks = (
            out.error is None,
            "ETH/USDT" in out.output,
            "buy" in out.output.lower(),
        )
        assert all(checks)

    def test_bogus_side_rejected(self) -> None:
        payload = {**self._ORDER, "side": "diagonal"}
        out = PaperPlaceOrderTool(PaperOrderManager()).execute(payload)
        checks = (out.error is not None, "side" in out.error.lower())
        assert all(checks)

    def test_bogus_order_type_rejected(self) -> None:
        payload = {**self._ORDER, "order_type": "exotic"}
        assert (
            PaperPlaceOrderTool(PaperOrderManager()).execute(payload).error is not None
        )

    def test_incomplete_payload_rejected(self) -> None:
        out = PaperPlaceOrderTool(PaperOrderManager()).execute({"account_id": "acct-9"})
        assert out.error is not None


class TestPaperPositionsTool:
    def test_flat_account_message(self) -> None:
        out = PaperPositionsTool(PositionManager()).execute({"account_id": "acct-9"})
        assert "No positions found" in out.output

    def test_filled_position_listed(self) -> None:
        pm = PositionManager()
        pm.apply_fill_with_symbol(
            "acct-9",
            "ETH/USDT",
            Fill(id="f-1", order_id="o-1", price=3400.0, quantity=2.0, timestamp=_TS),
            OrderSide.BUY,
        )

        out = PaperPositionsTool(pm).execute({"account_id": "acct-9"})
        checks = (
            out.error is None,
            "ETH/USDT" in out.output,
            "buy" in out.output.lower(),
        )
        assert all(checks)

    def test_account_key_required(self) -> None:
        assert PaperPositionsTool(PositionManager()).execute({}).error is not None


class TestPaperPnlTool:
    def test_open_position_valued_against_ticker(self) -> None:
        pm = PositionManager()
        pm.apply_fill_with_symbol(
            "acct-9",
            "ETH/USDT",
            Fill(id="f-1", order_id="o-1", price=3200.0, quantity=1.0, timestamp=_TS),
            OrderSide.BUY,
        )

        quotes = _StubMarket(
            tickers={
                "ETH/USDT": TickerEvent(
                    exchange="okx",
                    symbol="ETH/USDT",
                    timestamp=_TS,
                    last_price=3450.0,
                    bid=3449.0,
                    ask=3451.0,
                    volume_24h=42_000.0,
                    change_24h_pct=2.5,
                ),
            }
        )
        out = PaperPnlTool(pm, quotes).execute({"account_id": "acct-9"})
        checks = (out.error is None, "ETH/USDT" in out.output)
        assert all(checks)

    def test_flat_account_message(self) -> None:
        out = PaperPnlTool(PositionManager(), _StubMarket()).execute(
            {"account_id": "acct-9"}
        )
        assert "No positions found" in out.output
