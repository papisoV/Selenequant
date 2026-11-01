"""Behavior locks for selene_space_security.tool_policy: name
normalization, group expansion, deny-first engine semantics, static risk
classification, and decision explanations."""

from selene_space_security.tool_policy import (
    TOOL_GROUPS,
    ToolPolicy,
    ToolPolicyEngine,
    expand_tool_groups,
    normalize_tool_name,
)
from selene_space_types import RiskLevel

# --- normalize_tool_name ---------------------------------------------------------


CASES = [
    ("Shell_Exec", "shell_exec"),
    ("market-ticker", "market_ticker"),
    ("  market_ticker  ", "market_ticker"),
    ("bash", "shell_exec"),
    ("exec", "shell_exec"),
    ("apply-patch", "file_write"),
    ("Apply-Patch", "file_write"),
    ("my_custom_tool", "my_custom_tool"),
]


def test_normalization_table() -> None:
    for raw, expected in CASES:
        assert normalize_tool_name(raw) == expected, raw


# --- expand_tool_groups --------------------------------------------------------------


def test_market_read_group_expands_to_read_tools() -> None:
    expanded = expand_tool_groups(["group:market-read"])
    assert {"market_ticker", "market_kline", "market_orderbook"} <= set(expanded)


def test_group_entries_mix_with_literals() -> None:
    expanded = expand_tool_groups(["group:market-read", "custom_tool"])
    assert "custom_tool" in expanded
    assert "market_ticker" in expanded


def test_unknown_group_silently_dropped() -> None:
    assert expand_tool_groups(["group:nonexistent", "market_ticker"]) == ["market_ticker"]


def test_duplicate_inputs_collapse() -> None:
    assert expand_tool_groups(["market_ticker", "market_ticker"]).count("market_ticker") == 1


def test_members_normalized_on_entry() -> None:
    assert "market_ticker" in expand_tool_groups(["Market-Ticker"])


def test_safe_group_superset_of_all_read_groups() -> None:
    safe = set(TOOL_GROUPS["group:safe"])
    for read_group in ("group:market-read", "group:strategy-read"):
        assert set(TOOL_GROUPS[read_group]) <= safe


# --- deny-first engine -------------------------------------------------------------------


class TestDenyFirst:
    def test_same_tool_on_both_lists_still_blocked(self) -> None:
        """If a tool is in both allow and deny, it must be blocked (deny-first)."""
        engine = ToolPolicyEngine([ToolPolicy(allow=["shell_exec"], deny=["shell_exec"])])
        assert engine.is_tool_allowed("shell_exec") is False

    def test_denied_group_beats_individual_allow(self) -> None:
        engine = ToolPolicyEngine([ToolPolicy(allow=["market_ticker"], deny=["group:market-read"])])
        assert engine.is_tool_allowed("market_ticker") is False

    def test_alias_form_of_denied_name_also_blocked(self) -> None:
        """Denying 'bash' must block 'shell_exec' (alias resolution)."""
        engine = ToolPolicyEngine([ToolPolicy(deny=["bash"])])
        assert engine.is_tool_allowed("shell_exec") is False
        assert engine.is_tool_allowed("bash") is False

    def test_denial_ignores_input_case(self) -> None:
        engine = ToolPolicyEngine([ToolPolicy(deny=["Shell_Exec"])])
        assert engine.is_tool_allowed("shell_exec") is False
        assert engine.is_tool_allowed("SHELL_EXEC") is False


class TestAllowListMode:
    def test_unlisted_tool_rejected_when_allow_present(self) -> None:
        engine = ToolPolicyEngine([ToolPolicy(allow=["market_ticker"])])
        assert engine.is_tool_allowed("market_ticker") is True
        assert engine.is_tool_allowed("shell_exec") is False

    def test_without_allow_list_everything_non_denied_passes(self) -> None:
        """No allow list = all non-denied tools permitted."""
        engine = ToolPolicyEngine([ToolPolicy(deny=["shell_exec"])])
        assert engine.is_tool_allowed("market_ticker") is True
        assert engine.is_tool_allowed("shell_exec") is False

    def test_empty_policy_stack_permits_all(self) -> None:
        engine = ToolPolicyEngine([])
        assert engine.is_tool_allowed("shell_exec") is True
        assert engine.is_tool_allowed("market_ticker") is True

    def test_none_policy_stack_permits_all(self) -> None:
        assert ToolPolicyEngine(None).is_tool_allowed("anything") is True


