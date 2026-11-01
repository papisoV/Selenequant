"""Strategy-file routing. See docs/DECISION_WORKFLOW_METHODOLOGY.md §1.5, §2.5."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class RoutingEntry:
    """One routing rule: if state_matcher(diagnosis) is True, include files."""

    state_matcher: Callable[[dict], bool]
    files: tuple[str, ...]


@dataclass(frozen=True)
class RoutingTable:
    """Ordered collection of routing entries."""

    entries: tuple[RoutingEntry, ...]


def route_strategy_files(diagnosis: dict, table: RoutingTable) -> list[str]:
    """Pure routing function.

    If diagnosis.get("state") == "unknown" (or missing) → return [].
    Otherwise iterate table.entries in order; for each entry whose
    state_matcher(diagnosis) returns True, collect its files.
    DEDUPE preserves first-occurrence order.
    """
    if diagnosis.get("state", "unknown") == "unknown":
        return []

    seen: set[str] = set()
    out: list[str] = []
    for entry in table.entries:
        if not entry.state_matcher(diagnosis):
            continue
        for f in entry.files:
            if f not in seen:
                out.append(f)
                seen.add(f)
    return out


CREDIT_ROUTING_TABLE = RoutingTable(
    entries=(
        RoutingEntry(
            state_matcher=lambda d: d.get("state") == "normal",
            files=("strategies/credit_normal.txt",),
        ),
        RoutingEntry(
            state_matcher=lambda d: d.get("state") == "medium_risk",
            files=("strategies/credit_medium.txt", "strategies/common_risk.txt"),
        ),
        RoutingEntry(
            state_matcher=lambda d: d.get("state") == "high_risk",
            files=("strategies/credit_high.txt", "strategies/common_risk.txt"),
        ),
    )
)


AML_ROUTING_TABLE = RoutingTable(
    entries=(
        RoutingEntry(
            state_matcher=lambda d: d.get("state") == "clear",
            files=("strategies/aml_clear.txt",),
        ),
        RoutingEntry(
            state_matcher=lambda d: d.get("state") == "suspicious",
            files=("strategies/aml_suspicious.txt", "strategies/compliance_review.txt"),
        ),
    )
)


# Trading scenario (decision tree: trading_decision.txt). Cleanroom original —
# state vocabulary declared in the tree header (trend_up/trend_down/range/
# breakout/unknown). Files reference selene strategy-engine templates.
TRADING_ROUTING_TABLE = RoutingTable(
    entries=(
        RoutingEntry(
            state_matcher=lambda d: d.get("state") == "trend_up",
            files=("strategies/trading_trend_up.txt",),
        ),
        RoutingEntry(
            state_matcher=lambda d: d.get("state") == "trend_down",
            files=("strategies/trading_trend_down.txt",),
        ),
        RoutingEntry(
            state_matcher=lambda d: d.get("state") == "range",
            files=("strategies/trading_range.txt",),
        ),
        RoutingEntry(
            state_matcher=lambda d: d.get("state") == "breakout",
            files=("strategies/trading_breakout.txt",),
        ),
        RoutingEntry(
            state_matcher=lambda d: d.get("state") in ("trend_up", "trend_down"),
            files=("strategies/trading_common_risk.txt",),
        ),
        # B3: multi-scale reading applies to every routed (declared) state,
        # appended after the state file so its composition rules come last.
        # Explicit set — unmatched states must still route to nothing.
        RoutingEntry(
            state_matcher=lambda d: d.get("state")
            in ("trend_up", "trend_down", "range", "breakout"),
            files=("strategies/trading_multiscale.txt",),
        ),
    )
)


# B4: overlay material is keyed by deterministic pattern detection, not by
# Stage1 state. The caller passes overlay_patterns from overlays.py; matching
# patterns inject their material file into Stage 2.
OVERLAY_ROUTING_TABLE = {
    "wedge": "strategies/trading_overlay_wedge.txt",
    "h2": "strategies/trading_overlay_h2.txt",
    "breakout_failure": "strategies/trading_overlay_breakout_failure.txt",
}


def route_overlay_files(overlay_patterns: dict) -> list[str]:
    """Map detected overlay patterns to their strategy-material files."""
    return [
        OVERLAY_ROUTING_TABLE[pattern]
        for pattern in ("wedge", "h2", "breakout_failure")
        if overlay_patterns.get(pattern)
    ]


def _iter_entries(table: RoutingTable) -> Iterable[RoutingEntry]:
    return table.entries
