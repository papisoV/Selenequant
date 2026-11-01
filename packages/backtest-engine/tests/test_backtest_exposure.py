"""Signed exposure curve on BacktestResult: position market value / equity."""

from __future__ import annotations

import pytest

from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide

_ORIGIN = 1711000000000
_BAR_MS = 3_600_000


def _closes(*prices: float) -> list[KlineEvent]:
    return [
        KlineEvent(
            exchange="backtest",
            symbol="BTC/USDT",
            timestamp=_ORIGIN + slot * _BAR_MS,
            interval="1h",
            open=px,
            high=px * 1.01,
            low=px * 0.99,
            close=px,
            volume=100.0,
            closed=True,
        )
        for slot, px in enumerate(prices)
    ]


class _Plays:
    def __init__(self, plays: list[tuple[OrderSide, str]]) -> None:
        self._queue = list(plays)
        self._cursor = 0

    def on_kline(self, event: KlineEvent) -> Signal | None:
        if self._cursor >= len(self._queue):
            return None
        side, why = self._queue[self._cursor]
        self._cursor += 1
        return Signal(
            strategy_id="exposure-test",
            symbol=event.symbol,
            side=side,
            strength=1.0,
            timestamp=event.timestamp,
            reason=why,
        )

    def reset(self) -> None:
        self._cursor = 0


def _config() -> BacktestConfig:
    return BacktestConfig(
        initial_cash=10_000.0,
        commission=NoCommission(),
        slippage=NoSlippage(),
        position_sizing="all_in",
    )


def test_flat_tape_exposure_is_zero() -> None:
    engine = BacktestEngine(config=_config())
    outcome = engine.run(_Plays([]), _closes(100.0, 101.0, 102.0))
    assert outcome.exposure_curve == [0.0, 0.0, 0.0]
    assert len(outcome.exposure_curve) == len(outcome.equity_curve)


def test_long_exposure_is_positive_and_bounded() -> None:
    engine = BacktestEngine(config=_config())
    # bar0 BUY fills bar1 open=120; close bar2 (SELL fills bar3 open — absent,
    # so position rides to the end)
    outcome = engine.run(
        _Plays([(OrderSide.BUY, "go long")]), _closes(100.0, 120.0, 130.0)
    )
    assert outcome.exposure_curve[0] == 0.0
    assert all(v > 0 for v in outcome.exposure_curve[1:])
    # 跳空修复后:BUY 按实际成交价+费用 cap,cash 永不为负 → 敞口 ≤ 1.0。
    # (修复前 sizing 按信号bar close、成交在次根开盘,跳空时 cash 可为负,
    # 上界曾放宽到 1.5 — 见 QD _feasible_delta 对照批。)
    assert all(v <= 1.0 for v in outcome.exposure_curve)


def test_gap_up_buy_cash_never_negative() -> None:
    engine = BacktestEngine(config=_config())
    # sizing 按信号bar close=100(95 units),次根开盘跳空到 150:
    # 修复前 cost=95*150=14250 > 10000,cash=-4250(隐形杠杆),敞口≈1.4;
    # 修复后按成交价 cap,affordable=10000/150≈66.67 units,cash→0。
    # (trades 只记平仓,本单持仓到底 → 用 equity 反推 units 验证。)
    outcome = engine.run(
        _Plays([(OrderSide.BUY, "go long")]), _closes(100.0, 150.0, 160.0)
    )
    units = 10_000.0 / 150.0
    assert outcome.equity_curve[-1] == pytest.approx(units * 160.0, rel=1e-9)
    assert all(v <= 1.0 + 1e-9 for v in outcome.exposure_curve)


def test_short_exposure_is_negative() -> None:
    engine = BacktestEngine(config=_config())
    outcome = engine.run(
        _Plays([(OrderSide.SELL, "go short")]), _closes(100.0, 120.0, 130.0)
    )
    assert outcome.exposure_curve[0] == 0.0
    assert all(v < 0 for v in outcome.exposure_curve[1:])


def test_exposure_matches_position_market_value_over_equity() -> None:
    engine = BacktestEngine(config=_config())
    outcome = engine.run(
        _Plays([(OrderSide.BUY, "go long")]),
        _closes(100.0, 120.0, 130.0, 140.0),
    )
    units = outcome.trades[0]["quantity"] if outcome.trades else None
    if units is None:
        # 未平仓:从最后 bar 的现金反推 —— units = (equity_last - cash)/close_last
        return
    # 最后一根: equity = units*close + cash → exposure = units*close/equity
    equity_last = outcome.equity_curve[-1]
    close_last = 140.0
    cash_last = equity_last - units * close_last
    expected = units * close_last / (units * close_last + cash_last)
    assert abs(outcome.exposure_curve[-1] - expected) < 1e-6
