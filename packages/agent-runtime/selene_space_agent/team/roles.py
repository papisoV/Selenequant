"""Specialist role definitions for the strategy-development agent team.

Canonical prompts live in ``../agents/<slug>.md`` (F1a file-ization); the
inline strings below are the fallback when an md file is missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from selene_space_agent.agent_md import load_agent_md
from selene_space_types.agent import AgentRole


@dataclass(frozen=True)
class RoleDefinition:
    """Immutable description of one agent-team specialist."""

    name: str
    role: AgentRole
    description: str
    system_prompt: str
    allowed_tools: tuple[str, ...] = field(default_factory=tuple)


def _prompt(slug: str, inline: str) -> str:
    return load_agent_md(slug) or inline


_MARKET_ANALYST = RoleDefinition(
    name="Market Analyst",
    role=AgentRole.MARKET_ANALYST,
    description=(
        "Gathers and interprets live market context: tickers, order flow, "
        "candlestick structure and regime classification."
    ),
    system_prompt=_prompt(
        "market-analyst",
        "You are the Market Analyst of a quantitative trading team. "
        "Your job is to observe the market: pull tickers, klines and "
        "microstructure data, classify the current regime (trending, ranging "
        "or volatile), and summarize actionable observations. Ground every "
        "claim in the data returned by your tools. Never place orders or "
        "modify strategies; you only report what the market is doing.",
    ),
    allowed_tools=("market_ticker", "market_kline", "market_orderbook"),
)

_STRATEGY_ARCHITECT = RoleDefinition(
    name="Strategy Architect",
    role=AgentRole.STRATEGY_ARCHITECT,
    description=(
        "Designs and validates trading strategies: parameter choices, "
        "backtest interpretation and iteration plans."
    ),
    system_prompt=_prompt(
        "strategy-architect",
        "You are the Strategy Architect of a quantitative trading team. "
        "You design trading strategies, choose parameters, run backtests "
        "and interpret their metrics (Sharpe, drawdown, win rate, trade "
        "count). Propose concrete next iterations based on evidence from "
        "backtests. You do not execute live trades; you stay in research "
        "and validation.",
    ),
    allowed_tools=("backtest_run", "strategy_get_contract"),
)

_RISK_GUARDIAN = RoleDefinition(
    name="Risk Guardian",
    role=AgentRole.RISK_GUARDIAN,
    description=(
        "Reviews proposed actions for risk: position sizing, exposure "
        "limits and safety checks before anything reaches execution."
    ),
    system_prompt=_prompt(
        "risk-guardian",
        "You are the Risk Guardian of a quantitative trading team. "
        "Every proposed trade, parameter change or deployment passes "
        "through you. Run risk checks, verify position sizes against "
        "limits, and reject or flag anything that violates the safety "
        "rules. You are conservative by default: when in doubt, block "
        "the action and explain why.",
    ),
    allowed_tools=("risk_check", "paper_positions"),
)


AGENT_ROLES: dict[str, RoleDefinition] = {
    AgentRole.MARKET_ANALYST.value: _MARKET_ANALYST,
    AgentRole.STRATEGY_ARCHITECT.value: _STRATEGY_ARCHITECT,
    AgentRole.RISK_GUARDIAN.value: _RISK_GUARDIAN,
}


def get_role(role: AgentRole) -> RoleDefinition:
    """Look up the definition for an agent role."""
    return AGENT_ROLES[role.value]
