"""Evidence report composer — 把一次实验固化成证据报告。

研究版本控制("Git for Quant Research")的展示层:从存储的 experiment 字段
组合出 data_source / config / version / result / robustness / risk / conclusion,
并带上血缘链(ancestors + children)供前端 blood-line 可视化。
纯函数,无 IO,便于单测。
"""

from __future__ import annotations

from typing import Any


def _fmt_pct(value: Any) -> str | None:
    if isinstance(value, (int, float)) and value == value:  # 排除 NaN
        return f"{value * 100:.1f}%"
    return None


def _risk_per_trade(setup: dict | None) -> dict | None:
    """每次入场承担的绝对风险:long = entry - SL;short = SL - entry。"""
    if not setup:
        return None
    entry = setup.get("entry")
    stop_loss = setup.get("stop_loss")
    direction = setup.get("direction")
    if entry is None or stop_loss is None or direction not in ("long", "short"):
        return None
    risk = entry - stop_loss if direction == "long" else stop_loss - entry
    if not isinstance(risk, (int, float)):
        return None
    return {"direction": direction, "entry": entry, "stop_loss": stop_loss, "amount": round(float(risk), 6)}


def _robustness_notes(result: dict | None) -> list[str]:
    """从回测结果派生的稳健性观察;无回测时返回空列表。"""
    if not result:
        return []
    notes: list[str] = []
    total = result.get("total_setups")
    if isinstance(total, (int, float)):
        if total < 10:
            notes.append(f"样本仅 {int(total)} 个,统计置信度低")
        elif total < 30:
            notes.append(f"样本 {int(total)} 个,结论需谨慎")
        else:
            notes.append(f"样本 {int(total)} 个,统计基础可用")
    wr = result.get("win_rate")
    if isinstance(wr, (int, float)):
        notes.append(f"胜率 {_fmt_pct(wr)}")
    pf = result.get("profit_factor")
    if isinstance(pf, (int, float)):
        notes.append(f"利润因子 {pf:.2f}(>1 有正期望)")
    return notes


def _conclusion_text(exp: dict) -> str:
    """基于存储字段生成的结论文本;回测缺失时降级为形态快照描述。"""
    name = exp.get("name") or "未命名实验"
    symbol = exp.get("symbol") or "—"
    timeframe = exp.get("timeframe") or "—"
    setup = exp.get("setup_params") or {}
    direction = setup.get("direction") or "—"
    result = exp.get("backtest_result") or {}
    total = result.get("total_setups")
    wr = result.get("win_rate")
    expectancy = result.get("expectancy")

    if total is None:
        return f"{name}:{symbol} {timeframe} 已登记 {direction} 形态快照,尚未跑 setup 回测,结论待补。"

    parts = [f"{name}:{symbol} {timeframe} {direction} 形态,{int(total)} 个样本"]
    if isinstance(wr, (int, float)):
        parts.append(f"胜率 {_fmt_pct(wr)}")
    if isinstance(expectancy, (int, float)):
        parts.append(f"单笔期望 {expectancy:+.3f}")
    verdict = "正期望,可考虑 fork 深化" if isinstance(expectancy, (int, float)) and expectancy > 0 else "未证实正期望,谨慎对待"
    parts.append(verdict)
    return "；".join(parts)


def compose_evidence_report(
    exp: dict,
    ancestors: list[dict],
    children: list[dict],
) -> dict[str, Any]:
    """组合证据报告。exp 为完整实验 dict;ancestors 由父到根;children 为直接子实验。"""
    setup = exp.get("setup_params") or {}
    result = exp.get("backtest_result") or {}
    return {
        "experiment": {
            "id": exp.get("id"),
            "name": exp.get("name"),
            "hypothesis": exp.get("hypothesis"),
            "symbol": exp.get("symbol"),
            "timeframe": exp.get("timeframe"),
            "strategy_version": exp.get("strategy_version"),
            "created_at": exp.get("created_at"),
        },
        "data_source": {
            "dataset": exp.get("dataset"),
            "symbol": exp.get("symbol"),
            "timeframe": exp.get("timeframe"),
        },
        "config": {
            "strategy_version": exp.get("strategy_version"),
            "setup_params": setup,
        },
        "version": {
            "id": exp.get("id"),
            "created_at": exp.get("created_at"),
            "strategy_version": exp.get("strategy_version"),
            "parent_experiment": exp.get("parent_experiment"),
        },
        "result": {
            "total_setups": result.get("total_setups"),
            "win_rate": result.get("win_rate"),
            "avg_win": result.get("avg_win"),
            "avg_loss": result.get("avg_loss"),
            "expectancy": result.get("expectancy"),
            "profit_factor": result.get("profit_factor"),
            "max_drawdown": result.get("max_drawdown"),
        },
        "robustness": {
            "has_backtest": bool(result),
            "notes": _robustness_notes(result),
        },
        "risk": {
            "max_drawdown": result.get("max_drawdown"),
            "risk_per_trade": _risk_per_trade(exp.get("setup_params")),
        },
        "conclusion": _conclusion_text(exp),
        "lineage": {
            "parent": exp.get("parent_experiment"),
            "ancestors": [
                {"id": a.get("id"), "name": a.get("name"), "created_at": a.get("created_at")}
                for a in ancestors
            ],
            "children": [
                {"id": c.get("id"), "name": c.get("name"), "created_at": c.get("created_at")}
                for c in children
            ],
        },
    }
