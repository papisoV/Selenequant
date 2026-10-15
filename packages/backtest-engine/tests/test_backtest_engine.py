"""End-to-end BacktestEngine runs driven by scripted one-shot strategies."""

from __future__ import annotations

import pytest

from selene_space_backtest.commissions import NoCommission, PercentageCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine, BacktestError
from selene_space_backtest.slippage import FixedSlippage, NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


class ScriptedPlays:
    """Emits one queued (side, reason) per processed bar, then goes quiet."""

    def __init__(self, plays: list[tuple[OrderSide, str]], actor: str = "scripted") -> None:
        self._queue = list(plays)
        self._cursor = 0
        self._actor = actor

    def on_kline(self, event: KlineEvent) -> Signal | None:
        if self._cursor >= len(self._queue):
            return None
        side, why = self._queue[self._cursor]
        self._cursor += 1
        return Signal(
            strategy_id=self._actor,
            symbol=event.symbol,
            side=side,
            strength=1.0,
            timestamp=event.timestamp,
            reason=why,
        )

    def reset(self) -> None:
        self._cursor = 0


LONG_SWING = [(OrderSide.BUY, "open long"), (OrderSide.SELL, "close long")]
SHORT_SWING = [(OrderSide.SELL, "open short"), (OrderSide.BUY, "close short")]


def _frictionless_setup(**overrides) -> BacktestConfig:
    settings: dict = {
        "initial_cash": 10_000.0,
        "commission": NoCommission(),
        "slippage": NoSlippage(),
    }
    settings.update(overrides)
    return BacktestConfig(**settings)


def _hourly_closes(*prices: float) -> list[KlineEvent]:
    session_origin = 1711000000000
    bar_ms = 3_600_000
    tape = []
    for slot, px in enumerate(prices):
        tape.append(
            KlineEvent(
                exchange="backtest",
                symbol="BTC/USDT",
                timestamp=session_origin + slot * bar_ms,
                interval="1h",
                open=px,
                high=px * 1.01,
                low=px * 0.99,
                close=px,
                volume=100.0,
                closed=True,
            )
        )
    return tape


class TestEngineRuns:
    def test_missing_history_is_rejected(self) -> None:
        engine = BacktestEngine()
        with pytest.raises(BacktestError, match="No kline data"):
            engine.run(ScriptedPlays(LONG_SWING), [])

    def test_round_trip_on_rising_tape(self) -> None:
        engine = BacktestEngine(config=_frictionless_setup())
        # next-bar-open model: bar0 BUY fills at bar1 open=120, bar1 SELL fills
        # at bar2 open=130
        outcome = engine.run(ScriptedPlays(LONG_SWING), _hourly_closes(100.0, 120.0, 130.0))

        assert outcome.trades_count == 1
        assert outcome.metrics.total_trades == 1
        assert len(outcome.equity_curve) == 3
        assert outcome.metrics.total_return > 0

    def test_friction_still_completes_the_trade(self) -> None:
        engine = BacktestEngine(
            config=_frictionless_setup(
                commission=PercentageCommission(rate=0.001),
                slippage=FixedSlippage(bps=5),
            )
        )
        outcome = engine.run(ScriptedPlays(LONG_SWING), _hourly_closes(100.0, 110.0, 105.0))

        assert outcome.trades_count == 1

    def test_artifact_metadata_shape(self) -> None:
        engine = BacktestEngine()
        outcome = engine.run(ScriptedPlays(LONG_SWING), _hourly_closes(100.0, 105.0, 110.0))

        assert outcome.id.startswith("bt-")
        assert outcome.strategy_id == "backtest"
        assert outcome.start_date is not None
        assert outcome.end_date is not None
        assert outcome.created_at > 0

    def test_defaults_inherit_kline_context(self) -> None:
        engine = BacktestEngine()
        outcome = engine.run(ScriptedPlays(LONG_SWING), _hourly_closes(100.0, 105.0, 110.0))

        assert outcome.symbol == "BTC/USDT"
        assert outcome.interval == "1h"

    def test_config_can_relabel_market(self) -> None:
        engine = BacktestEngine(config=BacktestConfig(symbol="ETH/USDT", interval="4h"))
        outcome = engine.run(ScriptedPlays(LONG_SWING), _hourly_closes(100.0, 105.0, 110.0))

        assert outcome.symbol == "ETH/USDT"
        assert outcome.interval == "4h"


class TestShortSide:
    def test_short_prospers_when_price_falls(self) -> None:
        engine = BacktestEngine(config=_frictionless_setup())
        # next-bar-open: bar0 SELL fills at bar1 open=80, bar1 BUY covers at
        # bar2 open=70
        outcome = engine.run(ScriptedPlays(SHORT_SWING), _hourly_closes(100.0, 80.0, 70.0))

        assert outcome.trades_count == 1
        assert outcome.metrics.total_return > 0

    def test_short_hurts_when_price_climbs(self) -> None:
        engine = BacktestEngine(config=_frictionless_setup())
        # next-bar-open: bar0 SELL fills at bar1 open=120, cover at bar2 open=130
        outcome = engine.run(ScriptedPlays(SHORT_SWING), _hourly_closes(100.0, 120.0, 130.0))

        assert outcome.trades_count == 1
        assert outcome.metrics.total_return < 0

    def test_long_path_unchanged_for_existing_users(self) -> None:
        engine = BacktestEngine(config=_frictionless_setup())
        outcome = engine.run(ScriptedPlays(LONG_SWING), _hourly_closes(100.0, 120.0, 130.0))

        assert outcome.trades_count == 1
        assert outcome.metrics.total_return > 0
