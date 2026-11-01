"""B5 tests: get_tree_graph serialization + graph endpoint contract.

Covers the service-side graph projection (single source = wf.parser_graph,
not a re-parse) and the FastAPI route contract via direct service calls.
API-layer smoke for the endpoint lives in the router module; this file keeps
to the package-agnostic service layer so pytest collects without uvicorn.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Service import lives under services/local-api — resolve without activate.bat
# by prepending the app package root (same pattern as other local-api tests).
_ROOT = Path(__file__).resolve().parents[1]
_API_ROOT = _ROOT / "services" / "local-api"
for _p in (str(_API_ROOT), str(_ROOT / "packages" / "decision-workflow")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from app.core.decision_workflow_service import DecisionWorkflowService  # noqa: E402


def _service() -> DecisionWorkflowService:
    return DecisionWorkflowService()


def test_get_tree_graph_returns_full_graph_shape() -> None:
    svc = _service()
    graph = svc.get_tree_graph("trading_decision")
    assert graph is not None
    assert graph["tree_id"] == "trading_decision"
    assert graph["entry_node_id"], "entry node must be set"
    assert graph["terminals"], "terminals must be non-empty"
    assert set(graph["terminals"]) >= {"trade", "wait", "reject"}
    nodes = graph["nodes"]
    assert graph["entry_node_id"] in nodes
    for node in nodes.values():
        assert {
            "node_id",
            "section",
            "question",
            "node_type",
            "branch_type",
            "branches",
        } <= set(node)
        for branch in node["branches"]:
            assert {
                "label",
                "target_ref",
                "terminal_outcome",
                "tag",
                "tag_action",
            } <= set(branch)


def test_get_tree_graph_branch_refs_resolve() -> None:
    svc = _service()
    graph = svc.get_tree_graph("trading_decision")
    assert graph is not None
    nodes = graph["nodes"]
    broken = []
    for node in nodes.values():
        for b in node["branches"]:
            if b["target_ref"] is not None and b["target_ref"] not in nodes:
                broken.append((node["node_id"], b["label"], b["target_ref"]))
    assert not broken, f"branches pointing to missing nodes: {broken}"


def test_get_tree_graph_terminals_marked_on_branches() -> None:
    svc = _service()
    graph = svc.get_tree_graph("trading_decision")
    assert graph is not None
    declared = set(graph["terminals"])
    seen = {
        b["terminal_outcome"]
        for node in graph["nodes"].values()
        for b in node["branches"]
        if b["terminal_outcome"] is not None
    }
    assert seen == declared, f"branch terminals {seen} != declared {declared}"


def test_get_tree_graph_unknown_tree_returns_none() -> None:
    svc = _service()
    assert svc.get_tree_graph("no_such_tree") is None


def test_all_loaded_trees_serialize() -> None:
    svc = _service()
    tree_ids = [t.tree_id for t in svc.list_trees()]
    assert "trading_decision" in tree_ids
    for tid in tree_ids:
        graph = svc.get_tree_graph(tid)
        assert graph is not None, f"tree {tid} loaded but graph missing"
        assert graph["nodes"], f"tree {tid} has no nodes"
