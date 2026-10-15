"""Two-stage AI decision workflow exceptions. See docs/DECISION_WORKFLOW_METHODOLOGY.md §7.2."""
from __future__ import annotations


class DecisionWorkflowError(Exception):
    """Base exception for the decision workflow."""


class ValidationError(DecisionWorkflowError):
    """Raised when LLM output fails layered validation (json / consistency / semantic)."""

    def __init__(
        self,
        layer: str,
        field: str,
        message: str,
        node_id: str | None = None,
        cheat_detected: bool = False,
    ) -> None:
        self.layer = layer
        self.field = field
        self.message = message
        self.node_id = node_id
        self.cheat_detected = cheat_detected
        super().__init__(f"[{layer}] {field}: {message}")


class CheatError(DecisionWorkflowError):
    """Raised when an immutable field mutates between retries (methodology §2.4 line 309)."""

    def __init__(self, field: str, message: str) -> None:
        self.field = field
        self.message = message
        super().__init__(f"{field}: {message}")


class SemanticError(DecisionWorkflowError):
    """NOT retryable semantic validation failure (methodology §7.2 line 277)."""

    def __init__(self, message: str, node_id: str | None = None) -> None:
        self.message = message
        self.node_id = node_id
        super().__init__(message)


class GateShortCircuit(DecisionWorkflowError):
    """Raised when Stage 1 gate != proceed (terminal outcome, skip Stage 2)."""

    def __init__(self, gate_result: str) -> None:
        self.gate_result = gate_result
        super().__init__(f"gate_result={gate_result}")
