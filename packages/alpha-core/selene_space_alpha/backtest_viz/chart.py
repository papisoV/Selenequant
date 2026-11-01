"""Chart generators for backtest visualization.

Clean-room implementation using matplotlib and networkx. No third-party
backtest visualization code was referenced.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # non-interactive backend for headless PNG output
import matplotlib.pyplot as plt
import networkx as nx


def plot_training_curve(history: dict, output_path: Path) -> None:
    """Plot step vs best_reward / mean_reward and save as PNG.

    Args:
        history: dict with keys "step", "best_reward", "mean_reward" — each a
            list of equal length.
        output_path: destination PNG path (parent dir must exist).

    Raises:
        KeyError: if required keys are missing.
        ValueError: if lists have mismatched lengths.
    """
    required = ("step", "best_reward", "mean_reward")
    for key in required:
        if key not in history:
            raise KeyError(f"history missing required key: {key!r}")

    step = list(history["step"])
    best = list(history["best_reward"])
    mean = list(history["mean_reward"])

    if not (len(step) == len(best) == len(mean)):
        raise ValueError(
            f"history lists must have equal length; got "
            f"{len(step)}/{len(best)}/{len(mean)}"
        )

    fig, ax = plt.subplots(figsize=(8, 5))
    ax.plot(step, best, label="best_reward", linewidth=2)
    ax.plot(step, mean, label="mean_reward", linewidth=1, alpha=0.8)
    ax.set_xlabel("step")
    ax.set_ylabel("reward")
    ax.set_title("Training Curve")
    ax.legend(loc="best")
    ax.grid(True, alpha=0.3)
    fig.tight_layout()
    fig.savefig(output_path, dpi=100, format="png")
    plt.close(fig)


def plot_formula_tree(
    formula_tokens: list[int],
    vocab: Any,
    output_path: Path,
) -> None:
    """Render a postfix token sequence as a binary expression tree.

    Args:
        formula_tokens: postfix token list (ints). Operators consume their arity
            operands off the top of the stack; features are leaves.
        vocab: object with a ``token_names`` tuple attribute mapping token index
            to name (e.g. ``FactorVocabulary``).
        output_path: destination PNG path.

    Raises:
        ValueError: if the token sequence is not a valid postfix expression.
    """
    token_names = vocab.token_names
    feature_count = vocab.feature_count

    # Build the tree by replaying the postfix evaluation with subtree roots.
    stack: list[tuple[str, int]] = []  # (node_label, unique_id)
    edges: list[tuple[int, int]] = []
    node_labels: dict[int, str] = {}
    next_id = 0

    for token in formula_tokens:
        name = token_names[token] if token < len(token_names) else f"UNK({token})"
        node_id = next_id
        next_id += 1
        node_labels[node_id] = name

        if token < feature_count:
            # Feature: leaf.
            stack.append((name, node_id))
            continue

        # Operator: determine arity by name lookup in OPERATOR_DEFS.
        arity = _lookup_arity(name)
        if arity is None or arity < 0:
            # Unknown / variable arity — treat as 0-ary leaf to avoid crash.
            stack.append((name, node_id))
            continue

        if len(stack) < arity:
            raise ValueError(
                f"postfix stack underflow: operator {name!r} needs arity "
                f"{arity} but stack has {len(stack)} operands"
            )

        children = [stack.pop() for _ in range(arity)]
        for child_label, child_id in reversed(children):
            edges.append((node_id, child_id))
        stack.append((name, node_id))

    if len(stack) != 1:
        raise ValueError(
            f"invalid postfix expression: {len(stack)} operands left on stack "
            f"(expected exactly 1)"
        )

    graph = nx.DiGraph()
    graph.add_nodes_from(node_labels.keys())
    graph.add_edges_from(edges)

    fig, ax = plt.subplots(figsize=(8, 6))
    pos = nx.nx_agraph.graphviz_layout(graph, prog="dot") if _has_graphviz() else nx.spring_layout(graph, seed=42)
    nx.draw_networkx(graph, pos=pos, labels=node_labels, ax=ax, node_color="#cfe8ff", node_size=1200, font_size=9)
    ax.set_title("Formula Tree")
    ax.axis("off")
    fig.tight_layout()
    fig.savefig(output_path, dpi=100, format="png")
    plt.close(fig)


def _lookup_arity(name: str) -> int | None:
    """Look up operator arity by name from the operator registry."""
    from selene_space_alpha.ops.registry import OPERATOR_DEFS

    for op_name, arity in OPERATOR_DEFS:
        if op_name == name:
            return arity
    return None


def _has_graphviz() -> bool:
    """Check whether pygraphviz is available for hierarchical layout."""
    try:
        import pygraphviz  # noqa: F401
    except Exception:
        return False
    return True
