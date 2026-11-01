"""Behaviour locks for ToolCatalog: registration, risk filtering, and
tool-policy integration."""

from __future__ import annotations

from typing import Any

import pytest

from selene_space_agent.tool_catalog import ToolCatalog, ToolCatalogError
from selene_space_agent.tools.base import BaseTool, ToolResult
from selene_space_security.tool_policy import ToolPolicy, ToolPolicyEngine
from selene_space_types.risk import RiskLevel


def _tool(slug: str, risk: RiskLevel = RiskLevel.SAFE) -> BaseTool:
    """Minimal concrete tool bound to *slug*."""

    class _T(BaseTool):
        @property
        def name(self) -> str:
            return slug

        @property
        def description(self) -> str:
            return f"Stub tool {slug}"

        @property
        def parameters(self) -> dict[str, Any]:
            return {"type": "object", "properties": {}, "required": []}

        @property
        def risk_level(self) -> RiskLevel:
            return risk

        def execute(self, args: dict[str, Any]) -> ToolResult:
            return ToolResult(output=f"ran {slug}")

    return _T()


class TestRegistration:
    def test_register_then_identity_get(self) -> None:
        cat = ToolCatalog()
        probe = _tool("market_ticker")
        cat.register(probe)
        assert cat.get("market_ticker") is probe

    def test_duplicate_slug_rejected(self) -> None:
        cat = ToolCatalog()
        cat.register(_tool("market_ticker"))
        with pytest.raises(ToolCatalogError, match="already registered"):
            cat.register(_tool("market_ticker"))

    def test_bulk_registration_counted(self) -> None:
        cat = ToolCatalog()
        cat.register_many([_tool("p"), _tool("q"), _tool("r")])
        assert len(cat) == 3

    def test_unknown_lookup_is_none(self) -> None:
        assert ToolCatalog().get("phantom") is None

    def test_names_return_sorted(self) -> None:
        cat = ToolCatalog()
        cat.register_many([_tool("zulu"), _tool("alpha"), _tool("mike")])
        assert cat.tool_names() == ["alpha", "mike", "zulu"]


class TestRiskFiltering:
    def test_unfiltered_lists_everything(self) -> None:
        cat = ToolCatalog()
        cat.register_many([_tool("x1"), _tool("y1")])
        assert len(cat.list_tools()) == 2

    def test_filter_by_risk_band(self) -> None:
        cat = ToolCatalog()
        cat.register(_tool("calm_a"))
        cat.register(_tool("calm_b"))
        cat.register(_tool("spicy", RiskLevel.RESTRICTED))

        calm = cat.list_tools(risk_level=RiskLevel.SAFE)
        spicy = cat.list_tools(risk_level=RiskLevel.RESTRICTED)
        checks = (len(calm) == 2, len(spicy) == 1, spicy[0].name == "spicy")
        assert all(checks)


class TestPolicyIntegration:
    def test_no_engine_means_all_allowed(self) -> None:
        cat = ToolCatalog()
        cat.register(_tool("anything-goes"))
        checks = (cat.is_tool_allowed("anything-goes") is True, len(cat.list_allowed_tools()) == 1)
        assert all(checks)

    def test_deny_rule_hides_tool(self) -> None:
        policy = ToolPolicyEngine([ToolPolicy(deny=["backtest_run"])])
        cat = ToolCatalog(policy_engine=policy)
        cat.register(_tool("market_ticker"))
        cat.register(_tool("backtest_run", RiskLevel.RESTRICTED))

        pair = (cat.is_tool_allowed("market_ticker"), cat.is_tool_allowed("backtest_run"))
        assert pair == (True, False)

        survivors = cat.list_allowed_tools()
        pair2 = (len(survivors), survivors[0].name)
        assert pair2 == (1, "market_ticker")

    def test_allow_group_whitelists(self) -> None:
        policy = ToolPolicyEngine([ToolPolicy(allow=["group:market-read"])])
        cat = ToolCatalog(policy_engine=policy)
        cat.register(_tool("market_ticker"))
        cat.register(_tool("backtest_run", RiskLevel.RESTRICTED))

        kept = [t.name for t in cat.list_allowed_tools()]
        checks = ("market_ticker" in kept, "backtest_run" not in kept)
        assert all(checks)

    def test_definitions_cover_all_registered(self) -> None:
        cat = ToolCatalog()
        cat.register(_tool("market_ticker"))
        cat.register(_tool("risk_check"))

        defs = cat.get_tool_definitions()
        checks = (
            len(defs) == 2,
            all({"name", "description", "parameters"} <= set(d) for d in defs),
        )
        assert all(checks)

    def test_definitions_honour_deny(self) -> None:
        policy = ToolPolicyEngine([ToolPolicy(deny=["risk_check"])])
        cat = ToolCatalog(policy_engine=policy)
        cat.register(_tool("market_ticker"))
        cat.register(_tool("risk_check"))

        defs = cat.get_tool_definitions()
        pair = (len(defs), defs[0]["name"])
        assert pair == (1, "market_ticker")
