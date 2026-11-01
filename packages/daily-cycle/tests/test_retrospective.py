"""Behaviour locks for the run retrospective (📋7 absorption, batch B5).

These port the *semantics* -- not the fixtures -- of DSA's run-diagnostics
summary (`build_run_diagnostic_summary` / `sanitize_diagnostic_text`) onto
Selene's existing ``WorkflowResult`` / ``PhaseOutcome`` run model. The
load-bearing cases:

  - the four-value rollup (normal / degraded / failed / unknown) maps onto
    Selene's ``WorkflowStatus`` correctly, *including* the skip case where the
    workflow finalises to SUCCESS but a phase was SKIPPED;
  - the build is fail-open: nothing raises, and a broken result degrades to
    ``unknown`` rather than propagating;
  - free-form text (phase errors, provider errors) is redacted.
"""

from __future__ import annotations

import ast
from datetime import date, datetime, timedelta
from pathlib import Path

from selene_space_daily_cycle import (
    LLMRunRecord,
    PhaseOutcome,
    ProviderRunRecord,
    RunStatus,
    WorkflowPhase,
    WorkflowResult,
    WorkflowStatus,
    build_run_flow,
    build_run_retrospective,
)

_MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "selene_space_daily_cycle"
    / "retrospective.py"
)


def _span(
    phase: WorkflowPhase,
    *,
    status: WorkflowStatus = WorkflowStatus.SUCCESS,
    disposition: str = "continue",
    start: datetime | None = None,
    seconds: float = 1.0,
    metrics: dict | None = None,
    artifacts: dict | None = None,
    error: str | None = None,
) -> PhaseOutcome:
    started = start or datetime(2026, 10, 11, 9, 0, 0)
    return PhaseOutcome(
        phase=phase,
        status=status,
        disposition=disposition,  # type: ignore[arg-type]
        started_at=started,
        ended_at=started + timedelta(seconds=seconds),
        metrics=metrics or {},
        artifacts=artifacts or {},
        error=error,
    )


def _result(
    phases: list[PhaseOutcome],
    *,
    status: WorkflowStatus = WorkflowStatus.SUCCESS,
    halted_at: WorkflowPhase | None = None,
) -> WorkflowResult:
    start = datetime(2026, 10, 11, 9, 0, 0)
    return WorkflowResult(
        workflow_id="daily_cycle_2026-10-11_ab12cd34",
        trading_date=date(2026, 10, 11),
        phases=phases,
        started_at=start,
        ended_at=start + timedelta(seconds=5),
        status=status,
        halted_at=halted_at,
    )


# --- rollup status ----------------------------------------------------------


def test_all_success_is_normal():
    result = _result(
        [
            _span(WorkflowPhase.PRE_MARKET),
            _span(WorkflowPhase.RISK_REVIEW),
        ]
    )
    retro = build_run_retrospective(result)
    assert retro.status == "normal"
    assert retro.status_label == "正常"
    assert retro.reason == "所有阶段正常完成"


def test_failed_phase_rolls_up_to_failed():
    result = _result(
        [
            _span(WorkflowPhase.PRE_MARKET),
            _span(
                WorkflowPhase.RISK_REVIEW,
                status=WorkflowStatus.FAILED,
                disposition="halt_urgent",
                error="provider exploded",
            ),
        ],
        status=WorkflowStatus.HALTED,
        halted_at=WorkflowPhase.RISK_REVIEW,
    )
    retro = build_run_retrospective(result)
    assert retro.status == "failed"
    assert retro.status_label == "失败"
    assert "risk_review" in retro.reason


def test_skipped_phase_rolls_up_to_degraded_even_if_workflow_says_success():
    # daily_cycle finalises to SUCCESS when every phase is SUCCESS *or SKIPPED*.
    result = _result(
        [
            _span(WorkflowPhase.PRE_MARKET),
            _span(
                WorkflowPhase.RISK_REVIEW,
                status=WorkflowStatus.SKIPPED,
                disposition="skip_remaining",
            ),
        ],
        status=WorkflowStatus.SUCCESS,
    )
    retro = build_run_retrospective(result)
    assert retro.status == "degraded"
    assert "阶段 risk_review 被跳过" in retro.limitations


def test_halted_workflow_without_failed_phase_is_degraded():
    result = _result(
        [_span(WorkflowPhase.EXECUTION, disposition="halt_graceful")],
        status=WorkflowStatus.HALTED,
        halted_at=WorkflowPhase.EXECUTION,
    )
    retro = build_run_retrospective(result)
    assert retro.status == "degraded"
    assert retro.status_label == "降级"


def test_empty_run_is_unknown():
    result = _result([], status=WorkflowStatus.PENDING)
    retro = build_run_retrospective(result)
    assert retro.status == "unknown"
    assert retro.status_label == "未知"


# --- phase spans ------------------------------------------------------------


def test_phase_span_carries_duration_and_keys_only():
    result = _result(
        [
            _span(
                WorkflowPhase.PRE_MARKET,
                seconds=2.5,
                metrics={"n_signals": 3, "secret_token": "x"},
                artifacts={"pre_market_report": object()},
            )
        ]
    )
    retro = build_run_retrospective(result)
    span = retro.phases[0]
    assert span.duration_ms == 2500
    assert set(span.metric_keys) == {"n_signals", "secret_token"}
    assert span.artifact_keys == ("pre_market_report",)
    # Values are never copied -- only keys.
    dumped = retro.to_dict()
    assert "n_signals" in dumped["phases"][0]["metric_keys"]


# --- optional records -------------------------------------------------------


