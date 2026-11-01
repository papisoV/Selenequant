"""P0-2: single canonical RealisticSlippage shared by backtest, paper, shadow.

The historical duplication (backtest-engine copy vs paper-engine copy held
in lockstep by an alignment test) is gone: ``selene_space_paper.fills``
re-exports the model from ``selene_space_backtest.slippage``, so fill-price
alignment across engines holds by construction. These tests pin the
canonical behavior itself:

- Degraded path: when volume info is absent, falls back to base 3bp.
- Volume-aware path: participation-rate impact with exact expected prices.
- Cap: extreme participation clamps at ``MAX_BPS``.
- Default config: BacktestConfig.slippage is RealisticSlippage.
- Re-export identity: paper-side names are the same objects.
- Broker integration: SimulatedBroker threads bar volume through;
  simple models ignore the context.
"""

from __future__ import annotations

import math

from selene_space_backtest import BacktestConfig, RealisticSlippage, SimulatedBroker
from selene_space_backtest.slippage import FixedSlippage, NoSlippage, VolumeSlippage
from selene_space_paper.fills import (
    FixedPaperSlippage,
    PaperSlippageModel,
    RealisticSlippage as PaperRealisticSlippage,
    try_fill,
)
from selene_space_types.market import KlineEvent
from selene_space_types.trading import Order, OrderSide, OrderType

_TS = 1_711_000_000_000


def _make_order(
    side: OrderSide = OrderSide.BUY,
    order_type: OrderType = OrderType.MARKET,
    quantity: float = 1.0,
    price: float | None = None,
) -> Order:
    return Order(
        id="test-ord-001",
        symbol="BTC/USDT",
        side=side,
        type=order_type,
        quantity=quantity,
        price=price,
        created_at=_TS,
        updated_at=_TS,
    )


def _make_kline(
    *,
    open_price: float = 50_000.0,
    high: float = 50_100.0,
    low: float = 49_900.0,
    close: float = 50_050.0,
    volume: float = 100.0,
) -> KlineEvent:
    return KlineEvent(
        exchange="binance",
        symbol="BTC/USDT",
        timestamp=_TS,
        interval="1h",
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        closed=True,
    )


# ---------------------------------------------------------------------------
# Single-implementation guarantees
# ---------------------------------------------------------------------------


def test_paper_side_names_are_reexports() -> None:
    """Paper/shadow-facing slippage names resolve to the canonical classes."""
    assert PaperRealisticSlippage is RealisticSlippage
    assert PaperSlippageModel.__name__ == "SlippageModel"
    assert FixedPaperSlippage.__name__ == "FixedSlippage"


def test_default_config_uses_realistic_slippage() -> None:
    """BacktestConfig.slippage default is RealisticSlippage (not FixedSlippage 1bp)."""
    cfg = BacktestConfig()
    assert isinstance(cfg.slippage, RealisticSlippage)


# ---------------------------------------------------------------------------
# Canonical model behavior
# ---------------------------------------------------------------------------


def test_degraded_path_base_only_when_volume_absent() -> None:
    """Missing volume/notional degrades to flat base 3bp."""
    model = RealisticSlippage()

    for price in (100.0, 50_000.0, 0.5):
        for side in (OrderSide.BUY, OrderSide.SELL):
            expected = (
                price * (1.0 + 3.0 / 10_000.0)
                if side == OrderSide.BUY
                else price * (1.0 - 3.0 / 10_000.0)
            )
            got = model.apply(price, side, order_notional=5_000.0, avg_24h_volume=0.0)
            assert math.isclose(got, expected, rel_tol=1e-12)

            got = model.apply(
                price, side, order_notional=0.0, avg_24h_volume=1_000_000.0
            )
            assert math.isclose(got, expected, rel_tol=1e-12)


def test_volume_aware_path_exact_prices() -> None:
    """Participation term adds impact_factor × notional/volume to the base."""
    model = RealisticSlippage(base_bps=3.0, volume_impact_factor=0.1)

    # 0.5% participation: fraction = 3bp + 0.1 * 5000/1_000_000
    fraction = 3.0 / 10_000.0 + 0.1 * (5_000.0 / 1_000_000.0)
    assert math.isclose(
        model.apply(
            50_000.0, OrderSide.BUY, order_notional=5_000.0, avg_24h_volume=1_000_000.0
        ),
        50_000.0 * (1.0 + fraction),
        rel_tol=1e-12,
    )
    assert math.isclose(
        model.apply(
            50_000.0, OrderSide.SELL, order_notional=5_000.0, avg_24h_volume=1_000_000.0
        ),
        50_000.0 * (1.0 - fraction),
        rel_tol=1e-12,
    )


def test_high_participation_hits_cap() -> None:
    """Extreme participation clamps total slippage at MAX_BPS (200bp = 2%)."""
    model = RealisticSlippage()
    capped = model.apply(
        100.0, OrderSide.BUY, order_notional=10_000.0, avg_24h_volume=1_000.0
    )
    assert math.isclose(capped, 102.0, rel_tol=1e-12)


def test_custom_params_flow_through() -> None:
    """base_bps / volume_impact_factor overrides behave consistently."""
    model = RealisticSlippage(base_bps=5.0, volume_impact_factor=0.2)
    fraction = 5.0 / 10_000.0 + 0.2 * (1_000.0 / 100_000.0)
    assert math.isclose(
        model.apply(
            100.0, OrderSide.BUY, order_notional=1_000.0, avg_24h_volume=100_000.0
        ),
        100.0 * (1.0 + fraction),
        rel_tol=1e-12,
    )


