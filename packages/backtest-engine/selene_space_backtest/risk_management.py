"""回测内风控 = targets 重写器（LEAN 专题⑥思想吸收,实现自写）。

规格:internal-plan。
LEAN 的 risk 是目标层重写器:组合构建产出初始 target 集 → 风控层追加清仓
targets → 合并走正常执行管道,风控动作进 trades/会计。
selene 单循环引擎降维为:每 bar 末尾检查 RiskManager 钩子,返回要清仓的
symbol 集,引擎转成 close order 走 broker 正常撮合,会计一致。

拦截式 RiskEngine(packages/risk-engine/)保留用于实盘硬闸;本模块只在回测里
模拟"风控触发的减仓行为本身对净值的影响",两者互补不替换。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class RiskTarget:
    """轻量清仓指令:全平 ``symbol`` 当前净持仓,不承载数量。

    语义对齐 LEAN 的清仓 target(清仓该 symbol),但去掉数量维度——
    selene 单资产引擎的 target 永远只表达"全平",避免引入完整目标抽象
    的过度设计。
    """

    symbol: str


@dataclass
class _TrailingState:
    """单 symbol 的峰值追踪状态(多空分开,换仓重置)。"""

    side: str  # "long" | "short"
    peak_price: float


class RiskManager(Protocol):
    """纯函数式风控钩子:输入持仓/现价/共享状态,输出清仓指令集。

    由引擎逐 bar 调用。状态字典由引擎持有并传入,模型自身不持有可变状态,
    便于多个模型共享同一状态、且可组合串联。
    """

    def manage(
        self,
        positions: dict[str, float],
        prices: dict[str, float],
        state: dict,
    ) -> list[RiskTarget]:
        """返回要清仓的 target 集。空列表 = 无动作。"""
        ...


class TrailingStopRiskManager:
    """最高浮盈回撤超阈值 → 清仓。

    跟踪每 symbol 的最高浮盈:多头从峰值价回撤、空头从谷底价反弹。峰值状态
    多空分开、换仓重置(见 _TrailingState)。
    """

    def __init__(self, trailing_pct: float) -> None:
        if trailing_pct <= 0:
            raise ValueError("trailing_pct must be > 0")
        self._pct = float(trailing_pct)

    def manage(
        self,
        positions: dict[str, float],
        prices: dict[str, float],
        state: dict,
    ) -> list[RiskTarget]:
        states: dict[str, _TrailingState] = state.setdefault("trailing", {})
        targets: list[RiskTarget] = []

        for symbol, qty in positions.items():
            qty_f = float(qty)
            if abs(qty_f) < 1e-12:
                states.pop(symbol, None)  # 无持仓清状态
                continue
            price = float(prices.get(symbol, 0.0))
            if price <= 0:
                continue
            side = "long" if qty_f > 0 else "short"
            st = states.get(symbol)
            if st is None or st.side != side:
                # 新开仓或换仓翻转:重置峰值
                states[symbol] = _TrailingState(side=side, peak_price=price)
                continue

            if side == "long":
                if price > st.peak_price:
                    st.peak_price = price  # 更新峰值
                elif (
                    st.peak_price > 0
                    and (st.peak_price - price) / st.peak_price * 100.0 >= self._pct
                ):
                    targets.append(RiskTarget(symbol=symbol))
                    states.pop(symbol, None)
            else:
                if price < st.peak_price:
                    st.peak_price = price  # 更新谷底
                elif (
                    st.peak_price > 0
                    and (price - st.peak_price) / st.peak_price * 100.0 >= self._pct
                ):
                    targets.append(RiskTarget(symbol=symbol))
                    states.pop(symbol, None)

        return targets


class MaxDrawdownRiskManager:
    """单标的未实现亏损超阈值 → 清仓(止亏保护)。"""

    def __init__(self, drawdown_pct: float) -> None:
        if drawdown_pct <= 0:
            raise ValueError("drawdown_pct must be > 0")
        self._pct = float(drawdown_pct)

    def manage(
        self,
        positions: dict[str, float],
        prices: dict[str, float],
        state: dict,
    ) -> list[RiskTarget]:
        entries: dict[str, float] = state.setdefault("drawdown_entries", {})
        targets: list[RiskTarget] = []

        for symbol, qty in positions.items():
            qty_f = float(qty)
            if abs(qty_f) < 1e-12:
                entries.pop(symbol, None)
                continue
            price = float(prices.get(symbol, 0.0))
            if price <= 0:
                continue
            entry = entries.get(symbol)
            if entry is None:
                entries[symbol] = price
                continue
            if qty_f > 0:  # long
                loss_pct = (price - entry) / entry * 100.0
            else:  # short
                loss_pct = (entry - price) / entry * 100.0
            if loss_pct <= -self._pct:
                targets.append(RiskTarget(symbol=symbol))
                entries.pop(symbol, None)

        return targets


class MaxUnrealizedProfitRiskManager:
    """单标的未实现盈利超阈值 → 获利了结清仓(利润保护)。"""

    def __init__(self, profit_pct: float) -> None:
        if profit_pct <= 0:
            raise ValueError("profit_pct must be > 0")
        self._pct = float(profit_pct)

    def manage(
        self,
        positions: dict[str, float],
        prices: dict[str, float],
        state: dict,
    ) -> list[RiskTarget]:
        entries: dict[str, float] = state.setdefault("profit_entries", {})
        targets: list[RiskTarget] = []

        for symbol, qty in positions.items():
            qty_f = float(qty)
            if abs(qty_f) < 1e-12:
                entries.pop(symbol, None)
                continue
            price = float(prices.get(symbol, 0.0))
            if price <= 0:
                continue
            entry = entries.get(symbol)
            if entry is None:
                entries[symbol] = price
                continue
            if qty_f > 0:  # long
                profit_pct = (price - entry) / entry * 100.0
            else:  # short
                profit_pct = (entry - price) / entry * 100.0
            if profit_pct >= self._pct:
                targets.append(RiskTarget(symbol=symbol))
                entries.pop(symbol, None)

        return targets


class CompositeRiskManager:
    """串联多个模型,任一产出清仓 target 即生效(LEAN 的 AddRiskManagement 形态)。"""

    def __init__(self, managers: list) -> None:
        self._managers = list(managers)

    def manage(
        self,
        positions: dict[str, float],
        prices: dict[str, float],
        state: dict,
    ) -> list[RiskTarget]:
        targets: list[RiskTarget] = []
        seen: set[str] = set()
        for mgr in self._managers:
            for t in mgr.manage(positions, prices, state):
                if t.symbol not in seen:
                    seen.add(t.symbol)
                    targets.append(t)
        return targets
