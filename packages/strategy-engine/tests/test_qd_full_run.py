"""Engine-driven runs of the QD runtime adapter.

Covers: a small self-authored strategy cycling open/close through the real
next-bar-open engine, a two-call same-bar flip resolved through the runtime's
target reconciliation (close t, reopen t+1 — no deadband lockout), reset
hygiene across two runs, and an end-to-end run of the actual exported src48
strategy when the QuantDinger backup exists locally (skipped otherwise, so the
suite never depends on a machine-specific path).
"""

from __future__ import annotations

from pathlib import Path

import math
import pytest

from selene_space_backtest.commissions import PercentageCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import FixedSlippage
from selene_space_strategy.qd import QDStrategyRuntime, compile_qd_strategy
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import BacktestResult

_SRC48_PATH = Path(
    r"<local-path-redacted>"
)

_MINICYCLE_SOURCE = """\
# @param target_pct float 0.50
def initialize(context):
    g.symbol = "Crypto:XLM/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close", "volume"])
    context.set_warmup(10)
    context.set_metadata(direction_mode="both")
    context.allow_leverage(max_leverage=2)
    g.counter = 0

def handle_data(context, data):
    g.counter += 1
    bars = get_history(5, "1h", ["open", "high", "low", "close"], g.symbol)
    if len(bars) < 5:
        return
    long_pos = get_position(g.symbol, position_side="long")
    has = abs(float(long_pos.amount or 0.0)) > 0
    if not has and g.counter % 3 == 0:
        order_target_percent(g.symbol, 0.5, position_side="long", reason="enter")
    elif has and g.counter % 4 == 0:
        order_target_percent(g.symbol, 0.0, position_side="long", reason="exit")
"""

_FLIP_SOURCE = """\
def initialize(context):
    g.symbol = "Crypto:XLM/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(8)
    context.set_metadata(direction_mode="both")
    context.allow_leverage(max_leverage=3)
    g.stage = 0

def handle_data(context, data):
    bars = get_history(8, "1h", ["open", "high", "low", "close"], g.symbol)
    if len(bars) < 8:
        return
    g.stage += 1
    if g.stage == 1:
        order_target_percent(g.symbol, 0.8, position_side="long", reason="enter_long")
    elif g.stage == 2:
        order_target_percent(g.symbol, 0.0, position_side="long", reason="flip_close_long")
        order_target_percent(g.symbol, 0.8, position_side="short", reason="flip_open_short")
"""


def _config(**over: object) -> BacktestConfig:
    base: dict = dict(
        initial_cash=10_000.0,
        commission=PercentageCommission(rate=0.001),
        slippage=FixedSlippage(bps=5.0),
        position_sizing="signal_quantity",
        allow_pyramiding=True,
        strategy_id="qd-test",
        symbol="XLM/USDT",
        interval="1h",
    )
    base.update(over)
    return BacktestConfig(**base)


def _bars_from_closes(
    closes: list[float], base_ts: int = 1_700_000_000_000
) -> list[KlineEvent]:
    events: list[KlineEvent] = []
    ts = base_ts
    for close in closes:
        high = max(close, close) * 1.002
        low = min(close, close) * 0.998
        events.append(
            KlineEvent(
                exchange="okx",
                symbol="XLM/USDT",
                timestamp=ts,
                interval="1h",
                open=close,
                high=high,
                low=low,
                close=close,
                volume=1000.0,
                closed=True,
            )
        )
        ts += 3_600_000
    return events


def _run(
    runtime: QDStrategyRuntime, bars: list[KlineEvent], **over: object
) -> BacktestResult:
    engine = BacktestEngine(_config(**over))
    return engine.run(runtime, bars)


def _trend_and_slump_closes(
    n_quiet: int = 380, n_rise: int = 120, n_fall: int = 120
) -> list[float]:
    closes: list[float] = []
    for i in range(n_quiet):
        closes.append(round(1.0 + 0.01 * math.sin(i / 12.0), 6))
    price = 1.0
    for _ in range(n_rise):
        price *= 1.012
        closes.append(round(price, 6))
    for _ in range(n_fall):
        price *= 0.985
        closes.append(round(price, 6))
    return closes


def test_mini_cycle_opens_and_closes_through_engine() -> None:
    runtime = QDStrategyRuntime(
        compile_qd_strategy(_MINICYCLE_SOURCE, source_name="mini"), symbol="XLM/USDT"
    )
    result = _run(runtime, _bars_from_closes([100.0] * 120))
    assert result.trades_count >= 2
    assert result.metrics.total_trades == result.trades_count
    assert result.equity_curve  # curve was marked each bar
    entry = result.trades[0]
    # target_pct 0.5 on 10k cash at price 100 -> ~50 units
    assert 40 <= float(entry["quantity"]) <= 60


