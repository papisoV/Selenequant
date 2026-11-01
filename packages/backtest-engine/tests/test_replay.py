"""Replay tests: reconstruct portfolio snapshots from a trade ledger.

Acceptance anchors 1-8 from the P1-3 spec, exercised TDD-style.
"""

import json

import pytest

from selene_space_backtest.replay import PortfolioReplayResult, replay_orders


def _trade(
    *,
    symbol: str = "BTC/USDT",
    side: str = "long",
    entry_price: float = 100.0,
    exit_price: float = 110.0,
    quantity: float = 2.0,
    pnl: float | None = None,
    entry_time: int = 1000,
    exit_time: int = 2000,
) -> dict:
    """Build a complete round-trip trade record shaped like engine output."""
    if pnl is None:
        pnl = (
            (exit_price - entry_price) * quantity
            if side == "long"
            else (entry_price - exit_price) * quantity
        )
    return {
        "symbol": symbol,
        "side": side,
        "entry_action": "buy" if side == "long" else "sell",
        "exit_action": "sell" if side == "long" else "buy",
        "entry_price": entry_price,
        "exit_price": exit_price,
        "quantity": quantity,
        "pnl": pnl,
        "entry_time": entry_time,
        "exit_time": exit_time,
    }


class TestAnchor1SingleLong:
    def test_open_and_close_on_default_points(self) -> None:
        res = replay_orders([_trade()], 10_000.0)

        assert res.timestamps == [1000, 2000]
        assert res.cash_curve == pytest.approx([9_800.0, 10_020.0])
        assert res.position_qty == pytest.approx([2.0, 0.0])
        assert res.equity_curve == pytest.approx([10_000.0, 10_020.0])
        assert res.exposure_curve == pytest.approx([200.0, 0.0])
        assert res.final_state["cash"] == pytest.approx(10_020.0)
        assert res.final_state["positions"] == {}

    def test_holding_stage_marks_cash_and_equity(self) -> None:
        res = replay_orders([_trade()], 10_000.0, timestamps=[1000, 1500, 2000])

        assert res.cash_curve == pytest.approx([9_800.0, 9_800.0, 10_020.0])
        assert res.position_qty == pytest.approx([2.0, 2.0, 0.0])
        assert res.equity_curve == pytest.approx([10_000.0, 10_000.0, 10_020.0])


class TestAnchor2SingleShort:
    def test_short_credits_cash_on_open_debits_on_close(self) -> None:
        res = replay_orders(
            [_trade(side="short", entry_price=100.0, exit_price=90.0)],
            10_000.0,
        )

        # pnl = (100 - 90) * 2 = 20; net cash change = +20
        assert res.cash_curve == pytest.approx([10_200.0, 10_020.0])
        assert res.position_qty == pytest.approx([-2.0, 0.0])
        assert res.equity_curve == pytest.approx([10_000.0, 10_020.0])
        assert res.exposure_curve == pytest.approx([200.0, 0.0])


class TestAnchor3OverlappingSameSymbol:
    def test_net_position_and_net_exposure(self) -> None:
        trades = [
            _trade(
                entry_price=100.0,
                exit_price=110.0,
                quantity=1.0,
                entry_time=1000,
                exit_time=3000,
            ),
            _trade(
                entry_price=100.0,
                exit_price=120.0,
                quantity=1.0,
                entry_time=2000,
                exit_time=4000,
            ),
        ]
        res = replay_orders(trades, 10_000.0)

        # t=2000: two overlapping longs -> net qty 2, exposure = 2 x 100
        assert res.timestamps == [1000, 2000, 3000, 4000]
        assert res.position_qty == pytest.approx([1.0, 2.0, 1.0, 0.0])
        assert res.exposure_curve == pytest.approx([100.0, 200.0, 110.0, 0.0])
        assert res.final_state["cash"] == pytest.approx(10_030.0)


class TestAnchor4ArbitraryPointsWithGaps:
    def test_no_trade_points_hold_position_and_mark_to_latest(self) -> None:
        trades = [
            _trade(
                entry_price=100.0,
                exit_price=110.0,
                quantity=1.0,
                entry_time=1000,
                exit_time=3000,
            ),
            _trade(
                entry_price=120.0,
                exit_price=130.0,
                quantity=1.0,
                entry_time=2000,
                exit_time=4000,
            ),
        ]
        points = [500, 1000, 1500, 2000, 2500, 3000, 3500, 4000]
        res = replay_orders(trades, 10_000.0, timestamps=points)

        assert res.timestamps == points
        # before any trade: untouched initial state
        assert res.cash_curve[0] == pytest.approx(10_000.0)
        assert res.position_qty[0] == pytest.approx(0.0)
        # mid-holding gap (t=2500): position unchanged, equity marks to latest price (120)
        assert res.position_qty[4] == pytest.approx(2.0)
        assert res.equity_curve[4] == pytest.approx(9_780.0 + 240.0)


class TestAnchor5EmptyTrades:
    def test_single_point_flat_initial_state(self) -> None:
        res = replay_orders([], 10_000.0)

        assert res.timestamps == [0]
        assert res.cash_curve == pytest.approx([10_000.0])
        assert res.position_qty == pytest.approx([0.0])
        assert res.equity_curve == pytest.approx([10_000.0])
        assert res.exposure_curve == pytest.approx([0.0])
        assert res.leverage_curve == pytest.approx([0.0])
        assert res.allocation_curve == [{}]


class TestAnchor6ZeroEquityNoDivideByZero:
    def test_leverage_records_zero_when_equity_is_zero(self) -> None:
        res = replay_orders(
            [_trade(entry_price=100.0, exit_price=90.0, quantity=1.0)],
            initial_cash=0.0,
        )

        # at entry: cash=-100, pos=1, mark=100 -> equity=0, exposure=100
        assert res.equity_curve[0] == pytest.approx(0.0)
        assert res.exposure_curve[0] == pytest.approx(100.0)
        assert res.leverage_curve[0] == pytest.approx(0.0)


class TestAnchor7Serialization:
    def test_model_dumps_to_json_with_all_fields(self) -> None:
        res = replay_orders([_trade()], 10_000.0)

        assert isinstance(res, PortfolioReplayResult)
        dumped = res.model_dump(mode="json")
        assert set(dumped) == {
            "cash_curve",
            "position_qty",
            "equity_curve",
            "exposure_curve",
            "leverage_curve",
            "allocation_curve",
            "timestamps",
            "final_state",
        }
        json.dumps(dumped)  # must serialize to plain JSON
        assert isinstance(dumped["timestamps"], list)
        assert isinstance(dumped["final_state"], dict)


class TestAnchor8OutOfOrderTrades:
    def test_shuffled_trades_replay_identically(self) -> None:
        trades = [
            _trade(
                entry_price=100.0,
                exit_price=110.0,
                quantity=1.0,
                entry_time=1000,
                exit_time=3000,
            ),
            _trade(
                entry_price=100.0,
                exit_price=120.0,
                quantity=1.0,
                entry_time=2000,
                exit_time=4000,
            ),
        ]
        expected = replay_orders(trades, 10_000.0)
        actual = replay_orders(list(reversed(trades)), 10_000.0)

        assert actual.model_dump() == expected.model_dump()
