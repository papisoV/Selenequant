"""Tests for startup tool-reference audit."""

from __future__ import annotations

from types import SimpleNamespace

from selene_space_agent.team.startup_check import check_tool_references


ALL_ROLE_TOOLS = {
    "market_ticker",
    "market_kline",
    "market_orderbook",
    "backtest_run",
    "strategy_get_contract",
    "risk_check",
    "paper_positions",
}


def _skill(name: str, requires_tools: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        name=name, frontmatter=SimpleNamespace(requires_tools=requires_tools)
    )


def test_role_unknown_tool_reported() -> None:
    problems = check_tool_references({"market_ticker", "market_klines"})
    assert any(
        "role 'market_analyst'" in p and "market_orderbook" in p for p in problems
    )


def test_skill_unknown_tool_reported() -> None:
    skills = [_skill("my-skill", ["strategy_validate", "ghost_tool"])]
    problems = check_tool_references({"strategy_validate"} | ALL_ROLE_TOOLS, skills)
    assert problems == [
        "skill 'my-skill' requires unknown tool 'ghost_tool' "
        "(skill silently hidden from eligible list)"
    ]


def test_skill_requirements_satisfied_clean() -> None:
    skills = [_skill("my-skill", ["strategy_validate", "backtest_run"])]
    tools = {"strategy_validate", "backtest_run"} | ALL_ROLE_TOOLS
    assert check_tool_references(tools, skills) == []


def test_healthy_catalog_zero_problems() -> None:
    assert check_tool_references(set(ALL_ROLE_TOOLS), []) == []


def test_problems_aggregated_not_fail_fast() -> None:
    skills = [_skill("a", ["missing_a"]), _skill("b", ["missing_b", "missing_c"])]
    problems = check_tool_references(set(), skills)
    assert len(problems) == 10  # 7 role tools + 3 skill requirements


def test_skill_without_frontmatter_ignored() -> None:
    assert check_tool_references(set(ALL_ROLE_TOOLS), [SimpleNamespace(name="x")]) == []