class TestStackedPolicies:
    def test_any_policy_denial_wins(self) -> None:
        engine = ToolPolicyEngine(
            [
                ToolPolicy(deny=["shell_exec"]),
                ToolPolicy(allow=["shell_exec", "market_ticker"]),
            ]
        )
        assert engine.is_tool_allowed("shell_exec") is False
        assert engine.is_tool_allowed("market_ticker") is True

    def test_allow_sets_union_across_policies(self) -> None:
        engine = ToolPolicyEngine(
            [
                ToolPolicy(allow=["market_ticker"]),
                ToolPolicy(allow=["market_kline"]),
            ]
        )
        assert engine.is_tool_allowed("market_ticker") is True
        assert engine.is_tool_allowed("market_kline") is True
        assert engine.is_tool_allowed("shell_exec") is False


# --- classify_tool ---------------------------------------------------------------------------


DANGEROUS_SET = ["shell_exec", "file_write", "network_fetch"]
RESTRICTED_SET = ["backtest_run", "paper_place_order"]
SAFE_SET = ["market_ticker", "explain_pnl"]


def test_static_levels_for_each_tier() -> None:
    engine = ToolPolicyEngine([])
    for tool in DANGEROUS_SET:
        assert engine.classify_tool(tool) == RiskLevel.DANGEROUS
    for tool in RESTRICTED_SET:
        assert engine.classify_tool(tool) == RiskLevel.RESTRICTED
    for tool in SAFE_SET:
        assert engine.classify_tool(tool) == RiskLevel.SAFE


def test_unrecognized_tool_falls_back_to_safe() -> None:
    assert ToolPolicyEngine([]).classify_tool("some_custom_tool") == RiskLevel.SAFE


def test_active_denial_upgrades_level_to_blocked() -> None:
    engine = ToolPolicyEngine([ToolPolicy(deny=["market_ticker"])])
    assert engine.classify_tool("market_ticker") == RiskLevel.BLOCKED


def test_alias_resolved_before_classification() -> None:
    assert ToolPolicyEngine([]).classify_tool("bash") == RiskLevel.DANGEROUS


# --- explain_decision ----------------------------------------------------------------------------


class TestExplainDecision:
    def test_denied_tool_reports_deny_reason(self) -> None:
        engine = ToolPolicyEngine([ToolPolicy(deny=["shell_exec"])])
        text = engine.explain_decision("shell_exec")
        assert "explicitly denied by policy" in text
        assert "critical" in text

    def test_allow_list_mode_reports_missing_allow(self) -> None:
        engine = ToolPolicyEngine([ToolPolicy(allow=["market_ticker"])])
        assert "not on any allow list" in engine.explain_decision("shell_exec")

    def test_clean_tool_reports_pass_and_groups(self) -> None:
        text = ToolPolicyEngine([]).explain_decision("market_ticker")
        assert "passes all configured policies" in text
        assert "group:market-read" in text
        assert "group:safe" in text

    def test_dangerous_tag_surfaced_through_alias(self) -> None:
        text = ToolPolicyEngine([]).explain_decision("bash")  # alias -> shell_exec
        assert "shell_exec" in text
        assert "dangerous" in text

    def test_restricted_tag_surfaced(self) -> None:
        assert "restricted" in ToolPolicyEngine([]).explain_decision("backtest_run")

    def test_unknown_tool_gets_minimal_explanation(self) -> None:
        text = ToolPolicyEngine([]).explain_decision("totally_unknown")
        assert "totally_unknown" in text
        assert "passes all configured policies" in text
