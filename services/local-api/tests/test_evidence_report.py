"""Tests for evidence_report composer (纯函数,无 IO)。"""

from __future__ import annotations

from app.core.evidence_report import (
    _conclusion_text,
    _risk_per_trade,
    _robustness_notes,
    compose_evidence_report,
)


def _experiment(**overrides) -> dict:
    exp = {
        "id": "exp-abc123",
        "name": "BTC 1h 双顶形态",
        "hypothesis": "关键阻力位双顶 → 跌破颈线放空有正期望",
        "symbol": "BTC/USDT",
        "timeframe": "1h",
        "dataset": "klines.db 2026-01-01~2026-07-01",
        "strategy_version": "pa-decision-v1",
        "setup_params": {
            "direction": "short",
            "entry": 65000.0,
            "stop_loss": 66000.0,
            "take_profit": 61000.0,
        },
        "backtest_result": {
            "total_setups": 12,
            "win_rate": 0.58,
            "expectancy": 0.42,
            "profit_factor": 1.35,
            "max_drawdown": 0.08,
        },
        "evidence_refs": ["conclusion-3"],
        "parent_experiment": None,
        "created_at": "2026-08-23 10:00:00",
    }
    exp.update(overrides)
    return exp


def test_compose_contains_all_sections():
    exp = _experiment()
    report = compose_evidence_report(exp, [], [])
    assert set(report.keys()) == {
        "experiment",
        "data_source",
        "config",
        "version",
        "result",
        "robustness",
        "risk",
        "conclusion",
        "lineage",
    }
    # experiment 节携带核心标识字段
    assert report["experiment"]["id"] == "exp-abc123"
    assert report["experiment"]["name"] == "BTC 1h 双顶形态"
    # data_source / config / version 组合自存储字段
    assert report["data_source"]["dataset"] == "klines.db 2026-01-01~2026-07-01"
    assert report["config"]["setup_params"]["direction"] == "short"
    assert report["version"]["parent_experiment"] is None
    # result 携带回测指标
    assert report["result"]["win_rate"] == 0.58
    assert report["result"]["total_setups"] == 12


def test_compose_risk_per_trade_short():
    exp = _experiment()  # short: entry 65000, SL 66000 → risk 1000
    report = compose_evidence_report(exp, [], [])
    assert report["risk"]["risk_per_trade"] == {
        "direction": "short",
        "entry": 65000.0,
        "stop_loss": 66000.0,
        "amount": 1000.0,
    }


def test_compose_risk_per_trade_long():
    exp = _experiment(
        setup_params={"direction": "long", "entry": 65000.0, "stop_loss": 64000.0, "take_profit": 68000.0}
    )
    report = compose_evidence_report(exp, [], [])
    assert report["risk"]["risk_per_trade"]["amount"] == 1000.0


def test_compose_risk_none_when_incomplete_setup():
    exp = _experiment(setup_params={"direction": "long", "entry": 65000.0})  # 缺 stop_loss
    report = compose_evidence_report(exp, [], [])
    assert report["risk"]["risk_per_trade"] is None


def test_compose_robustness_notes():
    exp = _experiment()
    report = compose_evidence_report(exp, [], [])
    notes = report["robustness"]["notes"]
    assert report["robustness"]["has_backtest"] is True
    assert any("样本 12 个" in n for n in notes)
    assert any("胜率 58.0%" in n for n in notes)
    assert any("利润因子 1.35" in n for n in notes)


def test_compose_no_backtest_fallback():
    exp = _experiment(backtest_result=None)
    report = compose_evidence_report(exp, [], [])
    assert report["robustness"]["has_backtest"] is False
    assert report["robustness"]["notes"] == []
    assert report["result"]["total_setups"] is None
    assert "尚未跑 setup 回测" in report["conclusion"]


def test_compose_lineage_ancestors_and_children():
    exp = _experiment(id="exp-leaf", parent_experiment="exp-mid")
    ancestors = [
        {"id": "exp-mid", "name": "mid", "created_at": "2026-08-23 09:00:00"},
        {"id": "exp-root", "name": "root", "created_at": "2026-08-23 08:00:00"},
    ]
    children = [
        {"id": "exp-child", "name": "child", "created_at": "2026-08-23 11:00:00"},
    ]
    report = compose_evidence_report(exp, ancestors, children)
    lineage = report["lineage"]
    assert lineage["parent"] == "exp-mid"
    assert [a["id"] for a in lineage["ancestors"]] == ["exp-mid", "exp-root"]
    assert [c["id"] for c in lineage["children"]] == ["exp-child"]


def test_conclusion_text_positive_expectancy():
    exp = _experiment()
    text = _conclusion_text(exp)
    assert "正期望" in text
    assert "12 个样本" in text


def test_conclusion_text_negative_expectancy():
    exp = _experiment(backtest_result={"total_setups": 12, "expectancy": -0.2})
    assert "未证实正期望" in _conclusion_text(exp)


def test_robustness_notes_low_sample():
    notes = _robustness_notes({"total_setups": 5})
    assert any("置信度低" in n for n in notes)


def test_risk_per_trade_unknown_direction_none():
    assert _risk_per_trade({"direction": "flat", "entry": 1, "stop_loss": 2}) is None
    assert _risk_per_trade(None) is None
