"""Source description + context tolerance for the QD adapter.

Covers the declaration-recovery path (:func:`describe_qd_source`) against real
backup exports when installed, the text fallback for sources whose
``initialize`` cannot run standalone, the runnable gate for multi-instrument
and non-crypto sources, and the two tolerances the wider backup library needs:
unknown ``set_*`` declarations are ignored and a single string ``fields``
argument reads as one column.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from selene_space_backtest.commissions import PercentageCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.slippage import FixedSlippage
from selene_space_strategy.qd import QDStrategyRuntime, compile_qd_strategy
from selene_space_strategy.qd.context import (
    QDContext,
    QDManifest,
    QDParams,
    normalize_instrument,
)
from selene_space_strategy.qd.describe import describe_qd_source, parse_venue
from selene_space_types.market import KlineEvent

_BACKUP = Path(r"<local-path-redacted>")
_SRC48 = _BACKUP / "src48_XLMUSDT_gk_vol_trail10_AlphaMaster.py"  # cleanroom:
_SRC1 = _BACKUP / "src1_WF_BTC-1h-MA.py"
_SRC41 = _BACKUP / "src41_P13_13Leg_EqualWeight_Portfolio_Buou.py"


def _context() -> QDContext:
    return QDContext(QDManifest(), QDParams(), data_symbol="XLM/USDT")


# -- US-101: tolerances the backup library depends on -----------------------


def test_unknown_set_declarations_are_ignored() -> None:
    """``set_benchmark`` and friends exist in QuantDinger but not here."""
    context = _context()
    assert context.set_benchmark("Crypto:XLM/USDT@okx:swap") is None
    assert context.set_slippage_bps(5) is None


def test_missing_non_declaration_attribute_still_raises() -> None:
    context = _context()
    with pytest.raises(AttributeError):
        context.begin_trading
    with pytest.raises(AttributeError):
        context.__deepcopy__  # dunder probes must not resolve to a no-op


def test_qd_context_tolerant_declarations() -> None:
    source = """\
def initialize(context):
    g.symbol = "Crypto:BTC/USDT@spot"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h", fields=["open", "close"])
    context.set_warmup(50)
    context.set_benchmark(g.symbol)
    context.set_risk_free_rate(0.02)

def handle_data(context, data):
    pass
"""
    info = describe_qd_source(source, source_name="wider")
    assert info.symbol == "BTC/USDT"
    assert info.warmup == 50


_STRING_FIELD_SOURCE = """\
def initialize(context):
    g.symbol = "Crypto:XLM/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(5)
    g.seen_columns = None

def handle_data(context, data):
    bars = get_history(5, "1h", "close", g.symbol)
    g.seen_columns = list(bars.columns)
    if len(bars) < 5:
        return
    rising = float(bars["close"].iloc[-1]) > float(bars["close"].iloc[0])
    order_target_percent(g.symbol, 0.5 if rising else 0.0, position_side="long",
                         reason="rise" if rising else "fall")
"""


def test_qd_get_history_string_field() -> None:
    """A bare ``"close"`` field must read one column, not five characters.

    The rise-then-fall series forces an open and a close, so the assertion
    covers both the column shape and that real prices reached the strategy
    (an all-NaN frame would never trigger an order).
    """
    compiled = compile_qd_strategy(_STRING_FIELD_SOURCE, source_name="strfield")
    runtime = QDStrategyRuntime(compiled, symbol="XLM/USDT")
    closes = [round(1.0 + i * 0.1, 6) for i in range(15)]
    closes += [round(closes[-1] - i * 0.1, 6) for i in range(1, 12)]
    bars = [
        KlineEvent(
            exchange="okx",
            symbol="XLM/USDT",
            timestamp=1_700_000_000_000 + i * 3_600_000,
            interval="1h",
            open=c,
            high=c,
            low=c,
            close=c,
            volume=10.0,
            closed=True,
        )
        for i, c in enumerate(closes)
    ]
    config = BacktestConfig(
        initial_cash=10_000.0,
        commission=PercentageCommission(rate=0.001),
        slippage=FixedSlippage(bps=5.0),
        position_sizing="signal_quantity",
        allow_pyramiding=True,
        strategy_id="qd:strfield",
        symbol="XLM/USDT",
        interval="1h",
    )
    result = BacktestEngine(config=config).run(runtime, bars)

    observed = getattr(compiled.module_globals["g"], "seen_columns")
    assert observed == ["close"], "string field was split into characters"
    assert (
        result.trades_count >= 1
    ), "no round trip -> prices never reached the strategy"


# -- US-102: describe --------------------------------------------------------


@pytest.mark.parametrize(
    ("instrument", "expected"),
    [
        ("Crypto:XLM/USDT@okx:swap", ("okx", "futures")),
        ("Crypto:BTC/USDT@swap", (None, "futures")),
        ("Crypto:BTC/USDT@spot", (None, "spot")),
        ("Crypto:SNDK/USDT@binance:swap", ("binance", "futures")),
        ("TSLA", (None, None)),
        ("", (None, None)),
    ],
)
def test_parse_venue(instrument: str, expected: tuple[str | None, str | None]) -> None:
    assert parse_venue(instrument) == expected


@pytest.mark.parametrize(
    ("instrument", "expected"),
    [
        ("Crypto:XLM/USDT@okx:swap", "XLM/USDT"),
        ("Crypto:BTC/USDT@spot", "BTC/USDT"),
        ("XLM/USDT", "XLM/USDT"),
        ("TSLA", "TSLA"),
    ],
)
def test_normalize_instrument(instrument: str, expected: str) -> None:
    assert normalize_instrument(instrument) == expected


def test_describe_multi_instrument_is_not_runnable() -> None:
    source = """\
