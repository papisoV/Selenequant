"""Golden regression lock for the canonical SMA(20,50) crossover run.

Deterministic synthetic BTC tape (seed=42) feeds an SMA(20,50) strategy and
the resulting report is diffed against ``fixtures/golden_sma_cross.json``.
Any code change that moves the golden output is a regression.

Intentional algorithm changes may refresh the artifact with:

    pytest tests/test_golden_sma_cross.py --regenerate-golden
"""

from __future__ import annotations

import json
import pathlib
import sys
from collections import deque

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).parent))

from fixtures.generate_data import generate_deterministic_btc_data

from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.reports import to_dict
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide

FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"
GOLDEN_FILE = FIXTURES_DIR / "golden_sma_cross.json"

SHORT_WINDOW = 20
LONG_WINDOW = 50


class SmaCrossStrategy:
    """Trade on short-window SMA crossing the long-window SMA."""

    def __init__(self, short_period: int = SHORT_WINDOW, long_period: int = LONG_WINDOW) -> None:
        self._short_period = short_period
        self._long_period = long_period
        self._window: deque[float] = deque(maxlen=long_period)
        self._last_short: float | None = None
        self._last_long: float | None = None

    def on_kline(self, event: KlineEvent) -> Signal | None:
        self._window.append(event.close)

        if len(self._window) < self._long_period:
            return None

        closes = list(self._window)
        fast = sum(closes[-self._short_period :]) / self._short_period
        slow = sum(closes) / self._long_period

        fired = None
        if self._last_short is not None and self._last_long is not None:
            was_above = self._last_short > self._last_long
            now_above = fast > slow

            if now_above and not was_above:
                fired = Signal(
                    strategy_id="sma_cross_20_50",
                    symbol=event.symbol,
                    side=OrderSide.BUY,
                    strength=1.0,
                    timestamp=event.timestamp,
                    reason=f"SMA({self._short_period}) crossed above SMA({self._long_period})",
                )
            elif not now_above and was_above:
                fired = Signal(
                    strategy_id="sma_cross_20_50",
                    symbol=event.symbol,
                    side=OrderSide.SELL,
                    strength=1.0,
                    timestamp=event.timestamp,
                    reason=f"SMA({self._short_period}) crossed below SMA({self._long_period})",
                )

        self._last_short = fast
        self._last_long = slow
        return fired

    def reset(self) -> None:
        self._window.clear()
        self._last_short = None
        self._last_long = None


def _canonical_run() -> dict:
    """Execute the reference backtest and strip non-deterministic fields."""
    df = generate_deterministic_btc_data(n_bars=200, seed=42)

    config = BacktestConfig(
        initial_cash=10_000.0,
        commission=NoCommission(),
        slippage=NoSlippage(),
        strategy_id="sma_cross_20_50",
    )
    result = BacktestEngine(config=config).run(
        strategy=SmaCrossStrategy(), data=df
    )

    report = to_dict(result)
    report.pop("id", None)
    report.pop("created_at", None)
    return report


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--regenerate-golden",
        action="store_true",
        default=False,
        help="Regenerate the golden file instead of comparing against it.",
    )


def _load_or_seed_golden(report: dict, request) -> dict | None:
    """Return the stored golden dict; seed the file when absent.

    Returns None in regenerate mode (comparison skipped).
    """
    if request.config.getoption("--regenerate-golden", default=False):
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        GOLDEN_FILE.write_text(json.dumps(report, indent=2, default=str))
        pytest.skip("Golden file regenerated.")

    if not GOLDEN_FILE.exists():
        FIXTURES_DIR.mkdir(parents=True, exist_ok=True)
        GOLDEN_FILE.write_text(json.dumps(report, indent=2, default=str))
        return report

    return json.loads(GOLDEN_FILE.read_text())


class TestGoldenSmaCross:
    def test_golden_file_matches(self, request: pytest.FixtureRequest) -> None:
        report = _canonical_run()
        golden = _load_or_seed_golden(report, request)
        if golden is None:
            return

        for key in golden["metrics"]:
            assert report["metrics"][key] == pytest.approx(golden["metrics"][key], abs=1e-6), (
                f"Metric '{key}' differs: got {report['metrics'][key]}, expected {golden['metrics'][key]}"
            )

        assert len(report["equity_curve"]) == len(golden["equity_curve"])

        for i, (actual, expected) in enumerate(
            zip(report["equity_curve"], golden["equity_curve"], strict=False)
        ):
            assert actual == pytest.approx(expected, abs=1e-4), (
                f"Equity curve point {i} differs: got {actual}, expected {expected}"
            )

        assert report["trades_count"] == golden["trades_count"]

    def test_deterministic_across_runs(self) -> None:
        first = _canonical_run()
        second = _canonical_run()

        assert first["metrics"] == second["metrics"]
        assert first["equity_curve"] == second["equity_curve"]
        assert first["trades_count"] == second["trades_count"]

    def test_has_trades(self) -> None:
        report = _canonical_run()
        assert report["trades_count"] > 0, "Expected at least one trade from SMA cross strategy"
