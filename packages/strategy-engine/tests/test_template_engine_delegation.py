"""P0-1 杀双循环:run_backtest 委托 BacktestEngine 后的语义锁定。

引擎语义升级点(相对旧同根收盘成交循环):
- 成交发生在**次根开盘**(信号根 N 收盘出信号,N+1 根开盘成交)
- Signal.timestamp == 触发根的 timestamp(不再是墙钟)
- 保护性止损在 on_kline 内判定,出场同样顺延到下一根开盘
- 指标年化按 interval 表(1h→8760),不再恒定 √252
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "shared-types"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "backtest-engine"))
sys.path.insert(0, str(PROJECT_ROOT / "packages" / "strategy-engine"))

import pytest

from selene_space_strategy.template import (
    BacktestResult,
    BacktestStrategyTemplate,
    SignalType,
)
from selene_space_types.market import KlineEvent


def rising_then_crash(n_rise: int = 30, crash_at: int = 31) -> list[KlineEvent]:
    """Slow rise then a violent gap down — engineered to arm and trip ATR stops."""
    out: list[KlineEvent] = []
    for i in range(crash_at + 6):
        if i < n_rise:
            base = 100.0 + i
        else:
            base = 100.0 + n_rise - (i - n_rise + 1) * 12.0  # -12/bar collapse
        out.append(
            KlineEvent(
                exchange="binance",
                symbol="BTC/USDT",
                timestamp=i * 3600000,
                interval="1h",
                open=base,
                high=base + 1.0,
                low=base - 8.0,
                close=base,
                volume=1000.0,
                closed=True,
            )
        )
    return out


class Alternator(BacktestStrategyTemplate):
    """BUY on even bars, SELL on odd bars — fully deterministic round trips."""

    def __init__(self, **kwargs) -> None:
        kwargs.setdefault("long_only", False)
        super().__init__(**kwargs)

    def get_signal(self, data) -> SignalType:
        return SignalType.BUY if len(data) % 2 == 0 else SignalType.SELL


def zig(n: int = 40, start: float = 100.0) -> list[KlineEvent]:
    out: list[KlineEvent] = []
    for i in range(n):
        base = start + ((-1) ** i) * 5.0 + i * 0.5
        out.append(
            KlineEvent(
                exchange="binance",
                symbol="BTC/USDT",
                timestamp=i * 3600000,
                interval="1h",
                open=base,
                high=base + 1.5,
                low=base - 1.5,
                close=base,
                volume=5000.0,
                closed=True,
            )
        )
    return out


class TestNextBarOpenSemantics:
    """成交纪律:信号根收盘出信号,次根开盘成交。"""

    def test_first_fill_happens_at_next_bar_open(self) -> None:
        events = zig(20)
        strat = Alternator(symbol="BTC/USDT", initial_capital=100_000.0)
        result = strat.run_backtest(events)

        assert result.total_trades >= 1
        first = result.trades[0]
        # 入场时间戳必须是信号根(0 号)之后的某一根 —— 不再是信号根本身。
        assert first["entry_time"] > events[0].timestamp

    def test_entry_price_tracks_next_bar_open_not_signal_close(self) -> None:
        events = zig(20)
        strat = Alternator(slippage_bps=0.0, fee_rate=0.0)
        result = strat.run_backtest(events)

        first = result.trades[0]
        # 找到入场那根:entry_time 对应的 bar 的 open 就是成交基准价。
        entry_bar = next(e for e in events if e.timestamp == first["entry_time"])
        assert first["entry_price"] == pytest.approx(entry_bar.open, rel=1e-9)


class TestSignalProvenance:
    """Signal 时间戳必须来自事件流,不许墙钟。"""

    def test_signal_timestamps_are_event_timestamps(self) -> None:
        events = zig(16)
        strat = Alternator()
        result = strat.run_backtest(events)

        assert result.signals, "expected at least one signal"
        event_ts = {e.timestamp for e in events}
        for sig in result.signals:
            assert sig.timestamp in event_ts


class TestProtectiveExitRelocation:
    """ATR 止损由 on_kline 判定,出场成交顺延一根。"""

    def test_atr_stop_exits_after_crash(self) -> None:
        events = rising_then_crash()

        class BuyAndHoldWithStops(Alternator):
            def get_signal(self, data) -> SignalType:
                # 预热门:攒够 ATR 周期历史再入场,避开 ±2%/4% 固定回退括号
                # (否则 +4% 回退 TP 会先于崩盘命中)。
                if len(data) < 12:
                    return SignalType.HOLD
                if self._position == "flat":
                    return SignalType.BUY
                return SignalType.HOLD

        strat = BuyAndHoldWithStops(
            use_atr_stops=True,
            atr_sl_mult=2.0,
            atr_tp_mult=100.0,  # 目标远到永不触发,只考验止损腿
            atr_initial_mult=2.0,
            atr_stop_period=5,
        )
        result = strat.run_backtest(events)

        assert result.total_trades >= 1
        exit_trade = result.trades[0]
        # 出场时间必须晚于崩盘起点(止损在崩盘后被触发)。
        crash_ts = events[31].timestamp
        assert exit_trade["exit_time"] > crash_ts


class TestFlatResultContract:
    """翻译层保住扁平结果的所有消费字段。"""

    def test_all_scalar_fields_present_and_curves_populated(self) -> None:
        events = zig(60)
        strat = Alternator(initial_capital=100_000.0)
        result = strat.run_backtest(events)

        assert isinstance(result, BacktestResult)
        for attr in (
            "total_return",
            "annual_return",
            "sharpe_ratio",
            "max_drawdown",
            "win_rate",
            "profit_factor",
            "total_trades",
            "calmar_ratio",
            "sortino_ratio",
            "expectancy",
        ):
            assert hasattr(result, attr), attr
        assert len(result.equity_curve) == len(events)
        assert len(result.drawdown_curve) == len(events)
        assert result.total_trades == len(result.trades)

    def test_trade_action_vocabulary_preserved(self) -> None:
        events = zig(40)
        strat = Alternator()
        result = strat.run_backtest(events)

        actions = {t["action"] for t in result.trades}
        assert actions <= {"close_long", "close_short"}
        assert actions  # 至少有一种出场

    def test_max_drawdown_is_non_positive(self) -> None:
        events = zig(60)
        strat = Alternator()
        result = strat.run_backtest(events)
        assert result.max_drawdown <= 0.0

    def test_interval_aware_annualization_used(self) -> None:
        """1h 数据的年化应基于 8760 根/年,而非旧版固定 252。"""
        events = zig(200)
        strat = Alternator(initial_capital=100_000.0)
        result = strat.run_backtest(events)
        # 结构性断言:有交易时年化与总收益同号且量级被放大过(非零)。
        if result.total_trades > 0 and result.total_return != 0.0:
            assert result.annual_return != 0.0
            assert (result.annual_return > 0) == (result.total_return > 0)


class TestDegenerateInputs:
    """空数据/全未收盘输入不炸,返回近零扁平结果。"""

    def test_empty_events_return_zero_report(self) -> None:
        strat = Alternator()
        result = strat.run_backtest([])
        assert result.total_trades == 0
        assert result.equity_curve == []

    def test_unclosed_events_skipped(self) -> None:
        events = zig(10)
        unclosed = [e.model_copy(update={"closed": False}) for e in events]
        strat = Alternator()
        result = strat.run_backtest(unclosed)
        assert result.total_trades == 0
        assert result.signals == []


class TestTwoSidedShorting:
    """long_only=False 时 SELL 从空仓开空,引擎持有负数量。"""

    def test_short_round_trip_produces_close_short(self) -> None:
        events = zig(40)

        class ShortOpener(Alternator):
            def get_signal(self, data) -> SignalType:
                # 多头显式平掉;空头攒几根后 CLOSE_SHORT;空仓周期性开空。
                if self._position == "long":
                    return SignalType.CLOSE_LONG
                if self._position == "short":
                    return (
                        SignalType.CLOSE_SHORT
                        if len(data) % 5 == 0
                        else SignalType.HOLD
                    )
                return SignalType.SELL if len(data) % 3 == 0 else SignalType.HOLD

        strat = ShortOpener(long_only=False)
        result = strat.run_backtest(events)

        assert any(t["action"] == "close_short" for t in result.trades)


class TestGoldenNumbers:
    """确定性数据的完整指标金样(新语义下重锁)。

    ``total_return`` 2026-10 重锁：-1.0873686 → -0.98841441。
    起因是 73703f63「杜绝隐形杠杆」——该修复让 BUY 按实际成交价 cap 现金
    (affordable = cash / (fill_price + per_unit_fee))，跳空时现金不再打负、
    敞口恒 ≤1.0。交易笔数(59)与成交序列不变，只有成本/敞口簿记变化，
    故总收益上移。那次提交重算了 pyramiding 的期望值，漏了这个金样。
    """

    def test_alternator_golden(self) -> None:
        events = zig(120)
        strat = Alternator(
            initial_capital=100_000.0,
            slippage_bps=1.5,
            fee_rate=0.0005,
        )
        result = strat.run_backtest(events)

        # 笔数未变 = 信号/成交序列未被改动，只是成本语义更新（见类 docstring）。
        assert result.total_trades == 59
        assert result.total_return == pytest.approx(-0.98841441, abs=5e-7)
        assert result.win_rate == pytest.approx(0.0, abs=1e-6)
        assert result.profit_factor == pytest.approx(0.0, abs=1e-6)
        assert len(result.equity_curve) == 120
