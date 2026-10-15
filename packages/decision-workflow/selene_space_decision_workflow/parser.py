"""Decision tree .txt parser. See docs/DECISION_WORKFLOW_METHODOLOGY.md §2.2."""
from __future__ import annotations

import re

from .models import Branch, DecisionTreeGraph, DecisionTreeNode

# Regex patterns
_SECTION_RE = re.compile(r"^##\s+(\d+)\.\s*(.+)$")
_NODE_RE = re.compile(r"^#{3,4}\s+(\d+\.\d+(?:\.\d+)?)\s+(.+)$")
_BRANCH_RE = re.compile(r"^(是|否|中性|是\[.+\])[：:](.+)$")
_MULTI_PREFIX_RE = re.compile(r"^(否|中性)\[.+\]")
_TARGET_GOTO_RE = re.compile(r"进入\s*§(\d+\.\d+(?:\.\d+)?)")
_TARGET_TERMINAL_RE = re.compile(r"终止\s*→\s*(\S+)")
_TARGET_MARK_RE = re.compile(r"标记\s+(\S+)\s+(\S+)")

# Required branches per node type
_REQUIRED_BINARY = ("是", "否")
_REQUIRED_TERNARY = ("是", "否", "中性")


def _make_branch(label: str, target_text: str) -> Branch:
    """Build a Branch from raw target text.

    Four target kinds:
      - "进入 §X.Y" → target_ref set
      - "终止 → {outcome}" → terminal_outcome set
      - "标记 {tag} {action}" → tag + tag_action set
      - bare continuation → "none" kind (all fields None)
    """
    stripped = target_text.strip()
    if not stripped:
        return Branch(label=label, target_ref=None, terminal_outcome=None, tag=None, tag_action=None)

    goto_match = _TARGET_GOTO_RE.search(stripped)
    terminal_match = _TARGET_TERMINAL_RE.search(stripped)
    mark_match = _TARGET_MARK_RE.search(stripped)

    if goto_match and not terminal_match and not mark_match:
        return Branch(
            label=label,
            target_ref=goto_match.group(1),
            terminal_outcome=None,
            tag=None,
            tag_action=None,
        )
    if terminal_match and not mark_match:
        return Branch(
            label=label,
            target_ref=None,
            terminal_outcome=terminal_match.group(1),
            tag=None,
            tag_action=None,
        )
    if mark_match:
        return Branch(
            label=label,
            target_ref=None,
            terminal_outcome=None,
            tag=mark_match.group(1),
            tag_action=mark_match.group(2),
        )
    # Bare continuation — "none" kind.
    return Branch(label=label, target_ref=None, terminal_outcome=None, tag=None, tag_action=None)


def _classify_branch_type(branches: tuple[Branch, ...]) -> str:
    """Determine branch_type from the set of branch labels.

    - mark: any branch has a tag set (e.g. "是[50%-70%]：标记 ...")
    - multi: any label is multi-form (是[...])
    - ternary: contains 是 + 否 + 中性
    - binary: contains 是 + 否 only
    """
    labels = [b.label for b in branches]
    has_multi = any(lbl.startswith("是[") for lbl in labels)
    has_mark = any(b.tag is not None for b in branches)

    if has_mark:
        return "mark"
    if has_multi:
        return "multi"
    label_set = set(labels)
    if {"是", "否", "中性"}.issubset(label_set):
        return "ternary"
    if {"是", "否"}.issubset(label_set):
        return "binary"
    return "binary"


def parse_decision_tree(
    text: str,
    *,
    allowed_terminals: frozenset[str],
) -> DecisionTreeGraph:
    """Parse a decision tree from text into a DecisionTreeGraph.

    Line-by-line state machine:
      - Blank / single-hash `#` comment lines skipped.
      - `## N.` → start new section.
      - `### N.M` or `#### N.M.K` → start new node.
      - Branch lines: `(是|否|中性|是[...])[：:]...` → build Branch.
      - `否[...]` / `中性[...]` multi-branch forms are rejected.

    Validation (raises ValueError):
      - Multi-branch with 否[...] / 中性[...] prefix.
      - Duplicate node_id.
      - Non-terminal node missing required branches.
      - Terminal outcome not in allowed_terminals.
      - 进入 §X.Y target not in graph.
      - Orphan node (not reachable from entry).
    """
    lines = text.splitlines()
    nodes: dict[str, DecisionTreeNode] = {}
    entry_node_id: str | None = None
    current_section_title: str = ""
    pending_node_id: str | None = None
    pending_question: str = ""
    pending_branches: list[Branch] = []

    def _flush_pending() -> None:
        nonlocal pending_node_id, pending_question, pending_branches
        if pending_node_id is None:
            return
        _validate_and_store(
            nodes=nodes,
            node_id=pending_node_id,
            section=current_section_title,
            question=pending_question,
            branches=tuple(pending_branches),
            allowed_terminals=allowed_terminals,
        )
        pending_node_id = None
        pending_question = ""
        pending_branches = []

    for raw_line in lines:
        line = raw_line.rstrip()
        if not line.strip():
            continue
        # Single-hash comment (but not ## / ### / ####).
        if line.startswith("#") and not line.startswith("##"):
            continue

        section_match = _SECTION_RE.match(line)
        if section_match:
            _flush_pending()
            current_section_title = section_match.group(2).strip()
            continue

        node_match = _NODE_RE.match(line)
        if node_match:
            _flush_pending()
            pending_node_id = node_match.group(1)
            pending_question = node_match.group(2).strip()
            if entry_node_id is None:
                entry_node_id = pending_node_id
            continue

        branch_match = _BRANCH_RE.match(line)
        if branch_match and pending_node_id is not None:
            prefix = branch_match.group(1)
            target_text = branch_match.group(2)
            branch = _make_branch(label=prefix, target_text=target_text)
            pending_branches.append(branch)
            continue

        # Reject 否[...] / 中性[...] multi-branch forms (checked after _BRANCH_RE
        # because the alternation in _BRANCH_RE may match 否 alone and fail on the
        # colon, leaving the line unmatched above).
        if pending_node_id is not None:
            multi_bad = _MULTI_PREFIX_RE.match(line)
            if multi_bad:
                raise ValueError(
                    f"Multi-branch with prefix '{multi_bad.group(1)}' is not supported "
                    f"(methodology §2.2 only supports 是[...]); use separate 否: branch "
                    f"lines instead. Line: '{line}'"
                )

        # Unrecognized line within a node context — ignore for forward-compat.

    _flush_pending()

    if entry_node_id is None:
        raise ValueError("Decision tree has no nodes (no '### N.M' header found).")

    # Post-parse validation: target_ref existence + orphan reachability.
    _validate_graph_targets(nodes=nodes, entry_node_id=entry_node_id)
    _validate_reachability(nodes=nodes, entry_node_id=entry_node_id)

    return DecisionTreeGraph(
        nodes=nodes,
        entry_node_id=entry_node_id,
        terminals=allowed_terminals,
    )


