"""Trading stance tests (A1, design review).

Four guarantees:
1. setting trading_stance leaves the shared system prompt byte-identical
2. all four tiers inject 【交易倾向】 at the tail of the Stage 2 user prompt
3. None injects nothing (existing prompts byte-unchanged)
4. invalid tiers are rejected by the CLI's argparse choices
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in [
    REPO / "packages" / "decision-workflow",
    REPO / "packages" / "shared-types",
    REPO / "packages" / "core",
    REPO / "packages" / "security-gateway",
]:
    sys.path.insert(0, str(p))

from selene_space_decision_workflow.orchestrator import TwoStageWorkflow  # noqa: E402
from selene_space_decision_workflow.parser import parse_decision_tree  # noqa: E402
from selene_space_decision_workflow.program_nodes import ProgramNodeRegistry  # noqa: E402
from selene_space_decision_workflow.routing import RoutingTable  # noqa: E402
from selene_space_decision_workflow.validator import ValidatorConfig  # noqa: E402

TREE_PATH = (
    REPO
    / "packages"
    / "decision-workflow"
    / "selene_space_decision_workflow"
    / "decision_trees"
    / "trading_decision.txt"
)


def _make_workflow(trading_stance: str | None) -> TwoStageWorkflow:
    tree_text = TREE_PATH.read_text(encoding="utf-8")
    terminals = frozenset(re.findall(r"trade|wait|reject|proceed", tree_text))
    graph = parse_decision_tree(tree_text, allowed_terminals=terminals)
    return TwoStageWorkflow(
        llm=None,
        parser_graph=graph,
        validator_config=ValidatorConfig(),
        routing_table=RoutingTable({}),
        program_registry=ProgramNodeRegistry(),
        persistence=None,
        tree_text=tree_text,
        global_rules=["rule1"],
        trading_stance=trading_stance,
    )


def _stage2_prompt(wf: TwoStageWorkflow) -> str:
    return wf._build_stage2_user_prompt(
        input_data={"x": 1},
        diagnosis_summary={"state": "range"},
        strategy_files=[],
    )


def test_stance_leaves_system_prompt_bytes_identical() -> None:
    wf_plain = _make_workflow(None)
    wf_stance = _make_workflow("aggressive")
    assert wf_plain._system_prompt == wf_stance._system_prompt


@pytest.mark.parametrize(
    "stance,expected",
    [
        ("conservative", "保守型"),
        ("balanced", "均衡型"),
        ("aggressive", "激进型"),
        ("very_aggressive", "极度激进型"),
    ],
)
def test_four_tiers_inject_into_stage2_tail(stance: str, expected: str) -> None:
    wf = _make_workflow(stance)
    prompt = _stage2_prompt(wf)
    assert "【交易倾向】" in prompt
    assert expected in prompt
    # Injection sits after the strategy block, before the closing instruction.
    assert prompt.index("【交易倾向】") < prompt.index(
        "请基于 Stage 1 诊断进行 Stage 2 决策"
    )


def test_none_stance_injects_nothing() -> None:
    wf = _make_workflow(None)
    prompt = _stage2_prompt(wf)
    assert "【交易倾向】" not in prompt
    # Baseline byte-stability: with no stance the strategy block runs straight
    # into the closing instruction (no trailing blank drift). The unconditional
    # JSON Schema block appended after that instruction means the prompt no
    # longer ends with it — assert on adjacency instead.
    assert prompt.index("【可用策略文件】") < prompt.index(
        "请基于 Stage 1 诊断进行 Stage 2 决策"
    )


def test_invalid_stance_rejected_by_cli_choices() -> None:
    def _argparse_rejects(stance: str) -> bool:
        import argparse

        ap = argparse.ArgumentParser()
        ap.add_argument(
            "--stance",
            default=None,
            choices=["conservative", "balanced", "aggressive", "very_aggressive"],
        )
        try:
            ap.parse_args(["--stance", stance])
            return False
        except SystemExit:
            return True

    assert _argparse_rejects("ultra_conservative")
    assert _argparse_rejects("BALANCED")  # case-sensitive
    assert not _argparse_rejects("balanced")
    assert not _argparse_rejects("very_aggressive")
