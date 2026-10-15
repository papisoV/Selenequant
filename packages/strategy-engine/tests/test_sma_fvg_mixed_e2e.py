"""E2E tests for sma_fvg_mixed.yaml — verify mixed approach compiles and runs."""

from __future__ import annotations

from pathlib import Path

from selene_space_strategy.compiler import compile
from selene_space_strategy.models import load_strategy
from selene_space_strategy.runtime import StrategyRuntime
from selene_space_types.market import KlineEvent


def _templates_dir() -> Path:
    return Path(__file__).parent.parent / "selene_space_strategy" / "templates"


def _kline(close, timestamp, interval="4h", symbol="BTC/USDT"):
    return KlineEvent(
        exchange="binance",
        symbol=symbol,
        timestamp=timestamp,
        interval=interval,
        open=close - 1,
        high=close + 1,
        low=close - 2,
        close=close,
        volume=100.0,
        closed=True,
    )


class TestSMAFVGMixedE2E:
    def test_mixed_yaml_compiles_and_runs(self) -> None:
        """Smoke test: sma_fvg_mixed.yaml compiles, runtime accepts klines without error."""
        path = _templates_dir() / "sma_fvg_mixed.yaml"
        config = load_strategy(path)
        compiled = compile(config)
        runtime = StrategyRuntime(compiled)
        for i in range(60):
            price = 100.0 + (i % 10) * 2
            runtime.on_kline(_kline(price, timestamp=i * 4 * 3600_000))
        assert runtime.bar_count == 60
