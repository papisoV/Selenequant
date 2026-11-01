"""Program-node resolver + override validator. See docs/DECISION_WORKFLOW_METHODOLOGY.md §2.3."""
from __future__ import annotations

from typing import Protocol

from .models import NodeAnswer, NodeOverride, DecisionTreeGraph


class ProgramNodeResolver(Protocol):
    """Protocol for a program-node resolver.

    Returns None if the node is AI-judged (no deterministic answer available).
    The returned NodeAnswer includes `program_prefill_nodes=[node_id]`
    (methodology §2.4 line 309).
    """

    def resolve(self, node_id: str, input_data: dict) -> NodeAnswer | None: ...


class ProgramNodeRegistry:
    """Registry of program-node resolvers keyed by node_id."""

    def __init__(self) -> None:
        self._resolvers: dict[str, ProgramNodeResolver] = {}

    def register(self, node_id: str, resolver: ProgramNodeResolver) -> None:
        self._resolvers[node_id] = resolver

    def prefill(self, node_ids: list[str], input_data: dict) -> dict[str, NodeAnswer]:
        """Iterate node_ids; collect resolved answers for registered nodes.

        Unregistered nodes are skipped (the AI will answer them).
        Returns a dict keyed by node_id.
        """
        out: dict[str, NodeAnswer] = {}
        for node_id in node_ids:
            resolver = self._resolvers.get(node_id)
            if resolver is None:
                continue
            answer = resolver.resolve(node_id, input_data)
            if answer is None:
                continue
            out[node_id] = answer
        return out


_BOILERPLATE_REASONS: frozenset[str] = frozenset(
    {"是", "否", "通过", "结构清晰", "风险高", "不符合要求"}
)


class ProgramOverrideValidator:
    """Validates NodeOverride objects against a decision tree graph."""

    def __init__(
        self,
        graph: DecisionTreeGraph,
        *,
        high_risk_nodes: frozenset[str] = frozenset(),
        immutable_nodes: frozenset[str] = frozenset(),
    ) -> None:
        self._graph = graph
        self._high_risk_nodes = high_risk_nodes
        self._immutable_nodes = immutable_nodes

    def validate(self, override: NodeOverride) -> list[str]:
        """Return a list of error messages (empty list = valid override)."""
        errors: list[str] = []

        if override.node_id in self._immutable_nodes:
            errors.append(
                f"Node {override.node_id} is immutable — overrides not allowed"
            )
            return errors

        if override.node_id not in self._graph.nodes:
            errors.append(f"Node {override.node_id} not found in graph")
            return errors

        if override.reason.strip() in _BOILERPLATE_REASONS:
            errors.append(
                f"Override reason for node {override.node_id} is boilerplate — "
                f"a substantive justification is required"
            )

        if override.node_id in self._high_risk_nodes and not override.bar_range:
            errors.append(
                f"Node {override.node_id} is high-risk — bar_range is required"
            )

        return errors
