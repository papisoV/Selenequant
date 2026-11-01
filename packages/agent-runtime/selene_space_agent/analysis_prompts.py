"""Server-side analysis prompt builder for the trading agent.

Each intent maps to a Chinese-language prompt template. Payload values are
rendered with plain f-strings only — JS-style template syntax must never
leak into the output (frontend patterns are explicitly tested against).
"""

from __future__ import annotations

from typing import Any

INTENT_MULTI_TIMEFRAME = "multi_timeframe"
INTENT_TIMEFRAME_TRADE = "timeframe_trade"
INTENT_CLOSE_EVALUATION = "close_evaluation"
INTENT_BACKTEST_EXPLAIN = "backtest_explain"
INTENT_KLINE_PRICE_ACTION = "kline_price_action"

VALID_INTENTS = (
    INTENT_MULTI_TIMEFRAME,
    INTENT_TIMEFRAME_TRADE,
    INTENT_CLOSE_EVALUATION,
    INTENT_BACKTEST_EXPLAIN,
    INTENT_KLINE_PRICE_ACTION,
)

_TIMEFRAME_LABELS = {
    "1m": "1分钟",
    "5m": "5分钟",
    "15m": "15分钟",
    "30m": "30分钟",
    "1h": "1小时",
    "4h": "4小时",
    "1d": "日线",
}

_METRIC_LABELS = {
    "total_return": "总收益率",
    "annual_return": "年化收益率",
    "sharpe_ratio": "夏普比率",
    "max_drawdown": "最大回撤",
    "win_rate": "胜率",
    "profit_factor": "盈亏比",
    "total_trades": "总交易次数",
    "calmar_ratio": "卡玛比率",
    "sortino_ratio": "索提诺比率",
    "expectancy": "期望收益",
    "recovery_factor": "恢复因子",
}


def _fmt(value: Any) -> str:
    if value is None:
        return "未知"
    return str(value)


