"""A1 — 市场契约层测试：Stage1/Stage2 pydantic 模型 + 三层校验。

覆盖 US-002 验收标准：
- entry_gate / market_regime / signal_patterns / trend_context / bar_features 过 pydantic
- 一致性校验：entry_gate=proceed 无信号、spike+proceed、跨阶段锚定篡改
- 命名隔离：仅用隔离表字段名，无参考实现逐字字段名
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from selene_space_market_analysis.models import (
    Evidence,
    NextBarForecast,
    NextCycleForecast,
    SignalPattern,
    Stage1Diagnosis,
    Stage2Decision,
    TradeSetup,
)
from selene_space_market_analysis.validator import validate_stage1, validate_stage2


# ---------------------------------------------------------------------------
# Stage 1 — pydantic 契约
# ---------------------------------------------------------------------------


def _valid_stage1() -> Stage1Diagnosis:
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
        trend_context={"swing": "higher_high", "key_level": "K8"},
        bar_features={"adx": 28.5, "ema_fast": 1.0, "ema_slow": 0.9},
        evidence=[
            Evidence(
                kind="structure",
                key="breakout_confirmed",
                statement="K8 收盘突破通道上沿，动量 adx=28.5 支持",
                kline_range="K1-K10",
            )
        ],
        diagnosis_summary={"regime": "trend_up", "strength": "strong"},
    )


def test_a1_stage1_valid_contract_passes() -> None:
    """合法 Stage1 契约：pydantic 过 + 3 层校验零错误。"""
    result = _valid_stage1()
    report = validate_stage1(result)
    assert report.is_valid is True
    assert report.errors == ()
    assert report.cheat_fields == ()


def test_a1_stage1_enum_coercion() -> None:
    """非法枚举值被 pydantic Literal 拒绝。"""
    with pytest.raises(ValidationError):
        Stage1Diagnosis(
            market_regime="sideways",  # type: ignore[arg-type]  # 故意非法,测拒绝
            entry_gate="proceed",
        )
    with pytest.raises(ValidationError):
        Stage1Diagnosis(
            market_regime="range",
            entry_gate="go",  # type: ignore[arg-type]  # 故意非法,测拒绝
        )


def test_a1_stage1_proceed_requires_signals() -> None:
    """一致性层：entry_gate=proceed 但 signal_patterns 为空 → 报 consistency 错误。"""
    result = Stage1Diagnosis(
        market_regime="range",
        entry_gate="proceed",
        signal_patterns=[],
        evidence=[Evidence(kind="range", key="narrow", statement="K1-K10 窄幅震荡")],
    )
    report = validate_stage1(result)
    assert report.is_valid is False
    layers = {e.layer for e in report.errors}
    assert "consistency" in layers
    assert any(e.field == "signal_patterns" for e in report.errors)


def test_a1_stage1_spike_forbids_proceed() -> None:
    """一致性层：market_regime=spike + entry_gate=proceed 自洽性检查。"""
    result = Stage1Diagnosis(
        market_regime="spike",
        entry_gate="proceed",
        signal_patterns=[
            SignalPattern(name="spike_pump", bias="bullish", confidence=0.6)
        ],
    )
    report = validate_stage1(result)
    assert report.is_valid is False
    assert any(
        e.field == "entry_gate" and e.layer == "consistency" for e in report.errors
    )


def test_a1_stage1_wait_passes_even_without_signals() -> None:
    """wait/unknown 短路语义：无信号时仍可合法。"""
    result = Stage1Diagnosis(market_regime="range", entry_gate="wait")
    report = validate_stage1(result)
    assert report.is_valid is True


def test_a1_stage1_bad_kline_range_json_error() -> None:
    """JSON 层：kline_range 含 K0 被拒绝。"""
    result = Stage1Diagnosis(
        market_regime="range",
        entry_gate="wait",
        signal_patterns=[
            SignalPattern(name="x", bias="neutral", confidence=0.5, kline_range="K0-K5")
        ],
    )
    report = validate_stage1(result)
    assert report.is_valid is False
    assert any(e.layer == "json" and "K0" in e.message for e in report.errors)


def test_a1_stage1_semantic_boilerplate_rejected() -> None:
    """语义层：evidence.statement 为模板化 token 被拒绝。"""
    result = Stage1Diagnosis(
        market_regime="range",
        entry_gate="wait",
        evidence=[Evidence(kind="range", key="flat", statement="是")],
    )
    report = validate_stage1(result)
    assert report.is_valid is False
    assert any(e.layer == "semantic" for e in report.errors)


# ---------------------------------------------------------------------------
# Stage 2 — 契约 + 跨阶段锚定
# ---------------------------------------------------------------------------


def _valid_stage2(stage1: Stage1Diagnosis) -> Stage2Decision:
    return Stage2Decision(
        decision="limit",
        dispatch_playbooks=["channel_breakout"],
        decision_summary={
            "diagnosis_summary": stage1.diagnosis_summary,
            "direction": "long",
        },
        evidence=[
            Evidence(
                kind="execution",
                key="entry",
                statement="K9 限价挂单",
                kline_range="K1-K10",
            )
        ],
        next_bar_forecast=NextBarForecast(direction="up", probability=0.62),
        next_cycle_forecast=NextCycleForecast(
            direction="up",
            probability=0.66,
            cycle_stage="early",
            phase_position=0.4,
        ),
        trade_setup=TradeSetup(
            direction="long",
            entry_price=100.0,
            stop_loss_price=95.0,
            take_profit_price=110.0,
            risk_reward_ratio=2.0,
            estimated_win_rate=0.5,
            confidence=0.7,
            trader_equation_passed=True,
            decision_path=["gate:proceed", "趋势确认", "入场", "止损设置"],
        ),
    )


def test_a1_stage2_valid_contract_passes() -> None:
    """合法 Stage2：pydantic 过 + 校验零错误（含 next_bar/next_cycle 后端契约）。"""
    stage1 = _valid_stage1()
    result = _valid_stage2(stage1)
    report = validate_stage2(result, stage1=stage1)
    assert report.is_valid is True
    assert result.next_bar_forecast is not None
    assert result.next_bar_forecast.probability == 0.62
    assert result.next_cycle_forecast is not None
    assert result.next_cycle_forecast.cycle_stage == "early"
    assert result.next_cycle_forecast.phase_position == 0.4


def test_a1_stage2_enum_coercion() -> None:
    """decision 非法值被 pydantic Literal 拒绝。"""
    with pytest.raises(ValidationError):
        Stage2Decision(decision="market_order")  # type: ignore[arg-type]  # 故意非法,测拒绝


def test_a1_stage2_cheat_detection_on_anchored_field() -> None:
    """跨阶段锚定：Stage2 篡改 diagnosis_summary → cheat_detected。"""
    stage1 = _valid_stage1()
    tampered = _valid_stage2(stage1)
    tampered = tampered.model_copy(
        update={"decision_summary": {"diagnosis_summary": {"regime": "range"}}}
    )
    report = validate_stage2(tampered, stage1=stage1)
    assert report.is_valid is False
    assert report.cheat_fields == ("diagnosis_summary",)
    assert any(e.cheat_detected for e in report.errors)


def test_a1_contract_has_no_tree_concepts() -> None:
    """契约不引入决策树概念：无 trace/DecisionTreeGraph 字段。"""
    stage1 = _valid_stage1()
    stage2 = _valid_stage2(stage1)
    assert not hasattr(stage1, "trace")
    assert not hasattr(stage2, "trace")
    assert "trace" not in Stage1Diagnosis.model_fields
    assert "trace" not in Stage2Decision.model_fields
    # 扁平 evidence[] 存在
    assert len(stage1.evidence) >= 1
    assert len(stage2.evidence) >= 1


# ---------------------------------------------------------------------------
# US-303 — next_cycle_forecast 契约（A-303）
# ---------------------------------------------------------------------------


def test_a1_next_cycle_forecast_enum_and_range() -> None:
    """契约过 pydantic：方向/周期阶段枚举 + 概率/相位位置 [0,1] 范围。"""
    ok = NextCycleForecast(
        direction="down",
        probability=0.8,
        cycle_stage="late",
        phase_position=0.9,
    )
    assert ok.phase_position == 0.9
    with pytest.raises(ValidationError):
        NextCycleForecast(
            direction="sideways",  # type: ignore[arg-type]  # 故意非法,测拒绝
            probability=0.6,
            cycle_stage="early",
            phase_position=0.5,
        )
    with pytest.raises(ValidationError):
        NextCycleForecast(
            direction="up", probability=1.5, cycle_stage="early", phase_position=0.5
        )
    with pytest.raises(ValidationError):
        NextCycleForecast(
            direction="up",
            probability=0.6,
            cycle_stage="nowhere",  # type: ignore[arg-type]  # 故意非法,测拒绝
            phase_position=0.5,
        )
    with pytest.raises(ValidationError):
        NextCycleForecast(
            direction="up", probability=0.6, cycle_stage="early", phase_position=-0.1
        )


def test_a1_next_cycle_forecast_has_no_forbidden_position_key() -> None:
    """位置子字段恰为 cycle_stage/phase_position（不含参考实现位置名）。"""
    stage2 = _valid_stage2(_valid_stage1())
    assert stage2.next_cycle_forecast is not None
    assert set(NextCycleForecast.model_fields) == {
        "direction",
        "probability",
        "cycle_stage",
        "phase_position",
        "note",
    }


def test_a1_stage2_validator_covers_next_cycle_bounds() -> None:
    """validator 覆盖 next_cycle_forecast 范围（绕过 pydantic 直接注入越界值）。"""
    stage1 = _valid_stage1()
    forged = Stage2Decision.model_construct(
        decision="no_trade",
        dispatch_playbooks=[],
        decision_summary={},
        evidence=[],
        next_bar_forecast=None,
        next_cycle_forecast=NextCycleForecast.model_construct(
            direction="up", probability=0.7, cycle_stage="early", phase_position=1.5
        ),
    )
    report = validate_stage2(forged, stage1=stage1)
    assert report.is_valid is False
    assert any(e.field == "next_cycle_forecast.phase_position" for e in report.errors)


def test_a1_stage2_validator_accepts_valid_next_cycle() -> None:
    """合法 next_cycle_forecast 通过校验，无错误。"""
    stage1 = _valid_stage1()
    result = _valid_stage2(stage1)
    report = validate_stage2(result, stage1=stage1)
    assert report.is_valid is True
    assert not any("next_cycle_forecast" in e.field for e in report.errors)


# ---------------------------------------------------------------------------
# N1 — LLM 数值占位符强转（吸收 TA agents/schemas.py:_coerce_optional_float）
# ---------------------------------------------------------------------------


class TestTradeSetupNumericCoercion:
    """LLM 在吃不准时会把占位符/人类格式写进数值字段。契约必须容忍，否则
    一个 "N/A" 就让整个 Stage2Decision 校验失败、重试耗尽、分析报废。

    测试走 model_validate(LLM JSON 的真实入口)，不是 TradeSetup(...=) 关键字
    构造 —— 生产路径就是 model_validate(parsed)，且 str 入参正是要点。
    """

    @staticmethod
    def _ts(**raw) -> TradeSetup:
        return TradeSetup.model_validate(raw)

    @pytest.mark.parametrize(
        "placeholder", ["N/A", "n/a", "None", "null", "-", "", "tbd", "unknown"]
    )
    def test_placeholder_becomes_none(self, placeholder: str) -> None:
        ts = self._ts(entry_price=placeholder, stop_loss_price=placeholder)
        assert ts.entry_price is None
        assert ts.stop_loss_price is None

    def test_human_formatted_price_is_parsed(self) -> None:
        assert self._ts(entry_price="$1,234.50").entry_price == 1234.5

    def test_percentage_where_price_expected_is_dropped(self) -> None:
        """`15%` 不能当 15 —— 会把 $15 的止损放在 $600 的股票上，只能丢弃。"""
        assert self._ts(stop_loss_price="15%").stop_loss_price is None

    def test_range_or_hedge_text_is_dropped(self) -> None:
        """`150-160` / `around 150` 不是单个数 → 丢弃该字段，不 fail 整个契约。"""
        assert self._ts(entry_price="150-160").entry_price is None
        assert self._ts(entry_price="around 150").entry_price is None

    def test_numeric_string_still_parses(self) -> None:
        assert self._ts(entry_price="189.5").entry_price == 189.5

    def test_real_numbers_pass_through(self) -> None:
        ts = self._ts(entry_price=100.0, take_profit_price_2=110.5)
        assert ts.entry_price == 100.0
        assert ts.take_profit_price_2 == 110.5

    def test_one_bad_field_does_not_discard_the_rest(self) -> None:
        """最关键的性质: bad field 置 None，同对象里正确的字段全保留。"""
        ts = self._ts(
            direction="long",
            entry_price="N/A",
            stop_loss_price=95.0,
            take_profit_price=110.0,
        )
        assert ts.entry_price is None
        assert ts.stop_loss_price == 95.0
        assert ts.take_profit_price == 110.0
        assert ts.direction == "long"

    def test_bounded_field_still_enforces_bounds(self) -> None:
        """强转不能让有界字段松弛 —— risk_reward_ratio 仍须 ge=0。"""
        from pydantic import ValidationError as _VE

        with pytest.raises(_VE):
            self._ts(risk_reward_ratio=-1.0)
