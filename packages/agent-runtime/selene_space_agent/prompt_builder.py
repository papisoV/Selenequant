"""System-prompt assembly for the SeleneQuant trading agent."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from selene_space_types.agent import AgentRole, MarketState


@dataclass
class AgentContext:
    """Everything the prompt builder needs to know about one conversation."""

    available_tools: list[dict[str, Any]] = field(default_factory=list)
    market_state: MarketState | None = None
    user_preferences: dict[str, Any] = field(default_factory=dict)
    role: AgentRole | None = None
    memory_context: str = ""
    active_symbols: list[str] = field(default_factory=list)
    react_enabled: bool = True
    max_tool_rounds: int = 8
    skills_prompt: str = ""


def _render_market_state(state: MarketState) -> list[str]:
    lines = ["## Market State", ""]
    lines.append(f"- Symbol: {state.symbol}")
    regime_text = str(state.regime).split(".")[-1].lower()
    lines.append(f"- Regime: {regime_text.replace('_', ' ')}")
    if getattr(state, "trend_strength", None) is not None:
        lines.append(f"- Trend strength: {state.trend_strength}")
    if getattr(state, "volatility", None) is not None:
        lines.append(f"- Volatility: {state.volatility}")
    return lines


def _render_tools(tools: list[dict[str, Any]]) -> list[str]:
    lines = ["## Available Tools", ""]
    for tool in tools:
        name = tool.get("name", "")
        description = tool.get("description", "")
        lines.append(f"### {name}")
        if description:
            lines.append(f"{description}")
        parameters = tool.get("parameters") or {}
        properties = parameters.get("properties") or {}
        required = parameters.get("required") or []
        if properties:
            lines.append("Parameters:")
            for prop_name, prop_spec in properties.items():
                prop_desc = ""
                if isinstance(prop_spec, dict):
                    prop_desc = prop_spec.get("description", "")
                marker = " (required)" if prop_name in required else ""
                suffix = f": {prop_desc}" if prop_desc else ""
                lines.append(f"- {prop_name}{marker}{suffix}")
        lines.append("")
    return lines


def build_system_prompt(context: AgentContext) -> str:
    """Compose the full system prompt from the agent context."""
    sections: list[str] = []

    sections.extend(
        [
            "You are the SeleneQuant AI assistant for quantitative crypto trading.",
            "",
            "You help users analyze markets, develop and backtest strategies,",
            "and manage paper-trading deployments.",
            "",
            "# Safety",
            "- Never place live orders or move real funds.",
            "- Prefer conservative defaults when information is uncertain.",
            "- Surface risks explicitly instead of hiding them behind confidence.",
            "",
        ]
    )

    if context.role is not None:
        role_name = str(context.role).split(".")[-1]
        role_title = role_name.replace("_", " ").title()
        sections.extend(["# Role", f"You are acting as a {role_title}.", ""])

    if context.market_state is not None:
        sections.extend(_render_market_state(context.market_state))
        sections.append("")

    if context.user_preferences:
        sections.extend(["## User Preferences", ""])
        for key, value in context.user_preferences.items():
            sections.append(f"- {key}: {value}")
        sections.append("")

    if context.active_symbols:
        sections.extend(
            [
                "## Active Symbols",
                "",
                ", ".join(context.active_symbols),
                "",
            ]
        )

    if context.memory_context:
        sections.extend(["## Memory Context", "", context.memory_context, ""])

    if context.available_tools:
        sections.extend(_render_tools(context.available_tools))

    if context.skills_prompt:
        sections.extend([context.skills_prompt, ""])

    if context.react_enabled:
        sections.extend(
            [
                "# Reasoning Protocol",
                "",
                "Think before you act: state what you need to find out,",
                "call tools to gather evidence, reflect on results, then answer.",
                "",
            ]
        )

    return "\n".join(sections).rstrip() + "\n"
