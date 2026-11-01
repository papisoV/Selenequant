"""市场分析三层校验（json / consistency / semantic）。

分层逻辑参考 selene 自有 MIT decision-workflow validator.py 的模式，市场侧独立撰写。
一致性层保证 entry_gate / market_regime 自洽；跨阶段不可变字段锚定检测篡改。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from .models import (
    Evidence,
    MarketValidationReport,
    Stage1Diagnosis,
    Stage2Decision,
    ValidationErrorModel,
)

_VALID_ENTRY_GATES: frozenset[str] = frozenset({"proceed", "wait", "unknown"})
_VALID_REGIMES: frozenset[str] = frozenset(
    {"trend_up", "trend_down", "range", "spike"}
)
_VALID_DECISIONS: frozenset[str] = frozenset(
    {"limit", "breakout", "market", "no_trade"}
)

# kline_range 必须形如 K{n}-K{m} 且 n>=1（禁止 K0）。
_KLINE_RANGE_RE: re.Pattern[str] = re.compile(r"^K(\d+)-K(\d+)$")
_K_CITATION_RE: re.Pattern[str] = re.compile(r"K(\d+)")

# 语义层禁止的模板化理由 token。
_STATEMENT_BLOCKLIST: frozenset[str] = frozenset(
    {"是", "否", "通过", "结构清晰", "风险高", "不符合要求", "交易异常", "可疑行为"}
)


@dataclass(frozen=True)
class ValidatorConfig:
    """校验策略旋钮（由编排器注入，不由 LLM 决定）。

    ``proceed_requires_signals`` — entry_gate=proceed 时必须至少存在一个
    signal_pattern（一致性层）；默认开启。
    ``spike_gate_restriction``     — market_regime=spike 时禁止 entry_gate=proceed
    （尖峰混乱区间不应直接进场）；默认开启。
    ``trader_equation_required``   — decision != no_trade 时交易设置必须完整且
    通过交易者方程（RR≥min_risk_reward 且胜率 > 盈亏平衡 1/(1+RR)）；默认开启。
    ``min_risk_reward``            — 交易者方程的 RR 硬门槛。
    """

    proceed_requires_signals: bool = True
    spike_gate_restriction: bool = True
    trader_equation_required: bool = True
    min_risk_reward: float = 1.0


# ---------------------------------------------------------------------------
# Layer 1 — JSON（枚举 / 格式 / 条件）
# ---------------------------------------------------------------------------


def _validate_kline_range_format(
    kline_range: str | None,
    *,
    errors: list[ValidationErrorModel],
    field_name: str = "kline_range",
) -> None:
    """kline_range 必须匹配 ^K\\d+-K\\d+$ 且禁止 K0。"""
    if kline_range is None:
        return
    m = _KLINE_RANGE_RE.match(kline_range)
    if m is None:
        errors.append(
            ValidationErrorModel(
                layer="json",
                field=field_name,
                message=(
                    f"kline_range '{kline_range}' 不匹配要求格式 "
                    "'K{n}-K{m}'（如 'K1-K10'）"
                ),
                kline_range=kline_range,
            )
        )
        return
    lo, hi = int(m.group(1)), int(m.group(2))
    if lo == 0 or hi == 0:
        errors.append(
            ValidationErrorModel(
                layer="json",
                field=field_name,
                message=(
                    f"kline_range '{kline_range}' 禁止 K0（K 线下标从 1 起）"
                ),
                kline_range=kline_range,
            )
        )


def _validate_stage1_json(
    result: Stage1Diagnosis, errors: list[ValidationErrorModel]
) -> None:
    """Layer 1 — Stage 1 枚举/格式检查。"""
    if result.market_regime not in _VALID_REGIMES:
        errors.append(
            ValidationErrorModel(
                layer="json",
                field="market_regime",
                message=(
                    f"market_regime '{result.market_regime}' 不在 "
                    f"{sorted(_VALID_REGIMES)}"
                ),
            )
        )
    if result.entry_gate not in _VALID_ENTRY_GATES:
        errors.append(
            ValidationErrorModel(
                layer="json",
                field="entry_gate",
                message=(
                    f"entry_gate '{result.entry_gate}' 不在 "
                    f"{sorted(_VALID_ENTRY_GATES)}"
                ),
            )
        )
    for sp in result.signal_patterns:
        _validate_kline_range_format(sp.kline_range, errors=errors)
    for ev in result.evidence:
        _validate_kline_range_format(ev.kline_range, errors=errors)


def _validate_stage2_json(
    result: Stage2Decision, errors: list[ValidationErrorModel]
) -> None:
    """Layer 1 — Stage 2 枚举/格式检查。"""
    if result.decision not in _VALID_DECISIONS:
        errors.append(
            ValidationErrorModel(
                layer="json",
                field="decision",
                message=(
                    f"decision '{result.decision}' 不在 {sorted(_VALID_DECISIONS)}"
                ),
            )
        )
    for ev in result.evidence:
        _validate_kline_range_format(ev.kline_range, errors=errors)
    if result.next_bar_forecast is not None:
        f = result.next_bar_forecast
        if not (0.0 <= f.probability <= 1.0):
            errors.append(
                ValidationErrorModel(
                    layer="json",
                    field="next_bar_forecast.probability",
                    message=f"probability {f.probability} 必须在 [0,1]",
                )
            )
    if result.next_cycle_forecast is not None:
        c = result.next_cycle_forecast
        if not (0.0 <= c.probability <= 1.0):
            errors.append(
                ValidationErrorModel(
                    layer="json",
                    field="next_cycle_forecast.probability",
                    message=f"probability {c.probability} 必须在 [0,1]",
                )
            )
        if not (0.0 <= c.phase_position <= 1.0):
            errors.append(
                ValidationErrorModel(
                    layer="json",
                    field="next_cycle_forecast.phase_position",
                    message=f"phase_position {c.phase_position} 必须在 [0,1]",
                )
            )


# ---------------------------------------------------------------------------
# Layer 2 — Consistency（自洽 / 跨阶段）
# ---------------------------------------------------------------------------


def _validate_stage1_consistency(
    result: Stage1Diagnosis,
    config: ValidatorConfig,
    errors: list[ValidationErrorModel],
) -> None:
    """entry_gate=proceed 需要至少一个 signal_pattern；spike 禁 proceed。"""
    if result.entry_gate == "proceed":
        if config.proceed_requires_signals and not result.signal_patterns:
            errors.append(
                ValidationErrorModel(
                    layer="consistency",
                    field="signal_patterns",
                    message=(
                        "entry_gate='proceed' 但 signal_patterns 为空"
                        "（proceed 需要至少一个信号形态）"
                    ),
                )
            )
    if config.spike_gate_restriction and result.market_regime == "spike":
        if result.entry_gate == "proceed":
            errors.append(
                ValidationErrorModel(
                    layer="consistency",
                    field="entry_gate",
                    message=(
                        "market_regime='spike'（尖峰混乱区间）不应 "
                        "entry_gate='proceed'"
                    ),
                )
            )


def _validate_stage2_consistency(
    result: Stage2Decision,
    stage1: Stage1Diagnosis,
    errors: list[ValidationErrorModel],
) -> None:
    """跨阶段不可变字段锚定：diagnosis_summary 不得被 Stage 2 篡改。"""
    carried = result.decision_summary.get("diagnosis_summary")
    if carried != stage1.diagnosis_summary:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="diagnosis_summary",
                cheat_detected=True,
                message=(
                    "Stage 2 decision_summary['diagnosis_summary'] 与 Stage 1 "
                    "不一致（不可变字段疑似被篡改）"
                ),
            )
        )


def _validate_trade_setup_consistency(
    result: Stage2Decision,
    config: ValidatorConfig,
    errors: list[ValidationErrorModel],
) -> None:
    """交易者方程（G2，自研）：decision != no_trade 时交易设置必须完整且过方程。

    T1 完整性 — propose 交易必须有 trade_setup 且 entry/SL/TP 非空。
    T2 价格方向 — long 需 stop_loss < entry < take_profit；short 反向。
    T3 RR 硬门槛 — RR 由价格确定性重算（防 LLM 虚报），须 ≥ config.min_risk_reward，
       且与声明的 risk_reward_ratio 偏差不超过 0.5。
    T4 交易者方程 — 期望值 win_rate > 1/(1+RR)（盈亏平衡）。
    T5 声称交叉校验 — 声明 trader_equation_passed=true 但 T3/T4 实际未过 → 报错。
    """
    if result.decision == "no_trade":
        return  # 不下单无需交易设置
    if not config.trader_equation_required:
        return

    ts = result.trade_setup
    if ts is None:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="trade_setup",
                message=(
                    f"decision='{result.decision}' 但 trade_setup 为空"
                    "（propose 交易必须给出完整交易设置）"
                ),
            )
        )
        return
    if (
        ts.entry_price is None
        or ts.stop_loss_price is None
        or ts.take_profit_price is None
    ):
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="trade_setup",
                message=(
                    "trade_setup 不完整：entry_price/stop_loss_price/"
                    "take_profit_price 不能为空"
                ),
            )
        )
        return

    entry, sl, tp = ts.entry_price, ts.stop_loss_price, ts.take_profit_price
    if ts.direction == "long":
        if not (sl < entry < tp):
            errors.append(
                ValidationErrorModel(
                    layer="consistency",
                    field="trade_setup",
                    message=(
                        f"long 价格顺序错误：需 stop_loss < entry < take_profit"
                        f"（sl={sl} entry={entry} tp={tp}）"
                    ),
                )
            )
    elif ts.direction == "short":
        if not (tp < entry < sl):
            errors.append(
                ValidationErrorModel(
                    layer="consistency",
                    field="trade_setup",
                    message=(
                        f"short 价格顺序错误：需 take_profit < entry < stop_loss"
                        f"（tp={tp} entry={entry} sl={sl}）"
                    ),
                )
            )
    else:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="trade_setup.direction",
                message=(
                    "propose 交易但 trade_setup.direction 为空（需 long/short）"
                ),
            )
        )
        return

    risk = abs(entry - sl)
    reward = abs(tp - entry)
    if risk == 0:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="trade_setup.stop_loss_price",
                message="stop_loss_price 与 entry_price 相同（risk=0，无法算 RR）",
            )
        )
        return
    rr_price = reward / risk
    equation_ok = True

    if rr_price < config.min_risk_reward:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="trade_setup.risk_reward_ratio",
                message=(
                    f"风险回报比 {rr_price:.2f} < 最小 {config.min_risk_reward}"
                    "（须提高目标价或收紧止损）"
                ),
            )
        )
        equation_ok = False
    if (
        ts.risk_reward_ratio is not None
        and abs(ts.risk_reward_ratio - rr_price) > 0.5
    ):
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="trade_setup.risk_reward_ratio",
                message=(
                    f"声明的 RR {ts.risk_reward_ratio} 与价格重算 {rr_price:.2f} "
                    "偏差过大（防虚报）"
                ),
            )
        )
        equation_ok = False

    breakeven = 1.0 / (1.0 + rr_price)
    if ts.estimated_win_rate < breakeven:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="trade_setup.trader_equation_passed",
                message=(
                    f"交易者方程未通过：win_rate {ts.estimated_win_rate:.3f} < "
                    f"盈亏平衡 {breakeven:.3f}（RR={rr_price:.2f}）"
                ),
            )
        )
        equation_ok = False

    if ts.trader_equation_passed and not equation_ok:
        errors.append(
            ValidationErrorModel(
                layer="consistency",
                field="trade_setup.trader_equation_passed",
                message=(
                    "声明 trader_equation_passed=true 但风险回报比/交易者方程"
                    "实际未通过（声称与事实不符）"
                ),
            )
        )


# ---------------------------------------------------------------------------
# Layer 3 — Semantic（非重试，按 §7.2 语义层）
# ---------------------------------------------------------------------------


def _check_statement_not_boilerplate(
    ev: Evidence, errors: list[ValidationErrorModel]
) -> None:
    """statement 不得为模板化 token。"""
    if ev.statement in _STATEMENT_BLOCKLIST:
        errors.append(
            ValidationErrorModel(
                layer="semantic",
                field="evidence.statement",
                message=f"statement='{ev.statement}' 是模板化理由",
                kline_range=ev.kline_range,
            )
        )


def _check_statement_cites_within_range(
    ev: Evidence, errors: list[ValidationErrorModel]
) -> None:
    """statement 中所有 K 引用必须在 kline_range 内。"""
    if not ev.kline_range:
        return
    m = _KLINE_RANGE_RE.match(ev.kline_range)
    if m is None:
        return  # 格式错误已由 Layer 1 报告
    lo, hi = int(m.group(1)), int(m.group(2))
    for cited in (int(x) for x in _K_CITATION_RE.findall(ev.statement)):
        if cited < lo or cited > hi:
            errors.append(
                ValidationErrorModel(
                    layer="semantic",
                    field="evidence.statement",
                    message=(
                        f"statement 引用 K{cited} 超出 kline_range "
                        f"'{ev.kline_range}'"
                    ),
                    kline_range=ev.kline_range,
                )
            )
            return


def _validate_semantic(
    evidence: list[Evidence], errors: list[ValidationErrorModel]
) -> None:
    for ev in evidence:
        _check_statement_not_boilerplate(ev, errors)
        _check_statement_cites_within_range(ev, errors)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def validate_stage1(
    result: Stage1Diagnosis,
    *,
    config: ValidatorConfig | None = None,
) -> MarketValidationReport:
    """对 Stage 1 输出运行全部 3 层校验。"""
    cfg = config or ValidatorConfig()
    errors: list[ValidationErrorModel] = []
    _validate_stage1_json(result, errors)
    _validate_stage1_consistency(result, cfg, errors)
    _validate_semantic(result.evidence, errors)
    return _assemble_report(errors)


def validate_stage2(
    result: Stage2Decision,
    *,
    stage1: Stage1Diagnosis,
    config: ValidatorConfig | None = None,
) -> MarketValidationReport:
    """对 Stage 2 输出运行全部 3 层校验（含跨阶段锚定 + 交易者方程）。"""
    cfg = config or ValidatorConfig()
    errors: list[ValidationErrorModel] = []
    _validate_stage2_json(result, errors)
    _validate_stage2_consistency(result, stage1, errors)
    _validate_trade_setup_consistency(result, cfg, errors)
    _validate_semantic(result.evidence, errors)
    return _assemble_report(errors)


def _assemble_report(errors: list[ValidationErrorModel]) -> MarketValidationReport:
    cheat_fields = tuple(sorted({e.field for e in errors if e.cheat_detected}))
    return MarketValidationReport(
        errors=tuple(errors),
        is_valid=(len(errors) == 0),
        cheat_fields=cheat_fields,
    )


__all__ = [
    "ValidatorConfig",
    "validate_stage1",
    "validate_stage2",
]
