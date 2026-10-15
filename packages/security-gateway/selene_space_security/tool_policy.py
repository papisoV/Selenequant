"""Tool allow/deny policy with deny-first semantics.

A :class:`ToolPolicy` is one named allow-list / deny-list pair whose
entries may be bare tool names or ``group:`` references. A
:class:`ToolPolicyEngine` evaluates the union of several policies:

- **deny wins, always and across policies.** If any policy denies a tool,
  it is blocked even when another policy allows it.
- **allow acts as a whitelist only when present.** If at least one policy
  carries a non-empty allow list, tools outside the combined allow set are
  refused; with no allow lists anywhere, everything not denied passes.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from selene_space_types import RiskLevel

__all__ = [
    "TOOL_GROUPS",
    "ToolPolicy",
    "ToolPolicyEngine",
    "expand_tool_groups",
    "normalize_tool_name",
]

# --- name canonicalization ---------------------------------------------------

_TOOL_ALIASES: dict[str, str] = {
    "bash": "shell_exec",
    "exec": "shell_exec",
    "apply_patch": "file_write",
    "applypatch": "file_write",
}


def normalize_tool_name(name: str) -> str:
    """Canonical tool identifier: trimmed, lowercased, hyphen→underscore,
    then resolved through the alias table (``bash`` → ``shell_exec``)."""
    canonical = name.strip().lower().replace("-", "_")
    return _TOOL_ALIASES.get(canonical, canonical)


# --- tool groups ----------------------------------------------------------------

# Read-only market data surface.
_MARKET_READ_TOOLS = frozenset(
    {"market_ticker", "market_kline", "market_orderbook", "market_trades"}
)

# Read-only strategy/backtest inspection.
_STRATEGY_READ_TOOLS = frozenset(
    {"strategy_list", "strategy_get", "backtest_results_read"}
)

# Everything an agent may do without elevated approval.
_SAFE_TOOLS = (
    frozenset({"explain_pnl", "explain_position", "portfolio_summary"})
    | _MARKET_READ_TOOLS
    | _STRATEGY_READ_TOOLS
)

TOOL_GROUPS: Mapping[str, frozenset[str]] = {
    "group:market-read": _MARKET_READ_TOOLS,
    "group:strategy-read": _STRATEGY_READ_TOOLS,
    "group:safe": _SAFE_TOOLS,
}

_GROUP_PREFIX = "group:"


def expand_tool_groups(entries: Sequence[str]) -> list[str]:
    """Normalize entries; expand ``group:`` members inline.

    Unknown groups vanish silently; duplicates collapse; order follows the
    input (groups contribute their members in group-definition order).
    """
    out: list[str] = []
    for entry in entries:
        normalized = normalize_tool_name(entry)
        if normalized.startswith(_GROUP_PREFIX):
            # Group keys use hyphens (e.g. group:market-read); normalization
            # turned those into underscores, so try both spellings on lookup.
            candidates = (normalized, normalized.replace("_", "-"))
            matched = next((c for c in candidates if c in TOOL_GROUPS), None)
            if matched is not None:
                for member in TOOL_GROUPS[matched]:
                    if member not in out:
                        out.append(member)
            continue
        if normalized not in out:
            out.append(normalized)
    return out


# --- policy objects ---------------------------------------------------------------


@dataclass(frozen=True)
class ToolPolicy:
    """One allow/deny rule pair over normalized tool names or groups."""

    allow: Sequence[str] | None = None
    deny: Sequence[str] | None = None


class ToolPolicyEngine:
    """Evaluates tool requests against the union of all configured policies."""

    def __init__(self, policies: Sequence[ToolPolicy] | None = None) -> None:
        self._policies = list(policies) if policies else []

        denied: set[str] = set()
        allowed: set[str] = set()
        has_allow = False
        for policy in self._policies:
            if policy.deny:
                denied.update(expand_tool_groups(policy.deny))
            if policy.allow:
                has_allow = True
                allowed.update(expand_tool_groups(policy.allow))
        self._denied = denied
        self._allowed = allowed
        self._has_allow = has_allow

    def is_tool_allowed(self, tool: str) -> bool:
        canonical = normalize_tool_name(tool)
        if canonical in self._denied:
            return False
        if self._has_allow and canonical not in self._allowed:
            return False
        return True

    # -- static risk classification ------------------------------------------

    _DANGEROUS_TOOLS = frozenset({"shell_exec", "file_write", "network_fetch"})
    _RESTRICTED_TOOLS = frozenset({"backtest_run", "paper_place_order"})

    def classify_tool(self, tool: str) -> RiskLevel:
        canonical = normalize_tool_name(tool)
        if not self.is_tool_allowed(canonical):
            return RiskLevel.BLOCKED
        if canonical in self._DANGEROUS_TOOLS:
            return RiskLevel.DANGEROUS
        if canonical in self._RESTRICTED_TOOLS:
            return RiskLevel.RESTRICTED
        return RiskLevel.SAFE

    def explain_decision(self, tool: str) -> str:
        """Human-readable reason chain for why ``tool`` got its risk class.

        Pure read over TOOL_GROUPS and the policy sets — no evaluation side
        effects. Useful for audit logs and debugging why a call was refused.
        """
        canonical = normalize_tool_name(tool)
        level = self.classify_tool(canonical)
        reasons: list[str] = []
        groups = [
            g for g, members in TOOL_GROUPS.items() if canonical in members
        ]
        if canonical in self._denied:
            reasons.append("explicitly denied by policy")
        elif self._has_allow and canonical not in self._allowed:
            reasons.append(
                "not on any allow list (allow-list mode is active)"
            )
        else:
            reasons.append("passes all configured policies")
        if groups:
            reasons.append(f"member of {', '.join(sorted(groups))}")
        if canonical in self._DANGEROUS_TOOLS:
            reasons.append("statically classified dangerous")
        elif canonical in self._RESTRICTED_TOOLS:
            reasons.append("statically classified restricted")
        return f"{canonical} -> {level.value}: " + "; ".join(reasons)
