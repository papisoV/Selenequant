"""Decision workflow package — two-stage AI decision engine.

See docs/DECISION_WORKFLOW_METHODOLOGY.md for the methodology.
Sub-modules are NOT re-exported through this package's __init__ —
users import via full path (e.g. `from selene_space_decision_workflow.models
import Stage1Result`). This matches the alpha-core convention where
sub-packages (serenity, event_guard) are not re-exported through the parent.
"""