def test_invalid_params_rejected() -> None:
    try:
        RealisticSlippage(base_bps=-1.0)
    except ValueError:
        pass
    else:
        raise AssertionError("negative base_bps must raise")


# ---------------------------------------------------------------------------
# Broker integration
# ---------------------------------------------------------------------------


def test_broker_threads_volume_into_realistic_slippage() -> None:
    """SimulatedBroker passes bar-volume context so fills carry the impact term.

    Small bar (volume=100 BTC × close 50050 ≈ 5M USDT), order of 0.5 BTC
    (~25_000 USDT) → participation ~0.5%, visible above base 3bp alone.
    """
    broker = SimulatedBroker(
        slippage=RealisticSlippage(base_bps=3.0, volume_impact_factor=0.1)
    )

    kline = _make_kline(open_price=50_000.0, close=50_050.0, volume=100.0)
    order = _make_order(side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=0.5)

    fill = broker.execute(order, kline)
    assert fill is not None

    # order_notional = 0.5 * 50_000 = 25_000
    # avg_24h_volume = 100 * 50_050 = 5_005_000
    # participation = 25_000 / 5_005_000 ≈ 0.004995
    # slip = 3bp + 0.1 * participation ≈ 0.0007995
    # fill_price = 50_000 * (1 + 0.0007995) ≈ 50_039.975
    expected_participation = (0.5 * 50_000.0) / (100.0 * 50_050.0)
    expected_slip = 3.0 / 10_000.0 + 0.1 * expected_participation
    expected_price = 50_000.0 * (1.0 + expected_slip)

    assert expected_slip > 3.0 / 10_000.0 + 1e-6
    assert math.isclose(
        float(fill.price), expected_price, rel_tol=1e-9
    ), f"fill.price={fill.price} expected={expected_price}"


def test_thin_bar_zeroes_context_instead_of_absurd_fills() -> None:
    """Bar notional below order notional → context zeroed → base 3bp fallback."""
    broker = SimulatedBroker(slippage=RealisticSlippage())

    # Bar quote notional = 1 * 100 = 100 << order notional = 10 * 100 = 1000.
    kline = _make_kline(open_price=100.0, close=100.0, volume=1.0)
    order = _make_order(side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=10.0)

    fill = broker.execute(order, kline)
    assert fill is not None
    expected = 100.0 * (1.0 + 3.0 / 10_000.0)
    assert math.isclose(float(fill.price), expected, rel_tol=1e-9)


def test_broker_fixed_slippage_ignores_volume_context() -> None:
    """FixedSlippage ignores volume context → flat bps regardless of bar size."""
    broker = SimulatedBroker(slippage=FixedSlippage(bps=10))
    kline = _make_kline(
        open_price=1000.0, high=1010.0, low=990.0, close=1005.0, volume=1000.0
    )
    order = _make_order(side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=1.0)

    fill = broker.execute(order, kline)
    assert fill is not None
    assert math.isclose(float(fill.price), 1001.0, rel_tol=1e-9)


def test_broker_volume_slippage_still_gets_context() -> None:
    """VolumeSlippage receives volume context through the unified apply path."""
    broker = SimulatedBroker(
        slippage=VolumeSlippage(volume_impact_factor=0.1, default_bps=5.0)
    )
    kline = _make_kline(
        open_price=100.0, high=101.0, low=99.0, close=100.0, volume=1_000_000.0
    )
    order = _make_order(side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=100.0)

    fill = broker.execute(order, kline)
    assert fill is not None
    # volume_ratio = (100*100) / (1M*100) = 0.0001
    # slippage_bps = min(0.1 * 0.0001 * 10000, 100) = 0.1 bps
    # fill = 100 * 1.00001 = 100.001
    assert math.isclose(float(fill.price), 100.001, rel_tol=1e-9)


def test_broker_no_slippage_unchanged() -> None:
    """NoSlippage ignores context → fill at raw price."""
    broker = SimulatedBroker(slippage=NoSlippage())
    kline = _make_kline(
        open_price=1234.5, high=1240.0, low=1230.0, close=1235.0, volume=500.0
    )
    order = _make_order(side=OrderSide.BUY, order_type=OrderType.MARKET, quantity=0.1)

    fill = broker.execute(order, kline)
    assert fill is not None
    assert math.isclose(float(fill.price), 1234.5, rel_tol=1e-9)


# ---------------------------------------------------------------------------
# Paper pipeline consumes the canonical model
# ---------------------------------------------------------------------------


def test_try_fill_accepts_canonical_realistic_slippage() -> None:
    """try_fill drives the canonical model; fill price carries the impact term."""
    order = Order(
        id="pf-test-001",
        symbol="BTC-USDT-SWAP",
        side=OrderSide.BUY,
        type=OrderType.MARKET,
        status=__import__(
            "selene_space_types.trading", fromlist=["OrderStatus"]
        ).OrderStatus.ACCEPTED,
        quantity=5_000.0,
        price=None,
        created_at=_TS,
        updated_at=_TS,
    )
    fill = try_fill(
        order,
        50_000.0,
        latency_range_ms=None,
        slippage_model=RealisticSlippage(),
        avg_24h_volume=1_000_000.0,
    )
    assert fill is not None
    fraction = 3.0 / 10_000.0 + 0.1 * (5_000.0 / 1_000_000.0)
    expected = 50_000.0 * (1.0 + fraction)
    assert math.isclose(float(fill.price), expected, rel_tol=1e-9)
