"""市场分析两阶段契约（Stage1 诊断 → entry_gate 短路 → Stage2 决策）。

Pydantic 模型为 LLM 边界契约（.model_validate + 枚举收窄）。
命名按 PROVENANCE.md 隔离表映射；不引入参考实现逐字字段名。
决策树概念不进入市场契约，用扁平 evidence[]。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

# LLM 在吃不准时把占位符写进数值字段，而不是省略该键（吸收 open-source-reference
# agents/schemas.py 的 _NULLISH_FLOAT，#1058）。这些都读作"未给"。
_NULLISH_FLOAT = {"", "none", "n/a", "na", "null", "nil", "-", "tbd", "unknown"}


def _coerce_optional_float(value: Any) -> Any:
    """在校验前归一化 LLM 写出的可选数值字段（吸收 TA _coerce_optional_float）。

    实践中出现三种形态:
    - 占位符字符串（"None"/"N/A"）代替省略 → None；
    - 问到价格却给了百分比（"15%"）→ **丢弃**（读成 15 会把 $15 的止损放在
      $600 的股票上，不可救，只能在未给处留空）；
    - 人类格式价格（"$1,234.50"）→ 去掉符号与千分位后取数。

    任何不是单个数值的输入（区间 "150-160"、模糊 "around 150"）同样丢弃。
    关键性质:**让一个坏字段 null 掉，而不是让整个 decision 校验失败、连同
    模型答对的其余字段一起报废**。非字符串原样透传（交由 pydantic 正常校验）。
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if text.lower() in _NULLISH_FLOAT or text.endswith("%"):
        return None
    cleaned = text.replace(",", "").lstrip("$€£¥").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Stage 1 — 行情诊断
# ---------------------------------------------------------------------------


class SignalPattern(BaseModel):
    """一个被识别出的行情信号形态。"""

    name: str
    bias: Literal["bullish", "bearish", "neutral"]
    confidence: float = Field(ge=0.0, le=1.0)
    kline_range: str | None = Field(
        None,
        description=(
            "K 线序号区间，必须用 'K{n}-K{m}' 形式（如 'K1-K60'），下标从 1 起且 n<m，"
            "禁止 K0；不得用单个 K 值、时间戳或空字符串"
        ),
        pattern=r"^K\d+-K\d+$",
    )


class Evidence(BaseModel):
    """一条扁平化诊断依据（无决策树概念）。"""

    kind: str
    key: str
    statement: str
    kline_range: str | None = Field(
        None,
        description=(
            "K 线序号区间，必须用 'K{n}-K{m}' 形式（如 'K1-K60'），下标从 1 起且 n<m，"
            "禁止 K0；不得用单个 K 值、时间戳或空字符串"
        ),
        pattern=r"^K\d+-K\d+$",
    )


class Stage1Diagnosis(BaseModel):
    """Stage 1（行情诊断）结构化输出。"""

    market_regime: Literal["trend_up", "trend_down", "range", "spike"]
    entry_gate: Literal["proceed", "wait", "unknown"]
    signal_patterns: list[SignalPattern] = Field(default_factory=list)
    trend_context: dict[str, Any] = Field(default_factory=dict)
    bar_features: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    diagnosis_summary: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Stage 2 — 交易决策
# ---------------------------------------------------------------------------


class NextBarForecast(BaseModel):
    """下一根 K 线预测（MVP 仅后端落盘，不下发前端）。"""

    direction: Literal["up", "down", "neutral"]
    probability: float = Field(ge=0.0, le=1.0)
    note: str | None = None


class NextCycleForecast(BaseModel):
    """下一周期预测（MVP 仅后端落盘，不下发前端）。

    位置子字段采用自研命名 ``cycle_stage`` / ``phase_position``，
    命名隔离登记见包内 PROVENANCE.md（位置子字段不引入参考实现命名）。
    """

    direction: Literal["up", "down", "neutral"]
    probability: float = Field(ge=0.0, le=1.0)
    cycle_stage: Literal["early", "middle", "late", "exhaustion"]
    phase_position: float = Field(ge=0.0, le=1.0)
    note: str | None = None


class TradeSetup(BaseModel):
    """完整交易设置（仅 decision != no_trade 时输出；no_trade/wait 时置 None）。

    命名隔离见 PROVENANCE.md：confidence = 参考实现 trade_confidence（0-100 → 自研 0-1）；
    decision_path = 参考实现 decision_trace（扁平列表，不引入决策树概念）；
    entry/SL/TP 为通用交易术语（领域常识）。风险回报比由校验器从价格重算
    （防 LLM 虚报），此字段仅作展示。
    """

    direction: Literal["long", "short"] | None = None
    entry_price: float | None = None
    stop_loss_price: float | None = None
    take_profit_price: float | None = None
    take_profit_price_2: float | None = None
    risk_reward_ratio: float | None = Field(None, ge=0.0)
    estimated_win_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    trader_equation_passed: bool = False
    decision_path: list[str] = Field(default_factory=list)

    @field_validator(
        "entry_price",
        "stop_loss_price",
        "take_profit_price",
        "take_profit_price_2",
        "risk_reward_ratio",
        mode="before",
    )
    @classmethod
    def _coerce_numeric_field(cls, v: Any) -> Any:
        """读入前清掉占位符/人类格式；有界字段的 ge/le 仍在校验里生效。"""
        return _coerce_optional_float(v)


class Stage2Decision(BaseModel):
    """Stage 2（交易决策）结构化输出。"""

    decision: Literal["limit", "breakout", "market", "no_trade"]
    dispatch_playbooks: list[str] = Field(default_factory=list)
    decision_summary: dict[str, Any] = Field(default_factory=dict)
    evidence: list[Evidence] = Field(default_factory=list)
    next_bar_forecast: NextBarForecast | None = None
    next_cycle_forecast: NextCycleForecast | None = None
    trade_setup: TradeSetup | None = None


# ---------------------------------------------------------------------------
# 校验契约
# ---------------------------------------------------------------------------


class ValidationErrorModel(BaseModel):
    """一条校验错误（json / consistency / semantic 层）。"""

    layer: Literal["json", "consistency", "semantic"]
    field: str
    message: str
    kline_range: str | None = None
    cheat_detected: bool = False


@dataclass(frozen=True)
class MarketValidationReport:
    """市场校验报告。"""

    errors: tuple[ValidationErrorModel, ...]
    is_valid: bool
    cheat_fields: tuple[str, ...]
