"""US-303:信号数分级 —— 纯函数 + daily_cycle 响应标注行为锁。"""

from __future__ import annotations

import pytest

from app.core.signal_grading import DEFAULT_SCAN_THRESHOLD, signal_grading


# --- 纯函数 ------------------------------------------------------------------


def test_below_threshold_full_scan():
    assert signal_grading(19) == "full_scan"


def test_at_threshold_watchlist():
    assert signal_grading(20) == "watchlist"


def test_custom_threshold_boundary():
    assert signal_grading(9, scan_threshold=10) == "full_scan"
    assert signal_grading(10, scan_threshold=10) == "watchlist"


def test_default_threshold_is_twenty():
    assert DEFAULT_SCAN_THRESHOLD == 20


def test_negative_rejected():
    with pytest.raises(ValueError, match="non-negative"):
        signal_grading(-1)


# --- daily_cycle 响应标注 ------------------------------------------------------


def _phase_outcome(
    n_signals: int | None, phase_name: str = "pre_market", *, key: str = "n_signals"
):
    """构造 WorkflowResult.phases 形状的轻量替身,免起真工作流。"""
    from types import SimpleNamespace

    from selene_space_daily_cycle import WorkflowPhase

    metrics = {key: n_signals} if n_signals is not None else {}
    return SimpleNamespace(
        phase=WorkflowPhase(phase_name),
        status=SimpleNamespace(value="completed"),
        disposition=None,
        error=None,
        metrics=metrics,
        artifacts={},
    )


def _workflow_result(phase_outcomes):
    from datetime import date, datetime

    from types import SimpleNamespace

    return SimpleNamespace(
        workflow_id="wf-test",
        trading_date=date(2026, 9, 22),
        status=SimpleNamespace(value="completed"),
        halted_at=None,
        started_at=datetime(2026, 9, 22, 8, 0, 0),
        ended_at=datetime(2026, 9, 22, 15, 0, 0),
        phases=phase_outcomes,
    )


def test_response_annotates_signal_grade():
    """metrics 带 n_signals 的 phase → 响应附 signal_grade。"""
    from app.api.v1.daily_cycle import _result_to_response

    result = _workflow_result(
        [_phase_outcome(19, "pre_market"), _phase_outcome(45, "execution")]
    )
    resp = _result_to_response(result)
    grades = {p.name: p.metrics.get("signal_grade") for p in resp.phases}
    assert grades["pre_market"] == "full_scan"
    assert grades["execution"] == "watchlist"


def test_response_without_n_signals_untouched():
    """无 n_signals 的 phase → metrics 原样,不加 grade 字段。"""
    from app.api.v1.daily_cycle import _result_to_response

    result = _workflow_result([_phase_outcome(None)])
    resp = _result_to_response(result)
    assert "signal_grade" not in resp.phases[0].metrics


def test_signals_in_key_also_graded():
    """实战检验发现:相位实际发 signals_in(ic_decision/execution.py),
    n_signals 只是测试约定的键 —— 生产键必须同样触发分级。"""
    from app.api.v1.daily_cycle import _result_to_response

    result = _workflow_result([_phase_outcome(3, "ic_decision", key="signals_in")])
    resp = _result_to_response(result)
    assert resp.phases[0].metrics["signal_grade"] == "full_scan"
