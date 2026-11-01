"""Regression lock: trade blotter must survive plain json.dumps.

The trade dicts are persisted via BacktestRepository.save → json.dumps with
no default= hook. H19 migrated Order price/quantity to Decimal; if a Decimal
leaks into _trade_record the save silently fails (swallowed by the API's
try/except) and the backtests row disappears.
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections import deque

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from fixtures.generate_data import generate_deterministic_btc_data

from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide


class BuyHoldAlternating:
    """Enter long on bar N, exit on N+3, repeat — guarantees closed trades."""

    def __init__(self) -> None:
        self._window: deque[float] = deque(maxlen=4)
        self._count = 0

    def on_kline(self, event: KlineEvent) -> Signal | None:
        self._window.append(event.close)
        self._count += 1
        if len(self._window) < 4:
            return None
        if self._count % 3 == 0:
            side = OrderSide.BUY if (self._count // 3) % 2 == 1 else OrderSide.SELL
            return Signal(
                strategy_id="alternating",
                symbol=event.symbol,
                side=side,
                strength=1.0,
                timestamp=event.timestamp,
                reason="test",
            )
        return None

    def reset(self) -> None:
        self._window.clear()
        self._count = 0


class TestTradeBlotterJsonSafe:
    def test_trades_json_serializable(self) -> None:
        df = generate_deterministic_btc_data(n_bars=300, seed=42)
        config = BacktestConfig(
            initial_cash=10_000.0,
            commission=NoCommission(),
            strategy_id="alternating",
        )
        result = BacktestEngine(config=config).run(BuyHoldAlternating(), df)

        assert result.trades_count > 0, "fixture must produce closed trades"
        # The exact call path BacktestRepository.save uses:
        payload = json.dumps(result.trades, ensure_ascii=False)
        assert len(payload) > 0

    def test_trade_price_fields_are_float(self) -> None:
        df = generate_deterministic_btc_data(n_bars=300, seed=42)
        config = BacktestConfig(
            initial_cash=10_000.0,
            commission=NoCommission(),
            strategy_id="alternating",
        )
        result = BacktestEngine(config=config).run(BuyHoldAlternating(), df)

        for trade in result.trades:
            assert isinstance(trade["entry_price"], float), trade
            assert isinstance(trade["exit_price"], float), trade
            assert isinstance(trade["quantity"], float), trade
