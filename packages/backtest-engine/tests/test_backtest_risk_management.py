"""P1-5 回测内风控 = targets 重写器（LEAN 专题⑥吸收）。

规格锚:internal-plan(验收锚 1-9)。
核心:risk 产出清仓 target 走正常执行管道(进 trades/会计)、cooldown 防重建仓、
TrailingStop 峰值状态字典(多空分开、换仓重置)。

时序约定(引擎 next-bar-open 撮合):信号在 bar N 发出 → 订单在 bar N+1 的
open 成交;风控 target 在 bar N 末尾产出 → 清算单在 bar N+1 open 成交。
trade 记录范式:引擎只记完整往返,开多→平多 = 1 条 trade(side/entry_action/
exit_action 区分方向)。所有 tape 因此比"理想触发点"多留一根成交 bar。
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "shared-types"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "backtest-engine"))

from selene_space_backtest.commissions import NoCommission
from selene_space_backtest.engine import BacktestConfig, BacktestEngine
from selene_space_backtest.risk_management import (
    MaxDrawdownRiskManager,
    MaxUnrealizedProfitRiskManager,
    TrailingStopRiskManager,
)
from selene_space_backtest.slippage import NoSlippage
from selene_space_types.market import KlineEvent
from selene_space_types.strategy import Signal
from selene_space_types.trading import OrderSide

_SYMBOL = "BTC/USDT"
_SESSION = 1711000000000
_BAR_MS = 3_600_000


def _bar(slot: int, close: float) -> KlineEvent:
    return KlineEvent(
        exchange="backtest",
        symbol=_SYMBOL,
        timestamp=_SESSION + slot * _BAR_MS,
        interval="1h",
        open=close,
        high=close * 1.01,
        low=close * 0.99,
        close=close,
        volume=100_000.0,
        closed=True,
    )


class Scripted:
    """按槽位发开仓/平仓信号,每槽最多一个(带 quantity,满足 signal_quantity)。

    plays = [(slot, side), ...]。信号在槽位 bar 发出,订单由引擎在下一根
    bar open 撮合。SELL 在有持仓时为平多,无持仓时为开空;BUY 反之。
    """

    def __init__(self, plays: list[tuple[int, OrderSide]]):
        self._plays = {slot: side for slot, side in plays}
        self._cursor = 0

    def on_kline(self, event: KlineEvent) -> Signal | None:
        slot = self._cursor
        self._cursor += 1
        side = self._plays.get(slot)
        if side is None:
            return None
        return Signal(
            strategy_id="p05",
            symbol=event.symbol,
            side=side,
            strength=1.0,
            timestamp=event.timestamp,
            reason=f"p05@{slot}",
            quantity=1.0,
        )


def _run(strategy, tape: list[KlineEvent], **cfg) -> list[dict]:
    config = BacktestConfig(
        initial_cash=10_000.0,
        commission=NoCommission(),
        slippage=NoSlippage(),
        position_sizing="signal_quantity",
        **cfg,
    )
    result = BacktestEngine(config).run(strategy, tape)
    return [dict(t) for t in result.trades]


class TestTrailingStopLong:
    """验收锚 1:多头峰值回撤触发清仓。"""

    def test_peak_pullback_triggers_liquidation(self) -> None:
        # 开多成交@1 open 100;升 110(@2),回撤 103(@3,6.4%>5%)→ 风控@3 末
        # 产出清算,成交@4 open 103。一条 long 完整往返。
        tape = [
            _bar(0, 100.0),
            _bar(1, 100.0),
            _bar(2, 110.0),
            _bar(3, 103.0),
            _bar(4, 103.0),
        ]
        trades = _run(
            Scripted([(0, OrderSide.BUY)]),
            tape,
            risk_managers=[TrailingStopRiskManager(trailing_pct=5.0)],
        )
        assert len(trades) == 1
        assert trades[0]["side"] == "long"
        assert trades[0]["entry_action"] == "buy"
        assert trades[0]["exit_action"] == "sell"  # 风控卖平
        assert trades[0]["exit_price"] == 103.0  # 下一根开盘平仓
        assert trades[0]["pnl"] > 0  # 103 > 入场 100

    def test_no_trigger_within_band(self) -> None:
        # 开多成交@1 open 100;升 110(@2),回撤 105(@3,4.5%<5%)→ 不清仓。
        # 多单保持开立,trades 空。
        tape = [
            _bar(0, 100.0),
            _bar(1, 100.0),
            _bar(2, 110.0),
            _bar(3, 105.0),
            _bar(4, 105.0),
        ]
        trades = _run(
            Scripted([(0, OrderSide.BUY)]),
            tape,
            risk_managers=[TrailingStopRiskManager(trailing_pct=5.0)],
        )
        assert trades == []  # 多单未平,无完整往返


class TestTrailingStopShort:
    """验收锚 2:空头峰值回撤镜像触发。"""

    def test_short_peak_reversal_triggers(self) -> None:
        # 开空成交@1 open 100;跌 90(@2),反弹 96(@3,6.7%>5%)→ 风控@3 末产出,
        # 买入平仓成交@4 open 96。一条 short 完整往返,pnl>0。
        tape = [
            _bar(0, 100.0),
            _bar(1, 100.0),
            _bar(2, 90.0),
            _bar(3, 96.0),
            _bar(4, 96.0),
        ]
        trades = _run(
            Scripted([(0, OrderSide.SELL)]),
            tape,
            risk_managers=[TrailingStopRiskManager(trailing_pct=5.0)],
        )
        assert len(trades) == 1
        assert trades[0]["side"] == "short"
        assert trades[0]["entry_action"] == "sell"
        assert trades[0]["exit_action"] == "buy"  # 风控买入平仓
        assert trades[0]["pnl"] > 0  # 96 买入,高于入场 100(空头赚 4)


class TestTrailingStopReentry:
    """验收锚 3:换仓(多→空)重置峰值状态。"""

    def test_direction_flip_resets_peak(self) -> None:
        # 开多@0 成交@1 open 100;平多@1 成交@2 open 110;开空@2 成交@3 open
        # 110。@4 升 115 对空头是损失方向(未从峰值回撤),不触发。
        # 空头峰值独立于多头,是"换仓重置"的正确表现。
        tape = [
            _bar(0, 100.0),
            _bar(1, 100.0),
            _bar(2, 110.0),
            _bar(3, 110.0),
            _bar(4, 115.0),
        ]
        trades = _run(
            Scripted([(0, OrderSide.BUY), (1, OrderSide.SELL), (2, OrderSide.SELL)]),
            tape,
            risk_managers=[TrailingStopRiskManager(trailing_pct=5.0)],
        )
        # 只有平多那笔完整往返;开空未平(115 反向移动不触发)。
        assert len(trades) == 1
        assert trades[0]["side"] == "long" and trades[0]["exit_action"] == "sell"


class TestCooldown:
    """验收锚 4:清仓后 N bar 内禁止重建仓。"""

    def test_cooldown_suppresses_reentry(self) -> None:
        # 开多@0 成交@1 open 100;升 110(@2),回撤 103(@3 触发风控,清算单成交
        # @4 open 103)。cooldown 从@3 末起 2 bar。策略@4 想重开多 → 仍在
        # cooldown → 抑制。
        tape = [
            _bar(0, 100.0),
            _bar(1, 100.0),
            _bar(2, 110.0),
            _bar(3, 103.0),
            _bar(4, 103.0),
            _bar(5, 103.0),
        ]
        trades = _run(
            Scripted([(0, OrderSide.BUY), (4, OrderSide.BUY)]),  # @4 想重开多
            tape,
            risk_managers=[TrailingStopRiskManager(trailing_pct=5.0)],
            risk_cooldown_bars=2,
        )
        # 只有开多+风控平仓这笔完整往返;@4 重建仓被 cooldown 抑制。
        assert len(trades) == 1
        assert trades[0]["exit_action"] == "sell"


class TestMaxDrawdown:
    """验收锚 5:单标的未实现亏损超阈值清仓。"""

    def test_drawdown_beyond_threshold(self) -> None:
        # 开多成交@1 open 100;98(@2,-2%),94(@3,-6%<-5%)→ 风控@3 末产出,
        # 清算成交@4 open 94。一条 long 往返,pnl 负。
        tape = [
            _bar(0, 100.0),
            _bar(1, 100.0),
            _bar(2, 98.0),
            _bar(3, 94.0),
            _bar(4, 94.0),
        ]
        trades = _run(
            Scripted([(0, OrderSide.BUY)]),
            tape,
            risk_managers=[MaxDrawdownRiskManager(drawdown_pct=5.0)],
        )
        assert len(trades) == 1
        assert trades[0]["exit_action"] == "sell"
        assert trades[0]["pnl"] < 0


class TestComposite:
    """验收锚 6:多个 mgr 串联,先触发者生效。"""

    def test_first_trigger_wins(self) -> None:
        # 开多成交@1 open 100;98(@2),94(@3,-6%)→ drawdown 5% 触发,profit
        # 20% 未触发。清算成交@4 open 94。
        tape = [
            _bar(0, 100.0),
            _bar(1, 100.0),
            _bar(2, 98.0),
            _bar(3, 94.0),
            _bar(4, 94.0),
        ]
        trades = _run(
            Scripted([(0, OrderSide.BUY)]),
            tape,
            risk_managers=[
                MaxDrawdownRiskManager(drawdown_pct=5.0),
                MaxUnrealizedProfitRiskManager(profit_pct=20.0),
            ],
        )
        assert len(trades) == 1
        assert trades[0]["exit_action"] == "sell"


class TestDisabledRegression:
    """验收锚 7:risk_managers=None 时行为与现状逐字节一致。"""

    def test_none_config_no_extra_trades(self) -> None:
        # 开多@0 成交@1,平多@2 成交@3。无风控介入,一条 long 往返。
        tape = [
            _bar(0, 100.0),
            _bar(1, 100.0),
            _bar(2, 110.0),
            _bar(3, 103.0),
            _bar(4, 103.0),
        ]
        trades = _run(
            Scripted([(0, OrderSide.BUY), (2, OrderSide.SELL)]),
            tape,
        )
        assert len(trades) == 1
        assert trades[0]["side"] == "long" and trades[0]["exit_action"] == "sell"
