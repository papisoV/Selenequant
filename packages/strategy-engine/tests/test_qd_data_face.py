"""QD adapter API-face gaps: current_dt, data.current / data.history, avg_cost.

These are the exact faces src7 (dual-TF breakout) and src8 (OU mean reversion)
read. Cutoff semantics mirror QD's ``_visible_cutoff`` under the backtest's
``include_current=False`` clock: the engine delivers bar T at its close, which
is QD's next iteration (clock = T + f(driving)), so a requested-frequency bar
is visible iff ``open <= clock - f(requested)``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from selene_space_backtest.commissions import PercentageCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import FixedSlippage
from selene_space_strategy.qd import QDStrategyRuntime, compile_qd_strategy
from selene_space_strategy.qd.describe import describe_qd_source
from selene_space_types.market import KlineEvent

_BASE_TS = 1_699_999_200_000  # 2023-11-14 22:00:00 UTC; hour/15m/5m bucket-aligned


def _g_state(runtime: QDStrategyRuntime) -> SimpleNamespace:
    """The strategy's ``g`` namespace, freshly re-created per run."""
    return runtime._compiled.module_globals["g"]  # type: ignore[return-value]


def _bars(interval_ms: int, closes: list[float]) -> list[KlineEvent]:
    events: list[KlineEvent] = []
    ts = _BASE_TS
    for close in closes:
        events.append(
            KlineEvent(
                exchange="okx",
                symbol="BTC/USDT",
                timestamp=ts,
                interval="5m" if interval_ms == 300_000 else "1h",
                open=close,
                high=close * 1.002,
                low=close * 0.998,
                close=close,
                volume=10.0,
                closed=True,
            )
        )
        ts += interval_ms
    return events


def _runtime(source: str) -> QDStrategyRuntime:
    return QDStrategyRuntime(
        compile_qd_strategy(source, source_name="probe"), symbol="BTC/USDT"
    )


_DT_SOURCE = """\
def initialize(context):
    context.subscribe(frequency="1h")
    context.set_warmup(1)
    g.dates = []

def handle_data(context, data):
    g.dates.append(str(context.current_dt)[:10])
"""

_CURRENT_SOURCE = """\
def initialize(context):
    context.subscribe(frequency="1h")
    context.set_warmup(2)
    g.px = []

def handle_data(context, data):
    g.px.append(data.current("Crypto:BTC/USDT@okx:swap", field="close"))
"""


def test_current_dt_is_bar_open_date() -> None:
    runtime = _runtime(_DT_SOURCE)
    engine = BacktestEngine(
        BacktestConfig(
            initial_cash=10_000.0,
            commission=PercentageCommission(rate=0.001),
            slippage=FixedSlippage(bps=5.0),
            position_sizing="signal_quantity",
            strategy_id="dt",
            symbol="BTC/USDT",
            interval="1h",
        )
    )
    engine.run(runtime, _bars(3_600_000, [100.0, 101.0, 102.0, 103.0]))
    # handle_data fires at the *next* bar's open (clock = bar_open + 1h):
    # bars open 22:00/23:00/00:00/01:00 -> clocks 23:00/00:00/01:00/02:00
    assert _g_state(runtime).dates == [
        "2023-11-14",
        "2023-11-15",
        "2023-11-15",
        "2023-11-15",
    ], _g_state(runtime).dates


def test_data_current_returns_last_closed_bar() -> None:
    runtime = _runtime(_CURRENT_SOURCE)
    engine = BacktestEngine(
        BacktestConfig(
            initial_cash=10_000.0,
            commission=PercentageCommission(rate=0.001),
            slippage=FixedSlippage(bps=5.0),
            position_sizing="signal_quantity",
            strategy_id="cur",
            symbol="BTC/USDT",
            interval="1h",
        )
    )
    engine.run(runtime, _bars(3_600_000, [100.0, 101.0, 102.0]))
    # warmup=2 -> handle_data at bars 2 and 3; current = the bar that just
    # closed (QD's include_current=False still shows it: cutoff = clock-1h
    # = the closed bar's open time, and searchsorted is right-inclusive)
    assert _g_state(runtime).px == [101.0, 102.0]


