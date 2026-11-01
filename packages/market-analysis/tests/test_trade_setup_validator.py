"""G2 — 交易者方程规则 T1-T5 + wait 短路干净校验。

覆盖验收标准（方案 §6.2 测试计划）：
- T1 完整性：no_trade 无需 trade_setup；limit/breakout/market 缺 trade_setup → 报错。
- T2 价格方向：long 需 stop_loss < entry < take_profit；short 反向。
- T3 RR 硬门槛：RR 由价格确定性重算（防 LLM 虚报），低于 min_risk_reward 或声明偏差大 → 报错。
- T4 交易者方程：win_rate 须高于盈亏平衡 1/(1+RR)。
- T5 交叉校验：声明 trader_equation_passed=true 但实际未过 → 报错。
- ValidatorConfig 旋钮：trader_equation_required=False / min_risk_reward 可调。
- wait 短路：_synth_wait_record 输出带决策路径且过 validate_stage2。
"""
from __future__ import annotations

from typing import Literal

import pytest

from selene_space_market_analysis.flow import MarketAnalysisFlow
from selene_space_market_analysis.llm_provider import HttpLLMProvider
from selene_space_market_analysis.models import (
    SignalPattern,
    Stage1Diagnosis,
    Stage2Decision,
    TradeSetup,
)
from selene_space_market_analysis.persistence import InMemoryPersistence
from selene_space_market_analysis.validator import ValidatorConfig, validate_stage2

_LLM_CONFIG = {
    "model": "test-model",
    "temperature": 0.2,
    "max_tokens": 256,
    "api_key": None,
    "base_url": None,
}

Decision = Literal["limit", "breakout", "market", "no_trade"]


def _stage1() -> Stage1Diagnosis:
    return Stage1Diagnosis(
        market_regime="trend_up",
        entry_gate="proceed",
        signal_patterns=[
            SignalPattern(
                name="channel_breakout",
                bias="bullish",
                confidence=0.8,
                kline_range="K1-K10",
            )
        ],
        diagnosis_summary={"regime": "trend_up", "reason": "均线多头排列"},
    )


def _valid_trade_setup(**overrides: object) -> TradeSetup:
    """合法 long：entry=100 sl=95 tp=110 → rr=2.0，breakeven=1/3，win_rate=0.5 过方程。"""
    base: dict[str, object] = {
        "direction": "long",
        "entry_price": 100.0,
        "stop_loss_price": 95.0,
        "take_profit_price": 110.0,
        "risk_reward_ratio": 2.0,
        "estimated_win_rate": 0.5,
        "confidence": 0.7,
        "trader_equation_passed": True,
        "decision_path": ["gate:proceed", "趋势确认", "入场", "止损设置"],
    }
    base.update(overrides)
    return TradeSetup(**base)


def _propose(
    decision: Decision, trade_setup: TradeSetup | None, **summary: object
) -> Stage2Decision:
    return Stage2Decision(
        decision=decision,
        dispatch_playbooks=[],
        decision_summary={"diagnosis_summary": _stage1().diagnosis_summary, **summary},
        evidence=[],
        next_bar_forecast=None,
        next_cycle_forecast=None,
        trade_setup=trade_setup,
    )


def _errors(report) -> list[str]:
    return [e.message for e in report.errors]


# ---------------------------------------------------------------------------
# T1 — 完整性
# ---------------------------------------------------------------------------


def test_t1_no_trade_does_not_need_trade_setup() -> None:
    """decision=no_trade 无需 trade_setup，校验干净。"""
    result = _propose("no_trade", None)
    report = validate_stage2(result, stage1=_stage1())
    assert report.is_valid is True


@pytest.mark.parametrize("decision", ["limit", "breakout", "market"])
def test_t1_propose_missing_trade_setup_errors(decision: str) -> None:
    """propose 交易缺 trade_setup → 一致性层报错。"""
    result = _propose(decision, None)  # type: ignore[arg-type]
    report = validate_stage2(result, stage1=_stage1())
    assert report.is_valid is False
    assert any("trade_setup 为空" in m for m in _errors(report))


def test_t1_incomplete_prices_errors() -> None:
    """entry/SL/TP 任一为空 → 报错。"""
    ts = _valid_trade_setup(entry_price=None)
    result = _propose("limit", ts)
    report = validate_stage2(result, stage1=_stage1())
    assert report.is_valid is False
    assert any("不完整" in m for m in _errors(report))


# ---------------------------------------------------------------------------
# T2 — 价格方向
# ---------------------------------------------------------------------------


def test_t2_long_price_order_passes() -> None:
    result = _propose("limit", _valid_trade_setup())
    assert validate_stage2(result, stage1=_stage1()).is_valid is True


def test_t2_long_wrong_order_errors() -> None:
    ts = _valid_trade_setup(stop_loss_price=105.0)  # sl > entry
    result = _propose("limit", ts)
    report = validate_stage2(result, stage1=_stage1())
    assert report.is_valid is False
    assert any("价格顺序错误" in m for m in _errors(report))