def test_provider_records_contribute_limitations():
    result = _result([_span(WorkflowPhase.PRE_MARKET)])
    retro = build_run_retrospective(
        result,
        provider_runs=[
            ProviderRunRecord(
                provider="akshare",
                data_type="daily_data",
                operation="fetch",
                success=False,
                error="boom",
            ),
            ProviderRunRecord(
                provider="eastmoney",
                data_type="realtime_quote",
                operation="fetch",
                success=True,
                fallback_to="sina",
            ),
        ],
    )
    assert "数据源 akshare 调用失败（daily_data）" in retro.limitations
    assert "数据源 eastmoney 回退至 sina" in retro.limitations
    # A failed *provider* (not phase) only degrades the limitations list; the
    # phase spine is intact, so the rollup stays normal.
    assert retro.status == "normal"


def test_llm_fallback_contributes_limitation():
    result = _result([_span(WorkflowPhase.IC_DECISION)])
    retro = build_run_retrospective(
        result,
        llm_runs=[
            LLMRunRecord(
                call_type="analysis",
                success=True,
                model="glm",
                fallback_model="haiku",
            )
        ],
    )
    assert "LLM 回退至 haiku" in retro.limitations


def test_limitations_are_deduplicated_in_order():
    result = _result([_span(WorkflowPhase.PRE_MARKET)])
    dup = ProviderRunRecord(
        provider="x", data_type="d", operation="o", success=False, error="e"
    )
    retro = build_run_retrospective(result, provider_runs=[dup, dup])
    assert retro.limitations.count("数据源 x 调用失败（d）") == 1


# --- redaction --------------------------------------------------------------


def test_phase_error_is_redacted():
    result = _result(
        [
            _span(
                WorkflowPhase.RISK_REVIEW,
                status=WorkflowStatus.FAILED,
                disposition="halt_urgent",
                error="auth failed: api_key=sk-abcdef1234567890xyz",
            )
        ],
        status=WorkflowStatus.FAILED,
    )
    retro = build_run_retrospective(result)
    error = retro.phases[0].error
    assert error is not None
    assert "sk-abcdef1234567890xyz" not in error


def test_default_redactor_is_the_shared_security_gateway_one():
    """The default path must reuse security-gateway, not a private copy."""
    from selene_space_daily_cycle.retrospective import _default_redact

    redactor = _default_redact()
    assert redactor.__module__ == "selene_space_security.redaction"
    assert redactor.__qualname__ == "redact_text"


def test_injected_redactor_is_used():
    calls: list[str] = []

    def spy(text: str) -> str:
        calls.append(text)
        return "<scrubbed>"

    result = _result(
        [
            _span(
                WorkflowPhase.RISK_REVIEW,
                status=WorkflowStatus.FAILED,
                disposition="halt_urgent",
                error="something leaked",
            )
        ],
        status=WorkflowStatus.FAILED,
    )
    retro = build_run_retrospective(result, redact=spy)
    assert retro.phases[0].error == "<scrubbed>"
    assert "something leaked" in calls


# --- fail-open --------------------------------------------------------------


def test_build_is_fail_open_on_broken_result():
    class Broken:
        workflow_id = "broken"
        phases = None  # iterating this raises

    retro = build_run_retrospective(Broken())  # type: ignore[arg-type]
    assert retro.status == "unknown"
    assert retro.trace_id == "broken"


# --- flow -------------------------------------------------------------------


def test_flow_has_phase_spine_in_canonical_order():
    result = _result(
        [
            _span(WorkflowPhase.PRE_MARKET),
            _span(WorkflowPhase.RISK_REVIEW),
            _span(WorkflowPhase.IC_DECISION),
        ]
    )
    flow = build_run_flow(build_run_retrospective(result))
    phase_nodes = [n for n in flow["nodes"] if n["kind"] == "phase"]
    assert [n["id"] for n in phase_nodes] == [
        "phase:pre_market",
        "phase:risk_review",
        "phase:ic_decision",
    ]
    assert {"from": "phase:pre_market", "to": "phase:risk_review"} in flow["edges"]


def test_flow_truncates_spine_after_halt_urgent():
    result = _result(
        [
            _span(WorkflowPhase.PRE_MARKET),
            _span(
                WorkflowPhase.RISK_REVIEW,
                status=WorkflowStatus.FAILED,
                disposition="halt_urgent",
            ),
            # A phase after the halt should not appear on the spine.
            _span(WorkflowPhase.IC_DECISION),
        ],
        status=WorkflowStatus.HALTED,
    )
    flow = build_run_flow(build_run_retrospective(result))
    phase_ids = [n["id"] for n in flow["nodes"] if n["kind"] == "phase"]
    assert phase_ids == ["phase:pre_market", "phase:risk_review"]


def test_flow_node_severity():
    result = _result([_span(WorkflowPhase.PRE_MARKET)])
    flow = build_run_flow(build_run_retrospective(result))
    node = flow["nodes"][0]
    assert node["severity"] == "info"
    assert node["status"] == "success"


# --- module contract: no IO -------------------------------------------------


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_module_has_no_forbidden_io_imports():
    """Pure-function contract: no network / db / filesystem at import time."""
    forbidden = ("httpx", "requests", "sqlite3", "aiosqlite", "aiohttp", "socket")
    imported = _imported_modules(_MODULE_PATH)
    for module in imported:
        top = module.split(".")[0]
        assert top not in forbidden, f"forbidden import: {module}"


def test_status_literal_matches_rollup_outputs():
    result = _result([_span(WorkflowPhase.PRE_MARKET)])
    retro = build_run_retrospective(result)
    assert retro.status in ("normal", "degraded", "failed", "unknown")
    # RunStatus is the Literal backing the field -- keep them in sync.
    assert set(RunStatus.__args__) == {"normal", "degraded", "failed", "unknown"}
