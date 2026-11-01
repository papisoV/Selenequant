"""US-003 tests: cleanroom trading decision tree + TRADING_ROUTING_TABLE.

Covers parser main branch kinds (binary/multi) and routing table main paths
for the trading scenario. Tree authored from Al Brooks public publications
only — see decision_trees/trading_decision.txt header.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from selene_space_decision_workflow.parser import parse_decision_tree
from selene_space_decision_workflow.routing import (
    TRADING_ROUTING_TABLE,
    route_strategy_files,
)

_TREE_PATH = (
    Path(__file__).resolve().parents[1]
    / "packages"
    / "decision-workflow"
    / "selene_space_decision_workflow"
    / "decision_trees"
    / "trading_decision.txt"
)

_EXPECTED_TERMINALS = frozenset({"trade", "wait", "reject"})


def _load_tree_text() -> str:
    assert _TREE_PATH.is_file(), f"tree file missing: {_TREE_PATH}"
    return _TREE_PATH.read_text(encoding="utf-8")


def _scan_terminals(tree_text: str) -> frozenset[str]:
    matches = re.findall(
        r"^[是否中性是\[否\[][：:].*?终止\s*→\s*(\S+)", tree_text, re.M
    )
    return frozenset(matches)


def test_trading_tree_parses_with_all_terminals() -> None:
    txt = _load_tree_text()
    graph = parse_decision_tree(txt, allowed_terminals=_scan_terminals(txt))
    assert graph.terminals == _EXPECTED_TERMINALS
    assert graph.entry_node_id == "1.1"
    # Data gate -> market state -> entry quality -> hard veto sections.
    assert {"1.1", "1.2", "2.1", "2.2", "3.1", "3.2", "3.3", "4.1"} == set(
        graph.nodes.keys()
    )


def test_trading_tree_has_multi_branch_node() -> None:
    txt = _load_tree_text()
    graph = parse_decision_tree(txt, allowed_terminals=_scan_terminals(txt))
    # 2.1 uses the multi-form 是[...] branches (trend up / trend down / none).
    assert graph.nodes["2.1"].branch_type == "multi"
    assert len(graph.nodes["2.1"].branches) == 3


def test_trading_tree_terminal_outcomes_present() -> None:
    txt = _load_tree_text()
    graph = parse_decision_tree(txt, allowed_terminals=_scan_terminals(txt))
    terminal_targets: set[str] = set()
    for node in graph.nodes.values():
        for branch in node.branches:
            if branch.terminal_outcome is not None:
                terminal_targets.add(branch.terminal_outcome)
    assert terminal_targets == _EXPECTED_TERMINALS


def test_trading_tree_header_cites_brooks() -> None:
    txt = _load_tree_text()
    assert "Al Brooks" in txt
    assert "Cleanroom" in txt or "cleanroom" in txt
    # Chapter-level citation present.
    assert re.search(r"Ch\.\s*\d", txt)


def test_routing_unknown_state_returns_empty() -> None:
    assert route_strategy_files({"state": "unknown"}, TRADING_ROUTING_TABLE) == []
    assert route_strategy_files({}, TRADING_ROUTING_TABLE) == []


def test_routing_trend_states() -> None:
    files = route_strategy_files({"state": "trend_up"}, TRADING_ROUTING_TABLE)
    assert files == [
        "strategies/trading_trend_up.txt",
        "strategies/trading_common_risk.txt",
        # B3 multiscale appended last for every declared state.
        "strategies/trading_multiscale.txt",
    ]

    files_down = route_strategy_files({"state": "trend_down"}, TRADING_ROUTING_TABLE)
    assert files_down == [
        "strategies/trading_trend_down.txt",
        "strategies/trading_common_risk.txt",
        "strategies/trading_multiscale.txt",
    ]


def test_routing_range_and_breakout_states() -> None:
    assert route_strategy_files({"state": "range"}, TRADING_ROUTING_TABLE) == [
        "strategies/trading_range.txt",
        "strategies/trading_multiscale.txt",
    ]
    assert route_strategy_files({"state": "breakout"}, TRADING_ROUTING_TABLE) == [
        "strategies/trading_breakout.txt",
        "strategies/trading_multiscale.txt",
    ]


@pytest.mark.parametrize("state", ["weird_state", "HIGH_RISK", ""])
def test_routing_unmatched_state_returns_empty(state: str) -> None:
    assert route_strategy_files({"state": state}, TRADING_ROUTING_TABLE) == []