def _fmt_price(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return _fmt(value)


def _timeframe_cn(tf: Any) -> str:
    key = str(tf or "")
    return _TIMEFRAME_LABELS.get(key, key or "未知")


def _position_side_cn(pos: dict[str, Any]) -> str:
    side = str(pos.get("pos_side", "")).lower()
    if side == "long":
        return "多单"
    if side == "short":
        return "空单"
    return "未知方向"


def _normalize_positions(positions: Any) -> list[dict[str, Any]]:
    if not positions:
        return []
    normalized: list[dict[str, Any]] = []
    for pos in positions:
        if isinstance(pos, dict):
            normalized.append(pos)
            continue
        dump = getattr(pos, "model_dump", None)
        if callable(dump):
            try:
                converted = dump(mode="json")
            except TypeError:
                converted = dump()
            if isinstance(converted, dict):
                normalized.append(converted)
    return normalized


def _classify_positions(positions: Any) -> tuple[str, list[dict[str, Any]]]:
    """把 positions 载荷分成三态,返回 ``(state, normalized)``。

    三态必须彼此区分(照 open-source-reference ``trading_graph.py:157-158`` 的铁律
    "None, an empty book and a changed book are three different runs"):

    - ``"unavailable"`` —— key 缺失或为 ``None``:上游**没提供**数据。载荷非空
      但规范化后为空(畸形输入)同样归此,不谎报"空仓"。
    - ``"empty"`` —— 显式 ``[]``:已确认**没有**持仓。
    - ``"present"`` —— 至少一条持仓。

    旧实现用 ``if positions:`` 把 None 与 ``[]`` 压成同一句"没有检测到持仓";
    若上游是"读取失败",模型会被告知一个未经证实的结论。
    """
    if positions is None:
        return "unavailable", []
    normalized = _normalize_positions(positions)
    if normalized:
        return "present", normalized
    try:
        genuinely_empty = len(positions) == 0
    except TypeError:
        genuinely_empty = False
    return ("empty" if genuinely_empty else "unavailable"), []


def _render_positions_unavailable() -> list[str]:
    """持仓读取失败时的诚实提示:区分"拿不到"与"确实空仓"。"""
    return [
        "",
        "持仓数据不可用(未能取到持仓,并非确认空仓)。",
        "请勿假设用户当前持有的仓位;若你的建议依赖仓位,请明确说明该前提缺失。",
    ]


def _render_positions(positions: list[dict[str, Any]]) -> list[str]:
    lines = ["", "## 用户当前持仓状态", ""]
    for pos in positions:
        side = _position_side_cn(pos)
        symbol = pos.get("symbol") or pos.get("contract_symbol") or "未知"
        lines.append(f"- {side} {symbol}")
        leverage = pos.get("leverage")
        if leverage is not None:
            lines.append(f"  - 杠杆: {leverage}x")
        margin = pos.get("margin")
        if margin is not None:
            lines.append(f"  - 保证金: {_fmt(margin)}")
        entry = pos.get("avg_entry_price")
        if entry is not None:
            lines.append(f"  - 开仓均价: {_fmt_price(entry)}")
        quantity = pos.get("quantity_base")
        if quantity is not None:
            lines.append(f"  - 持仓数量: {_fmt(quantity)}")
        upnl = pos.get("unrealized_pnl")
        if upnl is not None:
            lines.append(f"  - 未实现盈亏: {_fmt(upnl)}")
    lines.append("")
    return lines


def _header(payload: dict[str, Any], title: str) -> list[str]:
    symbol = payload.get("symbol") or "BTC/USDT"
    exchange = payload.get("exchange") or "未知"
    market_type = payload.get("market_type") or "未知"
    mark_price = _fmt(payload.get("mark_price"))
    contract = payload.get("contract_symbol")
    lines = [
        f"# {title}",
        "",
        f"- 交易对: {symbol}",
        f"- 交易所: {exchange}",
        f"- 市场类型: {market_type}",
        f"- 标记价格: {mark_price}",
    ]
    if contract:
        lines.append(f"- 合约: {contract}")
    return lines


def _build_multi_timeframe(payload: dict[str, Any]) -> str:
    timeframe_a = _timeframe_cn("15m")
    timeframe_b = _timeframe_cn("4h")
    lines = _header(payload, "多周期综合分析")
    lines.extend(
        [
            "",
            f"请结合 {timeframe_a} 与 {timeframe_b} 两个周期进行综合分析：",
            "1. 高周期判断趋势方向与关键位。",
            "2. 低周期寻找具体入场时机。",
            "3. 给出方向、入场区域、止损与目标位，并说明理由。",
        ]
    )
    state, positions = _classify_positions(payload.get("positions"))
    if state == "present":
        lines.extend(_render_positions(positions))
        lines.append("请将上述分析与你当前的持仓结合起来给出操作建议。")
    elif state == "empty":
        lines.append("")
        lines.append("当前没有检测到持仓，请从观望或轻仓试错的角度给建议。")
    else:
        lines.extend(_render_positions_unavailable())
    return "\n".join(lines)


def _build_timeframe_trade(payload: dict[str, Any]) -> str:
    tf_raw = payload.get("timeframe") or "未知"
    lines = _header(payload, f"{tf_raw} 周期交易分析")
    lines.extend(
        [
            "",
            f"请基于 {tf_raw} 周期的行情结构，给出具体的交易计划：",
            "- 入场点位（明确价格区间）",
            "- 止损位置与依据",
            "- 目标位与仓位建议",
        ]
    )
    state, positions = _classify_positions(payload.get("positions"))
    if state == "present":
        lines.extend(_render_positions(positions))
        lines.append("结合我的持仓情况调整上述计划。")
    elif state == "empty":
        lines.append("")
        lines.append("当前没有检测到持仓，仓位建议从空仓建仓的角度给出。")
    else:
        lines.extend(_render_positions_unavailable())
    return "\n".join(lines)


def _build_close_evaluation(payload: dict[str, Any]) -> str:
    lines = _header(payload, "平仓评估")
    state, positions = _classify_positions(payload.get("positions"))
    if state == "present":
        for pos in positions:
            side = _position_side_cn(pos)
            lines.extend(
                [
                    "",
                    f"当前持有{side}，请评估是否应该平仓：",
                    "1. 从趋势、关键位与盈亏比角度做风险评估。",
                    "2. 给出继续持有 / 部分平仓 / 全部平仓 的建议。",
                    "3. 如果建议持有，说明需要关注的失效条件。",
                ]
            )
        lines.extend(_render_positions(positions))
    elif state == "empty":
        base = (payload.get("symbol") or "").split("/")[0] or "未知"
        lines.extend(
            [
                "",
                f"没有找到活跃持仓。请针对 {base} 给出入场建议：",
                "1. 当前是否适合建仓。",
                "2. 若适合，给出入场点位、止损与目标。",
            ]
        )
    else:
        lines.extend(_render_positions_unavailable())
    return "\n".join(lines)


def _build_backtest_explain(payload: dict[str, Any]) -> str:
    name = payload.get("strategy_name") or "未知策略"
    backtest_id = payload.get("backtest_id") or "未知"
    strategy_id = payload.get("strategy_id") or "未知"
    symbol = payload.get("symbol") or "未知"
    timeframe = payload.get("timeframe") or "未知"

    lines = [
        f"# 回测结果解读 — {name}",
        "",
        f"- 回测 ID: {backtest_id}",
        f"- 策略 ID: {strategy_id}",
        f"- 交易对: {symbol}",
        f"- 周期: {timeframe}",
        "",
        "## 回测指标",
        "",
    ]
    metrics = payload.get("metrics") or {}
    for key, value in metrics.items():
        label = _METRIC_LABELS.get(key, key)
        lines.append(f"- {label}: {_fmt(value)}")

    lines.extend(
        [
            "",
            "请基于以上指标回答：",
            "1. 这份回测结果的主要优势与隐患分别是什么？",
            "2. 下一步应该优先优化什么？（参数、过滤条件还是风控）",
            "3. 有哪些过度拟合的风险信号需要注意？",
        ]
    )
    return "\n".join(lines)


def _build_kline_price_action(payload: dict[str, Any]) -> str:
    tf_raw = payload.get("timeframe") or "未知"
    mark_price = _fmt(payload.get("mark_price"))
    kline_data = payload.get("kline_data")

    lines = [
        "# K 线形态分析",
        "",
        f"- 交易对: {payload.get('symbol') or '未知'}",
        f"- 交易所: {payload.get('exchange') or '未知'}",
        f"- 市场类型: {payload.get('market_type') or '未知'}",
        f"- 周期: {tf_raw}",
        f"- 标记价格: {mark_price}",
        "",
    ]

    if kline_data:
        lines.extend(
            [
                "## 近期 K 线数据（时间,开,高,低,收,量）",
                "",
                str(kline_data),
                "",
                "请仔细阅读上面的 K 线数据，识别当前的价格结构与形态，然后回答：",
            ]
        )
    else:
        lines.extend(["无 K 线数据，请基于标记价格与已知信息谨慎判断。", ""])

    lines.extend(
        [
            "请严格按以下 JSON 结构输出（不要包含其他文字）：",
            "{",
            '  "direction": "long | short | neutral",',
            '  "entry": 价格或 null,',
            '  "stop_loss": 价格或 null,',
            '  "take_profit": 价格或 null,',
            '  "confidence": 0到1的小数,',
            '  "reasoning": "简要中文理由"',
            "}",
        ]
    )
    return "\n".join(lines)


_BUILDERS = {
    INTENT_MULTI_TIMEFRAME: _build_multi_timeframe,
    INTENT_TIMEFRAME_TRADE: _build_timeframe_trade,
    INTENT_CLOSE_EVALUATION: _build_close_evaluation,
    INTENT_BACKTEST_EXPLAIN: _build_backtest_explain,
    INTENT_KLINE_PRICE_ACTION: _build_kline_price_action,
}


def build_analysis_prompt(payload: dict[str, Any]) -> str | None:
    """Render one analysis prompt, or ``None`` for unknown intents."""
    intent = payload.get("intent")
    builder = _BUILDERS.get(intent)
    if builder is None:
        return None
    return builder(payload)
