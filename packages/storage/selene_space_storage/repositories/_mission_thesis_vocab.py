"""Thesis-tracker vocabulary — single source of truth for closed word lists.

Shared by the v019 migration (CHECK constraint DDL generation) and the
mission_thesis repository (write-path validation). Leaf module: imports
nothing from the rest of the package, so the migration layer never
depends on the repository layer.
"""

from __future__ import annotations

SIGNALS: tuple[str, ...] = (
    "strongly_confirming",
    "confirming",
    "mildly_confirming",
    "neutral",
    "mildly_weakening",
    "weakening",
    "strongly_weakening",
    "mixed",
    "invalidating",
    "untested",
)

PILLAR_STATUSES: tuple[str, ...] = (
    "strengthening",
    "intact",
    "watch",
    "impaired",
    "broken",
    "changed",
    "untested",
    "retired",
)

PILLAR_PRIORITIES: tuple[str, ...] = ("core", "supporting")

# 注意:名字像"来源",语义是【置信等级】——不是自由文本来源描述。
# 误填成 "probe:xxx" 之类会被 mission_thesis.py:162 拒绝。
# 来源描述请写 model_line(如 "TF_TREND — 来源:run_probe.py funding_ic")。
PROVENANCE_LEVELS: tuple[str, ...] = ("inherited", "draft", "approved")

DRIFT_PATTERNS: tuple[str, ...] = (
    "reason_changed",
    "catalyst_laundering",
    "valuation_substitution",
    "dismissal_by_horizon",
    "protection_as_reason",
)
