"""Tests for BorrowFeeModel protocol and BacktestConfig.borrow_fee_model wiring.

Verifies:
- Default None → borrow_fee_paid stays 0 (byte-identical to pre-borrow-fee runs).
- NoBorrowFee() explicit → same as None.
- Real model on short position → accrues correctly.
- Real model on long position → no accrual (borrow fee is short-only).
"""

from __future__ import annotations


from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.funding import NoBorrowFee
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


class _FixedBorrowFee:
    """Test stub: returns a fixed fraction of notional each call.

    Real borrow fee models would scale by holding period (dt/year), but for
    testing the engine wiring we use a flat rate so the assertion is exact.
    """

    def __init__(self, rate: float) -> None:
        self._rate = rate

    def apply_borrow_fee(self, position_notional: float, timestamp: int) -> float:
        return position_notional * self._rate


def _kline(symbol: str, close: float, ts: int) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol=symbol,
        timestamp=ts,
        interval="1d",
        open=close,
        high=close,
        low=close,
        close=close,
        volume=1.0,
        closed=True,
    )


def _make_klines(symbol: str, prices: list[float], base_ts: int = 1_700_000_000_000) -> list[KlineEvent]:
    return [_kline(symbol, p, base_ts + i * 86_400_000) for i, p in enumerate(prices)]


class _AlwaysSellStrategy:
    """Sells on first bar (opens short), then holds."""

    def __init__(self) -> None:
        self._bar_count = 0

    def on_kline(self, event: KlineEvent) -> Signal | None:
        self._bar_count += 1
        if self._bar_count == 1:
            return Signal(
                strategy_id="test",
                symbol=event.symbol,
                side=OrderSide.SELL,
                strength=1.0,
                timestamp=event.timestamp,
                reason="open short",
            )
        return None

    def reset(self) -> None:
        self._bar_count = 0


class _AlwaysBuyStrategy:
    """Buys on first bar (opens long), then holds."""

    def __init__(self) -> None:
        self._bar_count = 0

    def on_kline(self, event: KlineEvent) -> Signal | None:
        self._bar_count += 1
        if self._bar_count == 1:
            return Signal(
                strategy_id="test",
                symbol=event.symbol,
                side=OrderSide.BUY,
                strength=1.0,
                timestamp=event.timestamp,
                reason="open long",
            )
        return None

    def reset(self) -> None:
        self._bar_count = 0


class _NoOpStrategy:
    """Never emits a signal."""

    def on_kline(self, event: KlineEvent) -> Signal | None:
        return None

    def reset(self) -> None:
        pass


def test_borrow_fee_default_none_unchanged() -> None:
    """No borrow_fee_model → metrics.borrow_fee_paid == 0.0."""
    config = BacktestConfig(initial_cash=10_000.0)
    assert config.borrow_fee_model is None

    klines = _make_klines("TEST/USDT", [100.0, 100.0, 100.0])
    engine = BacktestEngine(config)
    result = engine.run(_NoOpStrategy(), klines)

    assert result.metrics.borrow_fee_paid == 0.0


def test_no_borrow_fee_explicit_zero() -> None:
    """NoBorrowFee() explicit → same as None (borrow_fee_paid == 0.0)."""
    config = BacktestConfig(initial_cash=10_000.0, borrow_fee_model=NoBorrowFee())

    klines = _make_klines("TEST/USDT", [100.0, 100.0, 100.0])
    engine = BacktestEngine(config)
    result = engine.run(_NoOpStrategy(), klines)

    assert result.metrics.borrow_fee_paid == 0.0


def test_borrow_fee_zero_on_long_position() -> None:
    """Long position + borrow_fee_model → no accrual (short-only).

    Even with a 100% borrow fee, long positions should accrue 0.
    """
    config = BacktestConfig(
        initial_cash=10_000.0,
        borrow_fee_model=_FixedBorrowFee(rate=1.0),
    )

    klines = _make_klines("TEST/USDT", [100.0, 100.0, 100.0, 100.0])
    engine = BacktestEngine(config)
    result = engine.run(_AlwaysBuyStrategy(), klines)

    # Long position → borrow_fee_paid must be 0 (borrow fees only on shorts).
    assert result.metrics.borrow_fee_paid == 0.0


def test_borrow_fee_charged_on_short_position() -> None:
    """Short position + borrow_fee_model → accrues correctly (> 0)."""
    config = BacktestConfig(
        initial_cash=10_000.0,
        borrow_fee_model=_FixedBorrowFee(rate=0.01),
    )

    klines = _make_klines("TEST/USDT", [100.0, 100.0, 100.0, 100.0])
    engine = BacktestEngine(config)
    result = engine.run(_AlwaysSellStrategy(), klines)

    # Short position with non-zero borrow fee → accrual must be > 0.
    assert result.metrics.borrow_fee_paid > 0.0


def test_borrow_fee_model_field_defaults_none() -> None:
    """BacktestConfig.borrow_fee_model defaults to None."""
    config = BacktestConfig()
    assert config.borrow_fee_model is None