def _validate_and_store(
    *,
    nodes: dict[str, DecisionTreeNode],
    node_id: str,
    section: str,
    question: str,
    branches: tuple[Branch, ...],
    allowed_terminals: frozenset[str],
) -> None:
    if node_id in nodes:
        raise ValueError(f"Duplicate node_id '{node_id}' in decision tree.")
    if not branches:
        raise ValueError(
            f"Node '{node_id}' has no branches (every node requires at least one branch line)."
        )

    branch_type = _classify_branch_type(branches)

    # Terminal outcome validation.
    for b in branches:
        if b.terminal_outcome is not None and b.terminal_outcome not in allowed_terminals:
            raise ValueError(
                f"Node '{node_id}' branch '{b.label}' has terminal_outcome "
                f"'{b.terminal_outcome}' not in allowed_terminals={sorted(allowed_terminals)}."
            )

    # Required-branches validation (binary / ternary / multi / mark).
    labels = [b.label for b in branches]
    label_set = set(labels)
    has_multi = any(lbl.startswith("是[") for lbl in labels)
    has_mark = any(b.tag is not None for b in branches)

    if has_mark:
        # Mark nodes with multi-form 是[...] branches follow multi rules (no bare 否 required).
        if has_multi:
            if not any(lbl.startswith("是[") for lbl in labels):
                raise ValueError(
                    f"Node '{node_id}' (mark branch) requires at least one 是[...] branch."
                )
        else:
            # Pure mark node (no 是[...] form) — require 是/否 siblings.
            missing = [req for req in _REQUIRED_BINARY if req not in label_set]
            if missing:
                raise ValueError(
                    f"Node '{node_id}' (mark branch) is missing required branch(es): {missing}. "
                    f"Mark branches must coexist with 是/否 branches."
                )
    elif has_multi:
        # Multi-branch nodes use 是[...] form; require at least two 是[...] branches.
        multi_count = sum(1 for lbl in labels if lbl.startswith("是["))
        if multi_count < 2:
            raise ValueError(
                f"Node '{node_id}' (multi branch) requires at least two 是[...] branches."
            )
    else:
        # Binary or ternary.
        if "中性" in label_set:
            missing = [req for req in _REQUIRED_TERNARY if req not in label_set]
            if missing:
                raise ValueError(
                    f"Node '{node_id}' (ternary) is missing required branch(es): {missing}."
                )
        else:
            missing = [req for req in _REQUIRED_BINARY if req not in label_set]
            if missing:
                raise ValueError(
                    f"Node '{node_id}' (binary) is missing required branch(es): {missing}."
                )

    nodes[node_id] = DecisionTreeNode(
        node_id=node_id,
        section=section,
        question=question,
        node_type="decision",
        branch_type=branch_type,  # type: ignore[arg-type]
        branches=branches,
    )


def _validate_graph_targets(*, nodes: dict[str, DecisionTreeNode], entry_node_id: str) -> None:
    for nid, node in nodes.items():
        for b in node.branches:
            if b.target_ref is not None and b.target_ref not in nodes:
                raise ValueError(
                    f"Node '{nid}' branch '{b.label}' references target §{b.target_ref} "
                    f"which does not exist in the graph."
                )


def _validate_reachability(*, nodes: dict[str, DecisionTreeNode], entry_node_id: str) -> None:
    visited: set[str] = set()
    stack: list[str] = [entry_node_id]
    while stack:
        nid = stack.pop()
        if nid in visited or nid not in nodes:
            continue
        visited.add(nid)
        for b in nodes[nid].branches:
            if b.target_ref is not None and b.target_ref in nodes:
                stack.append(b.target_ref)
    orphans = sorted(set(nodes.keys()) - visited)
    if orphans:
        raise ValueError(
            f"Orphan nodes not reachable from entry '{entry_node_id}': {orphans}."
        )
