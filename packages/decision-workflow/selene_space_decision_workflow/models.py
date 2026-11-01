"""Pydantic + dataclass schemas for the two-stage decision workflow. See docs/DECISION_WORKFLOW_METHODOLOGY.md §2.4."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# A. Pydantic BaseModel — LLM boundary contracts (.model_validate + enum coercion)
# ---------------------------------------------------------------------------


class NodeAnswer(BaseModel):
    """One node's answer in the LLM trace (methodology §2.4 line 309)."""

    node_id: str
    question: str
    answer: str
    bar_range: str | None
    reason: str
    branch: str
    program_prefill_nodes: list[str] = []


class NodeOverride(BaseModel):
    """Manual override applied to a node answer post-LLM."""

    node_id: str
    new_answer: str
    reason: str
    bar_range: str | None


class Stage1Result(BaseModel):
    """Stage 1 (diagnosis) structured output."""

    state: str
    gate_result: Literal["proceed", "wait", "unknown"]
    trace: list[NodeAnswer]
    diagnosis_summary: dict[str, Any]
    node_overrides: list[NodeOverride] = []


class Stage2Result(BaseModel):
    """Stage 2 (decision) structured output."""

    decision: str
    terminal_outcome: Literal["trade", "wait", "reject", "proceed"]
    order: dict | None
    trace: list[NodeAnswer]
    decision_summary: dict[str, Any]
    node_overrides: list[NodeOverride] = []


class ValidationErrorModel(BaseModel):
    """One validation error entry (json / consistency / semantic layer)."""

    layer: Literal["json", "consistency", "semantic"]
    field: str
    message: str
    node_id: str | None
    cheat_detected: bool = False


class RecordMeta(BaseModel):
    """Metadata for an analysis record."""

    timestamp: str
    input_id: str
    session_id: str
    # Demo-mode marker. Two routes set it, and they differ in what the record
    # contains: SELENE_PA_DEMO_MODE (and no explicit demo request) routes to
    # the canned walk-through (no LLM, order stays None), while an explicit
    # ``demo=True`` runs the REAL pipeline and is merely kept out of the audit
    # table. Default False — real runs and direct canned analyze() records are
    # untouched, so the flag never lies.
    demo: bool = False


class StageRecord(BaseModel):
    """Raw LLM I/O for one stage (audit / replay)."""

    messages: list[dict]
    response: dict
    diagnosis_or_decision: dict
    trace: list[NodeAnswer]


class AnalysisRecord(BaseModel):
    """Full audit record for a single analysis run."""

    meta: RecordMeta
    input_data: dict[str, Any]
    stage1: StageRecord
    stage2: StageRecord | None
    strategy_files_used: list[str]
    usage_total: dict[str, int]
    # W2 审计链后挂键(T2a 回测验证/T3a outcome):Pydantic v2 默认丢弃未知
    # 顶层键——不声明这两个可选字段,append_payload 写进去读不出来。
    backtest_verification: dict[str, Any] | None = None
    outcome: dict[str, Any] | None = None
    # Phase D 锚定追问:按追加顺序排列的问答条目(question/answer/at/usage)。
    # 同上——不声明就会被 model_validate 静默丢弃。
    followups: list[dict[str, Any]] | None = None
    # Item 5 方向归属:程序拥有的方向调用(compute_direction_signal),由
    # orchestrator 构造点写入,覆盖端点/心跳/CLI 三条写入臂。同上的 Pydantic
    # v2 陷阱——不声明则 model_dump 静默丢掉该顶层键。
    direction_signal: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# B. @dataclass(frozen=True) — internal DTOs (graph + validation reports)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Branch:
    """One branch of a decision node.

    Exactly one of target_ref / terminal_outcome / tag should be non-None,
    except for the "none" branch kind where all three are None.
    """

    label: str
    target_ref: str | None
    terminal_outcome: str | None
    tag: str | None
    tag_action: str | None

    def __post_init__(self) -> None:
        non_none = sum(
            1
            for v in (self.target_ref, self.terminal_outcome, self.tag)
            if v is not None
        )
        # allow "none" branch (all three None) OR exactly one non-None
        if non_none > 1:
            raise ValueError(
                f"Branch '{self.label}' has {non_none} of target_ref/terminal_outcome/tag "
                f"set; at most one is allowed (or all None for 'none' kind)."
            )


@dataclass(frozen=True)
class DecisionTreeNode:
    """A single node in the decision tree."""

    node_id: str
    section: str
    question: str
    node_type: Literal["gate", "decision"]
    branch_type: Literal["binary", "ternary", "multi", "mark"]
    branches: tuple[Branch, ...]


@dataclass(frozen=True)
class DecisionTreeGraph:
    """The full decision tree graph."""

    nodes: dict[str, DecisionTreeNode]
    entry_node_id: str
    terminals: frozenset[str]


@dataclass(frozen=True)
class ValidationReport:
    """Result of validating an LLM stage output."""

    errors: tuple[ValidationErrorModel, ...]
    is_valid: bool
    cheat_fields: tuple[str, ...]