def test_flip_resolves_via_reconciliation_no_deadband_lockout() -> None:
    """Two-call same-bar close-long + open-short must land as a short.

    Engine delivers closes at the next open, so the runtime persists the
    -0.8 target and re-opens the short one bar later even though the
    strategy's own deadband guard went silent.
    """
    runtime = QDStrategyRuntime(
        compile_qd_strategy(_FLIP_SOURCE, source_name="flip"), symbol="XLM/USDT"
    )
    result = _run(runtime, _bars_from_closes([100.0] * 40))
    # long entered then closed -> exactly one completed round trip
    assert result.trades_count == 1
    assert (
        result.trades[0]["side"] == "long" and result.trades[0]["exit_action"] == "sell"
    )
    final_qty = runtime._require_portfolio().get_position_quantity("XLM/USDT")
    assert final_qty < -60, f"expected a reopened short, got {final_qty}"


def test_reset_clean_gives_identical_second_run() -> None:
    compiled = compile_qd_strategy(_MINICYCLE_SOURCE, source_name="mini")
    runtime = QDStrategyRuntime(compiled, symbol="XLM/USDT")
    bars = _bars_from_closes([100.0] * 120)
    first = _run(runtime, bars)
    runtime.reset()
    second = _run(runtime, bars)
    assert first.trades_count == second.trades_count
    assert first.metrics.total_return == second.metrics.total_return


_STOP_SOURCE = """\
# @param stop_pct float 0.10
def initialize(context):
    g.symbol = "Crypto:XLM/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h", fields=["open", "high", "low", "close"])
    context.set_warmup(4)
    context.set_metadata(direction_mode="long_only")
    g.entered = False

def handle_data(context, data):
    if g.entered:
        return
    g.entered = True
    order_target_percent(
        g.symbol,
        0.5,
        position_side="long",
        reason="enter",
        stop_loss_pct=context.params.get("stop_pct", 0.10),
    )
"""


def _run_stop_arm(armed: bool) -> tuple[int, float]:
    """Enter long at 100, then crash to 89 — with/without the order's stop."""
    source = (
        _STOP_SOURCE
        if armed
        else _STOP_SOURCE.replace(
            'stop_loss_pct=context.params.get("stop_pct", 0.10),', "stop_loss_pct=0.0,"
        )
    )
    runtime = QDStrategyRuntime(
        compile_qd_strategy(source, source_name="stop-probe"), symbol="XLM/USDT"
    )
    result = _run(runtime, _bars_from_closes([100.0] * 6 + [89.0] * 8))
    return result.trades_count, runtime._require_portfolio().get_position_quantity(
        "XLM/USDT"
    )


def test_order_stop_loss_flattens_and_control_does_not() -> None:
    """A 10% stop must close the long; the same bars without it must not.

    Entry lands at 100, the crash to 89 trips the 90 trigger on the bar's
    low. The unarmed run is the control: identical bars, only the bracket
    differs, so the close is attributable to the stop and nothing else.
    """
    armed_trades, armed_qty = _run_stop_arm(armed=True)
    bare_trades, bare_qty = _run_stop_arm(armed=False)
    assert armed_trades == 1, "stop should close the position exactly once"
    assert abs(armed_qty) < 1e-6, f"stop must leave the book flat, got {armed_qty}"
    assert bare_trades == 0, "without the bracket the long stays open"
    assert bare_qty > 0, f"control should still hold the long, got {bare_qty}"


@pytest.mark.skipif(
    not _SRC48_PATH.exists(), reason="QuantDinger strategy backup not installed"
)
def test_src48_end_to_end_synthetic() -> None:
    source = _SRC48_PATH.read_text(encoding="utf-8")
    compiled = compile_qd_strategy(source, source_name="src48")
    runtime = QDStrategyRuntime(compiled, symbol="XLM/USDT", interval="1h")
    closes = _trend_and_slump_closes()
    result = _run(runtime, _bars_from_closes(closes))
    assert result.trades_count >= 1
    assert result.metrics.annual_return is not None
    # selene/QD convention: drawdown is negative (e.g. -0.15 = -15%)
    assert -1.0 <= result.metrics.max_drawdown <= 0.0