def initialize(context):
    g.symbols = {"a": "Crypto:BTC/USDT@spot", "b": "Crypto:ETH/USDT@spot"}
    context.set_universe(list(g.symbols.values()))
    context.subscribe(frequency="1h", fields=["close"])
    context.set_warmup(30)

def handle_data(context, data):
    pass
"""
    info = describe_qd_source(source, source_name="multi")
    assert info.runnable is False
    assert "multi-instrument" in info.reason
    assert len(info.instruments) == 2


def test_describe_non_crypto_is_not_runnable() -> None:
    source = """\
def initialize(context):
    g.symbol = "TSLA"
    context.set_universe([g.symbol])
    context.set_warmup(20)

def handle_data(context, data):
    pass
"""
    info = describe_qd_source(source, source_name="equity")
    assert info.symbol == "TSLA"
    assert info.runnable is False
    assert info.market_type is None


def test_describe_falls_back_to_text_when_initialize_cannot_run() -> None:
    """``initialize`` needing per-bar API must not lose the declaration."""
    source = """\
def initialize(context):
    g.symbol = "Crypto:ADA/USDT@okx:swap"
    context.set_universe([g.symbol])
    context.subscribe(frequency="4h", fields=["close"])
    context.set_warmup(77)
    warmup_bars = get_history(10, "4h", "close")  # only bound during handle_data

def handle_data(context, data):
    pass
"""
    info = describe_qd_source(source, source_name="fallback")
    assert info.instrument == "Crypto:ADA/USDT@okx:swap"
    assert info.symbol == "ADA/USDT"
    assert info.interval == "4h"
    assert info.warmup == 77
    assert info.runnable is True


@pytest.mark.skipif(
    not _SRC48.exists(), reason="QuantDinger strategy backup not installed"
)
def test_describe_src48_real_export() -> None:
    info = describe_qd_source(_SRC48.read_text(encoding="utf-8"), source_name="src48")
    assert info.symbol == "XLM/USDT"
    assert info.exchange == "okx"
    assert info.market_type == "futures"
    assert info.interval == "1h"
    assert info.warmup == 320
    assert info.runnable is True
    assert info.direction_mode == "both"


@pytest.mark.skipif(
    not _SRC1.exists(), reason="QuantDinger strategy backup not installed"
)
def test_describe_src1_real_export() -> None:
    info = describe_qd_source(_SRC1.read_text(encoding="utf-8"), source_name="src1")
    assert info.symbol == "BTC/USDT"
    assert info.market_type == "spot"
    assert info.interval == "1h"
    assert info.warmup == 220
    assert info.runnable is True
    # source declares @param defaults -> surfaced for the request payload
    assert info.params["fast_period"] == 30.0
    assert info.params["slow_period"] == 60.0


@pytest.mark.skipif(
    not _SRC41.exists(), reason="QuantDinger strategy backup not installed"
)
def test_describe_src41_portfolio_not_runnable() -> None:
    """Portfolio sources declare no ``handle_data`` -- render, don't raise."""
    info = describe_qd_source(_SRC41.read_text(encoding="utf-8"), source_name="src41")
    assert info.runnable is False
    assert "handle_data" in info.reason