def test_t2_short_price_order_passes() -> None:
    # short：tp=90 < entry=100 < sl=110 → rr=1.0，breakeven=0.5，win_rate=0.6 过
    ts = _valid_trade_setup(
        direction="short",
        entry_price=100.0,
        stop_loss_price=110.0,
        take_profit_price=90.0,
        risk_reward_ratio=1.0,
        estimated_win_rate=0.6,
    )
    result = _propose("market", ts)
    assert validate_stage2(result, stage1=_stage1()).is_valid is True


def test_t2_short_wrong_order_errors() -> None:
    ts = _valid_trade_setup(
        direction="short",
        entry_price=100.0,
        stop_loss_price=110.0,
        take_profit_price=105.0,  # tp > entry
    )
    result = _propose("market", ts)
    report = validate_stage2(result, stage1=_stage1())
    assert report.is_valid is False
    assert any("价格顺序错误" in m for m in _errors(report))


def test_t2_missing_direction_errors() -> None:
    ts = _valid_trade_setup(direction=None)
    result = _propose("limit", ts)
    report = validate_stage2(result, stage1=_stage1())
    assert report.is_valid is False
    assert any("direction 为空" in m for m in _errors(report))


# ---------------------------------------------------------------------------
# T3 — RR 硬门槛（价格重算，防虚报）
# ---------------------------------------------------------------------------


def test_t3_rr_below_min_errors() -> None:
    # entry=100 sl=90 tp=105 → risk=10 reward=5 rr=0.5 < min 1.0
    ts = _valid_trade_setup(
        entry_price=100.0,
        stop_loss_price=90.0,
        take_profit_price=105.0,
        estimated_win_rate=0.6,  # breakeven 1/(1+0.5)=0.667 → 也不满足 T4，但 T3 先报
    )
    result = _propose("limit", ts)
    report = validate_stage2(result, stage1=_stage1())
    assert report.is_valid is False
    assert any("风险回报比" in m and "最小" in m for m in _errors(report))


def test_t3_declared_rr_diverges_errors() -> None:
    # 价格重算 rr=2.0，但 LLM 声明 5.0 → 偏差 3.0 > 0.5 防虚报
    ts = _valid_trade_setup(risk_reward_ratio=5.0)
    result = _propose("limit", ts)
    report = validate_stage2(result, stage1=_stage1())
    assert report.is_valid is False
    assert any("偏差过大" in m for m in _errors(report))


def test_t3_min_risk_reward_knob() -> None:
    """调高 min_risk_reward → 原本通过的 rr=2.0 变失败；trader_equation_required=False 关闭校验。"""
    result = _propose("limit", _valid_trade_setup())
    strict = validate_stage2(result, stage1=_stage1(), config=ValidatorConfig(min_risk_reward=3.0))
    assert strict.is_valid is False
    assert any("最小" in m for m in _errors(strict))


def test_t3_trader_equation_required_false_disables() -> None:
    """旋钮关掉后 propose 交易缺 trade_setup 也不再报错。"""
    result = _propose("limit", None)  # type: ignore[arg-type]
    report = validate_stage2(
        result,
        stage1=_stage1(),
        config=ValidatorConfig(trader_equation_required=False),
    )
    assert report.is_valid is True


# ---------------------------------------------------------------------------
# T4 — 交易者方程（期望值）
# ---------------------------------------------------------------------------


def test_t4_win_rate_below_breakeven_errors() -> None:
    # rr=2.0 → breakeven=1/3≈0.333，win_rate=0.3 未过
    ts = _valid_trade_setup(estimated_win_rate=0.3)
    result = _propose("limit", ts)
    report = validate_stage2(result, stage1=_stage1())
    assert report.is_valid is False
    assert any("交易者方程未通过" in m for m in _errors(report))


def test_t4_win_rate_above_breakeven_passes() -> None:
    result = _propose("limit", _valid_trade_setup(estimated_win_rate=0.5))
    assert validate_stage2(result, stage1=_stage1()).is_valid is True


# ---------------------------------------------------------------------------
# T5 — 声称 vs 事实交叉校验
# ---------------------------------------------------------------------------


def test_t5_claimed_passed_but_actually_fails() -> None:
    ts = _valid_trade_setup(estimated_win_rate=0.3, trader_equation_passed=True)
    result = _propose("limit", ts)
    report = validate_stage2(result, stage1=_stage1())
    assert report.is_valid is False
    assert any("声称与事实不符" in m for m in _errors(report))


# ---------------------------------------------------------------------------
# wait 短路记录
# ---------------------------------------------------------------------------


def test_synth_wait_record_validates_cleanly() -> None:
    """_synth_wait_record 输出：decision=no_trade + 决策路径，且过 validate_stage2。"""
    from selene_space_llm.base import LLMConfig

    provider = HttpLLMProvider(
        config=LLMConfig(**dict(_LLM_CONFIG)),
        provider=lambda messages: "",
    )
    flow = MarketAnalysisFlow(llm=provider, persistence=InMemoryPersistence())

    stage1 = _stage1()
    synth = flow._synth_wait_record(stage1)
    assert synth.decision == "no_trade"
    assert synth.trade_setup is not None
    assert synth.trade_setup.decision_path == [
        "gate:proceed",
        "gate 短路",
        "no_trade",
    ]
    report = validate_stage2(synth, stage1=stage1)
    assert report.is_valid is True
