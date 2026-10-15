"""Compatibility re-export shim.

The loader moved into the decision-workflow package so the local-api
service and the CLI share one implementation. Import path preserved for
existing tests and run_pa_diagnosis.py (pa_research stays a namespace
package resolved via the scripts/ sys.path entry).
"""

from selene_space_decision_workflow.pa_stats_loader import (  # noqa: F401
    CALIBRATION_SENTENCE,
    load_pa_stats,
    stats_rule_or_fallback,
)
