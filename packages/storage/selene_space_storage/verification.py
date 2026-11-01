"""ATR 自适应到期验证判定器(纯函数,不拉数据)。

规格:internal-plan §一.2/§三。
阈值随品种波动率缩放——BTC 日线 3% 是噪音,稳定币对 0.5% 已是巨震;
固定百分比门槛会把低波品种大量误判。本模块只做数字到裁决的映射,
行情数字(ATR%、出入场价)由调用方喂入。
"""

from __future__ import annotations

_COST_FLOOR_PCT = 0.2
_CEILING_PCT = 15.0
_K_DIRECTION = 1.0
_K_HOLD = 0.75

_HOLD_TOKENS = {"hold", "neutral", "wait"}


def effective_threshold(atr_pct: float, direction: str) -> float:
    """k × ATR%,带成本下限与极端波动上限的双重钳制。

    方向单用 k=1.0(|收益|须超过一个 ATR% 才算方向对了);HOLD 用
    k=0.75(压在一个略窄的带内才算"等"对了)。
    """
    k = _K_HOLD if direction.lower() in _HOLD_TOKENS else _K_DIRECTION
    raw = k * float(atr_pct)
    return min(max(raw, _COST_FLOOR_PCT), _CEILING_PCT)


def judge(
    direction: str,
    *,
    entry: float,
    exit_price: float,
    threshold_pct: float,
) -> bool:
    """单条分析的对错裁决。

    - long:出场 > 入场且涨幅过阈 → 对;short 镜像。
    - hold:|涨跌| 压在阈值内 → 对(市场确实没走出方向)。
    """
    token = direction.lower()
    change_pct = (exit_price - entry) / entry * 100.0
    if token in _HOLD_TOKENS:
        return abs(change_pct) <= threshold_pct
    if token in ("long", "buy"):
        return change_pct >= threshold_pct
    if token in ("short", "sell"):
        return -change_pct >= threshold_pct
    raise ValueError(f"unknown direction: {direction!r}")