def test_data_history_resamples_with_closed_bar_cutoff() -> None:
    runtime = _runtime(_DT_SOURCE)
    # drive 5m bars; probe history(15m) from inside handle_data
    source = """\
def initialize(context):
    context.subscribe(frequency="5m")
    context.set_warmup(1)
    g.rows = []

def handle_data(context, data):
    h = data.history("Crypto:BTC/USDT@okx:swap", count=3, fields=["open", "high", "low", "close"], frequency="15m")
    g.rows.append(
        (len(h), [float(v) for v in h["close"]], [float(v) for v in h["open"]])
        if len(h) else (0, [], [])
    )
"""
    runtime = QDStrategyRuntime(
        compile_qd_strategy(source, source_name="resample"),
        symbol="BTC/USDT",
        interval="5m",
    )
    engine = BacktestEngine(
        BacktestConfig(
            initial_cash=10_000.0,
            commission=PercentageCommission(rate=0.001),
            slippage=FixedSlippage(bps=5.0),
            position_sizing="signal_quantity",
            strategy_id="resample",
            symbol="BTC/USDT",
            interval="5m",
        )
    )
    engine.run(runtime, _bars(300_000, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0]))

    rows = _g_state(runtime).rows
    # bar i closes at (i+1)*5m; clock = (i+1)*5m, cutoff = clock - 15m.
    # Buckets open at 0m/15m/30m; a bucket appears once cutoff >= its open:
    # i=0,1 (cutoff<0): 0 rows; i=2..4 (cutoff 0..10m): bucket 0 only;
    # i=5,6 (cutoff 15m,20m): buckets 0+1
    assert [r[0] for r in rows] == [0, 0, 1, 1, 1, 2, 2], rows
    # bucket closes: [1.0..3.0]=3, [4.0..6.0]=6; bucket opens: 1, 4
    assert rows[5][1] == [3.0, 6.0] and rows[5][2] == [1.0, 4.0]
    assert rows[2][1] == [3.0]


def test_data_history_rejects_finest_than_driving() -> None:
    runtime = _runtime(_DT_SOURCE)
    with pytest.raises(ValueError, match="finer than the driving"):
        runtime._data_history("BTC/USDT", count=3, frequency="5m")


def test_position_avg_cost_tracks_entry() -> None:
    source = """\
def initialize(context):
    context.subscribe(frequency="1h")
    context.set_warmup(2)
    g.costs = []

def handle_data(context, data):
    pos = get_position("Crypto:BTC/USDT@okx:swap")
    g.costs.append(pos.avg_cost)
    if len(g.costs) == 2:
        order_target_percent("Crypto:BTC/USDT@okx:swap", 0.5, position_side="long")
"""
    runtime = _runtime(source)
    engine = BacktestEngine(
        BacktestConfig(
            initial_cash=10_000.0,
            commission=PercentageCommission(rate=0.001),
            slippage=FixedSlippage(bps=0.0),
            position_sizing="signal_quantity",
            strategy_id="avgcost",
            symbol="BTC/USDT",
            interval="1h",
        )
    )
    engine.run(runtime, _bars(3_600_000, [100.0, 100.0, 100.0, 100.0]))
    costs = _g_state(runtime).costs
    assert costs[0] == 0.0  # flat -> 0.0 (sources gate on avg_cost > 0)
    # after the entry fills at the next bar open, avg_cost = entry price
    assert costs[2] == pytest.approx(100.0) or costs[3] == pytest.approx(100.0)


def test_describe_drives_on_fastest_subscribed_frequency() -> None:
    source = """\
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="15m", fields=["close"])
    context.subscribe(frequency="5m", fields=["close"])
    context.set_warmup(400)

def handle_data(context, data):
    pass
"""
    info = describe_qd_source(source, source_name="dual-tf")
    assert info.interval == "5m", info.interval


def test_current_dt_none_outside_bar() -> None:
    from selene_space_strategy.qd.context import QDContext, QDManifest, QDParams

    ctx = QDContext(QDManifest(), QDParams(), data_symbol="BTC/USDT")
    assert ctx.current_dt is None
    ctx = QDContext(
        QDManifest(), QDParams(), data_symbol="BTC/USDT", current_ts=1_700_000_000_000
    )
    assert ctx.current_dt == pd.Timestamp("2023-11-14 22:13:20")
