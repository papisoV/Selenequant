"""P0-3 撮合语义包:限价缺口穿越保护 + 止损显式滑点 + STOP_LIMIT 两段式。

规格锚:internal-plan(规则表+验收数字)。
所有 bar 手工构造,数字直接来自规格第四节验收锚。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "shared-types"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "backtest-engine"))

import pytest

from selene_space_backtest.broker import SimulatedBroker
from selene_space_backtest.slippage import FixedSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.trading import Order, OrderSide, OrderType

_STAMP = 1711000000000


def _bar(
    open_price: float,
    high: float,
    low: float,
    close: float,
    volume: float = 1_000_000.0,
) -> KlineEvent:
    return KlineEvent(
        exchange="backtest",
        symbol="BTC/USDT",
        timestamp=_STAMP,
        interval="1h",
        open=open_price,
        high=high,
        low=low,
        close=close,
        volume=volume,
        closed=True,
    )


def _ticket(
    side: OrderSide,
    order_type: OrderType,
    quantity: float = 1.0,
    price: float | None = None,
    stop_price: float | None = None,
) -> Order:
    return Order(
        id="p03-ord",
        symbol="BTC/USDT",
        side=side,
        type=order_type,
        quantity=quantity,
        price=price,
        stop_price=stop_price,
        created_at=_STAMP,
        updated_at=_STAMP,
    )


class TestLimitGapThrough:
    """限价单:整根 bar 跳空穿越也必须成交(旧实现漏成交场景)。"""

    def test_sell_limit_gap_up_fills_at_open(self) -> None:
        # 规格验收锚 3:卖 limit 96,bar 整体高于 96 → 按开盘 110 成交。
        broker = SimulatedBroker()
        fill = broker.execute(
            _ticket(OrderSide.SELL, OrderType.LIMIT, price=96.0),
            _bar(open_price=110.0, high=112.0, low=108.0, close=111.0),
        )
        assert fill is not None
        assert float(fill.price) == pytest.approx(110.0)

    def test_buy_limit_gapped_below_open_fills_at_open(self) -> None:
        # 规格验收锚 4:买 limit 96,开盘 94 已优于限价 → 按开盘 94 成交。
        broker = SimulatedBroker()
        fill = broker.execute(
            _ticket(OrderSide.BUY, OrderType.LIMIT, price=96.0),
            _bar(open_price=94.0, high=99.0, low=93.0, close=98.0),
        )
        assert fill is not None
        assert float(fill.price) == pytest.approx(94.0)

    def test_buy_limit_touch_fills_at_limit(self) -> None:
        # 锚 1:正常触及按限价。
        broker = SimulatedBroker()
        fill = broker.execute(
            _ticket(OrderSide.BUY, OrderType.LIMIT, price=96.0),
            _bar(open_price=98.0, high=105.0, low=95.0, close=100.0),
        )
        assert fill is not None
        assert float(fill.price) == pytest.approx(96.0)

    def test_buy_limit_unreached_stays_out(self) -> None:
        # 锚 2:low 未及限价不成交。
        broker = SimulatedBroker()
        fill = broker.execute(
            _ticket(OrderSide.BUY, OrderType.LIMIT, price=90.0),
            _bar(open_price=98.0, high=105.0, low=95.0, close=100.0),
        )
        assert fill is None

    def test_limit_resting_survives_unlimited_bars_until_filled(self) -> None:
        # QD 同构:"Resting limits remain active until filled"(runtime.py:855-860)
        # —— 未成交 LIMIT 跨 bar 持续有效,不因轮数过期;引擎层 queued_orders
        # 每根重试,broker 层每根独立判定,连续 miss 后触及仍成交。
        broker = SimulatedBroker()
        ticket = _ticket(OrderSide.BUY, OrderType.LIMIT, price=90.0)
        miss_bar = _bar(open_price=98.0, high=105.0, low=95.0, close=100.0)
        for _ in range(10):
            assert broker.execute(ticket, miss_bar) is None
        hit_bar = _bar(open_price=95.0, high=96.0, low=89.0, close=92.0)
        fill = broker.execute(ticket, hit_bar)
        assert fill is not None
        assert float(fill.price) == pytest.approx(90.0)

    def test_limit_fill_carries_no_slippage_while_market_does(self) -> None:
        # QD 同构:限价是被动 maker 成交不加滑点(runtime.py:841-860 无滑点项);
        # 市价主动吃单买+卖−对称(L862-864)。同一滑点模型下两族成交价分野。
        slip = FixedSlippage(bps=100)
        limit_broker = SimulatedBroker(slippage=slip)
        limit_fill = limit_broker.execute(
            _ticket(OrderSide.BUY, OrderType.LIMIT, price=96.0),
            _bar(open_price=98.0, high=105.0, low=95.0, close=100.0),
        )
        assert limit_fill is not None
        assert float(limit_fill.price) == pytest.approx(96.0)

        market_broker = SimulatedBroker(slippage=slip)
        buy_fill = market_broker.execute(
            _ticket(OrderSide.BUY, OrderType.MARKET),
            _bar(open_price=100.0, high=101.0, low=99.0, close=100.5),
        )
        sell_fill = market_broker.execute(
            _ticket(OrderSide.SELL, OrderType.MARKET),
            _bar(open_price=100.0, high=101.0, low=99.0, close=100.5),
        )
        assert buy_fill is not None and sell_fill is not None
        assert float(buy_fill.price) == pytest.approx(100.0 * 1.01)
        assert float(sell_fill.price) == pytest.approx(100.0 * 0.99)


class TestStopMarketSemantics:
    """止损市价:触发判定 + 开盘钳制 + 显式滑点。"""

    def test_sell_stop_triggers_with_explicit_slippage(self) -> None:
        # 锚 5:normal trigger,min(stop,open)=97,FixedSlippage(bps=100)
        # 卖方向推劣 → 97 * (1 - 0.01) = 96.03。
        broker = SimulatedBroker(slippage=FixedSlippage(bps=100))
        fill = broker.execute(
            _ticket(OrderSide.SELL, OrderType.STOP_MARKET, stop_price=97.0),
            _bar(open_price=98.0, high=99.0, low=95.0, close=96.0),
        )
        assert fill is not None
        assert float(fill.price) == pytest.approx(96.03)

    def test_sell_stop_gap_down_takes_open_then_slip(self) -> None:
        # 锚 6:跳空穿越吃跳空价 min(stop=97, open=92)=92 再叠滑点。
        broker = SimulatedBroker(slippage=FixedSlippage(bps=100))
        fill = broker.execute(
            _ticket(OrderSide.SELL, OrderType.STOP_MARKET, stop_price=97.0),
            _bar(open_price=92.0, high=96.0, low=91.0, close=93.0),
        )
        assert fill is not None
        assert float(fill.price) == pytest.approx(92.0 * 0.99)

    def test_buy_stop_normal_trigger_at_stop(self) -> None:
        # 锚 7 前半:买止损正常触发,max(stop=101, open=98)=101,无滑点恰 101。
        broker = SimulatedBroker()
        fill = broker.execute(
            _ticket(OrderSide.BUY, OrderType.STOP_MARKET, stop_price=101.0),
            _bar(open_price=98.0, high=103.0, low=97.0, close=102.0),
        )
        assert fill is not None
        assert float(fill.price) == pytest.approx(101.0)

    def test_buy_stop_gap_up_takes_open(self) -> None:
        # 锚 7 后半:跳空向上 max(stop, open)=105(更差价)。
        broker = SimulatedBroker()
        fill = broker.execute(
            _ticket(OrderSide.BUY, OrderType.STOP_MARKET, stop_price=101.0),
            _bar(open_price=105.0, high=107.0, low=104.0, close=106.0),
        )
        assert fill is not None
        assert float(fill.price) == pytest.approx(105.0)

    def test_stop_not_triggered_when_range_misses(self) -> None:
        # 卖止损 stop=90,bar low=95 未触及 → 留队。
        broker = SimulatedBroker()
        fill = broker.execute(
            _ticket(OrderSide.SELL, OrderType.STOP_MARKET, stop_price=90.0),
            _bar(open_price=98.0, high=99.0, low=95.0, close=96.0),
        )
        assert fill is None


class TestStopLimitTwoPhase:
    """STOP_LIMIT 触发根只转 LIMIT,下一根才按限价撮合。"""

    def test_trigger_bar_does_not_fill_even_if_limit_in_range(self) -> None:
        # 锚 8 前半:触发根即便 price 区间也在场也不成交(保守两段式)。
        broker = SimulatedBroker()
        ticket = _ticket(
            OrderSide.SELL,
            OrderType.STOP_LIMIT,
            price=96.5,
            stop_price=97.0,
        )
        fill = broker.execute(
            ticket,
            _bar(open_price=98.0, high=99.0, low=95.0, close=96.0),
        )
        assert fill is None

    def test_post_trigger_bar_matches_as_plain_limit(self) -> None:
        # 锚 8 后半:转 LIMIT 后由引擎重排队;此处验证 broker 对已触发
        # 单据(引擎改写 type→LIMIT)按普通限价撮合且不加滑点。
        broker = SimulatedBroker(slippage=FixedSlippage(bps=100))
        ticket = _ticket(OrderSide.SELL, OrderType.LIMIT, price=96.5)
        fill = broker.execute(
            ticket,
            _bar(open_price=98.0, high=99.0, low=95.0, close=96.0),
        )
        assert fill is not None
        # maker 语义:LIMIT 不加滑点,恰为 max(limit, open)=98? 否——
        # open=98 高于 limit=96.5 属缺口穿越保护 → 按 open。但本用例
        # 考察"触及"路径,故取区间内触及 bar:limit 落在 [low,high] 且
        # open 高于 limit 时按 max 规则仍应得 open…… 规则自洽性由
        # TestLimitGapThrough 全覆盖;此处锁 maker 无滑点这一独立维度。
        touched_bar = _bar(open_price=96.4, high=97.0, low=95.0, close=96.8)
        fill2 = broker.execute(ticket, touched_bar)
        assert fill2 is not None
        assert float(fill2.price) == pytest.approx(96.5)


class TestIntentPassthroughEngine:
    """引擎契约扩展:TradeIntent 形状对象直译 typed 订单。"""

    @pytest.fixture
    def engine_result(self):
        from selene_space_backtest.engine import BacktestConfig, BacktestEngine

        bars = [
            _bar_ts(i, o, hi, lo, c)
            for i, (o, hi, lo, c) in enumerate(
                [
                    (100.0, 101.0, 99.0, 100.0),  # 信号根:发 STOP 平多意图
                    (99.5, 100.5, 98.0, 99.0),  # 首根:low 98 <= stop 98.5 → 触发
                    (99.0, 99.5, 98.5, 99.2),  # 收尾
                ]
            )
        ]
        events = []
        for i, b in enumerate(bars):
            b.timestamp = i * 3600000
            events.append(b)

        class IntentEmitter:
            config = None

            def on_kline(self, event):
                if event.timestamp == 0:
                    return _FakeIntent(
                        side="sell",
                        order_type="stop_market",
                        price=None,
                        stop_price=98.5,
                    )
                return None

            def reset(self) -> None: ...

        engine = BacktestEngine(
            BacktestConfig(
                position_sizing="signal_quantity",
                lookahead_guard=False,
                symbol="BTC/USDT",
            )
        )

        class Opener(IntentEmitter):
            opened = False

            def on_kline(self, event):
                if not self.opened:
                    self.opened = True
                    return _FakeIntent(side="buy", order_type="market")
                if event.timestamp == 3600000:
                    return _FakeIntent(
                        side="sell",
                        order_type="stop_market",
                        price=None,
                        stop_price=98.5,
                    )
                return None

        result = engine.run(Opener(), events)
        return result

    def test_stop_exit_produces_close_long_trade(self, engine_result) -> None:
        # 锚 10:STOP 平多全流程。引擎级 trade 记录用 side/exit_action
        # 词表(action 是模板翻译层字段)。
        closes = [
            t
            for t in engine_result.trades
            if t["side"] == "long" and t["exit_action"] == "sell"
        ]
        assert closes, "expected a long round trip from stop exit"

    def test_intent_without_price_for_limit_is_dropped(self) -> None:
        # LIMIT 意图缺 price → 静默丢弃,不炸。
        from selene_space_backtest.engine import BacktestConfig, BacktestEngine

        bars = [
            _bar_ts(0, 100.0, 101.0, 99.0, 100.0),
            _bar_ts(1, 100.0, 101.0, 99.0, 100.5),
        ]
        for i, b in enumerate(bars):
            b.timestamp = i * 3600000

        class BadLimiter:
            config = None

            def on_kline(self, event):
                return _FakeIntent(side="buy", order_type="limit", price=None)

            def reset(self) -> None: ...

        engine = BacktestEngine(
            BacktestConfig(position_sizing="signal_quantity", lookahead_guard=False)
        )
        result = engine.run(BadLimiter(), bars)
        assert result.trades_count == 0


def _bar_ts(idx, o, h, lo, c) -> KlineEvent:
    bar = _bar(open_price=o, high=h, low=lo, close=c)
    bar.timestamp = idx * 3600000
    return bar


class _FakeIntent:
    """TradeIntent 形状的鸭子对象(order_type 属性存在即走 typed 分派)。"""

    def __init__(self, side, order_type, price=None, stop_price=None):
        self.side = side
        self.order_type = order_type
        self.price = price
        self.quantity = 1.0
        self.risk_params = {"stop_price": stop_price} if stop_price else {}
        self.reasoning = ""
        self.confidence = 1.0
