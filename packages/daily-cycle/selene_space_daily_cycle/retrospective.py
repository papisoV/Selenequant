# Absorbed from daily_stock_analysis (MIT License)
# Copyright (c) 2026 ZhuLinsen
# Source: https://github.com/ZhuLinsen/daily_stock_analysis
# Adapted for SeleneQuant (AGPL-3.0-only); DSA aggregated a ContextVar-held trace
# of provider/LLM/notification/history records for one report request. Selene's
# run is a *phase* pipeline, so the records are re-bound to WorkflowResult +
# PhaseOutcome (the existing run model) plus optional caller-supplied
# provider/LLM records. Pure function domain: no IO, no clock, stdlib only.

"""Run retrospective: turn one daily-cycle run into an auditable summary.

A *run* is one ``DailyTradingCycleWorkflow.run(trading_date)`` execution. The
record unit is the ``PhaseOutcome`` it already emits, and the trace identity is
the ``WorkflowResult.workflow_id`` it already mints. Nothing new is invented on
the write side -- this module only *reads* a finished run and reports on it.

Design contract (see ``DESIGN_RUN_RETROSPECTIVE.md``):

- **Zero IO, no clock.** Everything is derived from the ``WorkflowResult`` the
  caller already has; durations come from the phases' own timestamps. This keeps
  the module deterministic and trivially testable.
- **Fail-open.** :func:`build_run_retrospective` never raises. If anything goes
  wrong it logs and degrades to a minimal ``unknown`` retrospective.
- **Partial-degradation, not all-or-nothing.** A skipped or failed phase, a
  fallback provider, a failed LLM call -- each contributes a *limitation* line
  and moves the rollup to ``degraded``/``failed``; it never hides the phases
  that did succeed. Same philosophy as the stock-profile partial-data contract.
- **Redaction is injected, not vendored.** Text sanitisation reuses
  ``selene_space_security.redaction.redact_text`` (resolved lazily so importing
  this package stays cheap) with a tiny regex fallback if that package is absent.

Pipeline: ``build_run_retrospective(result)`` -> :class:`RunRetrospective`;
``build_run_flow(retrospective)`` -> node/edge graph for a UI.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Iterable, Literal, cast

from selene_space_daily_cycle.types import (
    PhaseOutcome,
    WorkflowPhase,
    WorkflowResult,
    WorkflowStatus,
)

logger = logging.getLogger(__name__)

__all__ = [
    "STATUS_LABELS",
    "LLMRunRecord",
    "PhaseSpan",
    "ProviderRunRecord",
    "RunRetrospective",
    "RunStatus",
    "build_run_flow",
    "build_run_retrospective",
]


RunStatus = Literal["normal", "degraded", "failed", "unknown"]

#: UI labels for the four rollup statuses (machine value -> display form).
STATUS_LABELS: dict[str, str] = {
    "normal": "正常",
    "degraded": "降级",
    "failed": "失败",
    "unknown": "未知",
}

_MAX_ERROR_LEN = 300

# Canonical phase order, as declared by WorkflowPhase -- the flow spine.
_PHASE_ORDER: tuple[WorkflowPhase, ...] = tuple(WorkflowPhase)


# ---------------------------------------------------------------------------
# Redaction -- lazy reuse of security-gateway, minimal fallback
# ---------------------------------------------------------------------------


_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_ASSIGN_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|token|secret|password)"
    r"\s*[:=]\s*[^\s,&;]+"
)


def _fallback_redact(text: str) -> str:
    """Minimal credential scrub used only when security-gateway is unavailable."""
    text = _BEARER_RE.sub("Bearer <redacted>", text)
    return _ASSIGN_RE.sub(lambda match: f"{match.group(1)}=<redacted>", text)


def _default_redact() -> Callable[[str], str]:
    """Resolve the shared redactor lazily; fall back to the local scrub."""
    try:
        from selene_space_security.redaction import redact_text
    except Exception as exc:  # noqa: BLE001 - absence is expected in slim envs
        logger.debug("security-gateway redactor unavailable (%s); using fallback", exc)
        return _fallback_redact
    return cast(Callable[[str], str], redact_text)


def _clean(
    text: Any, redact: Callable[[str], str], *, limit: int = _MAX_ERROR_LEN
) -> str | None:
    """Collapse whitespace, redact, and truncate one free-form string."""
    if text is None:
        return None
    collapsed = " ".join(str(text).split())
    if not collapsed:
        return None
    cleaned = redact(collapsed)
    if len(cleaned) > limit:
        return f"{cleaned[:limit].rstrip()}..."
    return cleaned


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderRunRecord:
    """One data-source attempt within a run (caller-supplied, optional)."""

    provider: str
    data_type: str
    operation: str
    success: bool
    latency_ms: int | None = None
    error: str | None = None
    fallback_from: str | None = None
    fallback_to: str | None = None
    cache_hit: bool | None = None
    record_count: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "provider": self.provider,
                "data_type": self.data_type,
                "operation": self.operation,
                "success": self.success,
                "latency_ms": self.latency_ms,
                "error": self.error,
                "fallback_from": self.fallback_from,
                "fallback_to": self.fallback_to,
                "cache_hit": self.cache_hit,
                "record_count": self.record_count,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class LLMRunRecord:
    """One LLM call within a run (caller-supplied, optional)."""

    call_type: str
    success: bool
    provider: str | None = None
    model: str | None = None
    tokens: int | None = None
    duration_ms: int | None = None
    fallback_model: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "call_type": self.call_type,
                "success": self.success,
                "provider": self.provider,
                "model": self.model,
                "tokens": self.tokens,
                "duration_ms": self.duration_ms,
                "fallback_model": self.fallback_model,
                "error": self.error,
            }.items()
            if value is not None
        }


@dataclass(frozen=True)
class PhaseSpan:
    """One phase's contribution to the run timeline.

    Only metric/artifact **keys** are carried -- values may hold credentials or
    large payloads and are deliberately not copied into the retrospective.
    """

    phase: str
    status: str
    disposition: str
    duration_ms: int | None = None
    metric_keys: tuple[str, ...] = ()
    artifact_keys: tuple[str, ...] = ()
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            key: value
            for key, value in {
                "phase": self.phase,
                "status": self.status,
                "disposition": self.disposition,
                "duration_ms": self.duration_ms,
                "metric_keys": list(self.metric_keys),
                "artifact_keys": list(self.artifact_keys),
                "error": self.error,
            }.items()
            if value not in (None, [], ())
        }


@dataclass(frozen=True)
class RunRetrospective:
    """User-facing diagnostic summary for one daily-cycle run."""

    trace_id: str
    status: RunStatus
    status_label: str
    reason: str
    trading_date: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    duration_ms: int | None = None
    phases: tuple[PhaseSpan, ...] = ()
    provider_runs: tuple[ProviderRunRecord, ...] = ()
    llm_runs: tuple[LLMRunRecord, ...] = ()
    limitations: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "status": self.status,
            "status_label": self.status_label,
            "reason": self.reason,
            "trading_date": self.trading_date,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "phases": [span.to_dict() for span in self.phases],
            "provider_runs": [run.to_dict() for run in self.provider_runs],
            "llm_runs": [run.to_dict() for run in self.llm_runs],
            "limitations": list(self.limitations),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _enum_value(value: Any) -> str:
    """String form of a status enum (or a plain value) without raising."""
    return str(getattr(value, "value", value))


def _ms_between(started: Any, ended: Any) -> int | None:
    """Milliseconds between two datetimes, or None when either is unusable."""
    if not isinstance(started, datetime) or not isinstance(ended, datetime):
        return None
    delta = ended - started
    return max(0, int(delta.total_seconds() * 1000))


def _iso(value: Any) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


def _keys(mapping: Any) -> tuple[str, ...]:
    if not isinstance(mapping, dict):
        return ()
    return tuple(str(key) for key in mapping)


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def _phase_span(outcome: PhaseOutcome, redact: Callable[[str], str]) -> PhaseSpan:
    return PhaseSpan(
        phase=_enum_value(outcome.phase),
        status=_enum_value(outcome.status),
        disposition=str(outcome.disposition),
        duration_ms=_ms_between(outcome.started_at, outcome.ended_at),
        metric_keys=_keys(outcome.metrics),
        artifact_keys=_keys(outcome.artifacts),
        error=_clean(outcome.error, redact),
    )


def _sanitize_provider(
    run: ProviderRunRecord, redact: Callable[[str], str]
) -> ProviderRunRecord:
    return ProviderRunRecord(
        provider=run.provider,
        data_type=run.data_type,
        operation=run.operation,
        success=run.success,
        latency_ms=run.latency_ms,
        error=_clean(run.error, redact),
        fallback_from=run.fallback_from,
        fallback_to=run.fallback_to,
        cache_hit=run.cache_hit,
        record_count=run.record_count,
    )


def _sanitize_llm(run: LLMRunRecord, redact: Callable[[str], str]) -> LLMRunRecord:
    return LLMRunRecord(
        call_type=run.call_type,
        success=run.success,
        provider=run.provider,
        model=run.model,
        tokens=run.tokens,
        duration_ms=run.duration_ms,
        fallback_model=run.fallback_model,
        error=_clean(run.error, redact),
    )


def _rollup_status(workflow_status: str, spans: tuple[PhaseSpan, ...]) -> RunStatus:
    """Map the run + its phases onto one of the four rollup statuses.

    A phase that was SKIPPED degrades the run even when the workflow itself
    reports SUCCESS -- ``daily_cycle`` finalises to SUCCESS whenever every phase
    is SUCCESS *or SKIPPED*, so skips are otherwise invisible.
    """
    if not spans:
        return "unknown"
    phase_statuses = {span.status for span in spans}
    if workflow_status == WorkflowStatus.FAILED.value or "failed" in phase_statuses:
        return "failed"
    if (
        workflow_status == WorkflowStatus.HALTED.value
        or "halted" in phase_statuses
        or "skipped" in phase_statuses
    ):
        return "degraded"
    return "normal"


def _collect_limitations(
    spans: tuple[PhaseSpan, ...],
    providers: tuple[ProviderRunRecord, ...],
    llms: tuple[LLMRunRecord, ...],
) -> tuple[str, ...]:
    """Ordered, de-duplicated degradation notes."""
    notes: list[str] = []
    for span in spans:
        if span.status == WorkflowStatus.SKIPPED.value:
            notes.append(f"阶段 {span.phase} 被跳过")
        elif span.status == WorkflowStatus.FAILED.value:
            notes.append(f"阶段 {span.phase} 失败")
    for provider_run in providers:
        if not provider_run.success:
            notes.append(
                f"数据源 {provider_run.provider} 调用失败（{provider_run.data_type}）"
            )
        elif provider_run.fallback_to:
            notes.append(
                f"数据源 {provider_run.provider} 回退至 {provider_run.fallback_to}"
            )
    for llm_run in llms:
        if not llm_run.success:
            notes.append(f"LLM 调用失败（{llm_run.call_type}）")
        elif llm_run.fallback_model:
            notes.append(f"LLM 回退至 {llm_run.fallback_model}")
    return tuple(dict.fromkeys(notes))


def _reason(status: RunStatus, spans: tuple[PhaseSpan, ...]) -> str:
    if status == "unknown":
        return "运行未产生任何阶段记录"
    if status == "failed":
        failed = [s.phase for s in spans if s.status == WorkflowStatus.FAILED.value]
        return f"运行失败：{'、'.join(failed)}" if failed else "运行最终状态为失败"
    if status == "degraded":
        degraded = [
            s.phase
            for s in spans
            if s.status in (WorkflowStatus.SKIPPED.value, WorkflowStatus.HALTED.value)
        ]
        if degraded:
            return f"运行降级：{'、'.join(degraded)} 未正常完成"
        return "运行中途停止（halted）"
    return "所有阶段正常完成"


def build_run_retrospective(
    result: WorkflowResult,
    *,
    provider_runs: Iterable[ProviderRunRecord] = (),
    llm_runs: Iterable[LLMRunRecord] = (),
    redact: Callable[[str], str] | None = None,
) -> RunRetrospective:
    """Build a fail-open retrospective for one finished daily-cycle run.

    Args:
        result: the ``WorkflowResult`` returned by ``DailyTradingCycleWorkflow``.
        provider_runs: optional caller-supplied data-source attempts.
        llm_runs: optional caller-supplied LLM call records.
        redact: optional text redactor; defaults to the shared
            ``selene_space_security.redaction.redact_text``.

    Returns:
        A :class:`RunRetrospective`. Never raises -- on unexpected failure it
        logs a warning and returns a minimal ``unknown`` retrospective.
    """
    redactor = redact or _default_redact()
    try:
        return _build(result, provider_runs, llm_runs, redactor)
    except Exception as exc:  # noqa: BLE001 - fail-open by contract
        logger.warning("run retrospective failed: %s", exc)
        return _fallback(result, redactor)


def _build(
    result: WorkflowResult,
    provider_runs: Iterable[ProviderRunRecord],
    llm_runs: Iterable[LLMRunRecord],
    redact: Callable[[str], str],
) -> RunRetrospective:
    spans = tuple(_phase_span(outcome, redact) for outcome in result.phases)
    providers = tuple(_sanitize_provider(run, redact) for run in provider_runs)
    llms = tuple(_sanitize_llm(run, redact) for run in llm_runs)

    workflow_status = _enum_value(result.status)
    status = _rollup_status(workflow_status, spans)
    limitations = _collect_limitations(spans, providers, llms)

    return RunRetrospective(
        trace_id=result.workflow_id,
        status=status,
        status_label=STATUS_LABELS[status],
        reason=_reason(status, spans),
        trading_date=result.trading_date.isoformat(),
        started_at=_iso(result.started_at),
        ended_at=_iso(result.ended_at),
        duration_ms=_ms_between(result.started_at, result.ended_at),
        phases=spans,
        provider_runs=providers,
        llm_runs=llms,
        limitations=limitations,
    )


def _fallback(result: WorkflowResult, redact: Callable[[str], str]) -> RunRetrospective:
    """Minimal retrospective when the full build blew up."""
    trace_id = str(getattr(result, "workflow_id", "") or "unknown")
    return RunRetrospective(
        trace_id=trace_id,
        status="unknown",
        status_label=STATUS_LABELS["unknown"],
        reason=_clean("复盘构建失败", redact) or "复盘构建失败",
    )


# ---------------------------------------------------------------------------
# Flow rendering
# ---------------------------------------------------------------------------


def build_run_flow(retrospective: RunRetrospective) -> dict[str, Any]:
    """Render a retrospective as a node/edge graph along the phase spine.

    Nodes are the phases plus any provider/LLM records, ordered by the canonical
    ``WorkflowPhase`` sequence; edges link consecutive phases. Nodes after an
    ``halt_urgent`` phase are dropped from the spine, mirroring the executor.
    """
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    by_phase = {span.phase: span for span in retrospective.phases}

    spine: list[str] = []
    for phase in _PHASE_ORDER:
        span = by_phase.get(phase.value)
        if span is None:
            continue
        node_id = f"phase:{span.phase}"
        spine.append(node_id)
        nodes.append(
            {
                "id": node_id,
                "kind": "phase",
                "label": span.phase,
                "status": span.status,
                "severity": _severity(span),
                "duration_ms": span.duration_ms,
                "message": span.error,
            }
        )
        if span.disposition == "halt_urgent":
            break

    for earlier, later in zip(spine, spine[1:]):
        edges.append({"from": earlier, "to": later})

    for index, provider_run in enumerate(retrospective.provider_runs):
        nodes.append(
            {
                "id": f"provider:{index}",
                "kind": "provider",
                "label": provider_run.provider,
                "status": "success" if provider_run.success else "failed",
                "severity": "info" if provider_run.success else "error",
                "duration_ms": provider_run.latency_ms,
                "message": provider_run.error,
            }
        )

    for index, llm_run in enumerate(retrospective.llm_runs):
        nodes.append(
            {
                "id": f"llm:{index}",
                "kind": "llm",
                "label": llm_run.model or llm_run.call_type,
                "status": "success" if llm_run.success else "failed",
                "severity": "info" if llm_run.success else "error",
                "duration_ms": llm_run.duration_ms,
                "message": llm_run.error,
            }
        )

    return {
        "trace_id": retrospective.trace_id,
        "status": retrospective.status,
        "status_label": retrospective.status_label,
        "nodes": nodes,
        "edges": edges,
        "limitations": list(retrospective.limitations),
    }


def _severity(span: PhaseSpan) -> str:
    if span.status == WorkflowStatus.FAILED.value:
        return "error"
    if span.status in (
        WorkflowStatus.SKIPPED.value,
        WorkflowStatus.HALTED.value,
    ):
        return "warning"
    return "info"
